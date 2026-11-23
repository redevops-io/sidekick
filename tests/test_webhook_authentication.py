"""The webhook gateway over a real socket: a callback is acted on only when the provider's signature verifies.

Before this, the gateway listened on every interface and decided who was allowed from the sender id INSIDE the
request body. Anyone who could reach the port and knew an allowed Slack user id or phone number (neither is a secret)
could queue a task for an orchestrator that runs auto-approved shell commands."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import inspect
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from sidekick.channels.gateway import MAX_WEBHOOK_BYTES, Gateway
from sidekick.channels.slack import SlackChannel
from sidekick.channels.whatsapp import WhatsAppChannel
from sidekick.config import Config

SLACK_SECRET, APP_SECRET = "slack-signing-secret", "meta-app-secret"
SLACK_EVENT = json.dumps({"event": {"type": "message", "text": "run the deploy", "channel": "C1", "user": "U_ALLOWED"}}).encode()
WA_EVENT = json.dumps({"entry": [{"changes": [{"value": {"messages": [{"type": "text", "from": "15550001111", "text": {"body": "run it"}}]}}]}]}).encode()


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.delenv("SIDEKICK_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setenv("SIDEKICK_WHATSAPP_APP_SECRET", APP_SECRET)
    slack = SlackChannel(token="xoxb-1", signing_secret=SLACK_SECRET)
    wa = WhatsAppChannel(token="t", phone_id="p", verify_token="vt")
    gw = Gateway(Config(repo_root=tmp_path) if "repo_root" in inspect.signature(Config).parameters else Config(),
                 [slack, wa], allow=["U_ALLOWED", "15550001111"])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), gw._make_http_handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield gw, srv.server_address[1]
    srv.shutdown()


def post(port, body, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", "/", body=body, headers=headers or {})
    r = c.getresponse()
    return r.status, r.read()


def slack_headers(body, secret=SLACK_SECRET, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    return {"X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()}


def test_an_unsigned_callback_naming_an_allowed_sender_queues_nothing(served):
    gw, port = served
    assert post(port, SLACK_EVENT)[0] == 200 and post(port, WA_EVENT)[0] == 200
    assert gw.inbox.empty()


@pytest.mark.parametrize("headers", [
    lambda b: slack_headers(b, secret="wrong-secret"),
    lambda b: slack_headers(b, ts=int(time.time()) - 3600),                      # a replayed, genuine-looking request
    lambda b: {**slack_headers(b), "X-Slack-Signature": "v0=" + "0" * 64},
    lambda b: slack_headers(b + b" "),                                           # signature of a different body
])
def test_a_wrongly_signed_or_stale_slack_callback_queues_nothing(served, headers):
    gw, port = served
    post(port, SLACK_EVENT, headers(SLACK_EVENT))
    assert gw.inbox.empty()


def test_a_correctly_signed_slack_callback_is_queued_once(served):
    gw, port = served
    post(port, SLACK_EVENT, slack_headers(SLACK_EVENT))
    msg = gw.inbox.get(timeout=2)
    assert (msg.channel, msg.sender, msg.text) == ("slack", "U_ALLOWED", "run the deploy") and gw.inbox.empty()


def test_a_signed_callback_from_a_sender_not_on_the_allowlist_is_still_refused(served):
    gw, port = served
    other = SLACK_EVENT.replace(b"U_ALLOWED", b"U_STRANGER")
    post(port, other, slack_headers(other))
    assert gw.inbox.empty()


def test_whatsapp_needs_the_app_secret_signature(served):
    gw, port = served
    post(port, WA_EVENT, {"X-Hub-Signature-256": "sha256=" + hmac.new(b"not-the-secret", WA_EVENT, hashlib.sha256).hexdigest()})
    assert gw.inbox.empty()
    post(port, WA_EVENT, {"X-Hub-Signature-256": "sha256=" + hmac.new(APP_SECRET.encode(), WA_EVENT, hashlib.sha256).hexdigest()})
    assert gw.inbox.get(timeout=2).sender == "15550001111"


def test_no_secret_configured_means_no_inbound_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("SIDEKICK_SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("SIDEKICK_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    assert SlackChannel(token="xoxb-1").verify_webhook(slack_headers(SLACK_EVENT), SLACK_EVENT) is False
    monkeypatch.setenv("SIDEKICK_WEBHOOK_ALLOW_UNSIGNED", "1")                    # the explicit, documented escape hatch
    assert SlackChannel(token="xoxb-1").verify_webhook({}, SLACK_EVENT) is True


def test_an_unset_whatsapp_verify_token_does_not_match_an_empty_parameter():
    wa = WhatsAppChannel(token="t", phone_id="p")
    assert wa.webhook_challenge({"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}) is None


def test_an_oversized_body_is_refused_without_being_read(served):
    gw, port = served
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST", "/"); c.putheader("Content-Length", str(MAX_WEBHOOK_BYTES + 1)); c.endheaders()
    assert c.getresponse().status == 413 and gw.inbox.empty()


def test_the_gateway_listens_on_loopback_unless_told_otherwise():
    assert inspect.signature(Gateway.__init__).parameters["http_host"].default == "127.0.0.1"
