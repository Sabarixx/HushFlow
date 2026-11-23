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
    "large-v3-turbo": os.path.join(BASE_DIR, "ggml-large-v3-turbo-q5_0.bin"),
    "small": os.path.join(BASE_DIR, "ggml-small.en-q5_1.bin"),
    "base": os.path.join(BASE_DIR, "ggml-base.en.bin"),
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
    req_model = CONFIG.get("model", "large-v3-turbo").lower()
    if req_model in MODEL_REGISTRY and os.path.exists(MODEL_REGISTRY[req_model]):
        return MODEL_REGISTRY[req_model]
    for m_path in MODEL_REGISTRY.values():
        if os.path.exists(m_path):
            return m_path
    return MODEL_REGISTRY["large-v3-turbo"]

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
        "-t", "8",
        "-nt",
        "-sns",
        "-nth", "0.35"
    ]
    try:
        logging.info(f"Starting in-memory whisper server: {' '.join(cmd)}")
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        server_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
        for _ in range(30):
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

def is_hotkey_down():
    """
    STRICT SINGLE HOTKEY: ONLY [Ctrl + Alt].
    Zero Windows key checks to avoid conflict with Start Menu & Wispr Flow.
    """
    try:
        ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl') or keyboard.is_pressed('right ctrl')
        alt = keyboard.is_pressed('alt') or keyboard.is_pressed('left alt') or keyboard.is_pressed('right alt')
        return ctrl and alt
    except Exception:
        return False

def get_active_window_prompt():
    """Detect foreground application to dynamically prime Whisper with domain vocabulary."""
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

    base_priming = "Numbers, OTPs, codes: 1, 2, 3, 555-0123, 32559, 8:30 PM, $50, 449911, Python, PowerShell, Wispr Flow, API."

    coding_indicators = ["code", "visual studio", "terminal", "powershell", "cmd", "bash", "sublime", "git", "intellij", "pycharm"]
    comm_indicators = ["slack", "teams", "outlook", "chrome", "edge", "firefox", "discord", "mail", "whatsapp"]

    domain_terms = []
    if any(ind in title for ind in coding_indicators):
        domain_terms = ["Python", "PowerShell", "JSON", "API", "Git", "function", "variable", "CLI", "terminal", "async", "def", "class"]
    elif any(ind in title for ind in comm_indicators):
        domain_terms = ["email", "meeting", "schedule", "calendar", "message", "project", "client", "deadline", "regards"]

    custom_vocab = CONFIG.get("custom_vocabulary", [])

    all_prompts = [base_priming]
    if domain_terms:
        all_prompts.append(", ".join(domain_terms))
    if custom_vocab:
        all_prompts.append(", ".join(custom_vocab))

    return " ".join(all_prompts)

def transcribe_audio(wav_path):
    """
    Transcribes audio using in-memory whisper-server.exe,
    falling back to whisper-cli.exe if the server is unreachable.
    Timeout is set to 45s to avoid false-positive aborts on long speech.
    """
    prompt = get_active_window_prompt()

    # Strategy 1: High-speed In-Memory Server HTTP POST
    if is_server_healthy():
        try:
            with open(wav_path, "rb") as f:
                files = {"file": ("audio.wav", f, "audio/wav")}
                data = {
                    "temperature": "0.0",
                    "response_format": "json",
                    "prompt": prompt
                }
                resp = HTTP_SESSION.post(f"{SERVER_URL}/inference", files=files, data=data, timeout=180.0)
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    return text
        except Exception as e:
            logging.warning(f"whisper-server inference failed: {e}. Falling back to CLI.")

    # Strategy 2: CLI Fallback
    cmd = [
        WHISPER_CLI,
        "-m", ACTIVE_MODEL_PATH,
        "-f", wav_path,
        "-nt",
        "-np",
        "-t", "8",
        "-fa",
        "-nth", "0.35",
        "-sns",
        "--prompt", prompt
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return res.stdout.strip()
    except Exception as e:
        logging.error(f"whisper-cli execution failed: {e}")
        return ""

def paste_text(text):
    if not text:
        return
    while keyboard.is_pressed('ctrl') or keyboard.is_pressed('alt'):
        time.sleep(0.02)
    time.sleep(0.05)
    
    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send('ctrl+v')

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
    stream = None

    def audio_callback(indata, frames, time_info, status):
        if is_recording:
            audio_frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='int16',
        callback=audio_callback
    )
    stream.start()

    temp_wav = os.path.join(tempfile.gettempdir(), "whisper_flow_recording.wav")

    keyboard.add_hotkey('ctrl+shift+q', lambda: sys.exit(0))

    try:
        while True:
            hotkey_active = is_hotkey_down()

            if hotkey_active and not is_recording:
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

            elif not hotkey_active and is_recording:
                # Released hotkey - allow short tail buffer to capture final syllable
                time.sleep(0.2)
                is_recording = False
                time.sleep(0.15)
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

                    if duration_sec >= 0.3:
                        wav.write(temp_wav, SAMPLE_RATE, audio_data)
                        raw_text = transcribe_audio(temp_wav)

                        if raw_text:
                            cleaned_text = apply_smart_cleanup(raw_text, level=CLEANUP_LEVEL, config=CONFIG)
                            logging.info(f"Raw: '{raw_text}' -> Cleaned: '{cleaned_text}'")
                            print(f"[RAW]: \"{raw_text}\"")
                            print(f"[CLEANED]: \"{cleaned_text}\"")
                            paste_text(cleaned_text)
                            if hud:
                                hud.set_state("done")
                            print("[PASTED] Done!\n")
                        else:
                            if hud:
                                hud.set_state("idle")
                            print("[INFO] No speech detected.\n")
                    else:
                        if hud:
                            hud.set_state("idle")
                        print("[INFO] Audio too short, ignored.\n")
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
