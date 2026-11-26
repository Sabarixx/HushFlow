"""
HushFlow Studio - Executive Control Center, Onboarding, Settings & Lexicon UI
Features:
- Cohesive HushFlow dark-mode aesthetics with 4x supersampled squircle emblem
- On-demand microphone testing (never holds mic privacy indicator open when idle)
- Full in-app Settings (Mic chooser, Model switcher & downloader, Hotkey, Cleanup, Auto-Start)
- First-run Privacy & Model Onboarding wizard
- Opt-in local training audio privacy controls & Vault purge
- Live Transcription Canvas over connectionless loopback UDP IPC
- True Hide-to-Tray lifecycle synchronized with system tray icon
"""

import os
import sys
import time
import json
import socket
import threading
import subprocess
import ctypes
import winreg
from typing import Optional, List, Dict, Tuple

import psutil
import requests
import customtkinter as ctk
from PIL import Image

from app_paths import (
    APP_NAME,
    APP_VERSION,
    APP_TAGLINE,
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
    get_hardware_summary,
    get_input_devices,
    get_vault_disk_usage_bytes,
    load_config,
    model_exists,
    purge_vault_audio_and_logs,
    resolve_model_path,
    save_config,
)
from brand_assets import get_app_icon_path, get_app_logo_path

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

SERVER_HEALTH_URL = "http://127.0.0.1:8080/health"
ENGINE_CTL_PORT = 48766

ensure_initialized()


def set_native_dark_titlebar(window):
    """Enables Windows 11 DWM immersive dark titlebar."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def send_engine_command(cmd: str):
    """Notify running HushFlow Engine cleanly over loopback UDP."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps({"cmd": cmd}).encode("utf-8"), ("127.0.0.1", ENGINE_CTL_PORT))
        sock.close()
    except Exception:
        pass


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart_enabled(enabled: bool) -> bool:
    """Manage standard HKCU Run registry entry and prune legacy Startup shortcut."""
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


class OnDemandAudioTester:
    """Opens audio input stream ONLY during explicit user mic testing (never left idle)."""

    def __init__(self, on_level, on_finished):
        self.on_level = on_level
        self.on_finished = on_finished
        self.running = False
        self.stream = None
        self.current_level = 0.0
        self.thread = None

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
                if self.on_level and self.running:
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


class HushFlowStudio(ctk.CTk):
    def __init__(self, auto_boot_check: bool = True):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_NAME)
        self.geometry("840x710")
        self.minsize(780, 650)
        self.configure(fg_color="#090c13")

        ico = get_app_icon_path()
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except Exception:
                pass

        self.cfg = load_config()
        self.hw = get_hardware_summary()
        self.devices = get_input_devices()

        self.is_running = False
        self.engine_state_label = "idle"
        self.audio_tester = OnDemandAudioTester(self._on_mic_test_level, self._on_mic_test_ended)
        self.meter_level = 0.0
        self.rules_cache: Dict[str, str] = {}
        self.filtered_rules: List[Tuple[str, str]] = []
        self.meter_segments = []
        self.ipc_running = True
        self.onboarding_overlay = None
        self.downloading_model = False

        self._build_header()
        self._build_navigation()
        self._build_footer()

        set_native_dark_titlebar(self)
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self.after(120, self._initial_sync)
        self._start_ipc_listener()
        if auto_boot_check:
            self.after(1500, self._periodic_process_check)

        # Show clean onboarding wizard on first launch or if model is absent
        if not self.cfg.get("onboarding_completed", False) or not model_exists(self.cfg.get("model")):
            self.after(220, self._show_onboarding_wizard)

    # -------------------------------------------------------------------------
    # Header & Navigation
    # -------------------------------------------------------------------------
    def _build_header(self):
        header = ctk.CTkFrame(
            self, fg_color="#10141e", corner_radius=14, border_width=1, border_color="#1b2232"
        )
        header.pack(fill="x", padx=18, pady=(16, 10))

        left_box = ctk.CTkFrame(header, fg_color="transparent")
        left_box.pack(side="left", padx=16, pady=12)

        logo_path = get_app_logo_path()
        if os.path.exists(logo_path):
            try:
                pil_img = Image.open(logo_path)
                self.logo_ctk = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(44, 44))
                ctk.CTkLabel(left_box, image=self.logo_ctk, text="").pack(side="left", padx=(0, 14))
            except Exception:
                pass

        titles = ctk.CTkFrame(left_box, fg_color="transparent")
        titles.pack(side="left")

        ctk.CTkLabel(
            titles,
            text=APP_NAME,
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=20, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w")

        ctk.CTkLabel(
            titles,
            text=APP_TAGLINE,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8592a6",
        ).pack(anchor="w")

        self.status_pill = ctk.CTkLabel(
            header,
            text="● CHECKING",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8592a6",
            fg_color="#161c28",
            corner_radius=16,
            padx=14,
            pady=5,
        )
        self.status_pill.pack(side="right", padx=16, pady=12)

    def _build_navigation(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color="#10141e",
            segmented_button_fg_color="#151b27",
            segmented_button_selected_color="#212a3d",
            segmented_button_selected_hover_color="#28334a",
            segmented_button_unselected_hover_color="#1a2233",
            corner_radius=14,
        )
        self.tabview.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        self.tab_overview = self.tabview.add("Overview")
        self.tab_lexicon = self.tabview.add("Lexicon")
        self.tab_settings = self.tabview.add("Settings")
        self.tab_shortcuts = self.tabview.add("Shortcuts")

        self._build_overview_tab()
        self._build_lexicon_tab()
        self._build_settings_tab()
        self._build_shortcuts_tab()

    # -------------------------------------------------------------------------
    # Tab 1: Overview
    # -------------------------------------------------------------------------
    def _build_overview_tab(self):
        service_card = ctk.CTkFrame(
            self.tab_overview, fg_color="#151b27", corner_radius=12, border_width=1, border_color="#212a3d"
        )
        service_card.pack(fill="x", padx=14, pady=10)

        info_box = ctk.CTkFrame(service_card, fg_color="transparent")
        info_box.pack(side="left", padx=18, pady=14, fill="both", expand=True)

        self.service_title = ctk.CTkLabel(
            info_box,
            text="Local Speech Service",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=16, weight="bold"),
            text_color="#f8fafc",
        )
        self.service_title.pack(anchor="w")

        self.service_desc = ctk.CTkLabel(
            info_box,
            text="Checking local inference server and global keyboard listeners...",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8592a6",
        )
        self.service_desc.pack(anchor="w", pady=(3, 0))

        self.btn_service_toggle = ctk.CTkButton(
            service_card,
            text="Start Service",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="#f8fafc",
            hover_color="#e2e8f0",
            text_color="#090c13",
            height=38,
            width=146,
            corner_radius=8,
            command=self._on_toggle_engine,
        )
        self.btn_service_toggle.pack(side="right", padx=18, pady=14)

        # On-demand Microphone Calibration Bar (privacy-safe: closed unless testing)
        meter_card = ctk.CTkFrame(
            self.tab_overview, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232"
        )
        meter_card.pack(fill="x", padx=14, pady=6)

        meter_inner = ctk.CTkFrame(meter_card, fg_color="transparent")
        meter_inner.pack(fill="x", padx=16, pady=10)

        m_head = ctk.CTkFrame(meter_inner, fg_color="transparent")
        m_head.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            m_head,
            text="Microphone Check",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0",
        ).pack(side="left")

        self.btn_mic_test = ctk.CTkButton(
            m_head,
            text="Test Microphone",
            width=116,
            height=24,
            corner_radius=6,
            fg_color="#1b2232",
            hover_color="#252f44",
            text_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self._toggle_mic_test,
        )
        self.btn_mic_test.pack(side="right")

        self.lbl_mic_status = ctk.CTkLabel(
            m_head,
            text="Idle (mic released until dictation)",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8592a6",
        )
        self.lbl_mic_status.pack(side="right", padx=12)

        self.meter_frame = ctk.CTkFrame(meter_inner, fg_color="transparent")
        self.meter_frame.pack(fill="x")

        self.meter_segments = []
        for _ in range(24):
            seg = ctk.CTkFrame(self.meter_frame, height=8, corner_radius=2, fg_color="#1b2232")
            seg.pack(side="left", fill="both", expand=True, padx=1)
            self.meter_segments.append(seg)

        # Dynamic Hardware & Runtime Specs Row (Zero hardcoded CPU/brand strings)
        specs_row = ctk.CTkFrame(self.tab_overview, fg_color="transparent")
        specs_row.pack(fill="x", padx=14, pady=6)

        model_key = self.cfg.get("model", "small.en-q5_1")
        self.spec_model_lbl = self._build_spec_box(specs_row, "Active Model", model_key)
        self.spec_threads_lbl = self._build_spec_box(specs_row, "CPU Allocation", self.hw["threads"])
        self.spec_hotkey_lbl = self._build_spec_box(specs_row, "Push-to-Talk", self._format_hotkey_label(self.cfg.get("hotkey", "ctrl+alt")))
        self.spec_privacy_lbl = self._build_spec_box(
            specs_row,
            "Audio Privacy",
            "Opt-In Vault" if self.cfg.get("save_training_audio") else "RAM Only (Zero Disk)",
        )

        # Live Transcription Mirror Canvas
        preview_card = ctk.CTkFrame(
            self.tab_overview, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232"
        )
        preview_card.pack(fill="both", expand=True, padx=14, pady=(6, 10))

        p_head = ctk.CTkFrame(preview_card, fg_color="transparent")
        p_head.pack(fill="x", padx=16, pady=(10, 6))

        ctk.CTkLabel(
            p_head,
            text="Live Dictation Mirror",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0",
        ).pack(side="left")

        ctk.CTkButton(
            p_head,
            text="Clear",
            width=54,
            height=22,
            fg_color="#1b2232",
            hover_color="#252f44",
            text_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=4,
            command=self._clear_scratchpad,
        ).pack(side="right")

        ctk.CTkButton(
            p_head,
            text="Copy All",
            width=68,
            height=22,
            fg_color="#1b2232",
            hover_color="#252f44",
            text_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=4,
            command=self._copy_scratchpad,
        ).pack(side="right", padx=6)

        self.preview_text = ctk.CTkTextbox(
            preview_card,
            fg_color="#0b0e15",
            text_color="#f1f5f9",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            corner_radius=6,
            border_width=1,
            border_color="#1b2232",
        )
        self.preview_text.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.preview_text.insert(
            "1.0",
            "Hold [Ctrl + Alt] anywhere in Windows to dictate, or tap [Ctrl + Alt + Space] for Hands-Free mode.\n"
            "Spoken output transcribed during this session will appear here automatically.\n\n",
        )

    def _build_spec_box(self, parent, title: str, val: str):
        box = ctk.CTkFrame(parent, fg_color="#10141e", corner_radius=8, border_width=1, border_color="#1b2232")
        box.pack(side="left", fill="both", expand=True, padx=3)
        ctk.CTkLabel(box, text=title, font=ctk.CTkFont(family="Segoe UI", size=10), text_color="#8592a6").pack(
            anchor="w", padx=10, pady=(6, 1)
        )
        val_lbl = ctk.CTkLabel(
            box, text=val, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color="#e2e8f0"
        )
        val_lbl.pack(anchor="w", padx=10, pady=(0, 6))
        return val_lbl

    # -------------------------------------------------------------------------
    # Tab 2: Lexicon
    # -------------------------------------------------------------------------
    def _build_lexicon_tab(self):
        top_card = ctk.CTkFrame(
            self.tab_lexicon, fg_color="#151b27", corner_radius=10, border_width=1, border_color="#212a3d"
        )
        top_card.pack(fill="x", padx=14, pady=10)

        left_info = ctk.CTkFrame(top_card, fg_color="transparent")
        left_info.pack(side="left", padx=16, pady=12)

        self.lexicon_count_lbl = ctk.CTkLabel(
            left_info,
            text="Custom Lexicon & Replacements",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=15, weight="bold"),
            text_color="#f8fafc",
        )
        self.lexicon_count_lbl.pack(anchor="w")

        ctk.CTkLabel(
            left_info,
            text="Custom pronunciations and technical terms resolve automatically during transcription.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8592a6",
        ).pack(anchor="w", pady=(2, 0))

        ctk.CTkButton(
            top_card,
            text="QuickFix (Alt + Z)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#1e2638",
            hover_color="#2b364e",
            text_color="#e2e8f0",
            height=32,
            corner_radius=6,
            command=self._launch_quickfix,
        ).pack(side="right", padx=16, pady=12)

        form_card = ctk.CTkFrame(
            self.tab_lexicon, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232"
        )
        form_card.pack(fill="x", padx=14, pady=4)

        ctk.CTkLabel(
            form_card,
            text="Add Lexicon Replacement",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#cbd5e1",
        ).pack(anchor="w", padx=14, pady=(10, 6))

        inputs_box = ctk.CTkFrame(form_card, fg_color="transparent")
        inputs_box.pack(fill="x", padx=14, pady=(0, 12))

        self.entry_spoken = ctk.CTkEntry(
            inputs_box,
            placeholder_text="Spoken phrase (e.g. 'antiravity')",
            height=34,
            fg_color="#0b0e15",
            border_color="#242e42",
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.entry_spoken.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.entry_corrected = ctk.CTkEntry(
            inputs_box,
            placeholder_text="Normalized output (e.g. 'Antigravity')",
            height=34,
            fg_color="#0b0e15",
            border_color="#242e42",
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.entry_corrected.pack(side="left", fill="both", expand=True, padx=(0, 10))

        ctk.CTkButton(
            inputs_box,
            text="Add Rule",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#f8fafc",
            hover_color="#e2e8f0",
            text_color="#090c13",
            width=90,
            height=34,
            corner_radius=6,
            command=self._on_add_rule,
        ).pack(side="right")

        table_head = ctk.CTkFrame(self.tab_lexicon, fg_color="transparent")
        table_head.pack(fill="x", padx=14, pady=(10, 4))

        ctk.CTkLabel(
            table_head,
            text="Configured Lexicon Rules",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#cbd5e1",
        ).pack(side="left")

        self.entry_search = ctk.CTkEntry(
            table_head,
            placeholder_text="Filter entries...",
            width=180,
            height=26,
            fg_color="#10141e",
            border_color="#242e42",
            corner_radius=4,
            font=ctk.CTkFont(family="Segoe UI", size=11),
        )
        self.entry_search.pack(side="right")
        self.entry_search.bind("<KeyRelease>", lambda e: self._filter_rules())

        self.rules_scroll = ctk.CTkScrollableFrame(
            self.tab_lexicon, fg_color="#0b0e15", corner_radius=8, border_width=1, border_color="#1b2232"
        )
        self.rules_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 12))

    def _render_rules_list(self):
        for w in self.rules_scroll.winfo_children():
            w.destroy()

        if not self.filtered_rules:
            ctk.CTkLabel(
                self.rules_scroll,
                text="No lexicon replacement rules configured.",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#64748b",
            ).pack(pady=30)
            return

        for spoken, corrected in self.filtered_rules:
            row = ctk.CTkFrame(self.rules_scroll, fg_color="#10141e", corner_radius=6, height=34)
            row.pack(fill="x", pady=2, padx=4)

            ctk.CTkLabel(
                row,
                text=spoken,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                fg_color="#161c28",
                corner_radius=4,
                padx=8,
                pady=2,
                text_color="#e2e8f0",
            ).pack(side="left", padx=8, pady=4)

            ctk.CTkLabel(
                row, text="→", font=ctk.CTkFont(family="Segoe UI", size=12), text_color="#64748b"
            ).pack(side="left", padx=4)

            ctk.CTkLabel(
                row,
                text=corrected,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                fg_color="#12231c",
                corner_radius=4,
                padx=8,
                pady=2,
                text_color="#34d399",
            ).pack(side="left", padx=4, pady=4)

            ctk.CTkButton(
                row,
                text="Remove",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                fg_color="#1b2232",
                hover_color="#3b1f2b",
                text_color="#94a3b8",
                width=64,
                height=22,
                corner_radius=4,
                command=lambda s=spoken: self._on_delete_rule(s),
            ).pack(side="right", padx=8, pady=4)

    # -------------------------------------------------------------------------
    # Tab 3: Settings & Privacy
    # -------------------------------------------------------------------------
    def _build_settings_tab(self):
        scroll = ctk.CTkScrollableFrame(self.tab_settings, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=8)

        # Section 1: Audio & Model
        audio_card = ctk.CTkFrame(scroll, fg_color="#151b27", corner_radius=10, border_width=1, border_color="#212a3d")
        audio_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            audio_card,
            text="Audio & Speech Recognition",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w", padx=16, pady=(12, 8))

        # Microphone Selector
        mic_row = ctk.CTkFrame(audio_card, fg_color="transparent")
        mic_row.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(mic_row, text="Input Microphone", font=ctk.CTkFont(size=12), text_color="#cbd5e1", width=160, anchor="w").pack(side="left")

        dev_names = [name for _, name in self.devices]
        self.dev_name_to_idx = {name: idx for idx, name in self.devices}
        current_dev = self.cfg.get("mic_device")
        current_dev_name = dev_names[0]
        for idx, name in self.devices:
            if idx == current_dev:
                current_dev_name = name
                break

        self.var_mic = ctk.StringVar(value=current_dev_name)
        self.combo_mic = ctk.CTkOptionMenu(
            mic_row,
            values=dev_names,
            variable=self.var_mic,
            fg_color="#0b0e15",
            button_color="#1b2232",
            button_hover_color="#252f44",
            width=360,
            command=self._on_mic_selected,
        )
        self.combo_mic.pack(side="right")

        # Speech Model Selector
        model_row = ctk.CTkFrame(audio_card, fg_color="transparent")
        model_row.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(model_row, text="Whisper Model", font=ctk.CTkFont(size=12), text_color="#cbd5e1", width=160, anchor="w").pack(side="left")

        model_keys = list(MODEL_REGISTRY.keys())
        self.var_model = ctk.StringVar(value=canonical_model_key(self.cfg.get("model")))
        self.combo_model = ctk.CTkOptionMenu(
            model_row,
            values=model_keys,
            variable=self.var_model,
            fg_color="#0b0e15",
            button_color="#1b2232",
            button_hover_color="#252f44",
            width=220,
            command=self._on_model_selected,
        )
        self.combo_model.pack(side="left", padx=(0, 8))

        self.btn_download_model = ctk.CTkButton(
            model_row,
            text="Check Model",
            width=130,
            height=28,
            fg_color="#1b2232",
            hover_color="#252f44",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_download_active_model,
        )
        self.btn_download_model.pack(side="right")

        self.model_dl_progress = ctk.CTkProgressBar(audio_card, height=6, corner_radius=3, fg_color="#0b0e15", progress_color="#34d399")
        self.model_dl_progress.set(1.0 if model_exists(self.var_model.get()) else 0.0)
        self.model_dl_progress.pack(fill="x", padx=16, pady=(6, 12))

        # Section 2: Behavior & Controls
        beh_card = ctk.CTkFrame(scroll, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232")
        beh_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            beh_card,
            text="Dictation Behavior & Startup",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w", padx=16, pady=(12, 8))

        # Hotkey Selector
        hk_row = ctk.CTkFrame(beh_card, fg_color="transparent")
        hk_row.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(hk_row, text="Push-to-Talk Hotkey", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(side="left")
        self.var_hotkey = ctk.StringVar(value=self.cfg.get("hotkey", "ctrl+alt"))
        ctk.CTkSegmentedButton(
            hk_row,
            values=["ctrl+alt", "win+alt", "right_alt"],
            variable=self.var_hotkey,
            selected_color="#212a3d",
            selected_hover_color="#2b3750",
            command=self._on_setting_changed,
        ).pack(side="right")

        # Cleanup Mode
        cl_row = ctk.CTkFrame(beh_card, fg_color="transparent")
        cl_row.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(cl_row, text="Cleanup Level", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(side="left")
        self.var_cleanup = ctk.StringVar(value=self.cfg.get("cleanup_level", "medium"))
        ctk.CTkSegmentedButton(
            cl_row,
            values=["none", "light", "medium", "high"],
            variable=self.var_cleanup,
            selected_color="#212a3d",
            selected_hover_color="#2b3750",
            command=self._on_setting_changed,
        ).pack(side="right")

        # HUD Visibility
        hud_row = ctk.CTkFrame(beh_card, fg_color="transparent")
        hud_row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(hud_row, text="Show Floating Edge Pill", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(side="left")
        self.var_hud = ctk.BooleanVar(value=bool(self.cfg.get("hud_visible", True)))
        ctk.CTkSwitch(
            hud_row, text="", variable=self.var_hud, progress_color="#34d399", command=self._on_setting_changed
        ).pack(side="right")

        # Windows Auto-start
        auto_row = ctk.CTkFrame(beh_card, fg_color="transparent")
        auto_row.pack(fill="x", padx=16, pady=(6, 12))
        ctk.CTkLabel(auto_row, text="Start HushFlow Automatically with Windows", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(side="left")
        self.var_autostart = ctk.BooleanVar(value=is_autostart_enabled())
        ctk.CTkSwitch(
            auto_row, text="", variable=self.var_autostart, progress_color="#34d399", command=self._on_toggle_autostart
        ).pack(side="right")

        # Section 3: Privacy & Local Storage
        priv_card = ctk.CTkFrame(scroll, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232")
        priv_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            priv_card,
            text="Privacy & Local Vault Controls",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w", padx=16, pady=(12, 8))

        wav_row = ctk.CTkFrame(priv_card, fg_color="transparent")
        wav_row.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(
            wav_row,
            text="Archive training WAVs on disk (only when correcting via Alt + Z)",
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
        ).pack(side="left")
        self.var_save_audio = ctk.BooleanVar(value=bool(self.cfg.get("save_training_audio", False)))
        ctk.CTkSwitch(
            wav_row, text="", variable=self.var_save_audio, progress_color="#34d399", command=self._on_setting_changed
        ).pack(side="right")

        vault_action_row = ctk.CTkFrame(priv_card, fg_color="transparent")
        vault_action_row.pack(fill="x", padx=16, pady=(6, 12))

        self.lbl_vault_usage = ctk.CTkLabel(
            vault_action_row,
            text="Vault usage: 0.00 MB",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8592a6",
        )
        self.lbl_vault_usage.pack(side="left")

        ctk.CTkButton(
            vault_action_row,
            text="Setup Guide",
            width=96,
            height=28,
            fg_color="#1b2232",
            hover_color="#252f44",
            font=ctk.CTkFont(size=11),
            command=self._show_onboarding_wizard,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            vault_action_row,
            text="Open Data Folder",
            width=116,
            height=28,
            fg_color="#1b2232",
            hover_color="#252f44",
            font=ctk.CTkFont(size=11),
            command=self._open_data_folder,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            vault_action_row,
            text="Purge Saved Audio & Logs",
            width=156,
            height=28,
            fg_color="#1f161c",
            hover_color="#2e1d28",
            text_color="#f87171",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_purge_vault,
        ).pack(side="right")

        self._refresh_model_status_ui()
        self._refresh_vault_usage_label()

    # -------------------------------------------------------------------------
    # Tab 4: Shortcuts
    # -------------------------------------------------------------------------
    def _build_shortcuts_tab(self):
        container = ctk.CTkScrollableFrame(self.tab_shortcuts, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=14, pady=10)

        hw_card = ctk.CTkFrame(container, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232")
        hw_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            hw_card,
            text="System Keybindings",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0",
        ).pack(anchor="w", padx=14, pady=(12, 8))

        shortcuts = [
            ("Hold [Ctrl + Alt]", "Push-to-Talk (or Right Alt / Win+Alt in Settings): records while held."),
            ("Press [Ctrl + Alt + Space]", "Hands-Free Dictation: toggles continuous speech; press again or Esc to finish."),
            ("Press [Alt + Z]", "QuickFix Correction: edits last phrase and teaches your custom lexicon."),
            ("Press [Ctrl + Shift + Q]", "Emergency Stop: immediately shuts down background listeners and server."),
        ]
        for key, desc in shortcuts:
            r = ctk.CTkFrame(hw_card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(
                r, text=key, font=ctk.CTkFont(family="Consolas", size=11, weight="bold"), text_color="#38bdf8", width=210, anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(r, text=desc, font=ctk.CTkFont(family="Segoe UI", size=11), text_color="#8592a6").pack(
                side="left", fill="x", expand=True
            )

        ctk.CTkLabel(hw_card, text="", height=4).pack()

        cmd_card = ctk.CTkFrame(container, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232")
        cmd_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            cmd_card,
            text="Voice Formatting Commands",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0",
        ).pack(anchor="w", padx=14, pady=(12, 8))

        voice_cmds = [
            ("new line", "Inserts a line break"),
            ("new paragraph", "Inserts a double line break"),
            ("snake case <words>", "Formats phrase as snake_case"),
            ("camel case <words>", "Formats phrase as camelCase"),
            ("kebab case <words>", "Formats phrase as kebab-case"),
            ("all caps <words>", "Formats phrase as UPPERCASE"),
            ("double <digit>", "Repeats spoken digits (e.g. 'double 4' -> 44)"),
            ("triple <digit>", "Repeats spoken digits (e.g. 'triple 7' -> 777)"),
        ]
        for phrase, effect in voice_cmds:
            r = ctk.CTkFrame(cmd_card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(
                r, text=f'"{phrase}"', font=ctk.CTkFont(family="Consolas", size=11, weight="bold"), text_color="#34d399", width=210, anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(r, text=effect, font=ctk.CTkFont(family="Segoe UI", size=11), text_color="#8592a6").pack(
                side="left", fill="x", expand=True
            )

        ctk.CTkLabel(cmd_card, text="", height=4).pack()

    # -------------------------------------------------------------------------
    # Footer
    # -------------------------------------------------------------------------
    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(2, 14))

        ctk.CTkLabel(
            footer,
            text=f"{APP_NAME} v{APP_VERSION}  •  {self.hw['os']}  •  {self.hw['ram']}  •  100% Offline",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#64748b",
        ).pack(side="left")

        ctk.CTkButton(
            footer,
            text="Quit All",
            width=74,
            height=24,
            fg_color="#1b2232",
            hover_color="#3b1f2b",
            text_color="#94a3b8",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=4,
            command=self._quit_completely,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            footer,
            text="Hide to Tray",
            width=92,
            height=24,
            fg_color="#151b27",
            hover_color="#1e2638",
            text_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=4,
            command=self.hide_to_tray,
        ).pack(side="right")

    # -------------------------------------------------------------------------
    # First-Run Onboarding Overlay
    # -------------------------------------------------------------------------
    def _show_onboarding_wizard(self):
        if self.onboarding_overlay is not None:
            return

        overlay = ctk.CTkFrame(self, fg_color="#090c13", corner_radius=0)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.onboarding_overlay = overlay

        card = ctk.CTkFrame(overlay, fg_color="#10141e", corner_radius=16, border_width=1, border_color="#212a3d")
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.78, relheight=0.82)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=28, pady=(26, 10))

        logo_path = get_app_logo_path()
        if os.path.exists(logo_path):
            try:
                pil_img = Image.open(logo_path)
                self.onboard_logo = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(48, 48))
                ctk.CTkLabel(top, image=self.onboard_logo, text="").pack(side="left", padx=(0, 14))
            except Exception:
                pass

        head_txt = ctk.CTkFrame(top, fg_color="transparent")
        head_txt.pack(side="left")
        ctk.CTkLabel(
            head_txt,
            text=f"Welcome to {APP_NAME}",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=21, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w")
        ctk.CTkLabel(
            head_txt,
            text="Local, offline voice dictation built for speed and privacy.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8592a6",
        ).pack(anchor="w")

        # 3 Trust & Setup Cards
        steps_box = ctk.CTkFrame(card, fg_color="transparent")
        steps_box.pack(fill="both", expand=True, padx=28, pady=10)

        p1 = ctk.CTkFrame(steps_box, fg_color="#151b27", corner_radius=10, border_width=1, border_color="#212a3d")
        p1.pack(fill="x", pady=6)
        ctk.CTkLabel(p1, text="1. Zero-Cloud Privacy Guarantee", font=ctk.CTkFont(size=13, weight="bold"), text_color="#34d399").pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            p1,
            text="Audio is processed purely in local RAM via whisper-server (127.0.0.1:8080). No accounts, telemetry, or cloud uploads. Global keyboard hooks listen only for your configured Push-to-Talk key.",
            font=ctk.CTkFont(size=11),
            text_color="#cbd5e1",
            wraplength=540,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))

        p2 = ctk.CTkFrame(steps_box, fg_color="#151b27", corner_radius=10, border_width=1, border_color="#212a3d")
        p2.pack(fill="x", pady=6)
        m_key = canonical_model_key(self.cfg.get("model"))
        ready_now = model_exists(m_key)
        ctk.CTkLabel(p2, text="2. Local Speech Model", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=14, pady=(10, 4))

        m_row = ctk.CTkFrame(p2, fg_color="transparent")
        m_row.pack(fill="x", padx=14, pady=(0, 10))
        self.onboard_model_lbl = ctk.CTkLabel(
            m_row,
            text=f"Model ({m_key}) detected locally and ready." if ready_now else f"Download {m_key} ({MODEL_REGISTRY[m_key]['size_mb']} MB) to enable offline speech.",
            font=ctk.CTkFont(size=11),
            text_color="#34d399" if ready_now else "#cbd5e1",
        )
        self.onboard_model_lbl.pack(side="left")
        self.onboard_dl_btn = ctk.CTkButton(
            m_row,
            text="Ready" if ready_now else "Download Model",
            width=130,
            height=28,
            state="disabled" if ready_now else "normal",
            fg_color="#1b2232" if ready_now else "#f8fafc",
            text_color="#34d399" if ready_now else "#090c13",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._onboard_download_model,
        )
        self.onboard_dl_btn.pack(side="right")

        self.onboard_prog = ctk.CTkProgressBar(p2, height=6, fg_color="#0b0e15", progress_color="#34d399")
        self.onboard_prog.set(1.0 if ready_now else 0.0)
        self.onboard_prog.pack(fill="x", padx=14, pady=(0, 10))

        p3 = ctk.CTkFrame(steps_box, fg_color="#151b27", corner_radius=10, border_width=1, border_color="#212a3d")
        p3.pack(fill="x", pady=6)
        ctk.CTkLabel(p3, text="3. Permissions & Controls", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=14, pady=(10, 4))
        c3_row = ctk.CTkFrame(p3, fg_color="transparent")
        c3_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(c3_row, text="Hotkey:", font=ctk.CTkFont(size=11), text_color="#cbd5e1").pack(side="left", padx=(0, 8))
        ctk.CTkSegmentedButton(
            c3_row,
            values=["ctrl+alt", "win+alt", "right_alt"],
            variable=self.var_hotkey,
            selected_color="#212a3d",
            command=self._on_setting_changed,
        ).pack(side="left")

        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.pack(fill="x", padx=28, pady=(6, 22))
        ctk.CTkButton(
            bottom,
            text=f"Get Started with {APP_NAME}",
            height=40,
            corner_radius=8,
            fg_color="#f8fafc",
            hover_color="#e2e8f0",
            text_color="#090c13",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._finish_onboarding,
        ).pack(side="right")

    def _onboard_download_model(self):
        m_key = canonical_model_key(self.var_model.get())
        self.onboard_dl_btn.configure(text="Downloading...", state="disabled")
        self._download_model_async(
            m_key,
            on_progress=lambda p: self.after(0, lambda v=p: self.onboard_prog.set(v)),
            on_done=lambda ok, msg: self.after(0, lambda: self._on_onboard_dl_done(ok, msg)),
        )

    def _on_onboard_dl_done(self, ok: bool, msg: str):
        if not self.onboarding_overlay:
            return
        if ok:
            self.onboard_model_lbl.configure(text="Model downloaded and verified.", text_color="#34d399")
            self.onboard_dl_btn.configure(text="Ready", state="disabled", fg_color="#1b2232", text_color="#34d399")
            self._refresh_model_status_ui()
        else:
            self.onboard_model_lbl.configure(text=f"Download failed: {msg}", text_color="#f87171")
            self.onboard_dl_btn.configure(text="Retry", state="normal", fg_color="#f8fafc", text_color="#090c13")

    def _finish_onboarding(self):
        self.cfg = load_config()
        self.cfg["onboarding_completed"] = True
        save_config(self.cfg)
        if self.onboarding_overlay:
            self.onboarding_overlay.destroy()
            self.onboarding_overlay = None

    # -------------------------------------------------------------------------
    # Lifecycle, IPC & Tray Visibility
    # -------------------------------------------------------------------------
    def _initial_sync(self):
        self._check_engine_status()
        self._load_vault_data()

    def _start_ipc_listener(self):
        """Listen on UDP 48765 for live transcripts, engine state updates, and tray show pings."""
        def _worker():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("127.0.0.1", STUDIO_IPC_PORT))
                sock.settimeout(1.0)
            except Exception:
                return

            while self.ipc_running:
                try:
                    data, _ = sock.recvfrom(65535)
                    msg = json.loads(data.decode("utf-8"))
                    mtype = msg.get("type")
                    if mtype == "show_studio":
                        self.after(0, self.bring_to_front)
                    elif mtype == "transcript":
                        text = msg.get("cleaned", "").strip()
                        ts = msg.get("ts", "")
                        if text:
                            self.after(0, lambda t=text, s=ts: self._append_live_transcript(t, s))
                    elif mtype == "lexicon_updated":
                        self.after(0, self._load_vault_data)
                except socket.timeout:
                    continue
                except Exception:
                    pass
            try:
                sock.close()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _append_live_transcript(self, text: str, ts: str):
        try:
            prefix = f"[{ts}] " if ts else ""
            self.preview_text.insert("end", f"{prefix}{text}\n")
            self.preview_text.see("end")
        except Exception:
            pass

    def bring_to_front(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
            self._load_vault_data()
            self._refresh_vault_usage_label()
        except Exception:
            pass

    def hide_to_tray(self):
        self.audio_tester.stop()
        self.withdraw()

    def _on_window_close(self):
        # If background engine is active, hide cleanly to System Tray; otherwise exit app
        if self.is_running:
            self.hide_to_tray()
        else:
            self.destroy()

    def _quit_completely(self):
        self.audio_tester.stop()
        if self.is_running:
            self._stop_engine_sync()
        self.destroy()

    def _toggle_mic_test(self):
        if self.audio_tester.running:
            self.audio_tester.stop()
        else:
            dev_idx = self.dev_name_to_idx.get(self.var_mic.get()) if hasattr(self, "var_mic") else self.cfg.get("mic_device")
            self.btn_mic_test.configure(text="Stop Mic Test", fg_color="#1f161c", text_color="#f87171")
            self.lbl_mic_status.configure(text="Testing (10s calibration)...")
            self.audio_tester.start(device_index=dev_idx, auto_stop_sec=10.0)

    def _on_mic_test_level(self, level: float):
        self.meter_level = level
        self.after(0, self._render_meter)

    def _on_mic_test_ended(self):
        self.meter_level = 0.0
        self.after(0, self._reset_mic_test_ui)

    def _reset_mic_test_ui(self):
        try:
            self._render_meter()
            self.btn_mic_test.configure(text="Test Microphone", fg_color="#1b2232", text_color="#cbd5e1")
            self.lbl_mic_status.configure(text="Idle (mic released until dictation)")
        except Exception:
            pass

    def _render_meter(self):
        try:
            total_segs = len(self.meter_segments)
            active_count = int(self.meter_level * total_segs)
            for i, seg in enumerate(self.meter_segments):
                if i < active_count:
                    color = "#34d399" if i < int(total_segs * 0.75) else "#38bdf8"
                    seg.configure(fg_color=color)
                else:
                    seg.configure(fg_color="#1b2232")
            if self.audio_tester.running:
                pct = int(self.meter_level * 100)
                self.lbl_mic_status.configure(text=f"Signal Detected ({pct}%)" if pct > 3 else "Listening for voice...")
        except Exception:
            pass

    def _periodic_process_check(self):
        threading.Thread(target=self._check_engine_status, daemon=True).start()
        self.after(2000, self._periodic_process_check)

    def _check_engine_status(self):
        server_ok = False
        try:
            r = requests.get(SERVER_HEALTH_URL, timeout=0.6)
            if r.status_code == 200:
                server_ok = True
        except Exception:
            server_ok = False

        flow_running = False
        pids = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                name = (p.info.get("name") or "").lower()
                if "whisper_flow.py" in cmd or (IS_FROZEN and "hushflow" in name and "--engine" in cmd):
                    flow_running = True
                    pids.append(p.info["pid"])
            except Exception:
                pass

        running = server_ok or flow_running
        self.is_running = running
        self.after(0, lambda: self._apply_engine_state(running, pids))

    def _apply_engine_state(self, running: bool, pids: List[int]):
        try:
            if running:
                pid_info = f"PID {pids[0]}" if pids else "Port 8080 Active"
                self.status_pill.configure(text="● ACTIVE", text_color="#34d399", fg_color="#0d2119")
                self.service_title.configure(text="Local Engine Active")
                self.service_desc.configure(
                    text=f"Engine online ({pid_info}). Dictate anytime with [{self._format_hotkey_label(self.cfg.get('hotkey', 'ctrl+alt'))}]."
                )
                self.btn_service_toggle.configure(
                    text="Stop Service", fg_color="#1f161c", hover_color="#2e1d28", text_color="#f87171"
                )
            else:
                self.status_pill.configure(text="○ INACTIVE", text_color="#8592a6", fg_color="#161c28")
                self.service_title.configure(text="Local Engine Inactive")
                self.service_desc.configure(text="Click Start Service to enable background dictation and the floating edge pill.")
                self.btn_service_toggle.configure(
                    text="Start Service", fg_color="#f8fafc", hover_color="#e2e8f0", text_color="#090c13"
                )
        except Exception:
            pass

    def _on_toggle_engine(self):
        if self.is_running:
            self._stop_engine_threaded()
        else:
            self._start_engine_threaded()

    def _start_engine_threaded(self):
        self.status_pill.configure(text="● STARTING", text_color="#fbbf24", fg_color="#241a0d")
        self.btn_service_toggle.configure(text="Starting...", state="disabled")

        def task():
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
                time.sleep(0.5)
                try:
                    r = requests.get(SERVER_HEALTH_URL, timeout=0.5)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass

            self.after(0, lambda: self.btn_service_toggle.configure(state="normal"))
            self.after(0, self._check_engine_status)

        threading.Thread(target=task, daemon=True).start()

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

    def _stop_engine_threaded(self):
        self.status_pill.configure(text="● STOPPING", text_color="#fbbf24", fg_color="#241a0d")
        self.btn_service_toggle.configure(text="Stopping...", state="disabled")

        def task():
            self._stop_engine_sync()
            time.sleep(0.5)
            self.after(0, lambda: self.btn_service_toggle.configure(state="normal"))
            self.after(0, self._check_engine_status)

        threading.Thread(target=task, daemon=True).start()

    # -------------------------------------------------------------------------
    # Settings Callbacks & Model Downloader
    # -------------------------------------------------------------------------
    def _format_hotkey_label(self, raw: str) -> str:
        mapping = {"ctrl+alt": "Ctrl + Alt", "win+alt": "Win + Alt", "right_alt": "Right Alt"}
        return mapping.get((raw or "").lower(), "Ctrl + Alt")

    def _on_mic_selected(self, choice: str):
        self.cfg = load_config()
        self.cfg["mic_device"] = self.dev_name_to_idx.get(choice)
        save_config(self.cfg)
        send_engine_command("reload_config")

    def _on_model_selected(self, choice: str):
        self._refresh_model_status_ui()
        if model_exists(choice):
            self.cfg = load_config()
            self.cfg["model"] = choice
            save_config(self.cfg)
            self.spec_model_lbl.configure(text=choice)
            send_engine_command("reload_config")

    def _refresh_model_status_ui(self):
        m_key = canonical_model_key(self.var_model.get())
        exists = model_exists(m_key)
        if exists:
            self.btn_download_model.configure(text="Installed", state="disabled", fg_color="#1b2232", text_color="#34d399")
            self.model_dl_progress.set(1.0)
        else:
            sz = MODEL_REGISTRY[m_key]["size_mb"]
            self.btn_download_model.configure(text=f"Download ({sz} MB)", state="normal", fg_color="#f8fafc", text_color="#090c13")
            self.model_dl_progress.set(0.0)

    def _on_download_active_model(self):
        if self.downloading_model:
            return
        m_key = canonical_model_key(self.var_model.get())
        self.btn_download_model.configure(text="Downloading...", state="disabled")
        self._download_model_async(
            m_key,
            on_progress=lambda p: self.after(0, lambda v=p: self.model_dl_progress.set(v)),
            on_done=lambda ok, msg: self.after(0, lambda: self._on_settings_dl_done(ok, m_key)),
        )

    def _on_settings_dl_done(self, ok: bool, m_key: str):
        self.downloading_model = False
        if ok:
            self.cfg = load_config()
            self.cfg["model"] = m_key
            save_config(self.cfg)
            self.spec_model_lbl.configure(text=m_key)
            send_engine_command("reload_config")
        self._refresh_model_status_ui()

    def _download_model_async(self, model_key: str, on_progress, on_done):
        self.downloading_model = True
        meta = MODEL_REGISTRY[model_key]
        os.makedirs(MODELS_DIR, exist_ok=True)
        dest_path = os.path.join(MODELS_DIR, meta["filename"])
        tmp_path = dest_path + ".part"

        def _worker():
            try:
                with requests.get(meta["url"], stream=True, timeout=30) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0)) or (meta["size_mb"] * 1024 * 1024)
                    downloaded = 0
                    with open(tmp_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 256):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                on_progress(min(0.99, downloaded / max(1, total)))
                os.replace(tmp_path, dest_path)
                on_progress(1.0)
                on_done(True, dest_path)
            except Exception as e:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                on_done(False, str(e))
            finally:
                self.downloading_model = False

        threading.Thread(target=_worker, daemon=True).start()

    def _on_setting_changed(self, *_):
        self.cfg = load_config()
        self.cfg["hotkey"] = self.var_hotkey.get()
        self.cfg["cleanup_level"] = self.var_cleanup.get()
        self.cfg["hud_visible"] = bool(self.var_hud.get())
        self.cfg["save_training_audio"] = bool(self.var_save_audio.get())
        save_config(self.cfg)

        self.spec_hotkey_lbl.configure(text=self._format_hotkey_label(self.cfg["hotkey"]))
        self.spec_privacy_lbl.configure(text="Opt-In Vault" if self.cfg["save_training_audio"] else "RAM Only (Zero Disk)")
        send_engine_command("reload_config")

    def _on_toggle_autostart(self):
        ok = set_autostart_enabled(bool(self.var_autostart.get()))
        if not ok:
            self.var_autostart.set(is_autostart_enabled())

    def _refresh_vault_usage_label(self):
        try:
            used_mb = get_vault_disk_usage_bytes() / (1024 * 1024)
            self.lbl_vault_usage.configure(text=f"Local vault usage: {used_mb:.2f} MB ({USER_DATA_DIR})")
        except Exception:
            pass

    def _on_purge_vault(self):
        purge_vault_audio_and_logs()
        self._refresh_vault_usage_label()
        self._load_vault_data()

    def _open_data_folder(self):
        try:
            os.startfile(USER_DATA_DIR)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Lexicon Data Methods
    # -------------------------------------------------------------------------
    def _load_vault_data(self):
        self.cfg = load_config()
        self.rules_cache = self.cfg.get("word_replacements", {})
        custom_vocab = self.cfg.get("custom_vocabulary", [])

        total_rules = len(self.rules_cache)
        total_vocab = len(custom_vocab)
        self.lexicon_count_lbl.configure(text=f"Acoustic Lexicon ({total_rules} Rules • {total_vocab} Custom Terms)")
        self._filter_rules()

    def _filter_rules(self):
        query = self.entry_search.get().strip().lower()
        if not query:
            self.filtered_rules = list(self.rules_cache.items())
        else:
            self.filtered_rules = [
                (k, v) for k, v in self.rules_cache.items() if query in k.lower() or query in v.lower()
            ]
        self._render_rules_list()

    def _on_add_rule(self):
        spoken = self.entry_spoken.get().strip()
        corrected = self.entry_corrected.get().strip()
        if not spoken or not corrected:
            return

        cfg = load_config()
        reps = cfg.get("word_replacements", {})
        reps[spoken.lower()] = corrected
        cfg["word_replacements"] = reps

        vocab = cfg.get("custom_vocabulary", [])
        for word in corrected.split():
            clean_word = word.strip(".,!?;:\"'()")
            if clean_word and clean_word not in vocab:
                vocab.append(clean_word)
        cfg["custom_vocabulary"] = vocab
        save_config(cfg)

        record = {
            "timestamp": time.strftime("%Y%m%d_%H%M%S"),
            "original": spoken,
            "corrected": corrected,
            "replacements": [[spoken.lower(), corrected]],
            "window_title": f"{APP_NAME} Lexicon",
        }
        try:
            with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

        self.entry_spoken.delete(0, "end")
        self.entry_corrected.delete(0, "end")
        self._load_vault_data()
        send_engine_command("reload_config")

    def _on_delete_rule(self, spoken_key: str):
        cfg = load_config()
        reps = cfg.get("word_replacements", {})
        if spoken_key in reps:
            del reps[spoken_key]
            cfg["word_replacements"] = reps
            save_config(cfg)
        self._load_vault_data()
        send_engine_command("reload_config")

    def _launch_quickfix(self):
        if IS_FROZEN:
            subprocess.Popen([sys.executable, "--quickfix"], cwd=INSTALL_DIR)
        else:
            quick_fix_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quick_fix.py")
            if os.path.exists(quick_fix_script):
                subprocess.Popen([sys.executable, quick_fix_script], cwd=os.path.dirname(os.path.abspath(__file__)))

    def _clear_scratchpad(self):
        self.preview_text.delete("1.0", "end")

    def _copy_scratchpad(self):
        try:
            import pyperclip
            pyperclip.copy(self.preview_text.get("1.0", "end").strip())
        except Exception:
            pass

    def destroy(self):
        self.ipc_running = False
        self.audio_tester.stop()
        super().destroy()


_studio_mutex_handle = None


def main():
    global _studio_mutex_handle
    # Kernel Named Mutex prevents duplicate windows cleanly
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

    app = HushFlowStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
