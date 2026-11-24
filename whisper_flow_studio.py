"""
WhisperFlow Studio - Modern Control Center & Self-Learning Engine UI
Provides a single-window dashboard with logo, Start/Stop lifecycle controls,
real-time audio input meter, interactive dictation scratchpad, and the self-learning ML acoustic vault.
"""

import os
import sys
import time
import json
import threading
import subprocess
import ctypes
from typing import Optional, List, Dict, Tuple

import psutil
import requests
import customtkinter as ctk
from PIL import Image

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "whisper_flow_config.json")
VAULT_DIR = os.path.join(BASE_DIR, "vault")
VAULT_AUDIO_DIR = os.path.join(VAULT_DIR, "audio")
CORRECTIONS_FILE = os.path.join(VAULT_DIR, "corrections.jsonl")
ICO_PATH = os.path.join(BASE_DIR, "whisper_flow.ico")
PID_FILE = os.path.join(BASE_DIR, "whisper_flow.pid")
LOG_FILE = os.path.join(BASE_DIR, "whisper_flow.log")

SERVER_HEALTH_URL = "http://127.0.0.1:8080/health"

# Ensure directories exist
os.makedirs(VAULT_AUDIO_DIR, exist_ok=True)


def set_native_dark_titlebar(window):
    """Enables Windows 11 DWM dark titlebar and rounded corners."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


class AudioMeter:
    """Low-overhead background audio listener for live mic meter."""
    def __init__(self, callback):
        self.callback = callback
        self.running = False
        self.stream = None
        self.current_level = 0.0
        self.thread = None

    def start(self):
        if not AUDIO_AVAILABLE:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        def audio_cb(indata, frames, time_info, status):
            if not self.running:
                return
            try:
                rms = float(np.sqrt(np.mean(indata**2)))
                level = min(1.0, rms * 14.0)
                if level > self.current_level:
                    self.current_level = level
                else:
                    self.current_level = self.current_level * 0.82 + level * 0.18
                if self.callback and self.running:
                    self.callback(self.current_level)
            except Exception:
                pass

        try:
            self.stream = sd.InputStream(channels=1, samplerate=16000, blocksize=1024, callback=audio_cb)
            with self.stream:
                while self.running:
                    time.sleep(0.05)
        except Exception:
            pass

    def stop(self):
        self.running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class WhisperFlowStudio(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Appearance configuration
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("WhisperFlow Studio • Control Center")
        self.geometry("800x700")
        self.minsize(740, 640)
        self.configure(fg_color="#0b0f19")  # Deep premium dark background

        # Set taskbar & window icon
        if os.path.exists(ICO_PATH):
            try:
                self.iconbitmap(ICO_PATH)
            except Exception:
                pass

        # State tracking
        self.is_running = False
        self.audio_meter = None
        self.meter_level = 0.0
        self.rules_cache: Dict[str, str] = {}
        self.filtered_rules: List[Tuple[str, str]] = []

        # Build UI layout
        self._build_header()
        self._build_tabview()
        self._build_footer()

        # Windows Dark Titlebar
        set_native_dark_titlebar(self)

        # Start background polling & audio meter
        self.after(200, self._initial_sync)
        self._start_audio_meter()

        # Recurring system monitor (every 1.5s)
        self.after(1500, self._periodic_process_check)

    # -------------------------------------------------------------------------
    # UI Building Blocks
    # -------------------------------------------------------------------------
    def _build_header(self):
        header_frame = ctk.CTkFrame(self, fg_color="#111827", corner_radius=12, border_width=1, border_color="#1f2937")
        header_frame.pack(fill="x", padx=16, pady=(14, 8))

        # Brand / Logo layout
        logo_subframe = ctk.CTkFrame(header_frame, fg_color="transparent")
        logo_subframe.pack(side="left", padx=16, pady=12)

        # Prominent WhisperFlow Logo
        logo_loaded = False
        if os.path.exists(ICO_PATH):
            try:
                pil_img = Image.open(ICO_PATH)
                self.logo_ctk = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(54, 54))
                logo_lbl = ctk.CTkLabel(logo_subframe, image=self.logo_ctk, text="")
                logo_lbl.pack(side="left", padx=(0, 14))
                logo_loaded = True
            except Exception:
                pass

        if not logo_loaded:
            badge = ctk.CTkLabel(
                logo_subframe,
                text="⚡",
                font=ctk.CTkFont(size=28),
                width=54,
                height=54,
                fg_color="#10b981",
                corner_radius=27,
                text_color="#ffffff"
            )
            badge.pack(side="left", padx=(0, 14))

        # Title and tag
        title_box = ctk.CTkFrame(logo_subframe, fg_color="transparent")
        title_box.pack(side="left")

        title_lbl = ctk.CTkLabel(
            title_box,
            text="WHISPERFLOW",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color="#f9fafb"
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = ctk.CTkLabel(
            title_box,
            text="100% Local AI Voice Dictation & Adaptive Learning Engine",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#9ca3af"
        )
        subtitle_lbl.pack(anchor="w")

        # Top Right Status Pill
        self.status_pill = ctk.CTkLabel(
            header_frame,
            text="● CHECKING...",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#d1d5db",
            fg_color="#1e293b",
            corner_radius=20,
            padx=16,
            pady=6
        )
        self.status_pill.pack(side="right", padx=16, pady=12)

    def _build_tabview(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color="#111827",
            segmented_button_fg_color="#1f2937",
            segmented_button_selected_color="#10b981",
            segmented_button_selected_hover_color="#059669",
            segmented_button_unselected_hover_color="#374151",
            corner_radius=12
        )
        self.tabview.pack(fill="both", expand=True, padx=16, pady=8)

        self.tab_dashboard = self.tabview.add("🎮 Studio Dashboard")
        self.tab_vault = self.tabview.add("🧠 Self-Learning Vault")
        self.tab_cheatsheet = self.tabview.add("⌨️ Shortcuts & Commands")

        self._build_dashboard_tab()
        self._build_vault_tab()
        self._build_cheatsheet_tab()

    # -------------------------------------------------------------------------
    # Tab 1: Studio Dashboard
    # -------------------------------------------------------------------------
    def _build_dashboard_tab(self):
        # Master Hero Action Card
        hero_card = ctk.CTkFrame(self.tab_dashboard, fg_color="#1a2234", corner_radius=12, border_width=1, border_color="#2d3748")
        hero_card.pack(fill="x", padx=12, pady=10)

        hero_left = ctk.CTkFrame(hero_card, fg_color="transparent")
        hero_left.pack(side="left", padx=18, pady=16, fill="both", expand=True)

        self.hero_status_title = ctk.CTkLabel(
            hero_left,
            text="WhisperFlow Engine Offline",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#f3f4f6"
        )
        self.hero_status_title.pack(anchor="w")

        self.hero_status_desc = ctk.CTkLabel(
            hero_left,
            text="Press Start to launch the in-memory whisper server and global keyboard listeners.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#9ca3af"
        )
        self.hero_status_desc.pack(anchor="w", pady=(4, 0))

        # Master Start / Stop Button
        self.btn_master_toggle = ctk.CTkButton(
            hero_card,
            text="▶  START WHISPERFLOW",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            height=46,
            width=210,
            corner_radius=8,
            command=self._on_toggle_engine
        )
        self.btn_master_toggle.pack(side="right", padx=18, pady=16)

        # Telemetry & Mic Monitor Card
        telemetry_card = ctk.CTkFrame(self.tab_dashboard, fg_color="#111827", corner_radius=10, border_width=1, border_color="#1f2937")
        telemetry_card.pack(fill="x", padx=12, pady=6)

        # Microphone Input VU Meter
        vu_box = ctk.CTkFrame(telemetry_card, fg_color="transparent")
        vu_box.pack(fill="x", padx=14, pady=10)

        vu_header = ctk.CTkFrame(vu_box, fg_color="transparent")
        vu_header.pack(fill="x")

        ctk.CTkLabel(
            vu_header,
            text="🎙️ Live Microphone Input Level:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e5e7eb"
        ).pack(side="left")

        self.vu_text_val = ctk.CTkLabel(
            vu_header,
            text="Testing mic...",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af"
        )
        self.vu_text_val.pack(side="right")

        self.vu_bar = ctk.CTkProgressBar(
            vu_box,
            height=12,
            corner_radius=6,
            fg_color="#1f2937",
            progress_color="#10b981"
        )
        self.vu_bar.set(0.0)
        self.vu_bar.pack(fill="x", pady=(6, 2))

        # Hardware & Model Metrics Row
        stats_row = ctk.CTkFrame(self.tab_dashboard, fg_color="transparent")
        stats_row.pack(fill="x", padx=12, pady=6)

        self._build_metric_badge(stats_row, "🧠 Active Model", "small.en-q5_1 (~190 MB)", "#06b6d4")
        self._build_metric_badge(stats_row, "⚡ Latency", "< 0.8s Sub-Second", "#10b981")
        self._build_metric_badge(stats_row, "🛡️ Privacy", "100% Air-Gapped", "#8b5cf6")
        self._build_metric_badge(stats_row, "🧵 CPU Threads", "8 Threads (i5-13420H)", "#f59e0b")

        # Interactive Test Dictation Scratchpad
        scratch_frame = ctk.CTkFrame(self.tab_dashboard, fg_color="#111827", corner_radius=10, border_width=1, border_color="#1f2937")
        scratch_frame.pack(fill="both", expand=True, padx=12, pady=(8, 4))

        scratch_head = ctk.CTkFrame(scratch_frame, fg_color="transparent")
        scratch_head.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            scratch_head,
            text="📝 Interactive Dictation Scratchpad",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f3f4f6"
        ).pack(side="left")

        ctk.CTkLabel(
            scratch_head,
            text="Hold [Ctrl + Alt] or toggle [Ctrl + Alt + Space] to dictate directly into this box",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af"
        ).pack(side="left", padx=10)

        btn_clear = ctk.CTkButton(
            scratch_head,
            text="Clear",
            width=60,
            height=24,
            fg_color="#374151",
            hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._clear_scratchpad
        )
        btn_clear.pack(side="right")

        self.scratch_text = ctk.CTkTextbox(
            scratch_frame,
            fg_color="#0b0f19",
            text_color="#f9fafb",
            font=ctk.CTkFont(family="Consolas", size=13),
            corner_radius=8,
            border_width=1,
            border_color="#1f2937"
        )
        self.scratch_text.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.scratch_text.insert("1.0", "Welcome to WhisperFlow Studio!\nClick here, hold [Ctrl + Alt], speak, and release to see your words typed in real time.")

    def _build_metric_badge(self, parent, label: str, val: str, color: str):
        badge = ctk.CTkFrame(parent, fg_color="#111827", corner_radius=8, border_width=1, border_color="#1f2937")
        badge.pack(side="left", fill="both", expand=True, padx=4)

        ctk.CTkLabel(badge, text=label, font=ctk.CTkFont(size=11), text_color="#9ca3af").pack(anchor="w", padx=8, pady=(6, 0))
        ctk.CTkLabel(badge, text=val, font=ctk.CTkFont(size=12, weight="bold"), text_color=color).pack(anchor="w", padx=8, pady=(0, 6))

    # -------------------------------------------------------------------------
    # Tab 2: Self-Learning ML Vault
    # -------------------------------------------------------------------------
    def _build_vault_tab(self):
        # Vault Stats & QuickFix Trigger Banner
        top_vault_frame = ctk.CTkFrame(self.tab_vault, fg_color="#1a2234", corner_radius=10, border_width=1, border_color="#2d3748")
        top_vault_frame.pack(fill="x", padx=12, pady=10)

        v_info = ctk.CTkFrame(top_vault_frame, fg_color="transparent")
        v_info.pack(side="left", padx=14, pady=12)

        self.vault_stats_lbl = ctk.CTkLabel(
            v_info,
            text="🧠 Self-Learning Adaptive Vault: Loading...",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#f3f4f6"
        )
        self.vault_stats_lbl.pack(anchor="w")

        ctk.CTkLabel(
            v_info,
            text="Acoustic homophones and pronunciation corrections automatically adapt future speech without retraining.",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af"
        ).pack(anchor="w", pady=(2, 0))

        # Launch Alt+Z button
        btn_quickfix = ctk.CTkButton(
            top_vault_frame,
            text="⚡ Open QuickFix (Alt + Z)",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#8b5cf6",
            hover_color="#7c3aed",
            height=34,
            command=self._launch_quickfix
        )
        btn_quickfix.pack(side="right", padx=14, pady=12)

        # Teach Model (Instant Add) Card
        add_card = ctk.CTkFrame(self.tab_vault, fg_color="#111827", corner_radius=10, border_width=1, border_color="#1f2937")
        add_card.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(
            add_card,
            text="Teach New Acoustic Correction:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e5e7eb"
        ).pack(anchor="w", padx=12, pady=(10, 4))

        inputs_row = ctk.CTkFrame(add_card, fg_color="transparent")
        inputs_row.pack(fill="x", padx=12, pady=(0, 10))

        self.entry_spoken = ctk.CTkEntry(
            inputs_row,
            placeholder_text="What Whisper hears (e.g. 'read the setup' or 'tcu')",
            height=34,
            fg_color="#0b0f19",
            border_color="#374151"
        )
        self.entry_spoken.pack(side="left", fill="both", expand=True, padx=(0, 6))

        self.entry_corrected = ctk.CTkEntry(
            inputs_row,
            placeholder_text="What you actually meant (e.g. 'Rate the setup' or 'issue')",
            height=34,
            fg_color="#0b0f19",
            border_color="#374151"
        )
        self.entry_corrected.pack(side="left", fill="both", expand=True, padx=(0, 8))

        btn_learn = ctk.CTkButton(
            inputs_row,
            text="+ Learn Fix",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            width=100,
            height=34,
            command=self._on_add_rule
        )
        btn_learn.pack(side="right")

        # Search Bar & List of Learned Rules
        list_header = ctk.CTkFrame(self.tab_vault, fg_color="transparent")
        list_header.pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkLabel(
            list_header,
            text="Active Learned Acoustic Rules:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e5e7eb"
        ).pack(side="left")

        self.entry_search = ctk.CTkEntry(
            list_header,
            placeholder_text="🔍 Filter rules...",
            width=180,
            height=28,
            fg_color="#111827",
            border_color="#374151"
        )
        self.entry_search.pack(side="right")
        self.entry_search.bind("<KeyRelease>", lambda e: self._filter_rules())

        # Scrollable Rules Container
        self.rules_scroll = ctk.CTkScrollableFrame(
            self.tab_vault,
            fg_color="#0b0f19",
            corner_radius=8,
            border_width=1,
            border_color="#1f2937"
        )
        self.rules_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def _render_rules_list(self):
        # Clear existing rows
        for widget in self.rules_scroll.winfo_children():
            widget.destroy()

        if not self.filtered_rules:
            empty_lbl = ctk.CTkLabel(
                self.rules_scroll,
                text="No acoustic correction rules found.\nUse the input above or press [Alt + Z] to teach WhisperFlow new acoustic patterns.",
                font=ctk.CTkFont(size=12),
                text_color="#6b7280"
            )
            empty_lbl.pack(pady=30)
            return

        for spoken, corrected in self.filtered_rules:
            row = ctk.CTkFrame(self.rules_scroll, fg_color="#111827", corner_radius=6, height=36)
            row.pack(fill="x", pady=2, padx=4)

            # Spoken pill
            pill_spoken = ctk.CTkLabel(
                row,
                text=spoken,
                font=ctk.CTkFont(family="Consolas", size=12),
                fg_color="#1f2937",
                corner_radius=4,
                padx=8,
                pady=2,
                text_color="#f59e0b"
            )
            pill_spoken.pack(side="left", padx=8, pady=4)

            ctk.CTkLabel(
                row,
                text="➔",
                font=ctk.CTkFont(size=12),
                text_color="#9ca3af"
            ).pack(side="left", padx=4)

            # Corrected pill
            pill_corr = ctk.CTkLabel(
                row,
                text=corrected,
                font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
                fg_color="#064e3b",
                corner_radius=4,
                padx=8,
                pady=2,
                text_color="#34d399"
            )
            pill_corr.pack(side="left", padx=4, pady=4)

            # Delete button
            del_btn = ctk.CTkButton(
                row,
                text="✕ Remove",
                font=ctk.CTkFont(size=11),
                fg_color="#374151",
                hover_color="#ef4444",
                width=70,
                height=24,
                command=lambda s=spoken: self._on_delete_rule(s)
            )
            del_btn.pack(side="right", padx=8, pady=4)

    # -------------------------------------------------------------------------
    # Tab 3: Shortcuts & Commands Cheatsheet
    # -------------------------------------------------------------------------
    def _build_cheatsheet_tab(self):
        container = ctk.CTkScrollableFrame(self.tab_cheatsheet, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=12, pady=10)

        # Hardware Keys Card
        hw_card = ctk.CTkFrame(container, fg_color="#111827", corner_radius=10, border_width=1, border_color="#1f2937")
        hw_card.pack(fill="x", pady=6)

        ctk.CTkLabel(hw_card, text="⌨️ Global Hardware Shortcuts", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f3f4f6").pack(anchor="w", padx=12, pady=(10, 6))

        shortcuts = [
            ("Hold [Ctrl + Alt]", "Push-to-Talk: Audio records while held; transcribes and pastes on release."),
            ("Press [Ctrl + Alt + Space]", "Hands-Free Dictation: Starts hands-free mode. Press again or Esc to finish."),
            ("Press [Alt + Z]", "QuickFix Vault: Correct misheard words and train your personal voice model."),
            ("Press [Ctrl + Shift + Q]", "Emergency Stop: Instantly terminates WhisperFlow background listeners.")
        ]
        for key, desc in shortcuts:
            r = ctk.CTkFrame(hw_card, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=3)
            ctk.CTkLabel(r, text=key, font=ctk.CTkFont(family="Consolas", size=11, weight="bold"), text_color="#10b981", width=200, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=desc, font=ctk.CTkFont(size=11), text_color="#9ca3af").pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(hw_card, text="", height=4).pack()

        # Spoken Commands Card
        cmd_card = ctk.CTkFrame(container, fg_color="#111827", corner_radius=10, border_width=1, border_color="#1f2937")
        cmd_card.pack(fill="x", pady=6)

        ctk.CTkLabel(cmd_card, text="🗣️ Voice Formatting & Punctuation Commands", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f3f4f6").pack(anchor="w", padx=12, pady=(10, 6))

        voice_cmds = [
            ("new line", "Types a spoken line break (\\n)"),
            ("new paragraph", "Types two spoken line breaks (\\n\\n)"),
            ("snake case <words>", "Converts words to snake_case format (e.g. calculate_total_tax)"),
            ("camel case <words>", "Converts words to camelCase format (e.g. getUserProfile)"),
            ("kebab case <words>", "Converts words to kebab-case format (e.g. header-nav-bar)"),
            ("all caps <words>", "Converts words to ALL CAPS format (e.g. IMPORTANT NOTICE)"),
            ("double <number>", "Expands repeated digits (e.g. 'double 4' -> 44, 'double 5' -> 55)"),
            ("triple <number>", "Expands repeated digits (e.g. 'triple 7' -> 777)")
        ]
        for phrase, effect in voice_cmds:
            r = ctk.CTkFrame(cmd_card, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=3)
            ctk.CTkLabel(r, text=f'"{phrase}"', font=ctk.CTkFont(family="Consolas", size=11, weight="bold"), text_color="#06b6d4", width=200, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=effect, font=ctk.CTkFont(size=11), text_color="#9ca3af").pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(cmd_card, text="", height=4).pack()

    # -------------------------------------------------------------------------
    # Footer
    # -------------------------------------------------------------------------
    def _build_footer(self):
        footer_frame = ctk.CTkFrame(self, fg_color="transparent")
        footer_frame.pack(fill="x", padx=16, pady=(4, 12))

        self.footer_status = ctk.CTkLabel(
            footer_frame,
            text="WhisperFlow v2.0 • 13th Gen Intel Core i5-13420H • 16GB RAM • GGML Quantized",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280"
        )
        self.footer_status.pack(side="left")

        btn_min = ctk.CTkButton(
            footer_frame,
            text="Minimize to Tray",
            width=110,
            height=26,
            fg_color="#1f2937",
            hover_color="#374151",
            font=ctk.CTkFont(size=11),
            command=self.iconify
        )
        btn_min.pack(side="right")

    # -------------------------------------------------------------------------
    # System Lifecycle & State Management
    # -------------------------------------------------------------------------
    def _initial_sync(self):
        self._check_engine_status()
        self._load_vault_data()

    def _start_audio_meter(self):
        if AUDIO_AVAILABLE:
            self.audio_meter = AudioMeter(self._update_meter_gui)
            self.audio_meter.start()

    def _update_meter_gui(self, level: float):
        self.meter_level = level
        # Update progress bar safely on UI thread
        self.after(0, self._render_meter)

    def _render_meter(self):
        try:
            self.vu_bar.set(self.meter_level)
            pct = int(self.meter_level * 100)
            if pct < 5:
                self.vu_text_val.configure(text="Silent (Ready)")
                self.vu_bar.configure(progress_color="#10b981")
            elif pct < 70:
                self.vu_text_val.configure(text=f"Active Speech ({pct}%)")
                self.vu_bar.configure(progress_color="#10b981")
            else:
                self.vu_text_val.configure(text=f"High Volume ({pct}%)")
                self.vu_bar.configure(progress_color="#06b6d4")
        except Exception:
            pass

    def _periodic_process_check(self):
        threading.Thread(target=self._check_engine_status, daemon=True).start()
        self.after(2000, self._periodic_process_check)

    def _check_engine_status(self):
        """Checks if whisper-server (port 8080) and whisper_flow.py are running."""
        server_ok = False
        try:
            r = requests.get(SERVER_HEALTH_URL, timeout=0.6)
            if r.status_code == 200:
                server_ok = True
        except Exception:
            server_ok = False

        flow_running = False
        pids = []
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmd = " ".join(p.info['cmdline'] or [])
                if "whisper_flow.py" in cmd:
                    flow_running = True
                    pids.append(p.info['pid'])
            except Exception:
                pass

        running = server_ok or flow_running
        self.is_running = running
        self.after(0, lambda: self._apply_engine_state(running, server_ok, flow_running, pids))

    def _apply_engine_state(self, running: bool, server_ok: bool, flow_running: bool, pids: List[int]):
        try:
            if running:
                pid_str = f"PIDs: {', '.join(map(str, pids))}" if pids else "Port 8080 Active"
                self.status_pill.configure(
                    text="● ENGINE ONLINE",
                    text_color="#10b981",
                    fg_color="#064e3b"
                )
                self.hero_status_title.configure(text="WhisperFlow Engine Online & Ready")
                self.hero_status_desc.configure(
                    text=f"Microphone listener and in-memory server are active ({pid_str}). Hold [Ctrl + Alt] to dictate anywhere."
                )
                self.btn_master_toggle.configure(
                    text="■  STOP WHISPERFLOW",
                    fg_color="#ef4444",
                    hover_color="#dc2626"
                )
            else:
                self.status_pill.configure(
                    text="○ ENGINE STOPPED",
                    text_color="#9ca3af",
                    fg_color="#1e293b"
                )
                self.hero_status_title.configure(text="WhisperFlow Engine Offline")
                self.hero_status_desc.configure(
                    text="Press Start to launch the in-memory whisper server and global keyboard listeners."
                )
                self.btn_master_toggle.configure(
                    text="▶  START WHISPERFLOW",
                    fg_color="#10b981",
                    hover_color="#059669"
                )
        except Exception:
            pass

    def _on_toggle_engine(self):
        if self.is_running:
            self._stop_engine_threaded()
        else:
            self._start_engine_threaded()

    def _start_engine_threaded(self):
        self.status_pill.configure(text="● STARTING...", text_color="#f59e0b", fg_color="#78350f")
        self.btn_master_toggle.configure(text="STARTING...", state="disabled")

        def task():
            script_path = os.path.join(BASE_DIR, "whisper_flow.py")
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = "pythonw.exe"

            # Launch detached process
            subprocess.Popen(
                [pythonw, script_path],
                cwd=BASE_DIR,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )

            # Wait for server health up to 8s
            for _ in range(16):
                time.sleep(0.5)
                try:
                    r = requests.get(SERVER_HEALTH_URL, timeout=0.5)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass

            self.after(0, lambda: self.btn_master_toggle.configure(state="normal"))
            self.after(0, self._check_engine_status)

        threading.Thread(target=task, daemon=True).start()

    def _stop_engine_threaded(self):
        self.status_pill.configure(text="● STOPPING...", text_color="#f59e0b", fg_color="#78350f")
        self.btn_master_toggle.configure(text="STOPPING...", state="disabled")

        def task():
            # Terminate whisper_flow.py processes
            for p in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if "whisper_flow.py" in cmd:
                        p.terminate()
                except Exception:
                    pass

            # Terminate whisper-server.exe
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    if "whisper-server" in p.info['name'].lower():
                        p.terminate()
                except Exception:
                    pass

            # Clean port 8080 listeners
            try:
                for conn in psutil.net_connections(kind='inet'):
                    if conn.laddr and conn.laddr.port == 8080 and conn.pid:
                        try:
                            p = psutil.Process(conn.pid)
                            p.terminate()
                        except Exception:
                            pass
            except Exception:
                pass

            time.sleep(1.0)
            # Remove PID file
            if os.path.exists(PID_FILE):
                try:
                    os.remove(PID_FILE)
                except Exception:
                    pass

            self.after(0, lambda: self.btn_master_toggle.configure(state="normal"))
            self.after(0, self._check_engine_status)

        threading.Thread(target=task, daemon=True).start()

    # -------------------------------------------------------------------------
    # Vault & ML Learning Methods
    # -------------------------------------------------------------------------
    def _load_vault_data(self):
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

        self.rules_cache = cfg.get("word_replacements", {})
        custom_vocab = cfg.get("custom_vocabulary", [])

        # Count records in corrections.jsonl
        corrections_count = 0
        if os.path.exists(CORRECTIONS_FILE):
            try:
                with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            corrections_count += 1
            except Exception:
                pass

        total_rules = len(self.rules_cache)
        total_vocab = len(custom_vocab)
        self.vault_stats_lbl.configure(
            text=f"🧠 Adaptive Vault: {total_rules} Acoustic Rules • {total_vocab} Vocab Terms • {corrections_count} Verified Logs"
        )

        self._filter_rules()

    def _filter_rules(self):
        query = self.entry_search.get().strip().lower()
        if not query:
            self.filtered_rules = list(self.rules_cache.items())
        else:
            self.filtered_rules = [
                (k, v) for k, v in self.rules_cache.items()
                if query in k.lower() or query in v.lower()
            ]
        self._render_rules_list()

    def _on_add_rule(self):
        spoken = self.entry_spoken.get().strip()
        corrected = self.entry_corrected.get().strip()

        if not spoken or not corrected:
            return

        # Update config
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

        reps = cfg.get("word_replacements", {})
        reps[spoken.lower()] = corrected
        cfg["word_replacements"] = reps

        # Also add to custom vocabulary
        vocab = cfg.get("custom_vocabulary", [])
        for word in corrected.split():
            clean_word = word.strip(".,!?;:\"'()")
            if clean_word and clean_word not in vocab:
                vocab.append(clean_word)
        cfg["custom_vocabulary"] = vocab

        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # Append to corrections.jsonl
        record = {
            "timestamp": time.strftime("%Y%m%d_%H%M%S"),
            "original": spoken,
            "corrected": corrected,
            "replacements": [[spoken.lower(), corrected]],
            "window_title": "WhisperFlow Studio Manual Add"
        }
        try:
            with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

        self.entry_spoken.delete(0, "end")
        self.entry_corrected.delete(0, "end")
        self._load_vault_data()

    def _on_delete_rule(self, spoken_key: str):
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

        reps = cfg.get("word_replacements", {})
        if spoken_key in reps:
            del reps[spoken_key]
            cfg["word_replacements"] = reps
            try:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

        self._load_vault_data()

    def _launch_quickfix(self):
        quick_fix_script = os.path.join(BASE_DIR, "quick_fix.py")
        if os.path.exists(quick_fix_script):
            subprocess.Popen([sys.executable, quick_fix_script], cwd=BASE_DIR)

    def _clear_scratchpad(self):
        self.scratch_text.delete("1.0", "end")

    def destroy(self):
        if self.audio_meter:
            self.audio_meter.stop()
        super().destroy()


def main():
    app = WhisperFlowStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
