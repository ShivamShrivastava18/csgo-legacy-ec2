import base64
import json
import os

import boto3

import a2s
import discord_api
import server_control


def _env(name, default=""):
    return os.environ.get(name, default)


def _client(service):
    return boto3.client(service)


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
    ec2 = _client("ec2")
    iid = _env("INSTANCE_ID")
    st = server_control.get_status(ec2, iid)
    if st["state"] == "running" and st["ip"]:
        return msg(f"Already running. {connect_line(st['ip'])}")
    if st["state"] == "stopping":
        return msg("Instance is still stopping, try again in a minute.")
    if st["state"] not in ("stopped", "running"):
        return msg(f"Instance is {st['state']}, try again shortly.")
    map_name = opts.get("map", "de_mirage")
    server_control.start_with_tags(ec2, iid, map_name, opts.get("mode", "competitive"), opts.get("tickrate", "64"))
    _async_invoke({"source": "async-task", "task": "finish_start", "token": body["token"], "map": map_name})
    return _json_response({"type": 5})


def _async_invoke(payload):
    _client("lambda").invoke(
        FunctionName=_env("AWS_LAMBDA_FUNCTION_NAME"),
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )


def _do_stop():
    ec2 = _client("ec2")
    st = server_control.get_status(ec2, _env("INSTANCE_ID"))
    if st["state"] in ("stopped", "stopping"):
        return msg(f"Already {st['state']}.")
    server_control.stop_instance(ec2, _env("INSTANCE_ID"))
    hours = server_control.runtime_hours(st["launch_time"])
    return msg(f"Stopping. It ran {hours}h this session.")


def cmd_stop():
    return _do_stop()


def cmd_status():
    ec2 = _client("ec2")
    st = server_control.get_status(ec2, _env("INSTANCE_ID"))
    if st["state"] != "running" or not st["ip"]:
        return msg(f"Server is {st['state']}.")
    info = a2s.query(st["ip"])
    if not info:
        return msg(f"Instance running, srcds not answering yet. {connect_line(st['ip'])}")
    return msg(
        f"Running **{info['map']}** with {info['players']}/{info['max_players']} players. {connect_line(st['ip'])}"
    )


def cmd_map(opts, body):
    ec2 = _client("ec2")
    st = server_control.get_status(ec2, _env("INSTANCE_ID"))
    if st["state"] != "running":
        return msg("Server is not running. Use /csgo start.")
    command_id = server_control.change_map(_client("ssm"), _env("INSTANCE_ID"), opts["map"])
    _async_invoke(
        {
            "source": "async-task",
            "task": "finish_map",
            "token": body["token"],
            "map": opts["map"],
            "command_id": command_id,
        }
    )
    return _json_response({"type": 5})


def handle_button(body):
    if body["data"].get("custom_id") == "csgo_stop":
        return _do_stop()
    return msg("Unknown button.")


def hourly_check():
    return {"ok": True}


def run_async_task(event):
    return {"ok": True}
