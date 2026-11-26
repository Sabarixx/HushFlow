"""
HushFlow Engine - Local Offline Push-to-Talk & Hands-Free Voice Typing Service
Powered by whisper.cpp (in-memory whisper-server + CLI fallback).

Supports:
- Direct in-memory RAM WAV streaming (zero temporary disk writes when server is healthy)
- Push-to-Talk ([Ctrl + Alt], [Win + Alt], or [Right Alt]) + Hands-Free ([Ctrl + Alt + Space] / [Esc])
- Non-destructive clipboard preservation & Win32 AttachThreadInput focus restoration
- 3x supersampled 60 FPS floating edge pill (desktop_hud.py) & crisp squircle system tray icon
- Loopback UDP IPC for real-time Studio mirroring, live hot-reload, and Alt+Z QuickFix
"""

import os
import sys
import time
import socket
import tempfile
import subprocess
import winsound
import atexit
import logging
import json
import threading
import ctypes
import io
import shutil

import psutil

from app_paths import (
    APP_NAME,
    CONFIG_PATH,
    IS_FROZEN,
    INSTALL_DIR,
    MAIN_LOG_FILE,
    SERVER_LOG_FILE,
    PID_FILE,
    STUDIO_IPC_PORT,
    USER_DATA_DIR,
    MODEL_REGISTRY,
    canonical_model_key,
    ensure_initialized,
    find_binary,
    get_hardware_summary,
    load_config,
    resolve_model_path,
    save_config,
)
from brand_assets import render_tray_icon

ENGINE_CTL_PORT = 48766

ensure_initialized()
logging.basicConfig(
    filename=MAIN_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


class SafeWriter:
    def __init__(self, original):
        self.original = original

    def write(self, s):
        try:
            if self.original:
                self.original.write(s)
        except Exception:
            pass

    def flush(self):
        try:
            if self.original:
                self.original.flush()
        except Exception:
            pass


sys.stdout = SafeWriter(sys.stdout)
sys.stderr = SafeWriter(sys.stderr)

import requests
import requests.adapters
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wav
import keyboard
import pyperclip
import pystray

from desktop_hud import DesktopHUD
from smart_cleanup import apply_smart_cleanup
from quick_fix import spawn_quick_fix, record_correction_and_learn

HTTP_SESSION = requests.Session()
HTTP_SESSION.mount("http://", requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=10))

last_dictation_event = {
    "text": "",
    "wav_path": "",
    "hwnd": None,
    "window_title": "",
    "timestamp": 0,
}

WHISPER_SERVER = find_binary("whisper-server.exe")
WHISPER_CLI = find_binary("whisper-cli.exe")

CONFIG = load_config()
CLEANUP_LEVEL = CONFIG.get("cleanup_level", "medium").lower()
ACTIVE_MODEL_PATH = resolve_model_path(CONFIG.get("model"))

SAMPLE_RATE = 16000
CHANNELS = 1
SERVER_URL = "http://127.0.0.1:8080"
server_process = None
_hud_ref = None
_reload_mic_flag = threading.Event()
_shutdown_flag = threading.Event()


def notify_studio(payload: dict):
    """Best-effort connectionless UDP loopback packet to HushFlow Studio."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(payload, ensure_ascii=False).encode("utf-8"), ("127.0.0.1", STUDIO_IPC_PORT))
        sock.close()
    except Exception:
        pass


def open_studio_window():
    """Focus already-running HushFlow Studio via IPC, or spawn Studio process."""
    notify_studio({"type": "show_studio"})
    try:
        # Check if Studio process is already running
        studio_alive = False
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = proc.info.get("cmdline") or []
                cmd_str = " ".join(cmd).lower()
                if "whisper_flow_studio.py" in cmd_str:
                    studio_alive = True
                    break
                if IS_FROZEN and os.path.basename(sys.executable).lower() == (proc.info.get("name") or "").lower():
                    if "--engine" not in cmd_str and "--stop" not in cmd_str and "--quickfix" not in cmd_str:
                        studio_alive = True
                        break
            except Exception:
                pass

        if not studio_alive:
            if IS_FROZEN:
                subprocess.Popen([sys.executable], cwd=INSTALL_DIR)
            else:
                studio_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper_flow_studio.py")
                pythonw = sys.executable.replace("python.exe", "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                subprocess.Popen([pythonw, studio_script], cwd=os.path.dirname(os.path.abspath(__file__)))
    except Exception as e:
        logging.warning(f"Could not launch HushFlow Studio: {e}")


_engine_mutex_handle = None


def check_single_instance():
    """Kernel-enforced single instance lock via Windows Named Mutex (immune to cmdline noise)."""
    global _engine_mutex_handle
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Local\\HushFlow_Engine_SingleInstance_V2")
        last_err = kernel32.GetLastError()
        if handle and last_err == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _engine_mutex_handle = handle
        return True
    except Exception:
        current_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["pid"] == current_pid:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                name = (proc.info.get("name") or "").lower()
                if "python" in name and "whisper_flow.py" in cmdline:
                    return False
                if IS_FROZEN and "hushflow" in name and "--engine" in cmdline:
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return True


def save_pid():
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def cleanup_pid():
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


atexit.register(cleanup_pid)


def is_server_healthy():
    try:
        r = HTTP_SESSION.get(f"{SERVER_URL}/health", timeout=0.8)
        return r.status_code == 200
    except Exception:
        try:
            r = HTTP_SESSION.get(SERVER_URL, timeout=0.8)
            return r.status_code == 200
        except Exception:
            return False


def ensure_whisper_server():
    """Start whisper-server.exe in background if not alive, with hardware thread scaling."""
    global server_process, ACTIVE_MODEL_PATH
    ACTIVE_MODEL_PATH = resolve_model_path(CONFIG.get("model"))
    if is_server_healthy():
        logging.info("Whisper server is already running on port 8080.")
        return True

    if not os.path.exists(WHISPER_SERVER) or not os.path.exists(ACTIVE_MODEL_PATH):
        logging.warning(f"whisper-server ({WHISPER_SERVER}) or model ({ACTIVE_MODEL_PATH}) missing. CLI fallback mode.")
        return False

    threads = get_hardware_summary().get("opt_threads_num", "4")
    cmd = [
        WHISPER_SERVER,
        "-m", ACTIVE_MODEL_PATH,
        "--host", "127.0.0.1",
        "--port", "8080",
        "-l", "en",
        "-t", threads,
        "-nt",
        "-sns",
        "-nth", "0.65",
    ]
    try:
        logging.info(f"Starting in-memory whisper server: {' '.join(cmd)}")
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        server_log_file = open(SERVER_LOG_FILE, "w", encoding="utf-8")
        server_process = subprocess.Popen(
            cmd, stdout=server_log_file, stderr=subprocess.STDOUT, creationflags=creationflags
        )
        for _ in range(40):
            time.sleep(0.3)
            if is_server_healthy():
                logging.info(f"In-memory whisper-server online (PID {server_process.pid})")
                return True
        logging.warning("whisper-server did not respond within timeout. Falling back to CLI.")
        return False
    except Exception as e:
        logging.warning(f"Could not launch whisper-server: {e}. Will use CLI fallback.")
        return False


def cleanup_server():
    global server_process
    if server_process is not None:
        try:
            logging.info(f"Terminating whisper server (PID {server_process.pid})")
            server_process.terminate()
            server_process.wait(timeout=2)
        except Exception:
            try:
                server_process.kill()
            except Exception:
                pass
        server_process = None
    # Ensure orphaned whisper-server bound to HushFlow stops cleanly
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if "whisper-server" in (p.info.get("name") or "").lower():
                p.terminate()
        except Exception:
            pass


atexit.register(cleanup_server)


def play_beep_async(freq, duration_ms):
    def _beep():
        try:
            winsound.Beep(freq, duration_ms)
        except Exception:
            pass
    threading.Thread(target=_beep, daemon=True).start()


def is_key_pressed(vk):
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def is_hotkey_down():
    """
    Query Win32 hardware key state for configured Push-to-Talk hotkey.
    Supports 'ctrl+alt' (default, also accepts Win+Alt), 'win+alt', and 'right_alt'.
    Ignores while Tab is pressed so Alt+Tab never triggers dictation.
    """
    try:
        if is_key_pressed(0x09):  # VK_TAB
            return False

        alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
        ctrl = is_key_pressed(0x11) or is_key_pressed(0xA2) or is_key_pressed(0xA3)
        win = is_key_pressed(0x5B) or is_key_pressed(0x5C)
        r_alt = is_key_pressed(0xA5)

        configured = CONFIG.get("hotkey", "ctrl+alt").lower().strip()
        if configured == "right_alt":
            return r_alt and not ctrl and not win
        if "win" in configured and "alt" in configured:
            return win and alt
        return (ctrl and alt) or (win and alt)
    except Exception:
        return False


def is_toggle_hotkey_down():
    """Checks Hands-Free toggle hotkey [Ctrl + Alt + Space] or [Win + Alt + Space]."""
    try:
        if not is_key_pressed(0x20):  # VK_SPACE
            return False
        alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
        ctrl = is_key_pressed(0x11) or is_key_pressed(0xA2) or is_key_pressed(0xA3)
        win = is_key_pressed(0x5B) or is_key_pressed(0x5C)
        return (ctrl and alt) or (win and alt)
    except Exception:
        return False


class HotkeyStateController:
    """
    Deterministic state machine unifying Push-to-Talk, Double-Tap Alt Lock
    (Hands-Free), Ctrl+Alt+Space toggle, and floating HUD Done/Discard actions.
    """

    def __init__(
        self,
        tap_max_sec: float = 0.38,
        double_tap_gap_sec: float = 0.52,
        ptt_grace_sec: float = 0.45,
        toggle_debounce_sec: float = 0.45,
        lock_stop_guard_sec: float = 0.40,
        max_locked_sec: float = 900.0,
    ):
        self.tap_max_sec = tap_max_sec
        self.double_tap_gap_sec = double_tap_gap_sec
        self.ptt_grace_sec = ptt_grace_sec
        self.toggle_debounce_sec = toggle_debounce_sec
        self.lock_stop_guard_sec = lock_stop_guard_sec
        self.max_locked_sec = max_locked_sec

        self.mode = "idle"  # "idle" | "ptt" | "locked"
        self._prev_hotkey_down = False
        self._prev_toggle_down = False
        self._prev_esc_down = False
        self._ptt_start_time = -10.0
        self._ptt_press_time = None
        self._ptt_release_time = None
        self._last_short_tap_release_time = None
        self._last_toggle_time = -10.0
        self._lock_engaged_time = -10.0
        self._await_lock_release = False

    def _reset_to_idle(self, clear_tap_history: bool = True):
        self.mode = "idle"
        self._ptt_press_time = None
        self._ptt_release_time = None
        self._await_lock_release = False
        if clear_tap_history:
            self._last_short_tap_release_time = None

    def _engage_lock(self, now: float):
        self.mode = "locked"
        self._await_lock_release = True
        self._lock_engaged_time = now
        self._ptt_release_time = None
        self._last_short_tap_release_time = None

    def step(
        self,
        now: float,
        hotkey_down: bool,
        toggle_down: bool,
        esc_down: bool,
        config: dict,
        pill_action: str = None,
    ) -> str:
        """Advance state by one tick and return action: none|start_ptt|start_locked|upgrade_to_locked|transcribe|cancel."""
        hotkey_edge = hotkey_down and not self._prev_hotkey_down
        hotkey_fall = (not hotkey_down) and self._prev_hotkey_down
        toggle_edge = toggle_down and not self._prev_toggle_down
        esc_edge = esc_down and not self._prev_esc_down

        self._prev_hotkey_down = hotkey_down
        self._prev_toggle_down = toggle_down
        self._prev_esc_down = esc_down

        double_tap_enabled = bool(config.get("double_tap_lock", True))
        toggle_enabled = bool(config.get("hands_free_toggle_enabled", True))

        # 1. Explicit HUD floating pill buttons (Done -> transcribe, X -> discard)
        if pill_action == "cancel" and self.mode in ("ptt", "locked"):
            self._reset_to_idle(clear_tap_history=True)
            return "cancel"
        if pill_action == "finish" and self.mode in ("ptt", "locked"):
            self._reset_to_idle(clear_tap_history=True)
            return "transcribe"

        # 2. Hands-Free toggle chord [Ctrl + Alt + Space]
        if toggle_enabled and toggle_edge and (now - self._last_toggle_time) > self.toggle_debounce_sec:
            self._last_toggle_time = now
            if self.mode == "idle":
                self._engage_lock(now)
                return "start_locked"
            if self.mode == "ptt" and (now - self._ptt_start_time) <= self.toggle_debounce_sec:
                # Chord completion (Ctrl+Alt pressed just before Space) upgrades cleanly to locked listening
                self._engage_lock(now)
                return "upgrade_to_locked"
            self._reset_to_idle(clear_tap_history=True)
            return "transcribe"

        # 3. Active Locked Hands-Free listening
        if self.mode == "locked":
            if self._await_lock_release:
                if not hotkey_down and not toggle_down:
                    self._await_lock_release = False
            else:
                if (now - self._lock_engaged_time) >= self.max_locked_sec:
                    self._reset_to_idle(clear_tap_history=True)
                    return "transcribe"
                if (now - self._lock_engaged_time) >= self.lock_stop_guard_sec:
                    if esc_edge or (hotkey_edge and not toggle_down):
                        self._reset_to_idle(clear_tap_history=True)
                        return "transcribe"
            return "none"

        # 4. Active Push-to-Talk hold
        if self.mode == "ptt":
            if hotkey_down:
                if hotkey_edge:
                    self._ptt_press_time = now
                    if (
                        double_tap_enabled
                        and self._last_short_tap_release_time is not None
                        and (now - self._last_short_tap_release_time) <= self.double_tap_gap_sec
                    ):
                        self._engage_lock(now)
                        return "upgrade_to_locked"
                self._ptt_release_time = None
                return "none"

            if hotkey_fall:
                self._ptt_release_time = now
                hold_dur = now - (self._ptt_press_time if self._ptt_press_time is not None else now)
                self._last_short_tap_release_time = now if hold_dur <= self.tap_max_sec else None

            if self._ptt_release_time is not None and (now - self._ptt_release_time) >= self.ptt_grace_sec:
                self._reset_to_idle(clear_tap_history=False)
                return "transcribe"
            return "none"

        # 5. Idle state
        if hotkey_edge:
            if (
                double_tap_enabled
                and self._last_short_tap_release_time is not None
                and (now - self._last_short_tap_release_time) <= self.double_tap_gap_sec
            ):
                self._engage_lock(now)
                return "start_locked"
            self.mode = "ptt"
            self._ptt_start_time = now
            self._ptt_press_time = now
            self._ptt_release_time = None
            return "start_ptt"

        return "none"


MIN_AUDIO_DURATION = 0.35


def calculate_audio_rms(audio_data):
    if len(audio_data) == 0:
        return 0.0
    samples = audio_data.astype(np.float64).flatten()
    return float(np.sqrt(np.mean(samples ** 2)))


def is_audio_silent(audio_data, duration_sec):
    if duration_sec < MIN_AUDIO_DURATION:
        return True
    if duration_sec < 1.0:
        peak = float(np.max(np.abs(audio_data)))
        rms = calculate_audio_rms(audio_data)
        if peak < 500 and rms < 130.0:
            return True
    return False


HALLUCINATION_BLACKLIST = {
    "[blank_audio]", "[silence]", "(silence)", "(music)", "(applause)",
    "[applause]", "(bell rings)", "[whispering]", "(whispering)", ".", "you", "thank you.",
}


def is_hallucination_or_empty(text: str) -> bool:
    if not text:
        return True
    cleaned = text.strip()
    if not cleaned or len(cleaned) <= 1:
        return True
    if cleaned.lower() in HALLUCINATION_BLACKLIST:
        return True
    if "Numbers, OTPs, codes" in cleaned:
        return True
    return False


def get_active_window_prompt():
    """Detect foreground window context and combine with custom lexicon terms."""
    global CONFIG
    CONFIG = load_config()
    title = ""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.lower()
    except Exception:
        pass

    base_priming = "Clear English speech dictation. Accurate punctuation, technical terminology, and natural phrasing."
    coding_indicators = [
        "code", "visual studio", "terminal", "powershell", "cmd", "bash", "sublime",
        "git", "intellij", "pycharm", ".py", ".ts", ".js", ".cpp", ".rs",
    ]
    comm_indicators = ["slack", "teams", "outlook", "chrome", "edge", "firefox", "discord", "mail", "whatsapp"]

    domain_terms = []
    if any(ind in title for ind in coding_indicators):
        domain_terms = ["Python", "PowerShell", "JSON", "API", "Git", "function", "variable", "CLI", "terminal", "async", "def", "class", "return", "import"]
    elif any(ind in title for ind in comm_indicators):
        domain_terms = ["email", "meeting", "schedule", "calendar", "message", "project", "client", "deadline", "regards"]

    custom_vocab = CONFIG.get("custom_vocabulary", [])
    rep_targets = list(CONFIG.get("word_replacements", {}).values())

    all_prompts = [base_priming]
    if domain_terms:
        all_prompts.append(", ".join(domain_terms))
    if custom_vocab:
        all_prompts.append(", ".join(custom_vocab))
    if rep_targets:
        all_prompts.append(", ".join(rep_targets))

    return " ".join(all_prompts)


def transcribe_audio(audio_source, temp_wav_path=None):
    """
    In-memory transcription directly from RAM (BytesIO WAV -> HTTP POST).
    Falls back cleanly to whisper-cli.exe only if whisper-server is unreachable.
    """
    prompt = get_active_window_prompt()

    wav_bytes = None
    if isinstance(audio_source, np.ndarray):
        buf = io.BytesIO()
        wav.write(buf, SAMPLE_RATE, audio_source)
        wav_bytes = buf.getvalue()
    elif isinstance(audio_source, (bytes, bytearray)):
        wav_bytes = audio_source
    elif isinstance(audio_source, str) and os.path.exists(audio_source):
        with open(audio_source, "rb") as f:
            wav_bytes = f.read()

    dyn_timeout = max(60.0, (len(audio_source) / SAMPLE_RATE * 3.0) if isinstance(audio_source, np.ndarray) else 60.0)
    if is_server_healthy() and wav_bytes:
        try:
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {"temperature": "0.0", "response_format": "json", "prompt": prompt}
            resp = HTTP_SESSION.post(f"{SERVER_URL}/inference", files=files, data=data, timeout=dyn_timeout)
            if resp.status_code == 200:
                return resp.json().get("text", "").strip()
        except Exception as e:
            logging.warning(f"whisper-server inference failed: {e}. Falling back to CLI.")

    # Strategy 2: CLI fallback (temp file written and immediately deleted)
    created_temp = False
    target_wav = audio_source if isinstance(audio_source, str) else None
    if not target_wav and wav_bytes and temp_wav_path:
        with open(temp_wav_path, "wb") as f:
            f.write(wav_bytes)
        target_wav = temp_wav_path
        created_temp = True

    threads = get_hardware_summary().get("opt_threads_num", "4")
    try:
        if target_wav and os.path.exists(target_wav) and os.path.exists(WHISPER_CLI):
            cmd = [
                WHISPER_CLI,
                "-m", resolve_model_path(CONFIG.get("model")),
                "-f", target_wav,
                "-nt",
                "-np",
                "-t", threads,
                "-fa",
                "-nth", "0.65",
                "-sns",
                "--prompt", prompt,
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=dyn_timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return res.stdout.strip()
    except Exception as e:
        logging.error(f"whisper-cli execution failed: {e}")
    finally:
        if created_temp and target_wav and os.path.exists(target_wav):
            try:
                os.remove(target_wav)
            except Exception:
                pass

    return ""


def paste_text(text, target_hwnd=None):
    if not text:
        return

    start = time.time()
    while time.time() - start < 0.5:
        alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
        ctrl = is_key_pressed(0x11) or is_key_pressed(0xA2) or is_key_pressed(0xA3)
        win = is_key_pressed(0x5B) or is_key_pressed(0x5C)
        if not (alt or ctrl or win):
            break
        time.sleep(0.02)
    time.sleep(0.04)

    old_clipboard = None
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        pass

    if target_hwnd and ctypes.windll.user32.IsWindow(target_hwnd):
        try:
            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
            if fg_hwnd != target_hwnd:
                cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                target_thread = ctypes.windll.user32.GetWindowThreadProcessId(target_hwnd, None)
                if cur_thread != target_thread:
                    ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, True)
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    ctypes.windll.user32.BringWindowToTop(target_hwnd)
                    ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, False)
                else:
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.04)
        except Exception:
            pass

    try:
        pyperclip.copy(text)
        time.sleep(0.04)
        keyboard.send("ctrl+v")
        time.sleep(0.06)
    except Exception as e:
        logging.warning(f"Clipboard paste failed: {e}")

    def _restore_clipboard():
        try:
            time.sleep(0.5)
            if old_clipboard is not None:
                pyperclip.copy(old_clipboard)
        except Exception:
            pass

    threading.Thread(target=_restore_clipboard, daemon=True).start()


def create_tray_icon_image(state="idle"):
    """Delegate tray icon rendering to 4x supersampled HushFlow squircle generator."""
    return render_tray_icon(state=state, size=64)


def set_cleanup_level(level, hud=None):
    global CLEANUP_LEVEL, CONFIG
    CLEANUP_LEVEL = level
    CONFIG = load_config()
    CONFIG["cleanup_level"] = level
    save_config(CONFIG)
    if hud:
        hud.set_state("idle")
    logging.info(f"Cleanup level updated to: {level}")


def set_active_model(model_name):
    global ACTIVE_MODEL_PATH, CONFIG
    key = canonical_model_key(model_name)
    cand = resolve_model_path(key, fallback_any=False)
    if os.path.exists(cand):
        ACTIVE_MODEL_PATH = cand
        CONFIG = load_config()
        CONFIG["model"] = key
        save_config(CONFIG)
        logging.info(f"Active model set to {key} ({ACTIVE_MODEL_PATH})")
        cleanup_server()
        ensure_whisper_server()


def _start_control_listener():
    """Listen on UDP 48766 so HushFlow Studio can hot-reload config, mic, HUD visibility, or model."""
    def _worker():
        global CONFIG, CLEANUP_LEVEL
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", ENGINE_CTL_PORT))
            sock.settimeout(1.0)
        except Exception:
            return

        while not _shutdown_flag.is_set():
            try:
                data, _ = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                cmd = msg.get("cmd")
                if cmd == "reload_config":
                    old_mic = CONFIG.get("mic_device")
                    old_model = CONFIG.get("model")
                    CONFIG = load_config()
                    CLEANUP_LEVEL = CONFIG.get("cleanup_level", "medium").lower()
                    if _hud_ref is not None:
                        _hud_ref.set_visible(bool(CONFIG.get("hud_visible", True)))
                    if CONFIG.get("mic_device") != old_mic:
                        _reload_mic_flag.set()
                    if CONFIG.get("model") != old_model:
                        threading.Thread(target=set_active_model, args=(CONFIG.get("model"),), daemon=True).start()
                elif cmd == "stop":
                    _shutdown_flag.set()
            except socket.timeout:
                continue
            except Exception:
                pass
        try:
            sock.close()
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def stop_running_engine():
    """Cleanly terminate any active HushFlow Engine and whisper-server processes."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b'{"cmd":"stop"}', ("127.0.0.1", ENGINE_CTL_PORT))
        sock.close()
        time.sleep(0.4)
    except Exception:
        pass

    current_pid = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == current_pid:
                continue
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            name = (p.info.get("name") or "").lower()
            if "whisper_flow.py" in cmd or ("hushflow" in name and "--engine" in cmd) or "whisper-server" in name:
                p.terminate()
        except Exception:
            pass

    cleanup_pid()


def main():
    global CONFIG, CLEANUP_LEVEL, _hud_ref

    if not check_single_instance():
        logging.warning("HushFlow Engine already running. Aborting duplicate start.")
        sys.exit(0)

    logging.info(f"{APP_NAME} engine started (PID {os.getpid()})")
    save_pid()
    _start_control_listener()

    threading.Thread(target=ensure_whisper_server, daemon=True).start()

    pending_pill_lock = threading.Lock()
    pending_pill_action = None

    def _queue_pill_action(act: str):
        nonlocal pending_pill_action
        with pending_pill_lock:
            pending_pill_action = act

    def _pop_pill_action():
        nonlocal pending_pill_action
        with pending_pill_lock:
            act = pending_pill_action
            pending_pill_action = None
            return act

    hud = None
    try:
        hud_visible = CONFIG.get("hud_visible", True)
        hud = DesktopHUD(
            config_path=CONFIG_PATH,
            visible=hud_visible,
            on_finish=lambda: _queue_pill_action("finish"),
            on_cancel=lambda: _queue_pill_action("cancel"),
        )
        _hud_ref = hud
    except Exception as e:
        logging.warning(f"Could not initialize desktop HUD: {e}")

    tray_icon = None
    try:
        def on_quit(icon, item):
            _shutdown_flag.set()
            icon.stop()

        def toggle_hud(icon, item):
            if hud:
                new_state = not hud.visible
                hud.set_visible(new_state)
                cfg = load_config()
                cfg["hud_visible"] = new_state
                save_config(cfg)

        icon_menu = pystray.Menu(
            pystray.MenuItem(f"{APP_NAME} — Ready", None, enabled=False),
            pystray.MenuItem(f"Open {APP_NAME} Studio", lambda icon, item: open_studio_window(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Floating Edge Indicator", toggle_hud, checked=lambda item: hud.visible if hud else False),
            pystray.MenuItem(
                "Cleanup Mode",
                pystray.Menu(
                    pystray.MenuItem("None (Verbatim)", lambda icon, item: set_cleanup_level("none", hud), checked=lambda item: CLEANUP_LEVEL == "none"),
                    pystray.MenuItem("Light (Filler Removal)", lambda icon, item: set_cleanup_level("light", hud), checked=lambda item: CLEANUP_LEVEL == "light"),
                    pystray.MenuItem("Medium (Self-Correction)", lambda icon, item: set_cleanup_level("medium", hud), checked=lambda item: CLEANUP_LEVEL == "medium"),
                    pystray.MenuItem("High (Full Polish)", lambda icon, item: set_cleanup_level("high", hud), checked=lambda item: CLEANUP_LEVEL == "high"),
                ),
            ),
            pystray.MenuItem(
                "Speech Model",
                pystray.Menu(
                    pystray.MenuItem(
                        "Small English (Balanced)",
                        lambda icon, item: set_active_model("small.en-q5_1"),
                        checked=lambda item: "small" in ACTIVE_MODEL_PATH,
                        enabled=lambda item: os.path.exists(resolve_model_path("small.en-q5_1", fallback_any=False)),
                    ),
                    pystray.MenuItem(
                        "Large v3 Turbo (High Accuracy)",
                        lambda icon, item: set_active_model("large-v3-turbo"),
                        checked=lambda item: "large-v3-turbo" in ACTIVE_MODEL_PATH,
                        enabled=lambda item: os.path.exists(resolve_model_path("large-v3-turbo", fallback_any=False)),
                    ),
                    pystray.MenuItem(
                        "Base English (Ultra-Light)",
                        lambda icon, item: set_active_model("base.en"),
                        checked=lambda item: "base" in ACTIVE_MODEL_PATH,
                        enabled=lambda item: os.path.exists(resolve_model_path("base.en", fallback_any=False)),
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Quit {APP_NAME}", on_quit),
        )

        tray_icon = pystray.Icon(
            APP_NAME,
            create_tray_icon_image("idle"),
            f"{APP_NAME} — Ready",
            icon_menu,
        )
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except Exception as e:
        logging.warning(f"Could not initialize system tray icon: {e}")

    audio_frames = []
    is_recording = False
    is_hands_free = False
    locked_target_hwnd = None
    controller = HotkeyStateController()
    stream = None

    def _safe_tray_update(state: str, title: str):
        if tray_icon:
            try:
                tray_icon.icon = create_tray_icon_image(state)
                tray_icon.title = title
            except Exception:
                pass

    def _capture_active_target_hwnd():
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg and (not hud or not hud.hwnd or fg != hud.hwnd):
                return fg
        except Exception:
            pass
        return locked_target_hwnd

    def audio_callback(indata, frames, time_info, status):
        if is_recording:
            audio_frames.append(indata.copy())
            if hud:
                peak = float(np.max(np.abs(indata)))
                hud.set_audio_level(min(1.0, peak / 2500.0))

    def _open_audio_stream():
        dev = CONFIG.get("mic_device")
        kwargs = dict(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", callback=audio_callback)
        if dev is not None:
            try:
                s = sd.InputStream(device=dev, **kwargs)
                s.start()
                return s
            except Exception as exc:
                logging.warning(f"Configured mic device '{dev}' failed ({exc}); falling back to system default.")
        s = sd.InputStream(**kwargs)
        s.start()
        return s

    stream = _open_audio_stream()
    temp_wav = os.path.join(tempfile.gettempdir(), "hushflow_fallback_tmp.wav")

    try:
        keyboard.add_hotkey("ctrl+shift+q", lambda: _shutdown_flag.set())
    except Exception:
        pass

    last_hud_watchdog = 0
    try:
        while not _shutdown_flag.is_set():
            now_loop = time.time()
            if _reload_mic_flag.is_set() and not is_recording:
                _reload_mic_flag.clear()
                try:
                    if stream:
                        stream.stop()
                        stream.close()
                    stream = _open_audio_stream()
                    logging.info("Audio input stream reloaded cleanly.")
                except Exception as exc:
                    logging.warning(f"Failed reloading mic stream: {exc}")

            if hud and (now_loop - last_hud_watchdog > 1.0):
                last_hud_watchdog = now_loop
                hud.ensure_alive()

            hotkey_active = is_hotkey_down()
            toggle_active = is_toggle_hotkey_down()
            esc_pressed = is_key_pressed(0x1B)
            pill_action = _pop_pill_action()

            action = controller.step(
                now_loop,
                hotkey_down=hotkey_active,
                toggle_down=toggle_active,
                esc_down=esc_pressed,
                config=CONFIG,
                pill_action=pill_action,
            )

            if action == "start_ptt":
                locked_target_hwnd = _capture_active_target_hwnd()
                audio_frames = []
                is_hands_free = False
                is_recording = True
                play_beep_async(900, 60)
                notify_studio({"type": "state", "state": "recording", "locked": False})
                if hud:
                    hud.set_state("recording", locked=False)
                _safe_tray_update("recording", f"{APP_NAME} — Listening...")

            elif action == "start_locked":
                locked_target_hwnd = _capture_active_target_hwnd()
                audio_frames = []
                is_hands_free = True
                is_recording = True
                play_beep_async(1180, 75)
                notify_studio({"type": "state", "state": "recording", "locked": True})
                if hud:
                    hud.set_state("recording", locked=True)
                _safe_tray_update("recording", f"{APP_NAME} — Hands-Free Locked")

            elif action == "upgrade_to_locked":
                # Keep all audio already recorded since Tap 1 intact
                is_hands_free = True
                is_recording = True
                play_beep_async(1180, 75)
                notify_studio({"type": "state", "state": "recording", "locked": True})
                if hud:
                    hud.set_state("recording", locked=True)
                _safe_tray_update("recording", f"{APP_NAME} — Hands-Free Locked")

            elif action == "cancel" and is_recording:
                is_recording = False
                is_hands_free = False
                audio_frames = []
                play_beep_async(420, 55)
                if hud:
                    hud.set_state("idle")
                notify_studio({"type": "state", "state": "idle"})
                _safe_tray_update("idle", f"{APP_NAME} — Ready")

            elif action == "transcribe" and is_recording:
                is_recording = False
                is_hands_free = False
                time.sleep(0.08)
                play_beep_async(650, 60)
                notify_studio({"type": "state", "state": "transcribing"})
                if hud:
                    hud.set_state("transcribing")
                _safe_tray_update("transcribing", f"{APP_NAME} — Transcribing...")

                if len(audio_frames) > 0:
                    audio_data = np.concatenate(audio_frames, axis=0)
                    duration_sec = len(audio_data) / SAMPLE_RATE

                    if is_audio_silent(audio_data, duration_sec):
                        if hud:
                            hud.set_state("idle")
                        notify_studio({"type": "state", "state": "idle"})
                    else:
                        raw_text = transcribe_audio(audio_data, temp_wav_path=temp_wav)
                        if raw_text and not is_hallucination_or_empty(raw_text):
                            cleaned_text = apply_smart_cleanup(raw_text, level=CLEANUP_LEVEL, config=CONFIG)
                            logging.info(f"Transcribed ({duration_sec:.1f}s): len={len(cleaned_text)}")

                            target_hwnd = _capture_active_target_hwnd() or locked_target_hwnd
                            target_title = ""
                            try:
                                tl = ctypes.windll.user32.GetWindowTextLengthW(target_hwnd)
                                if tl > 0:
                                    tbuf = ctypes.create_unicode_buffer(tl + 1)
                                    ctypes.windll.user32.GetWindowTextW(target_hwnd, tbuf, tl + 1)
                                    target_title = tbuf.value
                            except Exception:
                                pass

                            # Strictly honor save_training_audio opt-in: zero temp WAV writes unless opted in
                            last_wav_snapshot = None
                            if CONFIG.get("save_training_audio", False):
                                last_wav_snapshot = os.path.join(tempfile.gettempdir(), f"hushflow_last_{int(time.time())}.wav")
                                try:
                                    wav.write(last_wav_snapshot, SAMPLE_RATE, audio_data)
                                except Exception:
                                    last_wav_snapshot = None

                            last_dictation_event = {
                                "text": cleaned_text,
                                "wav_path": last_wav_snapshot,
                                "hwnd": target_hwnd,
                                "window_title": target_title,
                                "timestamp": time.time(),
                            }

                            paste_text(cleaned_text, target_hwnd=target_hwnd)
                            notify_studio({
                                "type": "transcript",
                                "raw": raw_text,
                                "cleaned": cleaned_text,
                                "ts": time.strftime("%H:%M:%S"),
                            })
                            if hud:
                                hud.set_state("done", text="Done")
                        else:
                            if hud:
                                hud.set_state("idle")
                            notify_studio({"type": "state", "state": "idle"})
                else:
                    if hud:
                        hud.set_state("idle")
                    notify_studio({"type": "state", "state": "idle"})

                _safe_tray_update("idle", f"{APP_NAME} — Ready")

            # Check QuickFix Hotkey [Alt + Z]
            is_alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
            is_z = is_key_pressed(0x5A)
            if is_alt and is_z and not is_recording:
                if last_dictation_event.get("text") and (time.time() - last_dictation_event.get("timestamp", 0) < 15.0):
                    orig_to_fix = last_dictation_event["text"]
                    last_dictation_event["timestamp"] = 0

                    def handle_correction_submitted(corrected_text):
                        global CONFIG
                        learned = record_correction_and_learn(last_dictation_event, corrected_text, CONFIG_PATH)
                        CONFIG = load_config()
                        lbl = f"Learned: {learned[0]}" if learned else "Lexicon Updated"
                        if hud:
                            hud.set_state("done", text=lbl)
                        notify_studio({"type": "lexicon_updated"})

                    spawn_quick_fix(orig_to_fix, handle_correction_submitted)
                    time.sleep(0.35)

            time.sleep(0.016)

    except (KeyboardInterrupt, SystemExit):
        logging.info("HushFlow exiting cleanly.")
    except Exception as e:
        logging.exception(f"HushFlow unexpected error: {e}")
    finally:
        cleanup_pid()
        cleanup_server()
        if hud:
            hud.close()
        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        os.makedirs(os.path.dirname(MAIN_LOG_FILE), exist_ok=True)
        with open(os.path.join(os.path.dirname(MAIN_LOG_FILE), "error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
