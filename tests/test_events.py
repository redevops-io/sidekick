import json

from sidekick import events as ev


def test_parse_init():
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "model": "m"})
    out = ev.parse_line(line)
    assert len(out) == 1 and out[0].kind == ev.INIT
    assert out[0].session_id == "s1" and out[0].model == "m"


def test_parse_assistant_text_and_tooluse():
    msg = {
        "type": "assistant",
        "session_id": "s1",
        "message": {
            "model": "m",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "a.py"}},
            ],
        },
    }
    out = ev.parse_line(json.dumps(msg))
    kinds = [e.kind for e in out]
    assert kinds == [ev.TEXT, ev.TOOL_USE]
    assert out[1].tool_name == "Write" and out[1].tool_input["file_path"] == "a.py"


def test_parse_tool_result():
    msg = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    }
    out = ev.parse_line(json.dumps(msg))
    assert out[0].kind == ev.TOOL_RESULT and out[0].tool_result == "ok"


def test_parse_result_usage():
    msg = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1000,
        "ttft_ms": 200,
        "num_turns": 2,
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
    }
    e = ev.parse_line(json.dumps(msg))[0]
    assert e.kind == ev.RESULT and e.success is True
    assert e.usage == {"input": 10, "output": 5, "cache_read": 100, "cache_creation": 20}


def test_parse_garbage_is_raw():
    out = ev.parse_line("not json")
    assert out[0].kind == ev.RAW
    assert ev.parse_line("") == []
