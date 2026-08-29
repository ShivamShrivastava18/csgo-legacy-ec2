import base64
import json
import os

import discord_api


def _env(name, default=""):
    return os.environ.get(name, default)


def _json_response(payload, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def msg(content):
    return _json_response({"type": 4, "data": {"content": content}})


def connect_line(ip):
    line = f"connect {ip}:27015"
    password = _env("SV_PASSWORD")
    if password:
        line += f"; password {password}"
    return f"`{line}`"


def lambda_handler(event, context):
    if event.get("source") == "hourly-check":
        return hourly_check()
    if event.get("source") == "async-task":
        return run_async_task(event)
    return handle_http(event)


def handle_http(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    sig = headers.get("x-signature-ed25519", "")
    ts = headers.get("x-signature-timestamp", "")
    raw = event.get("body") or ""
    body_bytes = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()
    if not discord_api.verify_signature(_env("DISCORD_PUBLIC_KEY"), sig, ts, body_bytes):
        return _json_response({"error": "bad signature"}, status=401)
    body = json.loads(body_bytes)
    if body["type"] == 1:
        return _json_response({"type": 1})
    if body["type"] == 2:
        return handle_command(body)
    if body["type"] == 3:
        return handle_button(body)
    return msg("Unknown interaction.")


def handle_command(body):
    sub = body["data"]["options"][0]
    opts = {o["name"]: str(o["value"]) for o in sub.get("options", [])}
    name = sub["name"]
    if name == "start":
        return cmd_start(opts, body)
    if name == "stop":
        return cmd_stop()
    if name == "status":
        return cmd_status()
    if name == "map":
        return cmd_map(opts, body)
    return msg("Unknown command.")


def cmd_start(opts, body):
    return msg("not implemented")


def cmd_stop():
    return msg("not implemented")


def cmd_status():
    return msg("not implemented")


def cmd_map(opts, body):
    return msg("not implemented")


def handle_button(body):
    return msg("not implemented")


def hourly_check():
    return {"ok": True}


def run_async_task(event):
    return {"ok": True}
