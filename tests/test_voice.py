from pathlib import Path

import pytest

from loopie import voice


def test_stt_config_from_env(monkeypatch):
    monkeypatch.setenv("LOOPIE_STT_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("LOOPIE_STT_API_KEY", "k")
    monkeypatch.setenv("LOOPIE_STT_MODEL", "whisper-x")
    cfg = voice.STTConfig.from_env()
    assert cfg.base_url == "https://example.com/v1"  # trailing slash stripped
    assert cfg.api_key == "k" and cfg.model == "whisper-x"


def test_stt_config_defaults(monkeypatch):
    for k in ("LOOPIE_STT_BASE_URL", "OPENAI_BASE_URL", "LOOPIE_STT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    cfg = voice.STTConfig.from_env()
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "whisper-1"


def test_record_cmd_ffmpeg(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
    cmd = voice.record_cmd("/tmp/x.wav", 5, audio_input="pulse:default")
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and "pulse" in cmd and "default" in cmd
    assert cmd[cmd.index("-t") + 1] == "5"
    assert cmd[-1] == "/tmp/x.wav"


def test_record_cmd_arecord_fallback(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: "/usr/bin/arecord" if n == "arecord" else None)
    cmd = voice.record_cmd("/tmp/x.wav", 3)
    assert cmd[0] == "arecord" and cmd[-1] == "/tmp/x.wav"
    assert cmd[cmd.index("-d") + 1] == "3"


def test_record_cmd_no_recorder(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda n: None)
    with pytest.raises(voice.VoiceError):
        voice.record_cmd("/tmp/x.wav", 3)


def test_multipart_encodes_fields_and_file(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFFdata")
    body, boundary = voice._multipart({"model": "whisper-1"}, "file", f)
    assert boundary.encode() in body
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'filename="a.wav"' in body and b"RIFFdata" in body


def test_transcribe_requires_key(monkeypatch, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    cfg = voice.STTConfig(base_url="https://x/v1", api_key=None, model="whisper-1")
    with pytest.raises(voice.VoiceError, match="key"):
        voice.transcribe(f, cfg)
    _ = Path
