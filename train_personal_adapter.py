"""
train_personal_adapter.py - Continual Learning Manifest Compiler for HushFlow
Processes verified user corrections from %LocalAppData%\\HushFlow\\vault\\corrections.jsonl
into a standardized manifest when training audio archiving is enabled.
"""

import os
import json
import csv
import logging
from collections import Counter

from app_paths import (
    APP_NAME,
    USER_DATA_DIR,
    CORRECTIONS_FILE,
    TRAINING_MANIFEST,
    ensure_initialized,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def load_corrections():
    if not os.path.exists(CORRECTIONS_FILE):
        return []
    records = []
    with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def compile_training_manifest():
    """Generates a standardized speech-to-text manifest for LoRA/PEFT training."""
    ensure_initialized()
    records = load_corrections()
    if not records:
        print("[INFO] No verified corrections in vault yet. Speak and use [Alt + Z] to collect ground truth.")
        return 0

    valid_rows = []
    word_counter = Counter()

    for r in records:
        audio_rel = r.get("audio_file")
        if not audio_rel:
            continue
        audio_full = os.path.join(USER_DATA_DIR, audio_rel)
        if not os.path.exists(audio_full):
            continue

        corrected_text = r.get("corrected", "").strip()
        if corrected_text:
            valid_rows.append({
                "audio_path": audio_full,
                "transcript": corrected_text,
                "original": r.get("original", ""),
            })
            for old_w, new_w in r.get("replacements", []):
                word_counter[f"{old_w} -> {new_w}"] += 1

    if valid_rows:
        with open(TRAINING_MANIFEST, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["audio_path", "transcript", "original"])
            writer.writeheader()
            writer.writerows(valid_rows)

    print(f"\n==========================================")
    print(f"  {APP_NAME} Personal Vocabulary Report")
    print(f"==========================================")
    print(f"Verified Audio Samples : {len(valid_rows)}")
    print("Top Learned Corrections:")
    for pair, count in word_counter.most_common(5):
        print(f"  • {pair} ({count}x)")
    print(f"Training Manifest Saved : {TRAINING_MANIFEST}")
    print("==========================================\n")
    return len(valid_rows)


if __name__ == "__main__":
    count = compile_training_manifest()
    if count >= 10:
        print("[READY] Enough audio samples collected to run local acoustic adapter fine-tuning.")
    else:
        print(f"[STATUS] {count}/10 audio samples collected.")
