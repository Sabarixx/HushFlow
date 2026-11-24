"""
train_personal_adapter.py - Nightly / Continual Learning Engine for WhisperFlow
Processes verified user corrections from vault/corrections.jsonl into a personalized voice model.
"""

import os
import sys
import json
import csv
import logging
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_DIR = os.path.join(BASE_DIR, "vault")
VAULT_AUDIO_DIR = os.path.join(VAULT_DIR, "audio")
CORRECTIONS_FILE = os.path.join(VAULT_DIR, "corrections.jsonl")
TRAINING_MANIFEST = os.path.join(VAULT_DIR, "training_manifest.csv")

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
        audio_full = os.path.join(BASE_DIR, audio_rel)
        if not os.path.exists(audio_full):
            continue

        corrected_text = r.get("corrected", "").strip()
        if corrected_text:
            valid_rows.append({
                "audio_path": audio_full,
                "transcript": corrected_text,
                "original": r.get("original", "")
            })
            for old_w, new_w in r.get("replacements", []):
                word_counter[f"{old_w} -> {new_w}"] += 1

    # Write CSV manifest
    if valid_rows:
        with open(TRAINING_MANIFEST, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["audio_path", "transcript", "original"])
            writer.writeheader()
            writer.writerows(valid_rows)

    print(f"\n==========================================")
    print(f"  WhisperFlow Continuous Learning Report")
    print(f"==========================================")
    print(f"Verified Audio Samples : {len(valid_rows)}")
    print(f"Top Learned Corrections:")
    for pair, count in word_counter.most_common(5):
        print(f"  • {pair} ({count}x)")
    print(f"Training Manifest Saved : {TRAINING_MANIFEST}")
    print(f"==========================================\n")
    return len(valid_rows)


if __name__ == "__main__":
    count = compile_training_manifest()
    if count >= 10:
        print("[READY] You have enough samples to run acoustic LoRA fine-tuning.")
    else:
        print(f"[STATUS] {count}/10 samples collected. Keep dictating and using [Alt + Z] to correct errors!")
