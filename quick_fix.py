"""
quick_fix.py - Cohesive HushFlow Correction Dialog
Triggers on [Alt + Z] or from HushFlow Studio to correct misheard words
and teach custom acoustic replacements without visual clutter.
"""

import os
import sys
import time
import json
import threading
import difflib
import shutil
import ctypes
from typing import Optional

import customtkinter as ctk

from app_paths import (
    APP_NAME,
    CONFIG_PATH,
    CORRECTIONS_FILE,
    VAULT_AUDIO_DIR,
    VAULT_DIR,
    load_config,
    save_config,
    ensure_initialized,
)
from brand_assets import get_app_icon_path

ensure_initialized()


def _set_dark_titlebar(window):
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def extract_word_replacements(original_text: str, corrected_text: str):
    """
    Identifies specific word replacements between original and corrected text.
    e.g. 'What is the jurisdiction style' -> 'What is the dictation style'
    returns: [('jurisdiction', 'dictation')]
    """
    orig_words = original_text.split()
    corr_words = corrected_text.split()
    matcher = difflib.SequenceMatcher(None, orig_words, corr_words)
    replacements = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            old_str = " ".join(orig_words[i1:i2]).strip('.,!?;:"\'()')
            new_str = " ".join(corr_words[j1:j2]).strip('.,!?;:"\'()')
            if old_str and new_str and old_str.lower() != new_str.lower():
                replacements.append((old_str.lower(), new_str))
    return replacements


def record_correction_and_learn(last_event: dict, corrected_text: str, config_path: Optional[str] = None):
    """
    1. Saves correction pair in %LocalAppData%\\HushFlow\\vault (with audio only if opt-in enabled).
    2. Updates custom_vocabulary and word_replacements in config.
    3. Replaces text in target application window via Ctrl+Z followed by Ctrl+V.
    """
    if not last_event or not corrected_text.strip():
        return []

    original_text = last_event.get("text", "")
    target_hwnd = last_event.get("hwnd")
    wav_path = last_event.get("wav_path")
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")

    replacements = extract_word_replacements(original_text, corrected_text)
    learned_words = [rep[1] for rep in replacements]

    if not replacements and original_text.strip().lower() != corrected_text.strip().lower():
        replacements.append((original_text.strip().lower(), corrected_text.strip()))
        learned_words.append(corrected_text.strip())

    # Load config (allow overriding path for isolated unit tests)
    if config_path and os.path.normcase(os.path.abspath(config_path)) != os.path.normcase(os.path.abspath(CONFIG_PATH)):
        cfg = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
    else:
        cfg = load_config()

    # Archive audio strictly opt-in so zero-disk-stash users retain total privacy
    saved_wav_rel = None
    if cfg.get("save_training_audio", False) and wav_path and os.path.exists(wav_path):
        os.makedirs(VAULT_AUDIO_DIR, exist_ok=True)
        saved_wav_name = f"{timestamp_str}_{os.getpid()}.wav"
        saved_wav_path = os.path.join(VAULT_AUDIO_DIR, saved_wav_name)
        try:
            shutil.copy2(wav_path, saved_wav_path)
            saved_wav_rel = os.path.join("vault", "audio", saved_wav_name)
        except Exception:
            saved_wav_rel = None

    os.makedirs(VAULT_DIR, exist_ok=True)
    record = {
        "timestamp": timestamp_str,
        "original": original_text,
        "corrected": corrected_text,
        "replacements": replacements,
        "audio_file": saved_wav_rel,
        "window_title": last_event.get("window_title", ""),
    }

    try:
        with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[ERROR] Failed to save correction log: {e}")

    vocab = cfg.get("custom_vocabulary", [])
    rep_map = cfg.get("word_replacements", {})

    for word in learned_words:
        clean_word = word.strip(".,!?;:")
        if clean_word and clean_word not in vocab:
            vocab.append(clean_word)

    for old_w, new_w in replacements:
        rep_map[old_w] = new_w

    cfg["custom_vocabulary"] = vocab
    cfg["word_replacements"] = rep_map

    if config_path and os.path.normcase(os.path.abspath(config_path)) != os.path.normcase(os.path.abspath(CONFIG_PATH)):
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] Failed to update custom config: {e}")
    else:
        save_config(cfg)

    # Replace pasted text in user's active window
    if target_hwnd and ctypes.windll.user32.IsWindow(target_hwnd):
        try:
            import keyboard
            import pyperclip

            old_clip = None
            try:
                old_clip = pyperclip.paste()
            except Exception:
                pass

            cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            target_thread = ctypes.windll.user32.GetWindowThreadProcessId(target_hwnd, None)
            if cur_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, True)
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                ctypes.windll.user32.BringWindowToTop(target_hwnd)
                ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, False)
            else:
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.08)

            keyboard.send("ctrl+z")
            time.sleep(0.06)

            pyperclip.copy(corrected_text)
            time.sleep(0.04)
            keyboard.send("ctrl+v")

            def _restore():
                try:
                    time.sleep(0.45)
                    if old_clip is not None:
                        pyperclip.copy(old_clip)
                except Exception:
                    pass

            threading.Thread(target=_restore, daemon=True).start()
        except Exception as e:
            print(f"[ERROR] Could not auto-replace in window: {e}")

    return learned_words


class QuickFixWindow:
    """Clean, executive CustomTkinter correction dialog matching HushFlow Studio."""

    def __init__(self, initial_text: str, on_submit):
        self.initial_text = initial_text
        self.on_submit = on_submit
        self.root = None

    def show(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        root = ctk.CTk()
        self.root = root
        root.title(f"{APP_NAME} — Quick Correction")
        root.attributes("-topmost", True)
        root.configure(fg_color="#090c13")
        root.resizable(False, False)

        ico = get_app_icon_path()
        if os.path.exists(ico):
            try:
                root.iconbitmap(ico)
            except Exception:
                pass

        win_w, win_h = 620, 176
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        pos_x = max(20, (screen_w - win_w) // 2)
        pos_y = max(40, int(screen_h * 0.25))
        root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        _set_dark_titlebar(root)

        card = ctk.CTkFrame(
            root,
            fg_color="#10141e",
            corner_radius=12,
            border_width=1,
            border_color="#1e2638",
        )
        card.pack(fill="both", expand=True, padx=14, pady=14)

        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(12, 6))

        ctk.CTkLabel(
            top_row,
            text="Correct Phrase & Update Lexicon",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#f8fafc",
        ).pack(side="left")

        ctk.CTkLabel(
            top_row,
            text="Enter to save  •  Esc to cancel",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#64748b",
        ).pack(side="right")

        self.entry = ctk.CTkEntry(
            card,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            height=38,
            fg_color="#090c13",
            border_color="#28324a",
            text_color="#f8fafc",
            corner_radius=8,
            placeholder_text="Enter the corrected sentence...",
        )
        self.entry.pack(fill="x", padx=14, pady=(2, 10))
        if self.initial_text:
            self.entry.insert(0, self.initial_text)
            self.entry.select_range(0, "end")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 12))

        ctk.CTkLabel(
            btn_row,
            text="Saves replacement rule locally inside your custom lexicon.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#64748b",
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="Save Rule",
            width=96,
            height=30,
            corner_radius=6,
            fg_color="#f8fafc",
            hover_color="#e2e8f0",
            text_color="#090c13",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._on_enter,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            width=74,
            height=30,
            corner_radius=6,
            fg_color="#161c28",
            hover_color="#1f2738",
            text_color="#94a3b8",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self.close,
        ).pack(side="right")

        root.bind("<Return>", self._on_enter)
        root.bind("<KP_Enter>", self._on_enter)
        root.bind("<Escape>", lambda e: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)

        root.after(60, lambda: self.entry.focus_force())
        root.mainloop()

    def _on_enter(self, event=None):
        val = self.entry.get().strip()
        self.close()
        if val and self.on_submit:
            self.on_submit(val)

    def close(self):
        try:
            if self.root:
                self.root.destroy()
                self.root = None
        except Exception:
            pass


def spawn_quick_fix(initial_text: str, on_submit):
    """Spawns the correction window in a dedicated GUI thread."""
    def _run():
        dlg = QuickFixWindow(initial_text, on_submit)
        dlg.show()

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def main():
    """Standalone QuickFix entry point (e.g. launched from HushFlow Studio)."""
    initial = ""
    if os.path.exists(CORRECTIONS_FILE):
        try:
            with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            if lines:
                initial = json.loads(lines[-1]).get("corrected", "")
        except Exception:
            pass

    def _on_save(corrected_val: str):
        if initial:
            record_correction_and_learn({"text": initial, "window_title": "HushFlow QuickFix"}, corrected_val)

    dlg = QuickFixWindow(initial, _on_save)
    dlg.show()


if __name__ == "__main__":
    main()
