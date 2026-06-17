from loopie.context_budget import TranscriptEntry, clip, reduce_transcript


def test_clip_short_passthrough():
    assert clip("hi", 100) == "hi"


def test_clip_long_truncates_with_marker():
    text = "x" * 5000
    out = clip(text, 1000)
    assert len(out) < 1100 and "clipped" in out


def test_reduce_transcript_keeps_recent_detail():
    entries = [TranscriptEntry("tool", f"s{i}", detail="d" * 3000) for i in range(20)]
    out = reduce_transcript(entries, keep_recent=5, clip_old=50, clip_recent=2000)
    assert out[0].detail == ""  # old detail dropped
    assert out[-1].detail != ""  # recent detail kept


def test_reduce_transcript_dedups_repeated_reads():
    entries = [
        TranscriptEntry("tool", "read a.py", dedup_key="a.py"),
        TranscriptEntry("tool", "read a.py again", dedup_key="a.py"),
    ]
    out = reduce_transcript(entries, keep_recent=10)
    assert out[0].summary.startswith("[superseded]")
    assert not out[1].summary.startswith("[superseded]")
