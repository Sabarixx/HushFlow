"""
WhisperFlow - Push-to-Talk Voice Typing for Windows powered by whisper.cpp
Hold [Ctrl + Alt] -> Speak -> Release -> Auto-types into active window!

Features:
- In-Memory Engine: Runs whisper-server.exe in background with large-v3-turbo (sub-second speech inference)
- Graceful CLI Fallback: Seamlessly falls back to whisper-cli.exe if server is unreachable
- STRICT Single Hotkey: Ctrl + Alt Push-to-Talk (Zero Start menu / Wispr Flow conflicts)
- Context & Domain Priming: Active window detection primes technical, digit, & custom vocabulary
- High-Resolution 60 FPS HUD: Native Windows 32-bit ARGB Layered Window (DPI-aware, zero blur)
- Robust Process Management: Stale mutex immune, reliable tray and HUD initialization
"""

import os
import sys
import time
import queue
import tempfile
import subprocess
import winsound
import atexit
import logging
import json
import threading
import ctypes
import psutil
import io

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "whisper_flow.log")
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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
from PIL import Image, ImageDraw
import pystray

# Persistent HTTP Session with connection pooling for sub-millisecond request dispatch
HTTP_SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=10)
HTTP_SESSION.mount("http://", adapter)

from desktop_hud import DesktopHUD
from smart_cleanup import apply_smart_cleanup
from quick_fix import spawn_quick_fix, record_correction_and_learn

# Tracking last dictation for 1-click Quick Fix training (Alt + Z)
last_dictation_event = {
    "text": "",
    "wav_path": "",
    "hwnd": None,
    "window_title": "",
    "timestamp": 0
}

# Binary & Config paths
def find_binary(filename):
    for candidate in [
        os.path.join(BASE_DIR, "bin", filename),
        os.path.join(BASE_DIR, filename),
        os.path.join(BASE_DIR, "build", "bin", "Release", filename),
    ]:
        if os.path.exists(candidate):
            return candidate
    return os.path.join(BASE_DIR, "bin", filename)

WHISPER_SERVER = find_binary("whisper-server.exe")
WHISPER_CLI = find_binary("whisper-cli.exe")
PID_FILE = os.path.join(BASE_DIR, "whisper_flow.pid")
CONFIG_FILE = os.path.join(BASE_DIR, "whisper_flow_config.json")

MODEL_REGISTRY = {
    "large-v3-turbo": "ggml-large-v3-turbo-q5_0.bin",
    "large": "ggml-large-v3-turbo-q5_0.bin",
    "large-v3": "ggml-large-v3-turbo-q5_0.bin",
    "small": "ggml-small.en-q5_1.bin",
    "small.en": "ggml-small.en-q5_1.bin",
    "small.en-q5_1": "ggml-small.en-q5_1.bin",
    "base": "ggml-base.en.bin",
    "base.en": "ggml-base.en.bin",
}

def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"cleanup_level": "medium", "hud_visible": True, "model": "large-v3-turbo", "dock_y": 360}

CONFIG = get_config()
CLEANUP_LEVEL = CONFIG.get("cleanup_level", "medium").lower()

def resolve_model_path():
    req_model = CONFIG.get("model", "small").lower()
    target_filename = MODEL_REGISTRY.get(req_model, "ggml-small.en-q5_1.bin")
    search_dirs = [
        BASE_DIR,
        os.path.join(os.path.dirname(BASE_DIR), "whisper.cpp"),
        os.path.join(BASE_DIR, "models"),
    ]
    # First search for the requested model
    for d in search_dirs:
        candidate = os.path.join(d, target_filename)
        if os.path.exists(candidate):
            return candidate
    # Fallback to any available model in search dirs
    for d in search_dirs:
        for fname in MODEL_REGISTRY.values():
            candidate = os.path.join(d, fname)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(BASE_DIR, target_filename)

ACTIVE_MODEL_PATH = resolve_model_path()
SAMPLE_RATE = 16000
CHANNELS = 1
SERVER_URL = "http://127.0.0.1:8080"
server_process = None

def check_single_instance():
    """
    Stale-immune single instance check:
    Inspects active running processes for an existing whisper_flow.py instance.
    """
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] != current_pid and 'python' in (proc.info['name'] or '').lower():
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if 'whisper_flow.py' in cmdline:
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
        r = HTTP_SESSION.get(SERVER_URL, timeout=0.8)
        return r.status_code == 200
    except Exception:
        return False

def ensure_whisper_server():
    """Start whisper-server.exe in background if not already alive, with persistent memory warm-up."""
    global server_process
    if is_server_healthy():
        logging.info("Whisper server is already running on port 8080.")
        return True

    if not os.path.exists(WHISPER_SERVER) or not os.path.exists(ACTIVE_MODEL_PATH):
        logging.warning("whisper-server.exe or model not found. Running in CLI mode.")
        return False

    cmd = [
        WHISPER_SERVER,
        "-m", ACTIVE_MODEL_PATH,
        "--host", "127.0.0.1",
        "--port", "8080",
        "-l", "en",
        "-t", "8",
        "-nt",
        "-sns",
        "-nth", "0.65"
    ]
    try:
        logging.info(f"Starting in-memory whisper server: {' '.join(cmd)}")
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        server_log_path = os.path.join(BASE_DIR, "whisper_server.log")
        server_log_file = open(server_log_path, "w", encoding="utf-8")
        server_process = subprocess.Popen(cmd, stdout=server_log_file, stderr=subprocess.STDOUT, creationflags=creationflags)
        for _ in range(40):
            time.sleep(0.3)
            if is_server_healthy():
                logging.info(f"In-memory whisper-server online (PID {server_process.pid}) - Model fully warmed into RAM")
                return True
        logging.warning("whisper-server started but did not respond within 9s. Falling back to CLI.")
        return False
    except Exception as e:
        logging.warning(f"Could not launch whisper-server: {e}. Will use CLI fallback.")
        return False

def cleanup_server():
    global server_process
    if server_process is not None:
        try:
            logging.info(f"Terminating in-memory whisper server (PID {server_process.pid})")
            server_process.terminate()
            server_process.wait(timeout=2)
        except Exception:
            try:
                server_process.kill()
            except Exception:
                pass
        server_process = None

atexit.register(cleanup_server)

def play_beep_async(freq, duration_ms):
    """Play feedback beeps in a background thread so audio recording isn't delayed."""
    def _beep():
        try:
            winsound.Beep(freq, duration_ms)
        except Exception:
            pass
    threading.Thread(target=_beep, daemon=True).start()

def is_key_pressed(vk):
    """Direct hardware key state check via GetAsyncKeyState (immune to hook misses/latches)."""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False

def is_hotkey_down():
    """
    Checks if active Push-to-Talk hotkey is pressed.
    Directly queries Win32 hardware state to eliminate stuck keys.
    Supports both [Ctrl + Alt] and [Win + Alt] seamlessly.
    Ignores hotkey if Tab is pressed (prevents Alt+Tab conflict).
    """
    try:
        # Ignore if Tab is pressed (e.g. user is Alt+Tabbing between apps)
        if is_key_pressed(0x09):  # VK_TAB
            return False

        alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)   # VK_MENU, VK_LMENU, VK_RMENU
        ctrl = is_key_pressed(0x11) or is_key_pressed(0xA2) or is_key_pressed(0xA3)  # VK_CONTROL, VK_LCONTROL, VK_RCONTROL
        win = is_key_pressed(0x5B) or is_key_pressed(0x5C)                           # VK_LWIN, VK_RWIN

        configured = CONFIG.get("hotkey", "ctrl+alt").lower()
        if "win" in configured and "alt" in configured:
            return win and alt
        elif "ctrl" in configured and "alt" in configured:
            return (ctrl and alt) or (win and alt)
        else:
            return (ctrl and alt) or (win and alt)
    except Exception:
        return False

def is_toggle_hotkey_down():
    """Checks if Hands-Free toggle hotkey [Ctrl + Alt + Space] or [Win + Alt + Space] is pressed."""
    try:
        space = is_key_pressed(0x20)  # VK_SPACE
        if not space:
            return False
        alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
        ctrl = is_key_pressed(0x11) or is_key_pressed(0xA2) or is_key_pressed(0xA3)
        win = is_key_pressed(0x5B) or is_key_pressed(0x5C)
        return (ctrl and alt and space) or (win and alt and space)
    except Exception:
        return False

# Accidental key tap threshold (sub-350ms presses are ignored)
MIN_AUDIO_DURATION = 0.35

def calculate_audio_rms(audio_data):
    """Calculate the Root-Mean-Square (RMS) amplitude of int16 audio array."""
    if len(audio_data) == 0:
        return 0.0
    samples = audio_data.astype(np.float64).flatten()
    return float(np.sqrt(np.mean(samples ** 2)))

def is_audio_silent(audio_data, duration_sec):
    """
    Intelligent silence detector:
    - Only flags silence on very short clips (< 1.0s) with low amplitude (accidental tap).
    - Never drops sustained speech (>= 1.0s).
    """
    if duration_sec < MIN_AUDIO_DURATION:
        return True
    if duration_sec < 1.0:
        peak = float(np.max(np.abs(audio_data)))
        rms = calculate_audio_rms(audio_data)
        # Background ambient mic noise is ~150-160 RMS; speech peak is > 800
        if peak < 500 and rms < 130.0:
            return True
    return False

HALLUCINATION_BLACKLIST = {
    "[blank_audio]", "[silence]", "(silence)", "(music)", "(applause)",
    "[applause]", "(bell rings)", "[whispering]", "(whispering)", ".", "you", "thank you."
}

def is_hallucination_or_empty(text: str) -> bool:
    """Filter out empty text, Whisper silence tokens, or priming prompt regurgitation."""
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
    """Detect foreground application to dynamically prime Whisper with domain vocabulary."""
    global CONFIG
    CONFIG = get_config()
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

    coding_indicators = ["code", "visual studio", "terminal", "powershell", "cmd", "bash", "sublime", "git", "intellij", "pycharm", ".py", ".ts", ".js", ".cpp", ".rs"]
    comm_indicators = ["slack", "teams", "outlook", "chrome", "edge", "firefox", "discord", "mail", "whatsapp"]

    domain_terms = []
    if any(ind in title for ind in coding_indicators):
        domain_terms = ["Python", "PowerShell", "JSON", "API", "Git", "function", "variable", "CLI", "terminal", "async", "def", "class", "return", "import"]
    elif any(ind in title for ind in comm_indicators):
        domain_terms = ["email", "meeting", "schedule", "calendar", "message", "project", "client", "deadline", "regards"]

    custom_vocab = CONFIG.get("custom_vocabulary", [])
    # Also inject user-defined replacement target words into Whisper's priming prompt
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
    Ultra-low latency in-memory transcription:
    If audio_source is a numpy array, encodes directly in RAM (BytesIO) with 0 disk I/O.
    Falls back to whisper-cli.exe if the server is unreachable.
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

    # Strategy 1: Sub-millisecond In-Memory Server HTTP POST (Zero Disk I/O)
    if is_server_healthy() and wav_bytes:
        try:
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {
                "temperature": "0.0",
                "response_format": "json",
                "prompt": prompt
            }
            dyn_timeout = max(60.0, (len(audio_source) / SAMPLE_RATE * 3.0) if isinstance(audio_source, np.ndarray) else 60.0)
            resp = HTTP_SESSION.post(f"{SERVER_URL}/inference", files=files, data=data, timeout=dyn_timeout)
            if resp.status_code == 200:
                text = resp.json().get("text", "").strip()
                return text
        except Exception as e:
            logging.warning(f"whisper-server inference failed: {e}. Falling back to CLI.")

    # Strategy 2: CLI Fallback (requires disk file)
    target_wav = audio_source if isinstance(audio_source, str) else temp_wav_path
    if not target_wav or not os.path.exists(target_wav):
        if wav_bytes and temp_wav_path:
            with open(temp_wav_path, "wb") as f:
                f.write(wav_bytes)
            target_wav = temp_wav_path

    if target_wav and os.path.exists(target_wav):
        cmd = [
            WHISPER_CLI,
            "-m", ACTIVE_MODEL_PATH,
            "-f", target_wav,
            "-nt",
            "-np",
            "-t", "8",
            "-fa",
            "-nth", "0.65",
            "-sns",
            "--prompt", prompt
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45.0,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            return res.stdout.strip()
        except Exception as e:
            logging.error(f"whisper-cli execution failed: {e}")

    return ""

def paste_text(text, target_hwnd=None):
    if not text:
        return

    # Wait for hotkey release with a strict 0.5s timeout using GetAsyncKeyState
    start = time.time()
    while time.time() - start < 0.5:
        alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
        ctrl = is_key_pressed(0x11) or is_key_pressed(0xA2) or is_key_pressed(0xA3)
        win = is_key_pressed(0x5B) or is_key_pressed(0x5C)
        if not (alt or ctrl or win):
            break
        time.sleep(0.02)
    time.sleep(0.04)

    # 1. Preserve user's existing clipboard contents (zero clipboard pollution)
    old_clipboard = None
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        pass

    # 2. Robust Window Focus Reassertion with AttachThreadInput
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

    # 3. High-Speed Clipboard Injection
    try:
        pyperclip.copy(text)
        time.sleep(0.04)
        keyboard.send('ctrl+v')
        time.sleep(0.06)
    except Exception as e:
        logging.warning(f"Clipboard paste failed: {e}")

    # 4. Asynchronously restore original clipboard contents after 0.5s
    def _restore_clipboard():
        try:
            time.sleep(0.5)
            if old_clipboard is not None:
                pyperclip.copy(old_clipboard)
        except Exception:
            pass
    threading.Thread(target=_restore_clipboard, daemon=True).start()

def create_tray_icon_image(state="idle"):
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = 32, 32
    if state == "recording":
        draw.ellipse((4, 4, 60, 60), fill=(220, 38, 38, 255))
        draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), outline=(255, 255, 255, 220), width=3)
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=(255, 255, 255, 255))
    elif state == "transcribing":
        draw.ellipse((4, 4, 60, 60), fill=(245, 158, 11, 255))
        draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), outline=(255, 255, 255, 220), width=3)
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=(255, 255, 255, 255))
    else:
        # Distinctive WhisperFlow icon: Obsidian disc with bright cyan & ivory concentric rings
        draw.ellipse((4, 4, 60, 60), fill=(22, 22, 24, 255), outline=(60, 60, 65, 255), width=2)
        draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), outline=(56, 189, 248, 230), width=3)
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=(255, 255, 240, 255))
    return img

def set_cleanup_level(level, hud=None):
    global CLEANUP_LEVEL
    CLEANUP_LEVEL = level
    try:
        with open(CONFIG_FILE, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["cleanup_level"] = level
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
    except Exception:
        pass
    if hud:
        hud.set_state("idle")
    logging.info(f"Cleanup level updated to: {level}")

def set_active_model(model_name):
    global ACTIVE_MODEL_PATH
    if model_name in MODEL_REGISTRY and os.path.exists(MODEL_REGISTRY[model_name]):
        ACTIVE_MODEL_PATH = MODEL_REGISTRY[model_name]
        try:
            with open(CONFIG_FILE, "r+", encoding="utf-8") as f:
                data = json.load(f)
                data["model"] = model_name
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
        except Exception:
            pass
        logging.info(f"Active model set to {model_name} ({ACTIVE_MODEL_PATH})")
        cleanup_server()
        ensure_whisper_server()

def main():
    if not check_single_instance():
        logging.warning("WhisperFlow already running. Aborting duplicate start.")
        sys.exit(0)

    logging.info(f"WhisperFlow main started with PID {os.getpid()}")
    save_pid()

    # Pre-warm in-memory whisper server in background thread
    threading.Thread(target=ensure_whisper_server, daemon=True).start()
    
    print("=" * 60)
    print("  WHISPER FLOW - Push-to-Talk Voice Dictation (Pro Tier)")
    print("=" * 60)
    print(f"Server Engine : {WHISPER_SERVER}")
    print(f"CLI Fallback  : {WHISPER_CLI}")
    print(f"Active Model  : {os.path.basename(ACTIVE_MODEL_PATH)}")
    print(f"Cleanup Level : '{CLEANUP_LEVEL.upper()}' (Wispr Flow Smart Engine)")
    print(f"PID           : {os.getpid()} (saved to {PID_FILE})")
    print("-" * 60)
    print("Hotkey (Hold to speak, release to paste):")
    print("  -> Hold [Ctrl + Alt]")
    print("Live Desktop Indicator: Docked to Left Screen Edge (60 FPS Layered Pill)")
    print("System Tray: Active in taskbar notification area")
    print("Exit: Press Ctrl+Shift+Q or right-click Tray icon")
    print("-" * 60)
    print("Status: READY - listening for hotkey...\n")

    # Setup Native High-DPI 60 FPS Desktop HUD
    hud = None
    try:
        hud_visible = CONFIG.get("hud_visible", True)
        hud = DesktopHUD(config_path=CONFIG_FILE, visible=hud_visible)
    except Exception as e:
        logging.warning(f"Could not initialize desktop HUD: {e}")

    # Setup System Tray Icon
    tray_icon = None
    try:
        def on_quit(icon, item):
            icon.stop()
            if hud:
                hud.close()
            cleanup_server()
            sys.exit(0)

        def toggle_hud(icon, item):
            if hud:
                new_state = not hud.visible
                hud.set_visible(new_state)
                CONFIG["hud_visible"] = new_state
                try:
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(CONFIG, f, indent=2)
                except Exception:
                    pass

        def open_settings(icon, item):
            os.system(f'notepad.exe "{CONFIG_FILE}"')

        def open_folder(icon, item):
            os.system(f'explorer.exe "{BASE_DIR}"')

        icon_menu = pystray.Menu(
            pystray.MenuItem("WhisperFlow: Ready (Ctrl + Alt)", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Desktop Floating Widget", toggle_hud, checked=lambda item: hud.visible if hud else False),
            pystray.MenuItem(
                "Cleanup Level",
                pystray.Menu(
                    pystray.MenuItem("None (Verbatim)", lambda icon, item: set_cleanup_level("none", hud), checked=lambda item: CLEANUP_LEVEL == "none"),
                    pystray.MenuItem("Light (Filler Removal)", lambda icon, item: set_cleanup_level("light", hud), checked=lambda item: CLEANUP_LEVEL == "light"),
                    pystray.MenuItem("Medium (Self-Correction)", lambda icon, item: set_cleanup_level("medium", hud), checked=lambda item: CLEANUP_LEVEL == "medium"),
                    pystray.MenuItem("High (Full Polish)", lambda icon, item: set_cleanup_level("high", hud), checked=lambda item: CLEANUP_LEVEL == "high"),
                )
            ),
            pystray.MenuItem(
                "Model Engine",
                pystray.Menu(
                    pystray.MenuItem("Small.en (Fast Sub-Second)", lambda icon, item: set_active_model("small"), checked=lambda item: "small" in ACTIVE_MODEL_PATH),
                    pystray.MenuItem("Large v3 Turbo (High Accuracy)", lambda icon, item: set_active_model("large-v3-turbo"), checked=lambda item: "large-v3-turbo" in ACTIVE_MODEL_PATH),
                    pystray.MenuItem("Base.en (Ultra-light)", lambda icon, item: set_active_model("base"), checked=lambda item: "base" in ACTIVE_MODEL_PATH),
                )
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Settings", open_settings),
            pystray.MenuItem("Open Folder", open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", on_quit)
        )

        tray_icon = pystray.Icon("WhisperFlow", create_tray_icon_image("idle"), f"WhisperFlow - Ready ({CLEANUP_LEVEL.upper()})", icon_menu)
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()
    except Exception as e:
        logging.warning(f"Could not initialize system tray icon: {e}")

    audio_frames = []
    is_recording = False
    is_hands_free = False
    release_start_time = None
    last_toggle_press_time = 0
    stream = None

    def audio_callback(indata, frames, time_info, status):
        if is_recording:
            audio_frames.append(indata.copy())
            if hud:
                # Real-time vocal amplitude fed to HUD 60 FPS equalizer
                peak = float(np.max(np.abs(indata)))
                hud.set_audio_level(min(1.0, peak / 2500.0))

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='int16',
        callback=audio_callback
    )
    stream.start()

    temp_wav = os.path.join(tempfile.gettempdir(), "whisper_flow_recording.wav")

    keyboard.add_hotkey('ctrl+shift+q', lambda: sys.exit(0))

    last_hud_watchdog = 0
    try:
        while True:
            now_loop = time.time()
            if hud and (now_loop - last_hud_watchdog > 1.0):
                last_hud_watchdog = now_loop
                hud.ensure_alive()

            hotkey_active = is_hotkey_down()
            toggle_active = is_toggle_hotkey_down()
            esc_pressed = is_key_pressed(0x1B)  # VK_ESCAPE

            should_stop_recording = False

            # Case A: Hands-Free Dictation Toggle (Ctrl + Alt + Space)
            if toggle_active and (now_loop - last_toggle_press_time > 0.5):
                last_toggle_press_time = now_loop
                if not is_recording:
                    # Enter Hands-Free Recording Mode
                    is_recording = True
                    is_hands_free = True
                    release_start_time = None
                    audio_frames = []
                    play_beep_async(1050, 70)
                    if hud:
                        hud.set_state("recording", text="Hands-Free...")
                    if tray_icon:
                        try:
                            tray_icon.icon = create_tray_icon_image("recording")
                            tray_icon.title = "WhisperFlow - Hands-Free Dictating..."
                        except Exception:
                            pass
                    print("[HANDS-FREE] Recording continuously... Press Ctrl+Alt+Space or Esc to finish.", flush=True)
                else:
                    # Finished hands-free recording
                    should_stop_recording = True

            elif is_hands_free:
                # In hands-free mode, pressing Escape finishes and pastes
                if esc_pressed and (now_loop - last_toggle_press_time > 0.3):
                    last_toggle_press_time = now_loop
                    should_stop_recording = True

            # Case B: Push-to-Talk Mode (Hold Ctrl+Alt with 450ms sustained release debounce)
            elif not is_hands_free:
                if hotkey_active:
                    release_start_time = None
                    if not is_recording:
                        # Started pressing hotkey
                        is_recording = True
                        audio_frames = []
                        play_beep_async(900, 60)
                        if hud:
                            hud.set_state("recording")
                        if tray_icon:
                            try:
                                tray_icon.icon = create_tray_icon_image("recording")
                                tray_icon.title = "WhisperFlow - Recording..."
                            except Exception:
                                pass
                        print("[RECORDING...] Speak now...", end="\r", flush=True)

                elif is_recording:
                    # Key is NOT down right now: check release debounce window
                    if release_start_time is None:
                        release_start_time = now_loop

                    # Only stop if released continuously for at least 450ms!
                    # Prevents premature cutoffs from keyboard matrix chatter or finger shifts
                    if (now_loop - release_start_time) >= 0.45:
                        should_stop_recording = True

            # Process Completed Recording
            if should_stop_recording and is_recording:
                is_recording = False
                is_hands_free = False
                release_start_time = None
                time.sleep(0.08)
                play_beep_async(650, 60)
                if hud:
                    hud.set_state("transcribing")
                if tray_icon:
                    try:
                        tray_icon.icon = create_tray_icon_image("transcribing")
                        tray_icon.title = "WhisperFlow - Transcribing..."
                    except Exception:
                        pass
                print("[PROCESSING...] Transcribing...      ", end="\r", flush=True)

                if len(audio_frames) > 0:
                    audio_data = np.concatenate(audio_frames, axis=0)
                    duration_sec = len(audio_data) / SAMPLE_RATE

                    if is_audio_silent(audio_data, duration_sec):
                        if hud:
                            hud.set_state("idle")
                        print(f"[INFO] Audio too short or silent ({duration_sec:.2f}s), ignored.\n")
                    else:
                        # Ultra-fast in-memory transcription directly from RAM buffer (0 disk wait)
                        raw_text = transcribe_audio(audio_data, temp_wav_path=temp_wav)

                        if raw_text and not is_hallucination_or_empty(raw_text):
                            # Cache audio file for QuickFix vault
                            try:
                                wav.write(temp_wav, SAMPLE_RATE, audio_data)
                            except Exception:
                                pass
                            cleaned_text = apply_smart_cleanup(raw_text, level=CLEANUP_LEVEL, config=CONFIG)
                            logging.info(f"Raw: '{raw_text}' -> Cleaned: '{cleaned_text}'")
                            print(f"[RAW]: \"{raw_text}\"")
                            print(f"[CLEANED]: \"{cleaned_text}\"")

                            # Capture target window details before pasting
                            target_hwnd = ctypes.windll.user32.GetForegroundWindow()
                            target_title = ""
                            try:
                                tl = ctypes.windll.user32.GetWindowTextLengthW(target_hwnd)
                                if tl > 0:
                                    tbuf = ctypes.create_unicode_buffer(tl + 1)
                                    ctypes.windll.user32.GetWindowTextW(target_hwnd, tbuf, tl + 1)
                                    target_title = tbuf.value
                            except Exception:
                                pass

                            # Keep an audio snapshot for the vault in case user corrects
                            last_wav_snapshot = os.path.join(tempfile.gettempdir(), f"whisper_flow_last_{int(time.time())}.wav")
                            try:
                                import shutil
                                shutil.copy2(temp_wav, last_wav_snapshot)
                            except Exception:
                                last_wav_snapshot = temp_wav

                            last_dictation_event = {
                                "text": cleaned_text,
                                "wav_path": last_wav_snapshot,
                                "hwnd": target_hwnd,
                                "window_title": target_title,
                                "timestamp": time.time()
                            }

                            paste_text(cleaned_text, target_hwnd=target_hwnd)
                            if hud:
                                hud.set_state("done", text="Pasted! [Alt+Z to Fix]")
                            print("[PASTED] Done! (Press [Alt + Z] to correct & train)\n")
                        else:
                            if hud:
                                hud.set_state("idle")
                            print("[INFO] No speech detected or blank audio.\n")
                else:
                    if hud:
                        hud.set_state("idle")
                    print("[INFO] No audio captured.\n")

                if tray_icon:
                    try:
                        tray_icon.icon = create_tray_icon_image("idle")
                        tray_icon.title = f"WhisperFlow - Ready ({CLEANUP_LEVEL.upper()})"
                    except Exception:
                        pass

                print("Status: READY - listening for hotkey...", end="\r", flush=True)

            # Check for Quick-Fix Hotkey: [Alt + Z] (VK_MENU + VK_Z)
            is_alt = is_key_pressed(0x12) or is_key_pressed(0xA4) or is_key_pressed(0xA5)
            is_z = is_key_pressed(0x5A)
            if is_alt and is_z and not is_recording:
                if last_dictation_event.get("text") and (time.time() - last_dictation_event.get("timestamp", 0) < 15.0):
                    orig_to_fix = last_dictation_event["text"]
                    last_dictation_event["timestamp"] = 0  # Consume event to prevent re-opening

                    def handle_correction_submitted(corrected_text):
                        global CONFIG
                        learned = record_correction_and_learn(last_dictation_event, corrected_text, CONFIG_FILE)
                        CONFIG = get_config()  # Hot-reload updated vocabulary & mappings into RAM
                        lbl = f"Learned: {learned[0]}" if learned else "Model Updated!"
                        if hud:
                            hud.set_state("done", text=lbl)
                        print(f"[LEARNED] Ground truth saved: '{orig_to_fix}' -> '{corrected_text}'")

                    spawn_quick_fix(orig_to_fix, handle_correction_submitted)
                    time.sleep(0.35)

            time.sleep(0.016)

    except (KeyboardInterrupt, SystemExit) as e:
        logging.info(f"WhisperFlow caught exit: {type(e).__name__}")
        print("\nExiting WhisperFlow...")
    except Exception as e:
        logging.exception(f"WhisperFlow unexpected error: {e}")
    finally:
        logging.info("WhisperFlow cleanup and shutdown")
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
            stream.stop()
            stream.close()
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        with open(os.path.join(BASE_DIR, "error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
