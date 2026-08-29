import json
import urllib.request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

API = "https://discord.com/api/v10"


def verify_signature(public_key_hex, signature_hex, timestamp, body):
    try:
        key = VerifyKey(bytes.fromhex(public_key_hex))
        key.verify(timestamp.encode() + body, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def _request(method, url, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bot {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def patch_original(app_id, interaction_token, content):
    url = f"{API}/webhooks/{app_id}/{interaction_token}/messages/@original"
    return _request("PATCH", url, payload={"content": content})


def stop_button_components():
    return [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 4, "label": "Stop Server", "custom_id": "csgo_stop"}
            ],
        }
    ]


def post_channel_message(bot_token, channel_id, content, components=None):
    payload = {"content": content}
    if components:
        payload["components"] = components
    return _request("POST", f"{API}/channels/{channel_id}/messages", token=bot_token, payload=payload)
