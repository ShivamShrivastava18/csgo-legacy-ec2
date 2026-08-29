import json
from nacl.signing import SigningKey
import discord_api


def make_signed(body: bytes):
    sk = SigningKey.generate()
    ts = "1700000000"
    sig = sk.sign(ts.encode() + body).signature.hex()
    return sk.verify_key.encode().hex(), sig, ts


def test_verify_signature_accepts_valid():
    body = b'{"type":1}'
    pk, sig, ts = make_signed(body)
    assert discord_api.verify_signature(pk, sig, ts, body) is True


def test_verify_signature_rejects_tampered_body():
    body = b'{"type":1}'
    pk, sig, ts = make_signed(body)
    assert discord_api.verify_signature(pk, sig, ts, b'{"type":2}') is False


def test_verify_signature_rejects_garbage_hex():
    assert discord_api.verify_signature("zz", "zz", "0", b"x") is False


def test_patch_original_builds_request(monkeypatch):
    calls = {}

    def fake_request(method, url, token=None, payload=None):
        calls.update(method=method, url=url, token=token, payload=payload)
        return {}

    monkeypatch.setattr(discord_api, "_request", fake_request)
    discord_api.patch_original("app123", "tok456", "hello")
    assert calls["method"] == "PATCH"
    assert calls["url"].endswith("/webhooks/app123/tok456/messages/@original")
    assert calls["payload"] == {"content": "hello"}
    assert calls["token"] is None


def test_post_channel_message_with_button(monkeypatch):
    calls = {}

    def fake_request(method, url, token=None, payload=None):
        calls.update(method=method, url=url, token=token, payload=payload)
        return {}

    monkeypatch.setattr(discord_api, "_request", fake_request)
    discord_api.post_channel_message("bot-token", "chan9", "up", discord_api.stop_button_components())
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/channels/chan9/messages")
    assert calls["token"] == "bot-token"
    button = calls["payload"]["components"][0]["components"][0]
    assert button["custom_id"] == "csgo_stop"
    assert button["label"] == "Stop Server"
