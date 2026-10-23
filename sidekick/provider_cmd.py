"""``sidekick provider``: show, test, switch or rotate the model provider/key of a RUNNING ReDevOps stack.

A thin shell over the kernel capability ``operations.model_provider`` (agentic-os >= 0.3.43), so the CLI and the
Sidekick agent share one implementation and one set of rules:

* a key is never a flag or an argument: name the environment variable that holds it (``--key-env NAME``) or type it at
  the hidden prompt;
* where it persists is explicit (``--persist store|env_ref``): ``store`` writes it to Vault / KeePassXC / the OS keyring
  / a file store behind ``--key-ref backend:namespace/path``; ``env_ref`` records only a reference to the variable you
  already export and writes nothing;
* a change shows a PREVIEW (including whether model traffic would leave your trust boundary) and applies only after you
  confirm (or pass ``--yes``); it is then tested with a minimal call and rolled back to exactly the previous state if
  the test fails. The running agents pick it up on their next call: no reinstall, no restart.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

NEEDS_KERNEL = ("`sidekick provider` needs the ReDevOps kernel (agentic-os >= 0.3.43): "
                "pip install 'agentic-os @ git+https://github.com/redevops-io/agentic-os.git@v0.3.43'")


def _load():
    try:
        from agentic_os.sidekick.settings_skill import make_handler, mint_settings_approval  # noqa: PLC0415
    except ImportError:
        return None
    return make_handler, mint_settings_approval


def _inputs(args) -> Dict[str, Any]:
    out: Dict[str, Any] = {"action": {"show": "show", "test": "test", "set": "set", "rotate": "rotate_key"}[args.pcmd]}
    for src, dst in (("base_url", "base_url"), ("model", "model"), ("key_env", "key_env"), ("key_ref", "key_ref"),
                     ("persist", "persist")):
        v = getattr(args, src, None)
        if v:
            out[dst] = v
    if getattr(args, "allow_external_host", False):
        out["allow_external_host"] = True
    return out


def _print_preview(res: Dict[str, Any], say: Callable[[str], None]) -> None:
    pv = res.get("preview", {})
    say("Proposed change (nothing has been changed yet):")
    say(f"  provider  {pv.get('provider_host')}")
    say(f"  model     {pv.get('model')}")
    say(f"  key       {pv.get('key_reference')}   (persist: {pv.get('persist')})")
    say(f"  boundary  {pv.get('boundary')}")


def cmd_provider(args, *, prompt_secret: Optional[Callable[[str], str]] = None, confirm: Optional[Callable[[str], bool]] = None,
                 out=None) -> int:
    say = (lambda s: print(s, file=out or sys.stdout))
    loaded = _load()
    if loaded is None:
        say(NEEDS_KERNEL)
        return 2
    make_handler, mint = loaded
    handler = make_handler(prompt_secret=prompt_secret or (lambda label: getpass.getpass(label + ": ")))
    inputs = _inputs(args)
    as_json = bool(getattr(args, "json", False))
    try:
        res = handler(**inputs)
    except ValueError as e:                      # e.g. no settings file configured
        say(json.dumps({"status": "refused", "reason": str(e)}) if as_json else f"error: {e}")
        return 2
    if res.get("status") == "pending_approval":
        if not as_json:
            _print_preview(res, say)
        ok = bool(getattr(args, "yes", False))
        if not ok and not as_json:
            ask = confirm or (lambda q: input(q + " [y/N] ").strip().lower() in ("y", "yes"))
            ok = ask("Apply this change?")
        if not ok:
            say(json.dumps({**res, "applied": False}) if as_json else "Not applied.")
            return 1
        token = mint(res["payload"])
        if not token:
            msg = "no approval authority: set AGENTIC_OS_APPROVAL_SECRET (run `agentic-os secrets init`)"
            say(json.dumps({"status": "refused", "reason": msg}) if as_json else f"error: {msg}")
            return 2
        res = handler(**inputs, _approval=token)
    if as_json:
        say(json.dumps(res, default=str))
    else:
        st = res.get("status")
        if st == "applied":
            r = res["receipt"]
            say(f"Applied. Tested OK (HTTP {r['test'].get('status')}). Key {r.get('key') or '(unchanged)'}; "
                f"the running agents use it on their next call.")
        elif st == "rolled_back":
            say(f"Test FAILED (status {res['receipt']['test'].get('status')}): rolled back to the previous settings and key.")
        elif st == "ok":
            for k, v in res.items():
                if k not in ("status", "action"):
                    say(f"  {k}: {v}")
        else:
            say(f"Refused: {res.get('reason', res)}")
    return 0 if res.get("status") in ("applied", "ok") else 1


def add_parser(sub) -> None:
    pp = sub.add_parser("provider", help="Show / test / switch / rotate the model provider and key of a running stack")
    ps = pp.add_subparsers(dest="pcmd", required=True)
    for name, h in (("show", "Show the current provider, model and key reference (no secrets)"),
                    ("test", "Send one minimal test call with the current settings"),
                    ("set", "Switch provider/model (and optionally the key reference)"),
                    ("rotate", "Replace the key behind the current key reference")):
        sp = ps.add_parser(name, help=h)
        sp.add_argument("--json", action="store_true", dest="json", help="Machine-readable output")
        if name in ("set", "rotate"):
            sp.add_argument("--key-env", dest="key_env", help="NAME of the environment variable holding the key (never the key)")
            sp.add_argument("--key-ref", dest="key_ref", help="Where to keep the key: backend:namespace/path (e.g. keepassxc:Redevops/LLM)")
            sp.add_argument("--persist", choices=["store", "env_ref"], help="store = write it to the secret store; env_ref = only reference your exported variable")
            sp.add_argument("-y", "--yes", action="store_true", help="Apply without the confirmation prompt (never skips the preview)")
        if name == "set":
            sp.add_argument("--base-url", dest="base_url", help="OpenAI-compatible base URL")
            sp.add_argument("--model", help="Model name")
            sp.add_argument("--allow-external-host", dest="allow_external_host", action="store_true",
                            help="Allow a host OUTSIDE your trust boundary (must also be confirmed)")
        sp.set_defaults(func=cmd_provider)
