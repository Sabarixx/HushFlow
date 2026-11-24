"""
QuickFix - Sleek, Non-Blocking Floating Correction Dialog for WhisperFlow
Triggers on [Alt + Z] to correct misheard words and train your personal voice model.
"""

import os
import sys
import time
import json
import threading
import difflib
import shutil
import ctypes
import tkinter as tk
from tkinter import ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_DIR = os.path.join(BASE_DIR, "vault")
VAULT_AUDIO_DIR = os.path.join(VAULT_DIR, "audio")
CORRECTIONS_FILE = os.path.join(VAULT_DIR, "corrections.jsonl")

os.makedirs(VAULT_AUDIO_DIR, exist_ok=True)


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
        if tag == 'replace':
            old_str = ' '.join(orig_words[i1:i2]).strip('.,!?;:"\'()')
            new_str = ' '.join(corr_words[j1:j2]).strip('.,!?;:"\'()')
            if old_str and new_str and old_str.lower() != new_str.lower():
                replacements.append((old_str.lower(), new_str))
    return replacements


def record_correction_and_learn(last_event: dict, corrected_text: str, config_path: str):
    """
    1. Saves ground-truth audio + transcript pair in vault.
    2. Updates custom_vocabulary and word_replacements in whisper_flow_config.json.
    3. Replaces text in target application window via Ctrl+Z followed by Ctrl+V.
    """
    if not last_event or not corrected_text.strip():
        return []

    original_text = last_event.get("text", "")
    target_hwnd = last_event.get("hwnd")
    wav_path = last_event.get("wav_path")
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")

    # 1. Identify replaced words
    replacements = extract_word_replacements(original_text, corrected_text)
    learned_words = [rep[1] for rep in replacements]

    # If no word-by-word difference found but strings differ, treat whole phrase as replacement
    if not replacements and original_text.strip().lower() != corrected_text.strip().lower():
        replacements.append((original_text.strip().lower(), corrected_text.strip()))
        learned_words.append(corrected_text.strip())

    # 2. Archive verified audio to vault
    saved_wav_name = f"{timestamp_str}_{os.getpid()}.wav"
    saved_wav_path = os.path.join(VAULT_AUDIO_DIR, saved_wav_name)
    if wav_path and os.path.exists(wav_path):
        try:
            shutil.copy2(wav_path, saved_wav_path)
        except Exception:
            saved_wav_path = None

    # 3. Log to corrections database
    record = {
        "timestamp": timestamp_str,
        "original": original_text,
        "corrected": corrected_text,
        "replacements": replacements,
        "audio_file": os.path.relpath(saved_wav_path, BASE_DIR) if saved_wav_path else None,
        "window_title": last_event.get("window_title", "")
    }

    try:
        with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[ERROR] Failed to save correction log: {e}")

    # 4. Instant In-Memory Learning: update whisper_flow_config.json
    try:
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

        vocab = cfg.get("custom_vocabulary", [])
        rep_map = cfg.get("word_replacements", {})

        # Add new correct words to vocabulary prompt
        for word in learned_words:
            clean_word = word.strip(".,!?;:")
            if clean_word and clean_word not in vocab:
                vocab.append(clean_word)

        # Add replacement mapping (e.g. "jurisdiction" -> "dictation")
        for old_w, new_w in replacements:
            rep_map[old_w] = new_w

        cfg["custom_vocabulary"] = vocab
        cfg["word_replacements"] = rep_map

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    except Exception as e:
        print(f"[ERROR] Failed to update config: {e}")

    # 5. Replace text in the user's active window
    if target_hwnd and ctypes.windll.user32.IsWindow(target_hwnd):
        try:
            import keyboard
            import pyperclip

            # Bring target window back to foreground with AttachThreadInput
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

            # Undo the previously pasted text
            keyboard.send("ctrl+z")
            time.sleep(0.06)

            # Paste the corrected text
            pyperclip.copy(corrected_text)
            time.sleep(0.04)
            keyboard.send("ctrl+v")
        except Exception as e:
            print(f"[ERROR] Could not auto-replace in window: {e}")

    return learned_words


class QuickFixWindow:
    """Floating modern dark-themed correction window."""
    def __init__(self, initial_text: str, on_submit):
        self.initial_text = initial_text
        self.on_submit = on_submit
        self.result = None
        self.root = None

    def show(self):
        root = tk.Tk()
        self.root = root
        root.title("WhisperFlow Voice Correction")
        root.attributes("-topmost", True)
        root.overrideredirect(True)

        # Dark theme colors
        bg_main = "#121214"
        bg_card = "#1c1c20"
        bg_input = "#26262c"
        border_color = "#38bdf8"
        fg_text = "#f4f4f5"
        fg_muted = "#a1a1aa"
        accent_color = "#06b6d4"

        root.configure(bg=border_color)

        # Dimensions & position (centered horizontally, ~25% down the screen)
        win_w = 640
        win_h = 135
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        pos_x = (screen_w - win_w) // 2
        pos_y = int(screen_h * 0.28)

        root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

        # Main inner container with 1px border
        container = tk.Frame(root, bg=bg_main, padx=16, pady=12)
        container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Header row
        header_frame = tk.Frame(container, bg=bg_main)
        header_frame.pack(fill=tk.X, pady=(0, 6))

        title_lbl = tk.Label(
            header_frame,
            text="✨ WhisperFlow Voice Correction",
            font=("Segoe UI", 10, "bold"),
            fg=accent_color,
            bg=bg_main
        )
        title_lbl.pack(side=tk.LEFT)

        hint_lbl = tk.Label(
            header_frame,
            text="[Enter] Save & Train   [Esc] Cancel",
            font=("Segoe UI", 8),
            fg=fg_muted,
            bg=bg_main
        )
        hint_lbl.pack(side=tk.RIGHT)

        # Input Entry Box
        self.entry = tk.Entry(
            container,
            font=("Segoe UI", 12),
            bg=bg_input,
            fg=fg_text,
            insertbackground=accent_color,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#3f3f46",
            highlightcolor=accent_color
        )
        self.entry.pack(fill=tk.X, ipady=5, pady=(0, 8))
        self.entry.insert(0, self.initial_text)
        self.entry.select_range(0, tk.END)

        # Subtitle instructions
        sub_lbl = tk.Label(
            container,
            text="Fix any misheard words above. Your correction will be taught to your AI model immediately.",
            font=("Segoe UI", 8),
            fg=fg_muted,
            bg=bg_main,
            anchor="w"
        )
        sub_lbl.pack(fill=tk.X)

        # Key bindings
        root.bind("<Return>", self._on_enter)
        root.bind("<KP_Enter>", self._on_enter)
        root.bind("<Escape>", lambda e: self.close())

        # Focus
        root.after(50, lambda: self.entry.focus_force())
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
