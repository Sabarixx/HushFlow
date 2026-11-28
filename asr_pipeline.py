"""
asr_pipeline.py - Low-Latency Hybrid ASR & Live VAD Streaming Pipeline for HushFlow

Architecture:
1. Primary Cloud Engine: Groq Whisper (`whisper-large-v3-turbo` / `whisper-large-v3`)
   via persistent keep-alive HTTP client (~150-300 ms per chunk).
2. Live VAD Chunked Streaming: Splits long speech at natural pauses *while the user
   is still speaking* and transcribes completed chunks in a background worker thread,
   leaving only the final tail when the hotkey is released.
3. Seamless Local Fallback: Automatically falls back to in-memory `whisper-server.exe`
   (port 8080) or `whisper-cli.exe` if offline or if no Groq API key is configured.
"""

import io
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import requests
import requests.adapters

from app_paths import (
    BUNDLED_BINARY_SHA256,
    MODEL_REGISTRY,
    canonical_model_key,
    expected_model_integrity,
    get_groq_api_key,
    get_hardware_summary,
    is_cloud_model,
    resolve_model_path,
)
from security_utils import (
    MAX_AUDIO_WAV_BYTES,
    TokenBucketRateLimiter,
    harden_path_acl,
    safe_error_summary,
    verify_executable_integrity,
    verify_file_integrity,
)

try:
    from groq import Groq
    GROQ_SDK_AVAILABLE = True
except ImportError:
    Groq = None
    GROQ_SDK_AVAILABLE = False

SAMPLE_RATE = 16000
CHANNELS = 1
MIN_AUDIO_DURATION = 0.25

HALLUCINATION_BLACKLIST = {
    "[blank_audio]",
    "[silence]",
    "(silence)",
    "(music)",
    "(applause)",
    "[applause]",
    "(bell rings)",
    "[whispering]",
    "(whispering)",
    ".",
    "you",
    "thank you.",
    "thank you",
    "thanks for watching.",
    "bye.",
}


def encode_wav_bytes(audio_int16: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Serialize mono int16 PCM samples into a 44-byte RIFF WAV buffer in RAM (<0.1 ms)."""
    pcm = np.asarray(audio_int16, dtype=np.int16).reshape(-1)
    pcm_bytes = pcm.tobytes()
    data_size = len(pcm_bytes)
    byte_rate = sample_rate * CHANNELS * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,          # PCM subchunk size
        1,           # AudioFormat = PCM
        CHANNELS,
        sample_rate,
        byte_rate,
        CHANNELS * 2,
        16,          # BitsPerSample
        b"data",
        data_size,
    )
    return header + pcm_bytes


def calculate_audio_rms(audio_data: np.ndarray) -> float:
    if len(audio_data) == 0:
        return 0.0
    samples = audio_data.astype(np.float64).reshape(-1)
    return float(np.sqrt(np.mean(samples * samples)))


def is_audio_silent(audio_data: np.ndarray, duration_sec: float) -> bool:
    if duration_sec < MIN_AUDIO_DURATION or len(audio_data) == 0:
        return True
    peak = float(np.max(np.abs(audio_data)))
    rms = calculate_audio_rms(audio_data)
    if duration_sec < 1.0 and peak < 450.0 and rms < 110.0:
        return True
    if peak < 260.0 and rms < 70.0:
        return True
    return False


def is_hallucination_or_empty(text: str) -> bool:
    if not text:
        return True
    cleaned = text.strip()
    if not cleaned or len(cleaned) <= 1:
        return True
    if cleaned.lower() in HALLUCINATION_BLACKLIST:
        return True
    if "Numbers, OTPs, codes" in cleaned:
        return True
    return False


def merge_chunk_transcripts(chunks: List[str]) -> str:
    """Join sequential chunk transcripts cleanly without broken spacing."""
    parts = [c.strip() for c in chunks if c and not is_hallucination_or_empty(c)]
    if not parts:
        return ""
    merged = parts[0]
    for nxt in parts[1:]:
        if merged.endswith(("-", "\n")):
            merged += nxt
        else:
            merged += " " + nxt
    return merged.strip()


class GroqClientPool:
    """Thread-safe persistent Groq SDK client reusing HTTP/TLS keep-alive sockets."""

    def __init__(self):
        self._lock = threading.Lock()
        self._api_key: str = ""
        self._client = None
        self._key_test_limiter = TokenBucketRateLimiter(rate_per_sec=0.5, capacity=4.0)
        self._asr_limiter = TokenBucketRateLimiter(rate_per_sec=2.5, capacity=10.0)

    def get_client(self, api_key: str):
        key = (api_key or "").strip()
        if not key or not GROQ_SDK_AVAILABLE:
            return None
        with self._lock:
            if self._client is None or self._api_key != key:
                self._api_key = key
                self._client = Groq(api_key=key, timeout=8.0, max_retries=1)
            return self._client

    def warmup_async(self, api_key: str):
        """Warm TLS connection in background so the first dictation chunk has zero handshake lag."""
        key = (api_key or "").strip()
        if not key or not GROQ_SDK_AVAILABLE:
            return

        def _warm():
            try:
                client = self.get_client(key)
                if client is not None:
                    client.models.list()
            except Exception:
                pass

        threading.Thread(target=_warm, daemon=True).start()

    def validate_key(self, api_key: str) -> Tuple[bool, str]:
        """Validate a Groq API key by listing available models."""
        key = (api_key or "").strip()
        if not key:
            return False, "API key is empty."
        if not GROQ_SDK_AVAILABLE:
            return False, "groq Python SDK is not installed."
        if not self._key_test_limiter.allow():
            return False, "Rate limited: please wait a moment before testing again."
        try:
            client = Groq(api_key=key, timeout=5.0, max_retries=0)
            client.models.list()
            return True, "Connected to Groq Cloud."
        except Exception as exc:
            summary = safe_error_summary(exc, max_len=100)
            if "401" in summary or "invalid_api_key" in summary.lower() or "authentication" in summary.lower():
                return False, "Invalid Groq API key (401 Unauthorized)."
            return False, f"Connection failed: {summary}"


GROQ_POOL = GroqClientPool()


class ASREngine:
    """Unified speech-to-text dispatcher supporting Groq Cloud Turbo + local whisper.cpp fallback."""

    def __init__(
        self,
        http_session: Optional[requests.Session] = None,
        server_url: str = "http://127.0.0.1:8080",
        whisper_cli_path: str = "",
    ):
        if http_session is None:
            http_session = requests.Session()
            http_session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=10))
        self.http_session = http_session
        safe_url = (server_url or "http://127.0.0.1:8080").strip().rstrip("/")
        if not safe_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            safe_url = "http://127.0.0.1:8080"
        self.server_url = safe_url
        self.whisper_cli_path = whisper_cli_path

    def transcribe_wav_bytes(
        self,
        wav_bytes: bytes,
        config: Dict,
        prompt: str = "",
        temp_wav_path: Optional[str] = None,
        duration_sec: float = 0.0,
    ) -> Tuple[str, str]:
        """
        Transcribe WAV bytes using configured cloud or local model.
        Returns (transcript_text, backend_used).
        """
        if not wav_bytes or len(wav_bytes) > MAX_AUDIO_WAV_BYTES:
            if wav_bytes and len(wav_bytes) > MAX_AUDIO_WAV_BYTES:
                logging.warning("Audio payload exceeded MAX_AUDIO_WAV_BYTES cap; dropping oversized segment.")
            return "", "none"

        prompt = (prompt or "")[:1200]
        model_key = canonical_model_key(config.get("model"))
        meta = MODEL_REGISTRY[model_key]
        api_key = get_groq_api_key(config)
        lang = (config.get("language") or "en").strip().lower()

        # 1. Primary Cloud Path (Groq Whisper Large-v3-Turbo / Large-v3)
        if is_cloud_model(model_key) and api_key and GROQ_SDK_AVAILABLE:
            if GROQ_POOL._asr_limiter.allow():
                api_model = meta.get("api_model", "whisper-large-v3-turbo")
                t0 = time.perf_counter()
                try:
                    client = GROQ_POOL.get_client(api_key)
                    kwargs = {
                        "file": ("audio.wav", wav_bytes, "audio/wav"),
                        "model": api_model,
                        "response_format": "json",
                        "temperature": 0.0,
                    }
                    if prompt:
                        kwargs["prompt"] = prompt[-800:]
                    if lang and lang != "auto":
                        kwargs["language"] = lang
                    resp = client.audio.transcriptions.create(**kwargs)
                    text = (getattr(resp, "text", None) or "").strip()
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    logging.info(f"Groq ASR [{api_model}] {duration_sec:.1f}s audio -> {elapsed_ms:.0f} ms (len={len(text)})")
                    return text, f"groq:{api_model}"
                except Exception as exc:
                    logging.warning(f"Groq ASR failed ({safe_error_summary(exc)}); falling back to local whisper.")
            else:
                logging.warning("Groq ASR client rate limit reached; routing chunk to local whisper fallback.")

        # 2. Local In-Memory whisper-server Path (direct POST, zero pre-flight GET /health)
        dyn_timeout = max(20.0, duration_sec * 3.0) if duration_sec > 0 else 45.0
        try:
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {"temperature": "0.0", "response_format": "json", "prompt": prompt}
            resp = self.http_session.post(
                f"{self.server_url}/inference",
                files=files,
                data=data,
                timeout=dyn_timeout,
            )
            if resp.status_code == 200:
                text = resp.json().get("text", "").strip()
                return text, "local:whisper-server"
        except Exception as exc:
            logging.warning(f"whisper-server inference failed ({safe_error_summary(exc)}); checking CLI fallback.")

        # 3. Last-resort whisper-cli.exe Fallback (SHA-256 verified binary & model)
        if self.whisper_cli_path and os.path.exists(self.whisper_cli_path) and temp_wav_path:
            cli_sha = BUNDLED_BINARY_SHA256.get(os.path.basename(self.whisper_cli_path).lower())
            if cli_sha:
                ok_cli, reason_cli = verify_executable_integrity(self.whisper_cli_path, cli_sha)
                if not ok_cli:
                    logging.error(f"Refusing whisper-cli execution ({reason_cli}).")
                    return "", "none"

            fallback_model = config.get("local_fallback_model", "small.en-q5_1")
            model_path = resolve_model_path(fallback_model if is_cloud_model(model_key) else model_key)
            if os.path.exists(model_path):
                exp_sha, exp_size = expected_model_integrity(model_path)
                if exp_sha:
                    ok_mdl, reason_mdl = verify_file_integrity(model_path, exp_sha, exp_size)
                    if not ok_mdl:
                        logging.error(f"Refusing local model verification failure ({reason_mdl}).")
                        return "", "none"

                threads = get_hardware_summary().get("opt_threads_num", "4")
                try:
                    with open(temp_wav_path, "wb") as f:
                        f.write(wav_bytes)
                    harden_path_acl(temp_wav_path, is_dir=False)
                    cmd = [
                        self.whisper_cli_path,
                        "-m", model_path,
                        "-f", temp_wav_path,
                        "-nt", "-np", "-fa",
                        "-bo", "1", "-bs", "1",
                        "-t", threads,
                        "-nth", "0.65",
                        "-sns",
                        "--prompt", prompt,
                    ]
                    res = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=dyn_timeout,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    return res.stdout.strip(), "local:whisper-cli"
                except Exception as exc:
                    logging.error(f"whisper-cli execution failed ({safe_error_summary(exc)}).")
                finally:
                    if os.path.exists(temp_wav_path):
                        try:
                            os.remove(temp_wav_path)
                        except Exception:
                            pass

        return "", "none"


class StreamingVADSession:
    """
    Live VAD-chunked background transcription session.
    While the user speaks, pauses (>= 320 ms silence after >= 1.6 s speech) or max
    chunk limits (10.0 s) trigger immediate background ASR on the completed phrase so
    that releasing the hotkey only waits for the final short tail.
    """

    def __init__(
        self,
        engine: ASREngine,
        config: Dict,
        base_prompt: str,
        temp_wav_path: Optional[str] = None,
        sample_rate: int = SAMPLE_RATE,
        min_chunk_sec: float = 1.6,
        max_chunk_sec: float = 10.0,
        silence_split_sec: float = 0.32,
        silence_rms_threshold: float = 175.0,
        enabled: bool = True,
    ):
        self.engine = engine
        self.config = dict(config)
        self.base_prompt = base_prompt
        self.temp_wav_path = temp_wav_path
        self.sample_rate = sample_rate
        self.min_chunk_samples = int(min_chunk_sec * sample_rate)
        self.max_chunk_samples = int(max_chunk_sec * sample_rate)
        self.silence_split_samples = int(silence_split_sec * sample_rate)
        self.silence_rms_threshold = silence_rms_threshold
        self.enabled = enabled

        self._lock = threading.Lock()
        self._pending_frames: List[np.ndarray] = []
        self._pending_samples: int = 0
        self._all_frames: List[np.ndarray] = []
        self._silence_samples: int = 0
        self._has_speech: bool = False
        self._aborted: bool = False

        self._chunk_queue: "queue.Queue[Optional[Tuple[np.ndarray, float]]]" = queue.Queue()
        self._completed_texts: List[str] = []
        self._last_backend: str = "none"
        self._worker_thread: Optional[threading.Thread] = None

        if self.enabled:
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def feed(self, indata: np.ndarray):
        """Called from the sounddevice audio callback thread; must stay lock-light and fast."""
        if self._aborted:
            return
        chunk = indata.copy()
        n_samples = len(chunk)
        if n_samples == 0:
            return

        rms = calculate_audio_rms(chunk) if self.enabled else 0.0
        to_dispatch: Optional[Tuple[np.ndarray, float]] = None

        with self._lock:
            if self._aborted:
                return
            self._all_frames.append(chunk)
            if not self.enabled:
                return

            self._pending_frames.append(chunk)
            self._pending_samples += n_samples

            if rms >= self.silence_rms_threshold:
                self._has_speech = True
                self._silence_samples = 0
            elif self._has_speech:
                self._silence_samples += n_samples

            should_split_pause = (
                self._has_speech
                and self._pending_samples >= self.min_chunk_samples
                and self._silence_samples >= self.silence_split_samples
            )
            should_split_max = self._has_speech and self._pending_samples >= self.max_chunk_samples

            if should_split_pause or should_split_max:
                combined = np.concatenate(self._pending_frames, axis=0)
                dur = len(combined) / self.sample_rate
                self._pending_frames = []
                self._pending_samples = 0
                self._silence_samples = 0
                self._has_speech = False
                to_dispatch = (combined, dur)

        if to_dispatch is not None:
            self._chunk_queue.put(to_dispatch)

    def _build_chunk_prompt(self) -> str:
        tail = " ".join(self._completed_texts[-2:]).strip()
        if not tail:
            return self.base_prompt
        if not self.base_prompt:
            return tail[-200:]
        return f"{self.base_prompt} {tail[-200:]}"

    def _transcribe_segment(self, audio_seg: np.ndarray, duration_sec: float):
        if self._aborted or is_audio_silent(audio_seg, duration_sec):
            return
        wav_bytes = encode_wav_bytes(audio_seg, self.sample_rate)
        prompt = self._build_chunk_prompt()
        text, backend = self.engine.transcribe_wav_bytes(
            wav_bytes=wav_bytes,
            config=self.config,
            prompt=prompt,
            temp_wav_path=self.temp_wav_path,
            duration_sec=duration_sec,
        )
        if backend != "none":
            self._last_backend = backend
        if text and not is_hallucination_or_empty(text):
            self._completed_texts.append(text)

    def _worker_loop(self):
        while True:
            item = self._chunk_queue.get()
            try:
                if item is None:
                    return
                if not self._aborted:
                    audio_seg, dur = item
                    self._transcribe_segment(audio_seg, dur)
            except Exception as exc:
                logging.warning(f"Streaming VAD chunk error: {safe_error_summary(exc)}")
            finally:
                self._chunk_queue.task_done()

    def cancel(self):
        """Abort recording and discard any pending audio without transcribing."""
        self._aborted = True
        with self._lock:
            self._pending_frames = []
            self._pending_samples = 0
            self._all_frames = []
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
                self._chunk_queue.task_done()
            except queue.Empty:
                break
        if self._worker_thread is not None:
            self._chunk_queue.put(None)

    def finish(self) -> Tuple[str, np.ndarray, float, str]:
        """
        Flush remaining audio tail, wait for any in-flight chunk, and return:
        (merged_raw_text, full_audio_int16, total_duration_sec, backend_used)
        """
        tail_audio: Optional[np.ndarray] = None
        tail_dur = 0.0
        full_audio = np.empty((0, CHANNELS), dtype=np.int16)

        with self._lock:
            if self._all_frames:
                full_audio = np.concatenate(self._all_frames, axis=0)
            if self.enabled and self._pending_frames:
                tail_audio = np.concatenate(self._pending_frames, axis=0)
                tail_dur = len(tail_audio) / self.sample_rate
                self._pending_frames = []
                self._pending_samples = 0

        total_dur = len(full_audio) / self.sample_rate

        if not self.enabled:
            if not is_audio_silent(full_audio, total_dur):
                self._transcribe_segment(full_audio, total_dur)
            return merge_chunk_transcripts(self._completed_texts), full_audio, total_dur, self._last_backend

        if tail_audio is not None and not is_audio_silent(tail_audio, tail_dur):
            self._chunk_queue.put((tail_audio, tail_dur))

        self._chunk_queue.put(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=45.0)

        return merge_chunk_transcripts(self._completed_texts), full_audio, total_dur, self._last_backend
