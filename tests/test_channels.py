import json

from sidekick.channels import load_channels, make_notifier
from sidekick.channels.base import Channel, InboundMessage
from sidekick.channels.gateway import Gateway
from sidekick.channels.imessage import IMessageChannel
from sidekick.channels.notify import Notifier
from sidekick.channels.slack import SlackChannel
from sidekick.channels.telegram import TelegramChannel
from sidekick.channels.whatsapp import WhatsAppChannel

# --- Telegram (poll + send), HTTP mocked -------------------------------------


def test_telegram_send_payload(monkeypatch):
    calls = []

    def fake(url, payload=None, headers=None, method=None, timeout=30):
        calls.append((url, payload))
        return 200, {"ok": True}

    monkeypatch.setattr("sidekick.channels.telegram.http_json", fake)
    tg = TelegramChannel(token="T", chat_id="42")
    assert tg.enabled
    assert tg.send("hello") is True
    assert "sendMessage" in calls[0][0]
    assert calls[0][1]["chat_id"] == "42" and calls[0][1]["text"] == "hello"


def test_telegram_poll_parses_and_advances_offset(monkeypatch):
    updates = {
        "ok": True,
        "result": [
            {"update_id": 10, "message": {"text": "do x", "chat": {"id": 7}, "from": {"username": "bob"}}},
            {"update_id": 11, "message": {"text": "", "chat": {"id": 7}}},  # empty → skipped
        ],
    }

    def fake(url, payload=None, headers=None, method=None, timeout=30):
        return 200, updates

    monkeypatch.setattr("sidekick.channels.telegram.http_json", fake)
    tg = TelegramChannel(token="T")
    msgs = list(tg.poll())
    assert len(msgs) == 1
    assert msgs[0].text == "do x" and msgs[0].reply_to == "7" and msgs[0].sender == "bob"
    assert tg._offset == 12  # advanced past update_id 11


def test_telegram_disabled_without_token():
    assert TelegramChannel(token="").enabled is False


# --- Slack webhook -----------------------------------------------------------


def test_slack_url_verification_challenge():
    sl = SlackChannel(token="xoxb-1")
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    assert sl.parse_webhook({}, body) == []
    assert sl.take_challenge() == "abc123"
    assert sl.take_challenge() == ""  # consumed


def test_slack_message_event_and_bot_ignored():
    sl = SlackChannel(token="xoxb-1")
    human = json.dumps({"event": {"type": "message", "text": "fix it", "channel": "C1", "user": "U1"}}).encode()
    out = sl.parse_webhook({}, human)
    assert len(out) == 1 and out[0].text == "fix it" and out[0].reply_to == "C1"
    bot = json.dumps({"event": {"type": "message", "text": "echo", "channel": "C1", "bot_id": "B1"}}).encode()
    assert sl.parse_webhook({}, bot) == []


# --- WhatsApp webhook --------------------------------------------------------


def test_whatsapp_verify_challenge():
    wa = WhatsAppChannel(token="t", phone_id="p", verify_token="secret")
    q = {"hub.mode": "subscribe", "hub.verify_token": "secret", "hub.challenge": "99"}
    assert wa.webhook_challenge(q) == "99"
    assert wa.webhook_challenge({"hub.verify_token": "wrong"}) is None


def test_whatsapp_parse_inbound():
    wa = WhatsAppChannel(token="t", phone_id="p")
    body = json.dumps(
        {"entry": [{"changes": [{"value": {"messages": [
            {"type": "text", "from": "15551234567", "text": {"body": "build me a thing"}}
        ]}}]}]}
    ).encode()
    out = wa.parse_webhook({}, body)
    assert len(out) == 1 and out[0].text == "build me a thing" and out[0].reply_to == "15551234567"


# --- iMessage relay bridge ---------------------------------------------------


def test_imessage_poll_tails_inbox(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(json.dumps({"text": "task one", "from": "+1555"}) + "\n")
    im = IMessageChannel(inbox=str(inbox))
    assert im.enabled
    first = list(im.poll())
    assert len(first) == 1 and first[0].text == "task one"
    # Offset tracked: a second poll with no new lines yields nothing.
    assert list(im.poll()) == []
    # Append a new line → only the new one is read.
    with inbox.open("a") as f:
        f.write(json.dumps({"text": "task two", "from": "+1555"}) + "\n")
    second = list(im.poll())
    assert len(second) == 1 and second[0].text == "task two"


def test_imessage_send_via_command(tmp_path):
    out = tmp_path / "sent.txt"
    # Use a shell pipeline that records stdin to a file; {to} is substituted.
    im = IMessageChannel(send_cmd=f"sh -c 'cat > {out}; echo {{to}} >> {out}'")
    assert im.send("hi there", reply_to="+1999") is True
    written = out.read_text()
    assert "hi there" in written and "+1999" in written


# --- Notifier (best-effort) --------------------------------------------------


class _Recorder(Channel):
    name = "rec"

    def __init__(self, ok=True, boom=False):
        self.sent = []
        self._ok = ok
        self._boom = boom

    @property
    def enabled(self):
        return True

    def send(self, text, reply_to=None):
        if self._boom:
            raise RuntimeError("channel down")
        self.sent.append((text, reply_to))
        return self._ok


def test_notifier_broadcasts_and_swallows_errors():
    good = _Recorder()
    bad = _Recorder(boom=True)
    n = Notifier([good, bad])
    assert n.active
    n.run_started("t", 3, "claude:default")
    n.run_finished("t", 2, 3, 2, 1500)
    # Good channel received both; the raising channel did not propagate.
    assert len(good.sent) == 2
    assert "started" in good.sent[0][0] and "finished" in good.sent[1][0]


def test_notifier_drops_disabled_channels():
    disabled = TelegramChannel(token="")  # not enabled
    n = Notifier([disabled])
    assert n.active is False


# --- Registry ----------------------------------------------------------------


def test_load_channels_selects_and_filters(monkeypatch):
    monkeypatch.setenv("SIDEKICK_TELEGRAM_TOKEN", "T")
    monkeypatch.delenv("SIDEKICK_SLACK_BOT_TOKEN", raising=False)
    chans = load_channels(["telegram", "slack"])  # only telegram is configured
    assert [c.name for c in chans] == ["telegram"]
    # only_enabled=False keeps both.
    both = load_channels(["telegram", "slack"], only_enabled=False)
    assert {c.name for c in both} == {"telegram", "slack"}


# --- Gateway authorization ---------------------------------------------------


def _cfg(tmp_path):
    from sidekick.config import Config

    return Config(repo_root=tmp_path)


def test_gateway_closed_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SIDEKICK_GATEWAY_ALLOW", raising=False)
    monkeypatch.delenv("SIDEKICK_GATEWAY_OPEN", raising=False)
    gw = Gateway(_cfg(tmp_path), [_Recorder()])
    msg = InboundMessage(channel="rec", text="rm everything", reply_to="x", sender="stranger")
    assert gw._authorized(msg) is False


def test_gateway_allowlist_and_open(tmp_path):
    gw = Gateway(_cfg(tmp_path), [_Recorder()], allow=["alice"])
    assert gw._authorized(InboundMessage(channel="rec", text="t", reply_to="x", sender="alice")) is True
    assert gw._authorized(InboundMessage(channel="rec", text="t", reply_to="x", sender="eve")) is False
    open_gw = Gateway(_cfg(tmp_path), [_Recorder()], open_access=True)
    assert open_gw._authorized(InboundMessage(channel="rec", text="t", reply_to="x", sender="eve")) is True


def test_gateway_unauthorized_sender_gets_refusal(tmp_path):
    rec = _Recorder()
    rec.name = "rec"
    gw = Gateway(_cfg(tmp_path), [rec], allow=["alice"])
    gw._handle(InboundMessage(channel="rec", text="do bad", reply_to="x", sender="eve"))
    assert rec.sent and "Not authorized" in rec.sent[0][0]


def test_make_notifier_inactive_without_config(monkeypatch):
    for var in ("SIDEKICK_TELEGRAM_TOKEN", "SIDEKICK_SLACK_BOT_TOKEN",
                "SIDEKICK_WHATSAPP_TOKEN", "SIDEKICK_IMESSAGE_SEND_CMD", "SIDEKICK_IMESSAGE_INBOX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SIDEKICK_CHANNELS", "telegram,slack")
    assert make_notifier().active is False
