"""Voice input: mic capture + speech-to-text.

Shared by all provider branches (claude, kimi, …). Records a short clip from the mic with
`ffmpeg` (or `arecord`) and transcribes it via an OpenAI-compatible
`/audio/transcriptions` endpoint, so you can speak a coding task instead of typing it.

Config (all overridable; sensible host defaults):
  LOOPIE_STT_BASE_URL  (default: $OPENAI_BASE_URL or https://api.openai.com/v1)
  LOOPIE_STT_API_KEY   (default: $OPENAI_API_KEY)
  LOOPIE_STT_MODEL     (default: whisper-1)
  LOOPIE_AUDIO_INPUT   (default: auto — "pulse:default" if PulseAudio else "alsa:default")
  LOOPIE_AUDIO_SECONDS (default: 8)

Everything is best-effort: clear errors when no mic / no key / no recorder, so the rest of
loopie keeps working without voice.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


class VoiceError(RuntimeError):
    pass


@dataclass
class STTConfig:
    base_url: str
    api_key: str | None
    model: str

    @classmethod
    def from_env(cls) -> STTConfig:
        base = (
            os.environ.get("LOOPIE_STT_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        key = os.environ.get("LOOPIE_STT_API_KEY") or os.environ.get("OPENAI_API_KEY")
        model = os.environ.get("LOOPIE_STT_MODEL", "whisper-1")
        return cls(base_url=base, api_key=key, model=model)


def available() -> bool:
    """True when we can both record and transcribe."""
    has_recorder = shutil.which("ffmpeg") is not None or shutil.which("arecord") is not None
    return has_recorder and bool(STTConfig.from_env().api_key)


def record_cmd(out_path: str, seconds: int, audio_input: str | None = None) -> list[str]:
    """Build the mic-capture command (ffmpeg preferred, arecord fallback)."""
    audio_input = audio_input or os.environ.get("LOOPIE_AUDIO_INPUT") or _default_audio_input()
    if shutil.which("ffmpeg"):
        fmt, _, dev = audio_input.partition(":")
        dev = dev or "default"
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", fmt, "-i", dev,
            "-t", str(seconds), "-ar", "16000", "-ac", "1", out_path,
        ]
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), out_path]
    raise VoiceError("no recorder found (install ffmpeg or arecord)")


def _default_audio_input() -> str:
    # PulseAudio is the common case on desktop Linux / WSLg; ALSA otherwise.
    if shutil.which("pactl") or os.environ.get("PULSE_SERVER"):
        return "pulse:default"
    return "alsa:default"


def record(seconds: int | None = None, audio_input: str | None = None) -> Path:
    seconds = seconds or int(os.environ.get("LOOPIE_AUDIO_SECONDS", "8"))
    out = Path(tempfile.gettempdir()) / f"loopie-voice-{uuid.uuid4().hex}.wav"
    cmd = record_cmd(str(out), seconds, audio_input)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 20)
    except (OSError, subprocess.SubprocessError) as e:
        raise VoiceError(f"recording failed: {e}") from e
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise VoiceError(f"recording produced no audio ({proc.stderr.strip()[:200]})")
    return out


def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----loopie{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    data = file_path.read_bytes()
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{file_path.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def transcribe(audio_path: Path, cfg: STTConfig | None = None, timeout: int = 60) -> str:
    cfg = cfg or STTConfig.from_env()
    if not cfg.api_key:
        raise VoiceError("no STT API key (set LOOPIE_STT_API_KEY or OPENAI_API_KEY)")
    body, boundary = _multipart({"model": cfg.model, "response_format": "json"}, "file", audio_path)
    req = urllib.request.Request(
        f"{cfg.base_url}/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise VoiceError(f"STT HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise VoiceError(f"STT request failed: {e}") from e
    return (payload.get("text") or "").strip()


def listen(seconds: int | None = None) -> str:
    """Record one clip and return its transcript (the convenience entry point)."""
    audio = record(seconds)
    try:
        return transcribe(audio)
    finally:
        try:
            audio.unlink()
        except OSError:
            pass
