"""`sidekick provider`: preview -> confirm -> apply -> test; keys by reference only. Needs agentic-os >= 0.3.43."""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

pytest.importorskip("agentic_os.sidekick.settings_skill")

from sidekick import provider_cmd  # noqa: E402
from sidekick.cli import build_parser  # noqa: E402


@pytest.fixture
def server():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            auth = self.headers.get("Authorization", "")
            seen.append(auth)
            ok = auth.startswith("Bearer good-")
            out = json.dumps({"choices": [{"message": {"content": "pong"}}]} if ok else {"error": "x"}).encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_port}/v1", seen=seen)
    httpd.shutdown()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REDEVOPS_SECRET_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("REDEVOPS_SECRET_BACKEND", "file")
    monkeypatch.setenv("REDEVOPS_LLM_SETTINGS_FILE", str(tmp_path / "llm.json"))
    monkeypatch.setenv("AGENTIC_OS_APPROVAL_SECRET", "s" * 40)
    for k in ("REDEVOPS_LLM_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _run(argv, **kw):
    args = build_parser().parse_args(argv)
    buf = io.StringIO()
    rc = provider_cmd.cmd_provider(args, out=buf, **kw)
    return rc, buf.getvalue()


def test_the_command_group_is_registered_and_keeps_the_agent_provider_flag_separate():
    a = build_parser().parse_args(["provider", "show"])
    assert a.cmd == "provider" and a.pcmd == "show"
    assert build_parser().parse_args(["run", "x", "--provider", "openai"]).provider == "openai"


def test_set_previews_then_declined_changes_nothing(server, env):
    rc, out = _run(["provider", "set", "--base-url", server.url, "--model", "m", "--key-ref", "file:llm/provider",
                    "--persist", "store", "--key-env", "K"], confirm=lambda q: False)
    assert rc == 1 and "Proposed change" in out and "Not applied" in out and not (env / "llm.json").exists()


def test_confirmed_set_applies_tests_and_stores_by_reference(server, env, monkeypatch):
    monkeypatch.setenv("K", "good-key-1234")
    rc, out = _run(["provider", "set", "--base-url", server.url, "--model", "m", "--key-ref", "file:llm/provider",
                    "--persist", "store", "--key-env", "K"], confirm=lambda q: True)
    assert rc == 0 and "Applied. Tested OK" in out and "good-key-1234" not in out
    assert (env / "secrets" / "llm" / "provider").read_text() == "good-key-1234"
    assert "good-key-1234" not in (env / "llm.json").read_text()


def test_rotate_prompts_for_the_key_hidden_and_rolls_back_a_bad_one(server, env, monkeypatch):
    monkeypatch.setenv("K", "good-old-1")
    _run(["provider", "set", "--base-url", server.url, "--model", "m", "--key-ref", "file:llm/provider",
          "--persist", "store", "--key-env", "K", "--yes"])
    asked = []
    rc, out = _run(["provider", "rotate", "--persist", "store", "--yes"],
                   prompt_secret=lambda label: asked.append(label) or "BAD-new")
    assert rc == 1 and "rolled back" in out and "BAD-new" not in out and asked
    assert (env / "secrets" / "llm" / "provider").read_text() == "good-old-1"


def test_json_mode_never_approves_on_its_own_and_yes_is_explicit(server, env, monkeypatch):
    monkeypatch.setenv("K", "good-k")
    rc, out = _run(["provider", "set", "--base-url", server.url, "--model", "m", "--key-env", "K",
                    "--persist", "env_ref", "--json"])
    assert rc == 1 and json.loads(out)["status"] == "pending_approval" and json.loads(out)["applied"] is False
    rc, out = _run(["provider", "set", "--base-url", server.url, "--model", "m", "--key-env", "K",
                    "--persist", "env_ref", "--json", "--yes"])
    assert rc == 0 and json.loads(out)["status"] == "applied"


def test_show_prints_no_secret(server, env):
    rc, out = _run(["provider", "show"])
    assert rc == 0 and "key_ref" in out


def test_without_an_approval_secret_it_refuses_rather_than_applying(server, env, monkeypatch):
    monkeypatch.delenv("AGENTIC_OS_APPROVAL_SECRET")
    monkeypatch.setenv("REDEVOPS_SECRETS_FILE", str(env / "none.env"))
    rc, out = _run(["provider", "set", "--base-url", server.url, "--model", "m"], confirm=lambda q: True)
    assert rc == 2 and "approval authority" in out and not (env / "llm.json").exists()
