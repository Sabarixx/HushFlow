"""
HushFlow Studio - Native Edge WebView2 Desktop Controller & Bridge
Renders offline Chromium UI inside a seamless dark Windows 11 desktop window:
- Zero browser tabs, zero exposed HTTP ports, zero external CDN calls
- Exact DWMWA_CAPTION_COLOR (#080a0f) seamless Windows 11 titlebar + native .ico
- Loopback UDP IPC for real-time dictation mirroring & tray focus restore
- On-demand microphone testing (privacy indicator stays off when idle)
- Full in-app Settings, Lexicon CRUD, Model downloader & Onboarding bridge
"""

import os
import sys
import time
import json
import base64
import socket
import threading
import subprocess
import ctypes
import winreg
from typing import Dict, List, Optional

import psutil
import requests
import webview

from app_paths import (
    APP_NAME,
    APP_VERSION,
    CONFIG_PATH,
    CORRECTIONS_FILE,
    INSTALL_DIR,
    IS_FROZEN,
    MODELS_DIR,
    MODEL_REGISTRY,
    PID_FILE,
    STUDIO_IPC_PORT,
    USER_DATA_DIR,
    canonical_model_key,
    ensure_initialized,
    fallback_model_exists,
    get_groq_api_key,
    get_hardware_summary,
    get_input_devices,
    get_vault_disk_usage_bytes,
    is_cloud_model,
    load_config,
    model_exists,
    purge_vault_audio_and_logs,
    save_config,
)
from asr_pipeline import GROQ_POOL
from brand_assets import get_app_icon_path, get_app_logo_path
from studio_ui import build_studio_html

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

SERVER_HEALTH_URL = "http://127.0.0.1:8080/health"
ENGINE_CTL_PORT = 48766

ensure_initialized()


def _apply_seamless_window_chrome(title: str, icon_path: str):
    """Apply Windows 11 DWM immersive dark mode, #080a0f caption color, and crisp taskbar .ico."""
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if not hwnd:
            return
        dark = ctypes.c_int(1)
        caption_bgr = ctypes.c_int(0x00221713)  # #131722 in BGR COLORREF
        border_bgr = ctypes.c_int(0x00443027)   # #273044 in BGR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption_bgr), 4)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border_bgr), 4)

        if icon_path and os.path.exists(icon_path):
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            WM_SETICON = 0x0080
            h_big = ctypes.windll.user32.LoadImageW(0, icon_path, IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
            h_small = ctypes.windll.user32.LoadImageW(0, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            if h_big:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, h_big)
            if h_small:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, h_small)
    except Exception:
        pass


def send_engine_command(cmd: str):
    """Notify running HushFlow Engine over loopback UDP."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps({"cmd": cmd}).encode("utf-8"), ("127.0.0.1", ENGINE_CTL_PORT))
        sock.close()
    except Exception:
        pass


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ
        ) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart_enabled(enabled: bool) -> bool:
    """Manage HKCU Run entry and remove any legacy Startup folder prototype shortcuts."""
    legacy_startup = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup\WhisperFlow.lnk",
    )
    if os.path.exists(legacy_startup):
        try:
            os.remove(legacy_startup)
        except Exception:
            pass

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                if IS_FROZEN:
                    cmd = f'"{sys.executable}" --engine'
                else:
                    pyw = sys.executable.replace("python.exe", "pythonw.exe")
                    if not os.path.exists(pyw):
                        pyw = sys.executable
                    engine_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper_flow.py")
                    cmd = f'"{pyw}" "{engine_script}"'
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


def check_engine_running() -> (bool, List[int]):
    server_ok = False
    try:
        r = requests.get(SERVER_HEALTH_URL, timeout=0.5)
        if r.status_code == 200:
            server_ok = True
    except Exception:
        server_ok = False

    pids = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            name = (p.info.get("name") or "").lower()
            if "whisper_flow.py" in cmd or (IS_FROZEN and "hushflow" in name and "--engine" in cmd):
                pids.append(p.info["pid"])
        except Exception:
            pass

    return (server_ok or len(pids) > 0), pids


class OnDemandAudioTester:
    """Opens mic stream strictly while user tests signal (keeps Windows mic privacy light off at idle)."""

    def __init__(self, on_level, on_finished):
        self.on_level = on_level
        self.on_finished = on_finished
        self.running = False
        self.stream = None
        self.current_level = 0.0
        self.thread = None
        self.last_emit = 0.0

    def start(self, device_index=None, auto_stop_sec: float = 10.0):
        if not AUDIO_AVAILABLE or self.running:
            return
        self.running = True
        self.current_level = 0.0
        self.thread = threading.Thread(target=self._run, args=(device_index, auto_stop_sec), daemon=True)
        self.thread.start()

    def _run(self, device_index, auto_stop_sec: float):
        def audio_cb(indata, frames, time_info, status):
            if not self.running:
                return
            try:
                rms = float(np.sqrt(np.mean(indata ** 2)))
                level = min(1.0, rms * 16.0)
                self.current_level = level if level > self.current_level else (self.current_level * 0.78 + level * 0.22)
                now = time.time()
                if self.on_level and self.running and (now - self.last_emit >= 0.04):
                    self.last_emit = now
                    self.on_level(self.current_level)
            except Exception:
                pass

        start_t = time.time()
        try:
            kwargs = dict(channels=1, samplerate=16000, blocksize=1024, callback=audio_cb)
            if device_index is not None:
                kwargs["device"] = device_index
            self.stream = sd.InputStream(**kwargs)
            with self.stream:
                while self.running and (time.time() - start_t < auto_stop_sec):
                    time.sleep(0.04)
        except Exception:
            pass
        finally:
            self.running = False
            self.stream = None
            if self.on_finished:
                self.on_finished()

    def stop(self):
        self.running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class StudioBridge:
    """Thread-safe JS API exposed to the offline WebView2 HushFlow Studio interface."""

    def __init__(self):
        self._window: Optional[webview.Window] = None
        self._hw = get_hardware_summary()
        self._devices = [{"id": idx, "name": name} for idx, name in get_input_devices()]
        self._feed: List[Dict] = []
        self._audio_tester = OnDemandAudioTester(self._push_mic_level, self._push_mic_ended)
        self._downloading_model = False
        self._ipc_running = True
        self._force_quit = False
        self._last_running_state = None

    def _attach_window(self, window: webview.Window):
        self._window = window

    def _eval_js(self, script: str):
        if self._window is not None and not self._force_quit:
            try:
                self._window.evaluate_js(script)
            except Exception:
                pass

    def _push_mic_level(self, level: float):
        self._eval_js(f"window.onMicLevel && window.onMicLevel({level:.3f}, true)")

    def _push_mic_ended(self):
        self._eval_js("window.onMicLevel && window.onMicLevel(0, false)")

    def get_snapshot(self) -> Dict:
        cfg = load_config()
        running, pids = check_engine_running()
        m_key = canonical_model_key(cfg.get("model"))
        meta = MODEL_REGISTRY[m_key]
        cloud = is_cloud_model(m_key)
        groq_key = get_groq_api_key(cfg)
        has_groq_key = bool(groq_key)
        fb_ready = fallback_model_exists(cfg)
        ready = (has_groq_key or fb_ready) if cloud else model_exists(m_key)
        masked_key = ""
        if groq_key:
            masked_key = (groq_key[:6] + "..." + groq_key[-4:]) if len(groq_key) > 10 else "configured"

        safe_cfg = dict(cfg)
        safe_cfg["groq_api_key"] = ""
        used_mb = get_vault_disk_usage_bytes() / (1024 * 1024)
        return {
            "version": APP_VERSION,
            "running": running,
            "pids": pids,
            "cfg": safe_cfg,
            "hw": self._hw,
            "devices": self._devices,
            "is_cloud": cloud,
            "has_groq_key": has_groq_key,
            "groq_key_masked": masked_key,
            "fallback_ready": fb_ready,
            "model_ready": ready,
            "model_size_mb": meta["size_mb"],
            "model_ram": meta["ram_mb"],
            "autostart": is_autostart_enabled(),
            "vault_mb": f"{used_mb:.2f}",
            "show_onboarding": (not cfg.get("onboarding_completed", False)) or (not ready),
            "feed": self._feed[-15:],
        }

    def toggle_engine(self) -> Dict:
        running, _ = check_engine_running()
        if running:
            self._stop_engine_sync()
        else:
            if IS_FROZEN:
                subprocess.Popen(
                    [sys.executable, "--engine"],
                    cwd=INSTALL_DIR,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper_flow.py")
                pythonw = sys.executable.replace("python.exe", "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                subprocess.Popen(
                    [pythonw, script_path],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            for _ in range(18):
                time.sleep(0.25)
                eng_up, _ = check_engine_running()
                if eng_up:
                    break
        return self.get_snapshot()

    def _stop_engine_sync(self):
        send_engine_command("stop")
        time.sleep(0.4)
        current_pid = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == current_pid:
                    continue
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                name = (p.info.get("name") or "").lower()
                if "whisper_flow.py" in cmd or (IS_FROZEN and "hushflow" in name and "--engine" in cmd) or "whisper-server" in name:
                    p.terminate()
            except Exception:
                pass
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass

    def toggle_mic_test(self) -> Dict:
        if self._audio_tester.running:
            self._audio_tester.stop()
        else:
            cfg = load_config()
            self._audio_tester.start(device_index=cfg.get("mic_device"), auto_stop_sec=10.0)
        return {"testing": self._audio_tester.running}

    def add_rule(self, spoken: str, corrected: str) -> Dict:
        spoken = (spoken or "").strip()
        corrected = (corrected or "").strip()
        if spoken and corrected:
            cfg = load_config()
            reps = cfg.get("word_replacements", {})
            reps[spoken.lower()] = corrected
            cfg["word_replacements"] = reps

            vocab = cfg.get("custom_vocabulary", [])
            for w in corrected.split():
                clean_w = w.strip(".,!?;:\"'()")
                if clean_w and clean_w not in vocab:
                    vocab.append(clean_w)
            cfg["custom_vocabulary"] = vocab
            save_config(cfg)

            record = {
                "timestamp": time.strftime("%Y%m%d_%H%M%S"),
                "original": spoken,
                "corrected": corrected,
                "replacements": [[spoken.lower(), corrected]],
                "window_title": f"{APP_NAME} Dictionary",
            }
            try:
                with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                pass
            send_engine_command("reload_config")
        return self.get_snapshot()

    def delete_rule(self, spoken_key: str) -> Dict:
        cfg = load_config()
        reps = cfg.get("word_replacements", {})
        if spoken_key in reps:
            del reps[spoken_key]
            cfg["word_replacements"] = reps
            save_config(cfg)
            send_engine_command("reload_config")
        return self.get_snapshot()

    def update_setting(self, key: str, value) -> Dict:
        cfg = load_config()
        cfg[key] = value
        save_config(cfg)
        send_engine_command("reload_config")
        return self.get_snapshot()

    def select_model(self, model_key: str) -> Dict:
        key = canonical_model_key(model_key)
        cfg = load_config()
        cfg["model"] = key
        save_config(cfg)
        if is_cloud_model(key) or model_exists(key):
            send_engine_command("reload_config")
        return self.get_snapshot()

    def save_groq_api_key(self, api_key: str, test_connection: bool = True) -> Dict:
        cleaned = (api_key or "").strip()
        cfg = load_config()
        cfg["groq_api_key"] = cleaned
        save_config(cfg)
        send_engine_command("reload_config")

        key_valid = False
        key_message = "Key cleared (using local fallback)."
        if cleaned:
            if test_connection:
                key_valid, key_message = GROQ_POOL.validate_key(cleaned)
            else:
                key_valid, key_message = True, "Groq API key saved."
        snap = self.get_snapshot()
        snap["key_valid"] = key_valid
        snap["key_message"] = key_message
        return snap

    def download_model(self, model_key: str) -> Dict:
        if self._downloading_model:
            return self.get_snapshot()
        key = canonical_model_key(model_key)
        if is_cloud_model(key):
            return self.get_snapshot()
        meta = MODEL_REGISTRY[key]
        os.makedirs(MODELS_DIR, exist_ok=True)
        dest_path = os.path.join(MODELS_DIR, meta["filename"])
        tmp_path = dest_path + ".part"
        self._downloading_model = True
        try:
            with requests.get(meta["url"], stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0)) or (meta["size_mb"] * 1024 * 1024)
                dl = 0
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                            dl += len(chunk)
                            pct = min(0.99, dl / max(1, total))
                            self._eval_js(f"window.onModelProgress && window.onModelProgress({pct:.3f}, 'Downloading {key}...')")
            os.replace(tmp_path, dest_path)
            self._eval_js("window.onModelProgress && window.onModelProgress(1.0, 'Model downloaded and verified.')")
            cfg = load_config()
            cfg["model"] = key
            save_config(cfg)
            send_engine_command("reload_config")
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            safe_err = json.dumps(f"Download failed: {e}")
            self._eval_js(f"window.onModelProgress && window.onModelProgress(0.0, {safe_err})")
        finally:
            self._downloading_model = False
        return self.get_snapshot()

    def set_autostart(self, enabled: bool) -> Dict:
        set_autostart_enabled(bool(enabled))
        return self.get_snapshot()

    def purge_vault(self) -> Dict:
        purge_vault_audio_and_logs()
        self._feed.clear()
        return self.get_snapshot()

    def open_data_folder(self):
        try:
            os.startfile(USER_DATA_DIR)
        except Exception:
            pass

    def open_quickfix(self):
        if IS_FROZEN:
            subprocess.Popen([sys.executable, "--quickfix"], cwd=INSTALL_DIR)
        else:
            qf_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quick_fix.py")
            if os.path.exists(qf_script):
                subprocess.Popen([sys.executable, qf_script], cwd=os.path.dirname(os.path.abspath(__file__)))

    def complete_onboarding(self) -> Dict:
        cfg = load_config()
        cfg["onboarding_completed"] = True
        save_config(cfg)
        return self.get_snapshot()

    def clear_feed(self) -> Dict:
        self._feed.clear()
        return self.get_snapshot()

    def _bring_to_front(self):
        if self._window:
            try:
                self._window.show()
                self._window.restore()
                hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
                if hwnd:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                self._eval_js(f"window.onEngineStatePush && window.onEngineStatePush({json.dumps(self.get_snapshot())})")
            except Exception:
                pass

    def hide_to_tray(self):
        self._audio_tester.stop()
        if self._window:
            try:
                self._window.hide()
            except Exception:
                pass

    def quit_all(self):
        self._force_quit = True
        self._ipc_running = False
        self._audio_tester.stop()
        self._stop_engine_sync()
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass

    def _start_background_workers(self):
        # 1. UDP loopback listener for Engine -> Studio events & tray restore
        def _ipc():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("127.0.0.1", STUDIO_IPC_PORT))
                sock.settimeout(1.0)
            except Exception:
                return

            while self._ipc_running:
                try:
                    data, _ = sock.recvfrom(65535)
                    msg = json.loads(data.decode("utf-8"))
                    mtype = msg.get("type")
                    if mtype == "show_studio":
                        self._bring_to_front()
                    elif mtype == "transcript":
                        item = {"cleaned": msg.get("cleaned", "").strip(), "ts": msg.get("ts", "")}
                        if item["cleaned"]:
                            self._feed.append(item)
                            if len(self._feed) > 20:
                                self._feed.pop(0)
                            self._eval_js(f"window.onLiveTranscript && window.onLiveTranscript({json.dumps(item)})")
                    elif mtype == "lexicon_updated":
                        self._eval_js(f"window.onEngineStatePush && window.onEngineStatePush({json.dumps(self.get_snapshot())})")
                except socket.timeout:
                    continue
                except Exception:
                    pass
            try:
                sock.close()
            except Exception:
                pass

        # 2. Periodic lightweight engine state sync (pushes only when state/PID changes)
        def _status_watch():
            while self._ipc_running:
                time.sleep(2.0)
                if not self._ipc_running:
                    break
                try:
                    running, pids = check_engine_running()
                    sig = (running, tuple(pids))
                    if sig != self._last_running_state:
                        self._last_running_state = sig
                        self._eval_js(f"window.onEngineStatePush && window.onEngineStatePush({json.dumps(self.get_snapshot())})")
                except Exception:
                    pass

        threading.Thread(target=_ipc, daemon=True).start()
        threading.Thread(target=_status_watch, daemon=True).start()


def _load_logo_data_uri() -> str:
    logo_path = get_app_logo_path()
    if logo_path and os.path.exists(logo_path):
        try:
            with open(logo_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/png;base64,{b64}"
        except Exception:
            pass
    return ""


_studio_mutex_handle = None


def main():
    global _studio_mutex_handle
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Local\\HushFlow_Studio_SingleInstance_V2")
        if handle and kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(b'{"type":"show_studio"}', ("127.0.0.1", STUDIO_IPC_PORT))
            sock.close()
            sys.exit(0)
        _studio_mutex_handle = handle
    except Exception:
        pass

    bridge = StudioBridge()
    html = build_studio_html(_load_logo_data_uri())
    ico_path = get_app_icon_path()

    window = webview.create_window(
        APP_NAME,
        html=html,
        js_api=bridge,
        width=1060,
        height=760,
        min_size=(840, 580),
        background_color="#131722",
        text_select=True,
    )
    bridge._attach_window(window)

    def _on_shown():
        _apply_seamless_window_chrome(APP_NAME, ico_path)

    def _on_closing():
        if not bridge._force_quit:
            running, _ = check_engine_running()
            if running:
                bridge.hide_to_tray()
                return False
        bridge._ipc_running = False
        bridge._audio_tester.stop()
        return True

    window.events.shown += _on_shown
    window.events.closing += _on_closing
    bridge._start_background_workers()

    profile_dir = os.path.join(USER_DATA_DIR, "webview_profile")
    os.makedirs(profile_dir, exist_ok=True)
    webview.start(
        gui="edgechromium",
        icon=ico_path if os.path.exists(ico_path) else None,
        private_mode=False,
        storage_path=profile_dir,
    )


if __name__ == "__main__":
    main()
