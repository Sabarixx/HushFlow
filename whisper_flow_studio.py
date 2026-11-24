"""
WhisperFlow Studio - Executive Control Center & Lexicon Architecture
Designed with precision typography, discrete hardware-level meters,
concentric card framing, and an uncluttered professional aesthetic.
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
EMBLEM_PATH = os.path.join(BASE_DIR, "assets", "whisperflow_emblem.png")
ICO_PATH = os.path.join(BASE_DIR, "whisper_flow.ico")
PID_FILE = os.path.join(BASE_DIR, "whisper_flow.pid")

SERVER_HEALTH_URL = "http://127.0.0.1:8080/health"

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
    """Low-overhead background audio listener for discrete signal meter."""
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
                level = min(1.0, rms * 15.0)
                if level > self.current_level:
                    self.current_level = level
                else:
                    self.current_level = self.current_level * 0.80 + level * 0.20
                if self.callback and self.running:
                    self.callback(self.current_level)
            except Exception:
                pass

        try:
            self.stream = sd.InputStream(channels=1, samplerate=16000, blocksize=1024, callback=audio_cb)
            with self.stream:
                while self.running:
                    time.sleep(0.04)
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

        # Appearance configuration - Deep, warm OLED graphite
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("WhisperFlow • Voice Architecture")
        self.geometry("820x680")
        self.minsize(760, 620)
        self.configure(fg_color="#090c13")

        # Window icon
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
        self.meter_segments = []

        # Build UI layout
        self._build_header()
        self._build_navigation()
        self._build_footer()

        # Windows 11 Native Dark Titlebar
        set_native_dark_titlebar(self)

        # Start background polling & audio meter
        self.after(150, self._initial_sync)
        self._start_audio_meter()

        # Recurring system monitor (every 1.5s)
        self.after(1500, self._periodic_process_check)

    # -------------------------------------------------------------------------
    # Header & Navigation
    # -------------------------------------------------------------------------
    def _build_header(self):
        header = ctk.CTkFrame(
            self,
            fg_color="#10141e",
            corner_radius=14,
            border_width=1,
            border_color="#1b2232"
        )
        header.pack(fill="x", padx=18, pady=(16, 10))

        # Brand / Logo Box
        left_box = ctk.CTkFrame(header, fg_color="transparent")
        left_box.pack(side="left", padx=16, pady=14)

        # Single Emblem Logo
        logo_loaded = False
        target_logo = EMBLEM_PATH if os.path.exists(EMBLEM_PATH) else ICO_PATH
        if os.path.exists(target_logo):
            try:
                pil_img = Image.open(target_logo)
                self.logo_ctk = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(46, 46))
                logo_lbl = ctk.CTkLabel(left_box, image=self.logo_ctk, text="")
                logo_lbl.pack(side="left", padx=(0, 14))
                logo_loaded = True
            except Exception:
                pass

        if not logo_loaded:
            disc = ctk.CTkFrame(left_box, width=46, height=46, corner_radius=23, fg_color="#1e2638")
            disc.pack(side="left", padx=(0, 14))

        # Brand Titles
        titles = ctk.CTkFrame(left_box, fg_color="transparent")
        titles.pack(side="left")

        ctk.CTkLabel(
            titles,
            text="WhisperFlow",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=20, weight="bold"),
            text_color="#f8fafc"
        ).pack(anchor="w")

        ctk.CTkLabel(
            titles,
            text="Speech Recognition & Lexicon Architecture",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8592a6"
        ).pack(anchor="w")

        # Status Pill Badge
        self.status_pill = ctk.CTkLabel(
            header,
            text="● INITIALIZING",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8592a6",
            fg_color="#161c28",
            corner_radius=16,
            padx=14,
            pady=5
        )
        self.status_pill.pack(side="right", padx=16, pady=14)

    def _build_navigation(self):
        self.tabview = ctk.CTkTabview(
            self,
            fg_color="#10141e",
            segmented_button_fg_color="#151b27",
            segmented_button_selected_color="#212a3d",
            segmented_button_selected_hover_color="#28334a",
            segmented_button_unselected_hover_color="#1a2233",
            corner_radius=14
        )
        self.tabview.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        self.tab_overview = self.tabview.add("Overview")
        self.tab_lexicon = self.tabview.add("Lexicon")
        self.tab_shortcuts = self.tabview.add("Shortcuts")

        self._build_overview_tab()
        self._build_lexicon_tab()
        self._build_shortcuts_tab()

    # -------------------------------------------------------------------------
    # Tab 1: Overview
    # -------------------------------------------------------------------------
    def _build_overview_tab(self):
        # Service Controller Card (Clean Executive Hero)
        service_card = ctk.CTkFrame(
            self.tab_overview,
            fg_color="#151b27",
            corner_radius=12,
            border_width=1,
            border_color="#212a3d"
        )
        service_card.pack(fill="x", padx=14, pady=12)

        info_box = ctk.CTkFrame(service_card, fg_color="transparent")
        info_box.pack(side="left", padx=18, pady=16, fill="both", expand=True)

        self.service_title = ctk.CTkLabel(
            info_box,
            text="Local Engine Service",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=16, weight="bold"),
            text_color="#f8fafc"
        )
        self.service_title.pack(anchor="w")

        self.service_desc = ctk.CTkLabel(
            info_box,
            text="Checking background listeners and loopback server on port 8080...",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8592a6"
        )
        self.service_desc.pack(anchor="w", pady=(4, 0))

        # Primary Action Control Button (Apple / Linear style)
        self.btn_service_toggle = ctk.CTkButton(
            service_card,
            text="Start Service",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color="#f8fafc",
            hover_color="#e2e8f0",
            text_color="#090c13",
            height=38,
            width=150,
            corner_radius=8,
            command=self._on_toggle_engine
        )
        self.btn_service_toggle.pack(side="right", padx=18, pady=16)

        # Discrete Hardware Audio Meter Card
        meter_card = ctk.CTkFrame(
            self.tab_overview,
            fg_color="#10141e",
            corner_radius=10,
            border_width=1,
            border_color="#1b2232"
        )
        meter_card.pack(fill="x", padx=14, pady=6)

        meter_inner = ctk.CTkFrame(meter_card, fg_color="transparent")
        meter_inner.pack(fill="x", padx=16, pady=12)

        m_head = ctk.CTkFrame(meter_inner, fg_color="transparent")
        m_head.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            m_head,
            text="Input Signal",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0"
        ).pack(side="left")

        self.lbl_mic_status = ctk.CTkLabel(
            m_head,
            text="Listening • Default Input Array",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8592a6"
        )
        self.lbl_mic_status.pack(side="right")

        # 24-Segment Discrete Studio VU Level
        self.meter_frame = ctk.CTkFrame(meter_inner, fg_color="transparent")
        self.meter_frame.pack(fill="x")

        self.meter_segments = []
        for _ in range(24):
            seg = ctk.CTkFrame(self.meter_frame, height=8, corner_radius=2, fg_color="#1b2232")
            seg.pack(side="left", fill="both", expand=True, padx=1)
            self.meter_segments.append(seg)

        # Telemetry Specs Row (Muted, Typographic Precision)
        specs_row = ctk.CTkFrame(self.tab_overview, fg_color="transparent")
        specs_row.pack(fill="x", padx=14, pady=8)

        self._build_spec_box(specs_row, "Model Architecture", "small.en-q5_1 (GGML)")
        self._build_spec_box(specs_row, "Execution Threads", "8 CPU Threads")
        self._build_spec_box(specs_row, "Loopback Latency", "< 0.8s Sub-Second")
        self._build_spec_box(specs_row, "Storage Overhead", "RAM Only (Zero Disk I/O)")

        # Transcription Preview Canvas
        preview_card = ctk.CTkFrame(
            self.tab_overview,
            fg_color="#10141e",
            corner_radius=10,
            border_width=1,
            border_color="#1b2232"
        )
        preview_card.pack(fill="both", expand=True, padx=14, pady=(6, 10))

        p_head = ctk.CTkFrame(preview_card, fg_color="transparent")
        p_head.pack(fill="x", padx=16, pady=(10, 6))

        ctk.CTkLabel(
            p_head,
            text="Transcription Canvas",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0"
        ).pack(side="left")

        btn_clear = ctk.CTkButton(
            p_head,
            text="Clear",
            width=54,
            height=22,
            fg_color="#1b2232",
            hover_color="#252f44",
            text_color="#cbd5e1",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=4,
            command=self._clear_scratchpad
        )
        btn_clear.pack(side="right")

        self.preview_text = ctk.CTkTextbox(
            preview_card,
            fg_color="#0b0e15",
            text_color="#f1f5f9",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            corner_radius=6,
            border_width=1,
            border_color="#1b2232"
        )
        self.preview_text.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.preview_text.insert(
            "1.0",
            "Hold [Ctrl + Alt] or toggle [Ctrl + Alt + Space] to dictate into any window.\nText transcribed will also reflect here in real-time."
        )

    def _build_spec_box(self, parent, title: str, val: str):
        box = ctk.CTkFrame(parent, fg_color="#10141e", corner_radius=8, border_width=1, border_color="#1b2232")
        box.pack(side="left", fill="both", expand=True, padx=3)

        ctk.CTkLabel(box, text=title, font=ctk.CTkFont(family="Segoe UI", size=10), text_color="#8592a6").pack(anchor="w", padx=10, pady=(6, 1))
        ctk.CTkLabel(box, text=val, font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"), text_color="#e2e8f0").pack(anchor="w", padx=10, pady=(0, 6))

    # -------------------------------------------------------------------------
    # Tab 2: Lexicon & Rules
    # -------------------------------------------------------------------------
    def _build_lexicon_tab(self):
        # Lexicon Header Card
        top_card = ctk.CTkFrame(
            self.tab_lexicon,
            fg_color="#151b27",
            corner_radius=10,
            border_width=1,
            border_color="#212a3d"
        )
        top_card.pack(fill="x", padx=14, pady=10)

        left_info = ctk.CTkFrame(top_card, fg_color="transparent")
        left_info.pack(side="left", padx=16, pady=12)

        self.lexicon_count_lbl = ctk.CTkLabel(
            left_info,
            text="Acoustic Replacements & Glossary",
            font=ctk.CTkFont(family="Segoe UI Variable Display", size=15, weight="bold"),
            text_color="#f8fafc"
        )
        self.lexicon_count_lbl.pack(anchor="w")

        ctk.CTkLabel(
            left_info,
            text="Custom pronunciations and domain vocabulary resolve automatically without model fine-tuning.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8592a6"
        ).pack(anchor="w", pady=(2, 0))

        btn_qf = ctk.CTkButton(
            top_card,
            text="QuickFix Dialog (Alt + Z)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#1e2638",
            hover_color="#2b364e",
            text_color="#e2e8f0",
            height=32,
            corner_radius=6,
            command=self._launch_quickfix
        )
        btn_qf.pack(side="right", padx=16, pady=12)

        # Add Rule Card
        form_card = ctk.CTkFrame(
            self.tab_lexicon,
            fg_color="#10141e",
            corner_radius=10,
            border_width=1,
            border_color="#1b2232"
        )
        form_card.pack(fill="x", padx=14, pady=4)

        ctk.CTkLabel(
            form_card,
            text="Add Lexicon Replacement",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(anchor="w", padx=14, pady=(10, 6))

        inputs_box = ctk.CTkFrame(form_card, fg_color="transparent")
        inputs_box.pack(fill="x", padx=14, pady=(0, 12))

        self.entry_spoken = ctk.CTkEntry(
            inputs_box,
            placeholder_text="Spoken phrase (e.g. 'read the setup')",
            height=34,
            fg_color="#0b0e15",
            border_color="#242e42",
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.entry_spoken.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.entry_corrected = ctk.CTkEntry(
            inputs_box,
            placeholder_text="Normalized text (e.g. 'Rate the setup')",
            height=34,
            fg_color="#0b0e15",
            border_color="#242e42",
            corner_radius=6,
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.entry_corrected.pack(side="left", fill="both", expand=True, padx=(0, 10))

        btn_add = ctk.CTkButton(
            inputs_box,
            text="Add Rule",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#f8fafc",
            hover_color="#e2e8f0",
            text_color="#090c13",
            width=90,
            height=34,
            corner_radius=6,
            command=self._on_add_rule
        )
        btn_add.pack(side="right")

        # Rules Data Table
        table_head = ctk.CTkFrame(self.tab_lexicon, fg_color="transparent")
        table_head.pack(fill="x", padx=14, pady=(10, 4))

        ctk.CTkLabel(
            table_head,
            text="Configured Lexicon Rules",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(side="left")

        self.entry_search = ctk.CTkEntry(
            table_head,
            placeholder_text="Filter entries...",
            width=180,
            height=26,
            fg_color="#10141e",
            border_color="#242e42",
            corner_radius=4,
            font=ctk.CTkFont(family="Segoe UI", size=11)
        )
        self.entry_search.pack(side="right")
        self.entry_search.bind("<KeyRelease>", lambda e: self._filter_rules())

        # Scrollable Rules View
        self.rules_scroll = ctk.CTkScrollableFrame(
            self.tab_lexicon,
            fg_color="#0b0e15",
            corner_radius=8,
            border_width=1,
            border_color="#1b2232"
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
                text_color="#64748b"
            ).pack(pady=30)
            return

        for spoken, corrected in self.filtered_rules:
            row = ctk.CTkFrame(self.rules_scroll, fg_color="#10141e", corner_radius=6, height=34)
            row.pack(fill="x", pady=2, padx=4)

            # Spoken Tag
            ctk.CTkLabel(
                row,
                text=spoken,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                fg_color="#161c28",
                corner_radius=4,
                padx=8,
                pady=2,
                text_color="#e2e8f0"
            ).pack(side="left", padx=8, pady=4)

            ctk.CTkLabel(
                row,
                text="→",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color="#64748b"
            ).pack(side="left", padx=4)

            # Corrected Tag
            ctk.CTkLabel(
                row,
                text=corrected,
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                fg_color="#12231c",
                corner_radius=4,
                padx=8,
                pady=2,
                text_color="#34d399"
            ).pack(side="left", padx=4, pady=4)

            # Delete Button
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
                command=lambda s=spoken: self._on_delete_rule(s)
            ) .pack(side="right", padx=8, pady=4)

    # -------------------------------------------------------------------------
    # Tab 3: Shortcuts & Cheatsheet
    # -------------------------------------------------------------------------
    def _build_shortcuts_tab(self):
        container = ctk.CTkScrollableFrame(self.tab_shortcuts, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=14, pady=10)

        # Hardware Shortcuts Card
        hw_card = ctk.CTkFrame(container, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232")
        hw_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            hw_card,
            text="System Keybindings",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0"
        ).pack(anchor="w", padx=14, pady=(12, 8))

        shortcuts = [
            ("Hold [Ctrl + Alt]", "Push-to-Talk: Records audio while depressed; transcribes on release."),
            ("Press [Ctrl + Alt + Space]", "Hands-Free Mode: Continuous dictation toggle. Press again or Esc to end."),
            ("Press [Alt + Z]", "QuickFix: In-situ acoustic correction dialog for recent speech."),
            ("Press [Ctrl + Shift + Q]", "Emergency Terminate: Immediately terminates background whisper-server and hooks.")
        ]
        for key, desc in shortcuts:
            r = ctk.CTkFrame(hw_card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(
                r,
                text=key,
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                text_color="#38bdf8",
                width=210,
                anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(
                r,
                text=desc,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#8592a6"
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(hw_card, text="", height=4).pack()

        # Spoken Commands Card
        cmd_card = ctk.CTkFrame(container, fg_color="#10141e", corner_radius=10, border_width=1, border_color="#1b2232")
        cmd_card.pack(fill="x", pady=6)

        ctk.CTkLabel(
            cmd_card,
            text="Voice Formatting Commands",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e2e8f0"
        ).pack(anchor="w", padx=14, pady=(12, 8))

        voice_cmds = [
            ("new line", "Types a spoken line break (\\n)"),
            ("new paragraph", "Types two spoken line breaks (\\n\\n)"),
            ("snake case <words>", "Formats phrase to snake_case (e.g. calculate_total_tax)"),
            ("camel case <words>", "Formats phrase to camelCase (e.g. getUserProfile)"),
            ("kebab case <words>", "Formats phrase to kebab-case (e.g. header-nav-bar)"),
            ("all caps <words>", "Formats phrase to UPPERCASE (e.g. IMPORTANT NOTICE)"),
            ("double <number>", "Repeats spoken digits (e.g. 'double 4' -> 44)"),
            ("triple <number>", "Repeats spoken digits (e.g. 'triple 7' -> 777)")
        ]
        for phrase, effect in voice_cmds:
            r = ctk.CTkFrame(cmd_card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(
                r,
                text=f'"{phrase}"',
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                text_color="#34d399",
                width=210,
                anchor="w"
            ).pack(side="left")
            ctk.CTkLabel(
                r,
                text=effect,
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color="#8592a6"
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(cmd_card, text="", height=4).pack()

    # -------------------------------------------------------------------------
    # Footer
    # -------------------------------------------------------------------------
    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(2, 14))

        ctk.CTkLabel(
            footer,
            text="WhisperFlow v2.0 • Intel Core i5-13420H • 16GB RAM • Air-Gapped",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#64748b"
        ).pack(side="left")

        ctk.CTkButton(
            footer,
            text="Hide to Tray",
            width=90,
            height=24,
            fg_color="#151b27",
            hover_color="#1e2638",
            text_color="#8592a6",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            corner_radius=4,
            command=self.iconify
        ).pack(side="right")

    # -------------------------------------------------------------------------
    # System Lifecycle & Audio Polling
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
        self.after(0, self._render_meter)

    def _render_meter(self):
        try:
            total_segs = len(self.meter_segments)
            active_count = int(self.meter_level * total_segs)
            for i, seg in enumerate(self.meter_segments):
                if i < active_count:
                    # Discrete studio meter colors: soft emerald to calm cyan highlight
                    color = "#34d399" if i < int(total_segs * 0.75) else "#38bdf8"
                    seg.configure(fg_color=color)
                else:
                    seg.configure(fg_color="#1b2232")

            if active_count > 0:
                self.lbl_mic_status.configure(text="Signal Active • Realtek Microphone")
            else:
                self.lbl_mic_status.configure(text="Listening • Default Input Array")
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
                pid_info = f"PID {pids[0]}" if pids else "Port 8080 Active"
                self.status_pill.configure(
                    text="● ACTIVE",
                    text_color="#34d399",
                    fg_color="#0d2119"
                )
                self.service_title.configure(text="Local Engine Active")
                self.service_desc.configure(
                    text=f"Server and listeners running ({pid_info}). Hold [Ctrl + Alt] or toggle [Ctrl + Alt + Space]."
                )
                self.btn_service_toggle.configure(
                    text="Stop Service",
                    fg_color="#1f161c",
                    hover_color="#2e1d28",
                    text_color="#f87171"
                )
            else:
                self.status_pill.configure(
                    text="○ INACTIVE",
                    text_color="#8592a6",
                    fg_color="#161c28"
                )
                self.service_title.configure(text="Local Engine Inactive")
                self.service_desc.configure(
                    text="Press Start to launch the in-memory whisper server and background keyboard listeners."
                )
                self.btn_service_toggle.configure(
                    text="Start Service",
                    fg_color="#f8fafc",
                    hover_color="#e2e8f0",
                    text_color="#090c13"
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
            script_path = os.path.join(BASE_DIR, "whisper_flow.py")
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = "pythonw.exe"

            subprocess.Popen(
                [pythonw, script_path],
                cwd=BASE_DIR,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )

            for _ in range(16):
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

    def _stop_engine_threaded(self):
        self.status_pill.configure(text="● STOPPING", text_color="#fbbf24", fg_color="#241a0d")
        self.btn_service_toggle.configure(text="Stopping...", state="disabled")

        def task():
            for p in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if "whisper_flow.py" in cmd:
                        p.terminate()
                except Exception:
                    pass

            for p in psutil.process_iter(['pid', 'name']):
                try:
                    if "whisper-server" in p.info['name'].lower():
                        p.terminate()
                except Exception:
                    pass

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
            if os.path.exists(PID_FILE):
                try:
                    os.remove(PID_FILE)
                except Exception:
                    pass

            self.after(0, lambda: self.btn_service_toggle.configure(state="normal"))
            self.after(0, self._check_engine_status)

        threading.Thread(target=task, daemon=True).start()

    # -------------------------------------------------------------------------
    # Lexicon & Data Methods
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
        self.lexicon_count_lbl.configure(
            text=f"Acoustic Lexicon ({total_rules} Rules • {total_vocab} Custom Terms)"
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

        record = {
            "timestamp": time.strftime("%Y%m%d_%H%M%S"),
            "original": spoken,
            "corrected": corrected,
            "replacements": [[spoken.lower(), corrected]],
            "window_title": "WhisperFlow Lexicon Manual Add"
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
        self.preview_text.delete("1.0", "end")

    def destroy(self):
        if self.audio_meter:
            self.audio_meter.stop()
        super().destroy()


def main():
    app = WhisperFlowStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
