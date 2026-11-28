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

from security_utils import (
    atomic_write_text,
    classify_window_category,
    harden_path_acl,
    is_dpapi_protected,
    protect_string,
    unprotect_string,
)

APP_NAME = "HushFlow"
APP_VERSION = "2.2.0"
APP_PUBLISHER = "HushFlow"
APP_TAGLINE = "Ultra-Fast Voice Dictation (Cloud Turbo + Local Fallback)"
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
    "groq-whisper-large-v3-turbo": {
        "backend": "groq",
        "api_model": "whisper-large-v3-turbo",
        "filename": "",
        "label": "Groq Whisper Large v3 Turbo (Cloud • Ultra-Fast)",
        "size_mb": 0,
        "ram_mb": "Cloud ~150 ms",
        "url": "",
    },
    "groq-whisper-large-v3": {
        "backend": "groq",
        "api_model": "whisper-large-v3",
        "filename": "",
        "label": "Groq Whisper Large v3 (Cloud • Max Accuracy)",
        "size_mb": 0,
        "ram_mb": "Cloud ~250 ms",
        "url": "",
    },
    "small.en-q5_1": {
        "backend": "local",
        "filename": "ggml-small.en-q5_1.bin",
        "label": "Small English (Local Offline)",
        "size_mb": 190,
        "size_bytes": 190098681,
        "sha256": "bfdff4894dcb76bbf647d56263ea2a96645423f1669176f4844a1bf8e478ad30",
        "ram_mb": "~190 MB",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en-q5_1.bin",
    },
    "large-v3-turbo": {
        "backend": "local",
        "filename": "ggml-large-v3-turbo-q5_0.bin",
        "label": "Large v3 Turbo (Local Offline)",
        "size_mb": 574,
        "size_bytes": 574041195,
        "sha256": "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
        "ram_mb": "~574 MB",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
    },
    "base.en": {
        "backend": "local",
        "filename": "ggml-base.en.bin",
        "label": "Base English (Local Ultra-Light)",
        "size_mb": 148,
        "size_bytes": 147964211,
        "sha256": "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
        "ram_mb": "~148 MB",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
    },
}

BUNDLED_BINARY_SHA256 = {
    "whisper-server.exe": "3dcfe9aa94e53e98d9a8d8584b78dc8e6c921269fbc8a4f17b17b915a752775c",
    "whisper-cli.exe": "e0edcaf6b84f4faf89d6a170e5ea88940d876fe7d2069255926677b4882ad11c",
}

MODEL_ALIASES = {
    "groq": "groq-whisper-large-v3-turbo",
    "groq-turbo": "groq-whisper-large-v3-turbo",
    "whisper-large-v3-turbo": "groq-whisper-large-v3-turbo",
    "groq-whisper-large-v3-turbo": "groq-whisper-large-v3-turbo",
    "groq-whisper-large-v3": "groq-whisper-large-v3",
    "whisper-large-v3": "groq-whisper-large-v3",
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
    "model": "groq-whisper-large-v3-turbo",
    "local_fallback_model": "small.en-q5_1",
    "groq_api_key": "",
    "language": "en",
    "streaming_vad_enabled": True,
    "hud_visible": True,
    "hotkey": "ctrl+alt",
    "toggle_hotkey": "ctrl+alt+space",
    "double_tap_lock": True,
    "hands_free_toggle_enabled": True,
    "dock_y": 360,
    "mic_device": None,
    "save_training_audio": False,
    "zero_clipboard_typing": False,
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
        return "groq-whisper-large-v3-turbo"
    return MODEL_ALIASES.get(model_name.strip().lower(), "groq-whisper-large-v3-turbo")


def is_cloud_model(model_name: Optional[str]) -> bool:
    key = canonical_model_key(model_name)
    return MODEL_REGISTRY.get(key, {}).get("backend") == "groq"


def get_groq_api_key(cfg: Optional[Dict] = None) -> str:
    if cfg and isinstance(cfg, dict):
        raw = (cfg.get("groq_api_key") or "").strip()
        if raw:
            k = unprotect_string(raw, purpose="groq_api_key") if is_dpapi_protected(raw) else raw
            if k:
                return k
    return (os.environ.get("GROQ_API_KEY") or "").strip()


def expected_model_integrity(model_path: str) -> Tuple[Optional[str], Optional[int]]:
    """Look up expected SHA-256 and exact byte size if path matches a known MODEL_REGISTRY file."""
    fname = os.path.basename(model_path or "").lower()
    for meta in MODEL_REGISTRY.values():
        if meta.get("filename", "").lower() == fname and meta.get("sha256"):
            return meta["sha256"], meta.get("size_bytes")
    return None, None


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
    """Locate the local GGML model binary on disk, or return the target destination inside MODELS_DIR."""
    key = canonical_model_key(model_name)
    if is_cloud_model(key):
        key = canonical_model_key("small.en-q5_1")
    target_filename = MODEL_REGISTRY[key]["filename"]
    search_dirs = get_model_search_dirs()

    for d in search_dirs:
        candidate = os.path.join(d, target_filename)
        if os.path.exists(candidate):
            return candidate

    if fallback_any:
        for d in search_dirs:
            for meta in MODEL_REGISTRY.values():
                if not meta.get("filename"):
                    continue
                candidate = os.path.join(d, meta["filename"])
                if os.path.exists(candidate):
                    return candidate

    return os.path.join(MODELS_DIR, target_filename)


def model_exists(model_name: Optional[str] = None) -> bool:
    key = canonical_model_key(model_name)
    if is_cloud_model(key):
        return bool(get_groq_api_key(load_config()))
    return os.path.exists(resolve_model_path(key, fallback_any=False))


def fallback_model_exists(cfg: Optional[Dict] = None) -> bool:
    cfg = cfg or load_config()
    fb = cfg.get("local_fallback_model", "small.en-q5_1")
    if is_cloud_model(fb):
        fb = "small.en-q5_1"
    return os.path.exists(resolve_model_path(fb, fallback_any=True))


def _ensure_dirs():
    for d in (USER_DATA_DIR, MODELS_DIR, VAULT_DIR, VAULT_AUDIO_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
        harden_path_acl(d, is_dir=True)
    for fpath in (CONFIG_PATH, CORRECTIONS_FILE, TRAINING_MANIFEST):
        if os.path.exists(fpath):
            harden_path_acl(fpath, is_dir=False)


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
    needs_dpapi_migration = False
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception:
            pass
    raw_key = (cfg.get("groq_api_key") or "").strip()
    if raw_key:
        if is_dpapi_protected(raw_key):
            cfg["groq_api_key"] = unprotect_string(raw_key, purpose="groq_api_key")
        else:
            cfg["groq_api_key"] = raw_key
            needs_dpapi_migration = True
    cfg["model"] = canonical_model_key(cfg.get("model"))
    if needs_dpapi_migration:
        save_config(cfg)
    return cfg


def save_config(cfg: Dict) -> bool:
    _ensure_dirs()
    try:
        disk_cfg = dict(cfg)
        raw_key = (disk_cfg.get("groq_api_key") or "").strip()
        if raw_key:
            disk_cfg["groq_api_key"] = (
                raw_key if is_dpapi_protected(raw_key) else protect_string(raw_key, purpose="groq_api_key")
            )
        else:
            disk_cfg["groq_api_key"] = ""
        serialized = json.dumps(disk_cfg, indent=2, ensure_ascii=False)
        atomic_write_text(CONFIG_PATH, serialized, harden_acl=True)
        return True
    except Exception:
        return False


def save_correction_record(record: Dict) -> bool:
    """
    Sanitize window context (coarse category only, never raw window titles) and
    persist a DPAPI-encrypted line inside CORRECTIONS_FILE with strict NTFS ACLs.
    """
    _ensure_dirs()
    try:
        clean_rec = dict(record or {})
        raw_title = clean_rec.pop("window_title", "")
        if "window_category" not in clean_rec:
            clean_rec["window_category"] = classify_window_category(str(raw_title or ""))
        line_payload = protect_string(json.dumps(clean_rec, ensure_ascii=False), purpose="vault_record")
        with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
            f.write(line_payload + "\n")
        harden_path_acl(CORRECTIONS_FILE, is_dir=False)
        return True
    except Exception:
        return False


def load_correction_records() -> List[Dict]:
    """Load DPAPI-protected (or legacy plain JSON) records from CORRECTIONS_FILE."""
    if not os.path.exists(CORRECTIONS_FILE):
        return []
    records: List[Dict] = []
    try:
        with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    decoded = unprotect_string(line, purpose="vault_record") if is_dpapi_protected(line) else line
                    if decoded:
                        obj = json.loads(decoded)
                        if isinstance(obj, dict):
                            records.append(obj)
                except Exception:
                    continue
    except Exception:
        pass
    return records


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
