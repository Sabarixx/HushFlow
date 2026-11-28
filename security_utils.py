"""
security_utils.py - Core Security Primitives for HushFlow Desktop

Provides:
- Windows DPAPI (CryptProtectData / CryptUnprotectData) secret & vault record encryption
- Strict per-user Windows NTFS ACL enforcement (stripping inherited multi-SID access)
- Authenticated loopback UDP IPC via DPAPI-protected 256-bit HMAC-SHA256 + nonce replay guard
- SHA-256 regular-file verification for downloaded models & bundled whisper.cpp executables
- Log secret redaction filter & safe exception summarization
- Privacy-protected Win32 clipboard (blocks Win+V History & Cloud Clipboard) + SendInput typing
- Client-side token-bucket rate limiter & validated Settings schema
"""

import base64
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

try:
    import ntsecuritycon
    import win32api
    import win32security
    _WIN32_ACL_AVAILABLE = True
except ImportError:
    ntsecuritycon = None
    win32api = None
    win32security = None
    _WIN32_ACL_AVAILABLE = False

DPAPI_PREFIX = "dpapi:v1:"
CRYPTPROTECT_UI_FORBIDDEN = 0x01
MAX_IPC_BYTES = 32768
MAX_RULE_COUNT = 500
MAX_RULE_LEN = 120
MAX_AUDIO_WAV_BYTES = 24 * 1024 * 1024
ALLOWED_HOTKEYS = {"ctrl+alt", "win+alt", "right_alt"}
ALLOWED_CLEANUP_LEVELS = {"none", "light", "medium", "high"}

_REDACTION_PATTERNS = (
    (re.compile(r"\bgsk_[A-Za-z0-9_-]{6,}\b"), "gsk_[REDACTED]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"dpapi:v1:[A-Za-z0-9+/=_-]+"), "dpapi:v1:[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._-]{8,}"), r"\1[REDACTED]"),
)

_IPC_KEY_LOCK = threading.Lock()
_IPC_KEY_CACHE: Dict[str, bytes] = {}
_HARDENED_PATHS_CACHE: set = set()


# ---------------------------------------------------------------------------
# Log Redaction & Safe Error Formatting
# ---------------------------------------------------------------------------

def sanitize_log_text(text: Any) -> str:
    """Redact API keys, Bearer tokens, and DPAPI blobs from any log or UI status text."""
    s = "" if text is None else str(text)
    for pattern, replacement in _REDACTION_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def safe_error_summary(exc: Exception, max_len: int = 120) -> str:
    """Format exceptions cleanly with secret redaction and bounded length."""
    detail = sanitize_log_text(str(exc)).replace("\r", " ").replace("\n", " ").strip()
    if len(detail) > max_len:
        detail = detail[: max_len - 3] + "..."
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class SecretRedactingFilter(logging.Filter):
    """Logging filter that redacts secrets before records hit hushflow.log."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            sanitized = sanitize_log_text(rendered)
            if sanitized != rendered:
                record.msg = sanitized
                record.args = ()
        except Exception:
            pass
        return True


def install_log_redaction_filter():
    """Attach SecretRedactingFilter to root logger and all existing handlers."""
    filt = SecretRedactingFilter()
    root = logging.getLogger()
    if not any(isinstance(f, SecretRedactingFilter) for f in root.filters):
        root.addFilter(filt)
    for handler in root.handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            handler.addFilter(filt)


# ---------------------------------------------------------------------------
# Windows DPAPI Protection (Current-User Bound Encryption at Rest)
# ---------------------------------------------------------------------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _make_blob(raw: bytes) -> Tuple[_DATA_BLOB, Optional[ctypes.Array]]:
    if not raw:
        return _DATA_BLOB(0, None), None
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def is_dpapi_protected(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(DPAPI_PREFIX)


def dpapi_protect_bytes(raw: bytes, purpose: str = "secret") -> bytes:
    """Encrypt raw bytes using Windows CryptProtectData bound to current user."""
    if not raw:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    in_blob, _in_buf = _make_blob(raw)
    entropy_bytes = f"HushFlow::{purpose}::v1".encode("utf-8")
    ent_blob, _ent_buf = _make_blob(entropy_bytes)
    out_blob = _DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        f"HushFlow:{purpose}",
        ctypes.byref(ent_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(ctypes.GetLastError(), "CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, wintypes.HLOCAL))


def dpapi_unprotect_bytes(cipher: bytes, purpose: str = "secret") -> bytes:
    """Decrypt bytes previously protected by dpapi_protect_bytes."""
    if not cipher:
        return b""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    in_blob, _in_buf = _make_blob(cipher)
    entropy_bytes = f"HushFlow::{purpose}::v1".encode("utf-8")
    ent_blob, _ent_buf = _make_blob(entropy_bytes)
    out_blob = _DATA_BLOB()
    descr = wintypes.LPWSTR()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(descr),
        ctypes.byref(ent_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(ctypes.GetLastError(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if descr:
            kernel32.LocalFree(ctypes.cast(descr, wintypes.HLOCAL))
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, wintypes.HLOCAL))


def protect_string(plaintext: str, purpose: str = "secret") -> str:
    """Encrypt a UTF-8 string with DPAPI and tag with 'dpapi:v1:'."""
    val = (plaintext or "").strip()
    if not val:
        return ""
    if is_dpapi_protected(val):
        return val
    cipher = dpapi_protect_bytes(val.encode("utf-8"), purpose=purpose)
    return DPAPI_PREFIX + base64.b64encode(cipher).decode("ascii")


def unprotect_string(stored: str, purpose: str = "secret") -> str:
    """
    Decrypt a 'dpapi:v1:...' string; fail closed ('') if ciphertext is corrupted/invalid.
    Legacy plaintext strings without DPAPI_PREFIX pass through for one-time migration.
    """
    val = (stored or "").strip()
    if not val:
        return ""
    if not is_dpapi_protected(val):
        return val
    b64 = val[len(DPAPI_PREFIX):]
    try:
        cipher = base64.b64decode(b64.encode("ascii"), validate=True)
        plain = dpapi_unprotect_bytes(cipher, purpose=purpose)
        return plain.decode("utf-8").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Strict Per-User Windows NTFS ACL Hardening
# ---------------------------------------------------------------------------

def harden_path_acl(path: str, is_dir: Optional[bool] = None, force: bool = False) -> bool:
    """
    Remove inherited multi-SID permissions and restrict path DACL strictly to
    the current Windows user and NT AUTHORITY\\SYSTEM.
    """
    if not path or not os.path.exists(path) or not _WIN32_ACL_AVAILABLE:
        return False
    norm = os.path.normcase(os.path.abspath(path))
    if not force and norm in _HARDENED_PATHS_CACHE:
        return True

    if is_dir is None:
        is_dir = os.path.isdir(path)

    try:
        tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            user_sid = win32security.GetTokenInformation(tok, win32security.TokenUser)[0]
        finally:
            win32api.CloseHandle(tok)

        system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
        dacl = win32security.ACL()
        flags = (
            win32security.CONTAINER_INHERIT_ACE | win32security.OBJECT_INHERIT_ACE
            if is_dir
            else 0
        )
        dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, user_sid)
        dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, system_sid)

        sec_flags = win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        win32security.SetNamedSecurityInfo(
            path,
            win32security.SE_FILE_OBJECT,
            sec_flags,
            None,
            None,
            dacl,
            None,
        )
        _HARDENED_PATHS_CACHE.add(norm)
        return True
    except Exception:
        return False


def atomic_write_text(path: str, content: str, harden_acl: bool = True):
    """Write text atomically via temp file + os.replace and enforce strict ACLs."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
    if harden_acl:
        harden_path_acl(path, is_dir=False, force=True)


# ---------------------------------------------------------------------------
# Authenticated Loopback UDP IPC (HMAC-SHA256 + Timestamp + Nonce Guard)
# ---------------------------------------------------------------------------

def _ipc_key_path(user_data_dir: str) -> str:
    return os.path.join(user_data_dir, "ipc.key")


def get_or_create_ipc_secret(user_data_dir: str) -> bytes:
    """Load or generate a DPAPI-protected 256-bit HMAC key for Engine<->Studio IPC."""
    norm_dir = os.path.normcase(os.path.abspath(user_data_dir))
    with _IPC_KEY_LOCK:
        cached = _IPC_KEY_CACHE.get(norm_dir)
        if cached and len(cached) == 32:
            return cached

        os.makedirs(user_data_dir, exist_ok=True)
        harden_path_acl(user_data_dir, is_dir=True)
        key_file = _ipc_key_path(user_data_dir)
        if os.path.exists(key_file):
            try:
                with open(key_file, "r", encoding="utf-8") as f:
                    stored = f.read().strip()
                raw_hex = unprotect_string(stored, purpose="ipc_hmac")
                raw = bytes.fromhex(raw_hex)
                if len(raw) == 32:
                    _IPC_KEY_CACHE[norm_dir] = raw
                    return raw
            except Exception:
                pass

        new_secret = secrets.token_bytes(32)
        protected = protect_string(new_secret.hex(), purpose="ipc_hmac")
        atomic_write_text(key_file, protected, harden_acl=True)
        _IPC_KEY_CACHE[norm_dir] = new_secret
        return new_secret


def _ipc_canonical_msg(ts: float, nonce: str, payload: Dict[str, Any]) -> bytes:
    canonical_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{ts:.3f}:{nonce}:{canonical_payload}".encode("utf-8")


def sign_ipc_payload(payload: Dict[str, Any], user_data_dir: str) -> bytes:
    """Wrap an IPC dict into a timestamped, nonce-tagged, HMAC-SHA256 envelope."""
    secret = get_or_create_ipc_secret(user_data_dir)
    ts = round(time.time(), 3)
    nonce = secrets.token_hex(16)
    mac = hmac.new(secret, _ipc_canonical_msg(ts, nonce, payload), hashlib.sha256).hexdigest()
    envelope = {
        "v": 1,
        "ts": ts,
        "nonce": nonce,
        "payload": payload,
        "sig": mac,
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def verify_ipc_packet(
    raw_bytes: bytes,
    user_data_dir: str,
    max_age_sec: float = 15.0,
    max_bytes: int = MAX_IPC_BYTES,
    seen_nonces: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Verify UDP datagram authenticity, size limit, freshness, and nonce uniqueness.
    Returns unwrapped payload dict on success or None on forged/expired/replayed input.
    """
    if not raw_bytes or len(raw_bytes) > max_bytes:
        return None
    try:
        envelope = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(envelope, dict) or envelope.get("v") != 1:
            return None
        ts = float(envelope.get("ts"))
        nonce = envelope.get("nonce")
        payload = envelope.get("payload")
        sig = envelope.get("sig")
        if not isinstance(nonce, str) or len(nonce) < 16 or not isinstance(payload, dict) or not isinstance(sig, str):
            return None

        now = time.time()
        if abs(now - ts) > max_age_sec:
            return None

        if seen_nonces is not None:
            expired = [k for k, t in seen_nonces.items() if now - t > max_age_sec]
            for k in expired:
                seen_nonces.pop(k, None)
            if nonce in seen_nonces:
                return None

        secret = get_or_create_ipc_secret(user_data_dir)
        expected = hmac.new(secret, _ipc_canonical_msg(ts, nonce, payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            # Secret might have been rotated by a peer process; reload from disk once and recheck.
            norm_dir = os.path.normcase(os.path.abspath(user_data_dir))
            with _IPC_KEY_LOCK:
                _IPC_KEY_CACHE.pop(norm_dir, None)
            secret = get_or_create_ipc_secret(user_data_dir)
            expected = hmac.new(secret, _ipc_canonical_msg(ts, nonce, payload), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, sig):
                return None

        if seen_nonces is not None:
            if len(seen_nonces) >= 512:
                seen_nonces.clear()
            seen_nonces[nonce] = now
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SHA-256 Binary & Model Integrity Verification
# ---------------------------------------------------------------------------

def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            blk = f.read(chunk_size)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest().lower()


def verify_authenticode_signature(path: str) -> Tuple[bool, str]:
    """Verify PE Authenticode digital signature using Windows WinVerifyTrust."""
    if not path or not os.path.isfile(path) or os.path.islink(path):
        return False, "Invalid binary path."
    try:
        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class _WINTRUST_FILE_INFO(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.c_void_p),
            ]

        class _WINTRUST_DATA(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(_WINTRUST_FILE_INFO)),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p),
            ]

        action = _GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        finfo = _WINTRUST_FILE_INFO(ctypes.sizeof(_WINTRUST_FILE_INFO), os.path.abspath(path), None, None)
        wdata = _WINTRUST_DATA(
            ctypes.sizeof(_WINTRUST_DATA),
            None,
            None,
            2,  # WTD_UI_NONE
            0,  # WTD_REVOKE_NONE
            1,  # WTD_CHOICE_FILE
            ctypes.pointer(finfo),
            1,  # WTD_STATEACTION_VERIFY
            None,
            None,
            0,
            0,
            None,
        )
        wintrust = ctypes.windll.wintrust
        wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(_WINTRUST_DATA)]
        wintrust.WinVerifyTrust.restype = wintypes.LONG
        status = wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(wdata))
        wdata.dwStateAction = 2  # WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(wdata))
        if status == 0:
            return True, "valid_authenticode"
        return False, f"WinVerifyTrust status 0x{status & 0xFFFFFFFF:08x}"
    except Exception as exc:
        return False, safe_error_summary(exc)


def verify_executable_integrity(path: str, expected_sha256: str) -> Tuple[bool, str]:
    """
    Verify bundled native executable: accepts EITHER exact unsigned SHA-256 match
    OR a trusted, untampered Windows Authenticode signature (signed installer builds).
    """
    ok_hash, info_hash = verify_file_integrity(path, expected_sha256)
    if ok_hash:
        return True, info_hash
    ok_sig, info_sig = verify_authenticode_signature(path)
    if ok_sig:
        return True, info_sig
    return False, f"{info_hash}; {info_sig}"


def verify_file_integrity(
    path: str,
    expected_sha256: str,
    expected_size: Optional[int] = None,
) -> Tuple[bool, str]:
    """Verify target is a real regular file (no symlink), matches expected size, and matches SHA-256."""
    if not path or not expected_sha256:
        return False, "Missing path or expected SHA-256 digest."
    if os.path.islink(path):
        return False, "Refusing symlinked executable or model file."
    if not os.path.isfile(path):
        return False, f"File does not exist: {path}"
    try:
        st = os.stat(path)
        if expected_size is not None and st.st_size != expected_size:
            return False, f"Byte size mismatch ({st.st_size} != {expected_size})."
        actual = sha256_file(path)
        if not hmac.compare_digest(actual, expected_sha256.strip().lower()):
            return False, "SHA-256 digest mismatch."
        return True, actual
    except Exception as exc:
        return False, safe_error_summary(exc)


# ---------------------------------------------------------------------------
# Privacy-Protected Win32 Clipboard + Zero-Clipboard SendInput Typing
# ---------------------------------------------------------------------------

def copy_private_clipboard(text: str, retries: int = 6) -> bool:
    """
    Place text onto Windows clipboard alongside privacy exclusion formats:
    - ExcludeClipboardContentFromMonitorProcessing
    - CanIncludeInClipboardHistory = 0 (blocks Win+V logging)
    - CanUploadToCloudClipboard = 0 (blocks Microsoft Cloud Clipboard sync)
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def _set_raw_format(fmt_id: int, raw: bytes) -> bool:
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not hmem:
            return False
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            kernel32.GlobalFree(hmem)
            return False
        try:
            ctypes.memmove(ptr, raw, len(raw))
        finally:
            kernel32.GlobalUnlock(hmem)
        if not user32.SetClipboardData(fmt_id, hmem):
            kernel32.GlobalFree(hmem)
            return False
        return True

    opened = False
    for _ in range(max(1, retries)):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.015)

    if not opened:
        return False

    try:
        user32.EmptyClipboard()
        utf16 = ((text or "") + "\0").encode("utf-16-le")
        ok = _set_raw_format(CF_UNICODETEXT, utf16)
        dword_zero = (0).to_bytes(4, "little")
        for fmt_name in (
            "ExcludeClipboardContentFromMonitorProcessing",
            "CanIncludeInClipboardHistory",
            "CanUploadToCloudClipboard",
        ):
            fmt_id = user32.RegisterClipboardFormatW(fmt_name)
            if fmt_id:
                _set_raw_format(fmt_id, dword_zero)
        return ok
    finally:
        user32.CloseClipboard()


def _send_vk_key(vk: int):
    SendInput, KEYBDINPUT, INPUT, _ = _sendinput_types()
    down = INPUT(type=1)
    down.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
    up = INPUT(type=1)
    up.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0x0002, time=0, dwExtraInfo=0)
    arr = (INPUT * 2)(down, up)
    SendInput(2, arr, ctypes.sizeof(INPUT))


def _sendinput_types():
    ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]

    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    return user32.SendInput, KEYBDINPUT, INPUT, ULONG_PTR


def send_text_unicode(text: str, batch_chars: int = 32) -> bool:
    """
    Inject text directly into active Windows control via Win32 SendInput KEYEVENTF_UNICODE
    without touching the OS clipboard at all.
    """
    if not text:
        return True
    try:
        SendInput, KEYBDINPUT, INPUT, _ = _sendinput_types()
        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004
        VK_RETURN = 0x0D

        normalized = text.replace("\r\n", "\n")
        events = []

        def _flush():
            if not events:
                return
            arr = (INPUT * len(events))(*events)
            SendInput(len(events), arr, ctypes.sizeof(INPUT))
            events.clear()

        for ch in normalized:
            if ch == "\n":
                _flush()
                _send_vk_key(VK_RETURN)
                time.sleep(0.004)
                continue
            code_units = ch.encode("utf-16-le")
            for idx in range(0, len(code_units), 2):
                unit = int.from_bytes(code_units[idx:idx + 2], "little")
                down = INPUT(type=INPUT_KEYBOARD)
                down.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
                up = INPUT(type=INPUT_KEYBOARD)
                up.ki = KEYBDINPUT(wVk=0, wScan=unit, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                events.append(down)
                events.append(up)
            if len(events) >= batch_chars * 2:
                _flush()
                time.sleep(0.002)

        _flush()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Privacy Classification, Input Schema Validation & Rate Limiting
# ---------------------------------------------------------------------------

def classify_window_category(window_title: str) -> str:
    """Map raw window title into a privacy-safe coarse category (never logs full titles)."""
    title = (window_title or "").lower()
    if not title:
        return "desktop"
    if "hushflow" in title:
        return "hushflow"
    if any(k in title for k in ("code", "visual studio", "terminal", "powershell", "cmd", "bash", "sublime", "git", "intellij", "pycharm", ".py", ".ts", ".js", ".cpp", ".rs")):
        return "coding"
    if any(k in title for k in ("slack", "teams", "outlook", "discord", "mail", "whatsapp", "telegram")):
        return "communication"
    if any(k in title for k in ("chrome", "edge", "firefox", "brave", "opera")):
        return "browser"
    return "desktop"


def sanitize_rule_pair(spoken: str, corrected: str) -> Tuple[str, str]:
    """Clean and bound a custom dictionary pair to prevent control chars or oversized regex inputs."""
    def _clean(val: str) -> str:
        s = re.sub(r"[\x00-\x1f\x7f]+", " ", (val or "").strip())
        return re.sub(r"\s+", " ", s).strip()[:MAX_RULE_LEN]

    return _clean(spoken), _clean(corrected)


def validate_setting_update(key: str, value: Any) -> Tuple[bool, Any, str]:
    """Allowlist and validate Studio UI setting updates."""
    if not isinstance(key, str):
        return False, None, "Invalid setting key."

    if key == "hotkey":
        val = str(value or "").strip().lower()
        if val not in ALLOWED_HOTKEYS:
            return False, None, f"Unsupported hotkey: {val}"
        return True, val, ""

    if key == "cleanup_level":
        val = str(value or "").strip().lower()
        if val not in ALLOWED_CLEANUP_LEVELS:
            return False, None, f"Unsupported cleanup level: {val}"
        return True, val, ""

    if key in ("streaming_vad_enabled", "double_tap_lock", "hud_visible", "save_training_audio", "zero_clipboard_typing"):
        return True, bool(value), ""

    if key == "mic_device":
        if value is None:
            return True, None, ""
        try:
            idx = int(value)
            if 0 <= idx < 512:
                return True, idx, ""
        except Exception:
            pass
        return False, None, "Invalid microphone device index."

    if key == "language":
        val = str(value or "en").strip().lower()
        if re.fullmatch(r"(auto|[a-z]{2,8})", val):
            return True, val, ""
        return False, None, "Invalid language code."

    return False, None, f"Setting '{key}' is not writable via update_setting."


class TokenBucketRateLimiter:
    """Thread-safe token-bucket rate limiter to guard external API endpoints against bursts."""

    def __init__(self, rate_per_sec: float, capacity: float):
        self._rate = max(0.05, float(rate_per_sec))
        self._capacity = max(1.0, float(capacity))
        self._tokens = self._capacity
        self._last_ts = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, cost: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self._last_ts)
            self._last_ts = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if self._tokens >= cost:
                self._tokens -= cost
                return True
            return False
