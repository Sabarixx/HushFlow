"""
app_paths.py - Unified Branding, Windows AppData Storage, Hardware Discovery & Config Migration for HushFlow.
Supports both source-tree execution and frozen PyInstaller standalone builds.
"""

import os
import sys
import json
import shutil
import platform
from typing import Dict, List, Optional, Tuple

import psutil

APP_NAME = "HushFlow"
APP_VERSION = "2.1.0"
APP_PUBLISHER = "HushFlow"
APP_TAGLINE = "Private Offline Voice Dictation"
REPO_URL = "https://github.com/Sabarixx/HushFlow"

STUDIO_IPC_PORT = 48765

IS_FROZEN = getattr(sys, "frozen", False)
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.dirname(sys.executable) if IS_FROZEN else SOURCE_DIR
BUNDLE_DIR = getattr(sys, "_MEIPASS", INSTALL_DIR) if IS_FROZEN else SOURCE_DIR

LOCAL_APPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
USER_DATA_DIR = os.path.join(LOCAL_APPDATA, APP_NAME)
CONFIG_PATH = os.path.join(USER_DATA_DIR, "config.json")
MODELS_DIR = os.path.join(USER_DATA_DIR, "models")
VAULT_DIR = os.path.join(USER_DATA_DIR, "vault")
VAULT_AUDIO_DIR = os.path.join(VAULT_DIR, "audio")
CORRECTIONS_FILE = os.path.join(VAULT_DIR, "corrections.jsonl")
TRAINING_MANIFEST = os.path.join(VAULT_DIR, "training_manifest.csv")
LOGS_DIR = os.path.join(USER_DATA_DIR, "logs")
MAIN_LOG_FILE = os.path.join(LOGS_DIR, "hushflow.log")
SERVER_LOG_FILE = os.path.join(LOGS_DIR, "whisper_server.log")
PID_FILE = os.path.join(USER_DATA_DIR, "hushflow.pid")

MODEL_REGISTRY = {
    "small.en-q5_1": {
        "filename": "ggml-small.en-q5_1.bin",
        "label": "Small English (Balanced)",
        "size_mb": 190,
        "ram_mb": "~190 MB",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en-q5_1.bin",
    },
    "large-v3-turbo": {
        "filename": "ggml-large-v3-turbo-q5_0.bin",
        "label": "Large v3 Turbo (High Accuracy)",
        "size_mb": 574,
        "ram_mb": "~574 MB",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
    },
    "base.en": {
        "filename": "ggml-base.en.bin",
        "label": "Base English (Ultra-Light)",
        "size_mb": 148,
        "ram_mb": "~148 MB",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
    },
}

MODEL_ALIASES = {
    "small": "small.en-q5_1",
    "small.en": "small.en-q5_1",
    "small.en-q5_1": "small.en-q5_1",
    "large": "large-v3-turbo",
    "large-v3": "large-v3-turbo",
    "large-v3-turbo": "large-v3-turbo",
    "base": "base.en",
    "base.en": "base.en",
}

DEFAULT_CONFIG = {
    "cleanup_level": "medium",
    "model": "small.en-q5_1",
    "hud_visible": True,
    "hotkey": "ctrl+alt",
    "toggle_hotkey": "ctrl+alt+space",
    "dock_y": 360,
    "mic_device": None,
    "save_training_audio": False,
    "onboarding_completed": False,
    "custom_vocabulary": [
        "HushFlow",
        "PowerShell",
        "FastAPI",
        "Python",
    ],
    "word_replacements": {},
}


def get_resource_path(*parts: str) -> str:
    """Resolve a bundled read-only asset/binary path in source or frozen exe builds."""
    for base in (BUNDLE_DIR, INSTALL_DIR, SOURCE_DIR):
        candidate = os.path.join(base, *parts)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(INSTALL_DIR, *parts)


def find_binary(filename: str) -> str:
    """Locate bundled whisper.cpp static executables."""
    for base in (BUNDLE_DIR, INSTALL_DIR, SOURCE_DIR):
        for sub in (("bin", filename), (filename,), ("build", "bin", "Release", filename)):
            candidate = os.path.join(base, *sub)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(INSTALL_DIR, "bin", filename)


def canonical_model_key(model_name: Optional[str]) -> str:
    if not model_name:
        return "small.en-q5_1"
    return MODEL_ALIASES.get(model_name.strip().lower(), "small.en-q5_1")


def get_model_search_dirs() -> List[str]:
    dirs = [
        MODELS_DIR,
        os.path.join(INSTALL_DIR, "models"),
        INSTALL_DIR,
        os.path.join(SOURCE_DIR, "models"),
        SOURCE_DIR,
    ]
    home = os.path.expanduser("~")
    for legacy_sub in ("WhisperFlow", "whisper.cpp"):
        legacy_dir = os.path.join(home, legacy_sub)
        if os.path.isdir(legacy_dir):
            dirs.append(legacy_dir)
            dirs.append(os.path.join(legacy_dir, "models"))
    # Preserve order, deduplicate
    seen = set()
    unique = []
    for d in dirs:
        norm = os.path.normcase(os.path.abspath(d))
        if norm not in seen:
            seen.add(norm)
            unique.append(d)
    return unique


def resolve_model_path(model_name: Optional[str] = None, fallback_any: bool = True) -> str:
    """Locate the GGML model binary on disk, or return the target destination inside MODELS_DIR."""
    key = canonical_model_key(model_name)
    target_filename = MODEL_REGISTRY[key]["filename"]
    search_dirs = get_model_search_dirs()

    for d in search_dirs:
        candidate = os.path.join(d, target_filename)
        if os.path.exists(candidate):
            return candidate

    if fallback_any:
        for d in search_dirs:
            for meta in MODEL_REGISTRY.values():
                candidate = os.path.join(d, meta["filename"])
                if os.path.exists(candidate):
                    return candidate

    return os.path.join(MODELS_DIR, target_filename)


def model_exists(model_name: Optional[str] = None) -> bool:
    return os.path.exists(resolve_model_path(model_name, fallback_any=False))


def _ensure_dirs():
    for d in (USER_DATA_DIR, MODELS_DIR, VAULT_DIR, VAULT_AUDIO_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)


def _migrate_legacy_data():
    """One-time seamless migration from WhisperFlow / whisper.cpp working folders into %LocalAppData%\\HushFlow."""
    home = os.path.expanduser("~")
    legacy_roots = [INSTALL_DIR, SOURCE_DIR, os.path.join(home, "WhisperFlow"), os.path.join(home, "whisper.cpp")]

    # 1. Migrate config if missing
    if not os.path.exists(CONFIG_PATH):
        migrated = dict(DEFAULT_CONFIG)
        for root in legacy_roots:
            legacy_cfg_path = os.path.join(root, "whisper_flow_config.json")
            if os.path.exists(legacy_cfg_path):
                try:
                    with open(legacy_cfg_path, "r", encoding="utf-8") as f:
                        old = json.load(f)
                    for k in ("cleanup_level", "hud_visible", "hotkey", "toggle_hotkey", "dock_y", "word_replacements"):
                        if k in old:
                            migrated[k] = old[k]
                    if "model" in old:
                        migrated["model"] = canonical_model_key(old["model"])
                    if isinstance(old.get("custom_vocabulary"), list):
                        vocab = [w for w in old["custom_vocabulary"] if w.strip().lower() not in ("wispr flow", "whisperflow")]
                        if "HushFlow" not in vocab:
                            vocab.insert(0, "HushFlow")
                        migrated["custom_vocabulary"] = vocab
                    break
                except Exception:
                    pass
        save_config(migrated)

    # 2. Migrate vault corrections & audio if corrections.jsonl doesn't exist yet
    if not os.path.exists(CORRECTIONS_FILE):
        for root in legacy_roots:
            legacy_corr = os.path.join(root, "vault", "corrections.jsonl")
            if os.path.exists(legacy_corr):
                try:
                    shutil.copy2(legacy_corr, CORRECTIONS_FILE)
                    legacy_audio_dir = os.path.join(root, "vault", "audio")
                    if os.path.isdir(legacy_audio_dir):
                        for fname in os.listdir(legacy_audio_dir):
                            src = os.path.join(legacy_audio_dir, fname)
                            dst = os.path.join(VAULT_AUDIO_DIR, fname)
                            if os.path.isfile(src) and not os.path.exists(dst):
                                shutil.copy2(src, dst)
                    break
                except Exception:
                    pass


def ensure_initialized():
    _ensure_dirs()
    _migrate_legacy_data()


def load_config() -> Dict:
    ensure_initialized()
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception:
            pass
    cfg["model"] = canonical_model_key(cfg.get("model"))
    return cfg


def save_config(cfg: Dict) -> bool:
    _ensure_dirs()
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def get_vault_disk_usage_bytes() -> int:
    """Calculate total bytes stored in local vault/audio and logs."""
    total = 0
    for directory in (VAULT_AUDIO_DIR,):
        if os.path.isdir(directory):
            for entry in os.scandir(directory):
                if entry.is_file():
                    total += entry.stat().st_size
    if os.path.exists(CORRECTIONS_FILE):
        total += os.path.getsize(CORRECTIONS_FILE)
    return total


def purge_vault_audio_and_logs() -> Tuple[int, bool]:
    """Delete archived training WAVs and correction logs on user request."""
    removed_files = 0
    if os.path.isdir(VAULT_AUDIO_DIR):
        for entry in os.scandir(VAULT_AUDIO_DIR):
            if entry.is_file():
                try:
                    os.remove(entry.path)
                    removed_files += 1
                except Exception:
                    pass
    for fpath in (CORRECTIONS_FILE, TRAINING_MANIFEST):
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                removed_files += 1
            except Exception:
                pass
    return removed_files, True


def get_hardware_summary() -> Dict[str, str]:
    """Inspect real host hardware specs dynamically (no hardcoded strings)."""
    os_name = f"Windows {platform.release()}"
    try:
        win_ver = sys.getwindowsversion()
        if win_ver.build >= 22000:
            os_name = "Windows 11"
    except Exception:
        pass

    ram_gb = max(1, round(psutil.virtual_memory().total / (1024 ** 3)))
    cores_logical = psutil.cpu_count(logical=True) or 4
    cores_phys = psutil.cpu_count(logical=False) or cores_logical
    opt_threads = str(min(8, max(2, cores_logical)))

    return {
        "os": f"{os_name} ({platform.machine()})",
        "ram": f"{ram_gb} GB RAM",
        "threads": f"{opt_threads} Threads ({cores_phys} Cores)",
        "opt_threads_num": opt_threads,
    }


def get_input_devices() -> List[Tuple[Optional[int], str]]:
    """Return available audio input devices as [(device_index_or_None, display_name), ...]."""
    devices: List[Tuple[Optional[int], str]] = [(None, "System Default Microphone")]
    try:
        import sounddevice as sd
        default_in = sd.default.device[0]
        seen_names = set()
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                name = str(dev.get("name", f"Device {idx}")).strip()
                host_idx = dev.get("hostapi", 0)
                try:
                    host_name = sd.query_hostapis(host_idx).get("name", "")
                except Exception:
                    host_name = ""
                # Prefer primary MME / WASAPI devices cleanly without overwhelming duplicate hostapi clones
                if "DirectSound" in host_name or "WDM-KS" in host_name:
                    continue
                if name in seen_names and idx != default_in:
                    continue
                seen_names.add(name)
                tag = " (Default)" if idx == default_in else ""
                devices.append((idx, f"{name}{tag}"))
    except Exception:
        pass
    return devices
