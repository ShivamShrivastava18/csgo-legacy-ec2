import json
from nacl.signing import SigningKey
import lambda_function


def signed_event(body_dict, monkeypatch):
    body = json.dumps(body_dict).encode()
    sk = SigningKey.generate()
    ts = "1700000000"
    sig = sk.sign(ts.encode() + body).signature.hex()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", sk.verify_key.encode().hex())
    return {
        "headers": {"x-signature-ed25519": sig, "x-signature-timestamp": ts},
        "body": body.decode(),
        "isBase64Encoded": False,
    }


def test_rejects_bad_signature(monkeypatch):
    event = signed_event({"type": 1}, monkeypatch)
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", "00" * 32)
    resp = lambda_function.lambda_handler(event, None)
    assert resp["statusCode"] == 401


def test_ping_pong(monkeypatch):
    event = signed_event({"type": 1}, monkeypatch)
    resp = lambda_function.lambda_handler(event, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"type": 1}


def test_routes_subcommand_to_stub(monkeypatch):
    body = {
        "type": 2,
        "token": "tok",
        "data": {"name": "csgo", "options": [{"name": "status", "options": []}]},
    }
    event = signed_event(body, monkeypatch)
    called = []
    monkeypatch.setattr(lambda_function, "cmd_status", lambda: (called.append(True), lambda_function.msg("ok"))[1])
    resp = lambda_function.lambda_handler(event, None)
    assert called == [True]
    assert json.loads(resp["body"])["data"]["content"] == "ok"


def test_routes_hourly_event(monkeypatch):
    called = []
    monkeypatch.setattr(lambda_function, "hourly_check", lambda: called.append(True) or {"ok": True})
    lambda_function.lambda_handler({"source": "hourly-check"}, None)
    assert called == [True]


def test_routes_async_task(monkeypatch):
    seen = []
    monkeypatch.setattr(lambda_function, "run_async_task", lambda e: seen.append(e) or {"ok": True})
    lambda_function.lambda_handler({"source": "async-task", "task": "finish_start"}, None)
    assert seen[0]["task"] == "finish_start"


def test_unknown_command_is_polite(monkeypatch):
    body = {
        "type": 2,
        "token": "tok",
        "data": {"name": "csgo", "options": [{"name": "dance", "options": []}]},
    }
    event = signed_event(body, monkeypatch)
    resp = lambda_function.lambda_handler(event, None)
    assert "Unknown" in json.loads(resp["body"])["data"]["content"]


def test_connect_line_with_and_without_password(monkeypatch):
    monkeypatch.setenv("SV_PASSWORD", "hunter2")
    assert lambda_function.connect_line("1.2.3.4") == "`connect 1.2.3.4:27015; password hunter2`"
    monkeypatch.setenv("SV_PASSWORD", "")
    assert lambda_function.connect_line("1.2.3.4") == "`connect 1.2.3.4:27015`"
