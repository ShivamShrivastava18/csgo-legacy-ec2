# Discord Bot for CS:GO EC2 Server Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serverless Discord bot: `/csgo start [map] [mode] [tickrate]`, `/csgo stop`, `/csgo status`, `/csgo map`, plus an hourly still-running reminder with a Stop button, controlling EC2 instance `i-0ec3d2d9276b31cd5` in ap-south-1.

**Architecture:** One Python 3.12 Lambda behind a Function URL acts as the Discord Interactions Endpoint (Ed25519-verified). Slow work (waiting for the instance) runs via async self-invocation that edits the deferred Discord reply. EventBridge fires the same Lambda hourly for the reminder. Boot-time server config flows through EC2 tags read by an on-instance script via IMDSv2; live map changes go through SSM Run Command.

**Tech Stack:** Python 3.12, boto3 (in Lambda runtime), PyNaCl (only bundled dependency), urllib for Discord REST, pytest + botocore Stubber for tests, bash for deploy and the on-instance boot script.

**Spec:** `docs/superpowers/specs/2026-08-27-discord-bot-design.md`

## Global Constraints

- Region `ap-south-1`, AWS CLI profile `personal`, instance `i-0ec3d2d9276b31cd5`, account `403141583853`.
- Lambda name `csgo-discord-bot`, role `csgo-discord-bot-role`, EventBridge rule `csgo-hourly-reminder`, instance role/profile `csgo-server-ssm`.
- Lambda env vars: `DISCORD_PUBLIC_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_APP_ID`, `DISCORD_CHANNEL_ID`, `INSTANCE_ID`, `SV_PASSWORD`. Read env vars at call time, never at import time (tests set them per-test).
- No em dashes in any file, code without comments, per repo CLAUDE.md.
- Secrets live only in `bot/.env` (gitignored). Never commit tokens.
- All work on branch `discord-bot`.
- Run tests with `python3 -m pytest tests/ -v` from the repo root.

---

### Task 1: Test scaffolding and the A2S query module

**Files:**
- Create: `bot/a2s.py`
- Create: `tests/conftest.py`
- Create: `tests/test_a2s.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: `a2s.parse_info(data: bytes) -> dict | None` returning `{"name": str, "map": str, "players": int, "max_players": int}`; `a2s.query(ip: str, port: int = 27015, timeout: float = 3.0) -> dict | None` (same dict or None on any failure). Later tasks call `a2s.query(ip)` and treat None as "srcds not answering".

- [ ] **Step 1: Create scaffolding**

`requirements-dev.txt`:
```
pytest
pynacl
boto3
```

`tests/conftest.py`:
```python
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bot"))
```

Run: `pip3 install -r requirements-dev.txt`

- [ ] **Step 2: Write the failing tests**

`tests/test_a2s.py`:
```python
import struct
import a2s


def build_info_packet(map_name=b"de_dust2", players=3, max_players=16):
    return (
        b"\xff\xff\xff\xffI\x11"
        + b"LinuxGSM\x00"
        + map_name + b"\x00"
        + b"csgo\x00"
        + b"Counter-Strike: Global Offensive\x00"
        + struct.pack("<H", 730)
        + bytes([players, max_players])
        + b"\x00" * 5
    )


def test_parse_info_happy_path():
    info = a2s.parse_info(build_info_packet())
    assert info == {"name": "LinuxGSM", "map": "de_dust2", "players": 3, "max_players": 16}


def test_parse_info_rejects_non_info_packet():
    assert a2s.parse_info(b"\xff\xff\xff\xffA\x01\x02\x03\x04") is None


def test_parse_info_rejects_short_packet():
    assert a2s.parse_info(b"\xff\xff") is None


class FakeSocket:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def settimeout(self, t):
        pass

    def sendto(self, data, addr):
        self.sent.append(data)

    def recvfrom(self, n):
        return self.replies.pop(0), ("1.2.3.4", 27015)

    def close(self):
        pass


def test_query_handles_challenge(monkeypatch):
    challenge = b"\xff\xff\xff\xffA\xaa\xbb\xcc\xdd"
    fake = FakeSocket([challenge, build_info_packet()])
    monkeypatch.setattr(a2s.socket, "socket", lambda *a, **k: fake)
    info = a2s.query("1.2.3.4")
    assert info["map"] == "de_dust2"
    assert fake.sent[1] == a2s.A2S_INFO + b"\xaa\xbb\xcc\xdd"


def test_query_returns_none_on_timeout(monkeypatch):
    class TimeoutSocket(FakeSocket):
        def recvfrom(self, n):
            raise a2s.socket.timeout()

    monkeypatch.setattr(a2s.socket, "socket", lambda *a, **k: TimeoutSocket([]))
    assert a2s.query("1.2.3.4") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_a2s.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'a2s'`

- [ ] **Step 4: Write the implementation**

`bot/a2s.py`:
```python
import socket

A2S_INFO = b"\xff\xff\xff\xffTSource Engine Query\x00"


def _read_cstring(data, offset):
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8", "replace"), end + 1


def parse_info(data):
    if len(data) < 10 or data[4:5] != b"I":
        return None
    try:
        offset = 6
        name, offset = _read_cstring(data, offset)
        map_name, offset = _read_cstring(data, offset)
        _, offset = _read_cstring(data, offset)
        _, offset = _read_cstring(data, offset)
        offset += 2
        return {
            "name": name,
            "map": map_name,
            "players": data[offset],
            "max_players": data[offset + 1],
        }
    except (ValueError, IndexError):
        return None


def query(ip, port=27015, timeout=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(A2S_INFO, (ip, port))
        data, _ = s.recvfrom(4096)
        if data[4:5] == b"A":
            s.sendto(A2S_INFO + data[5:9], (ip, port))
            data, _ = s.recvfrom(4096)
        return parse_info(data)
    except (socket.timeout, OSError, IndexError):
        return None
    finally:
        s.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_a2s.py -v`
Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
git add bot/a2s.py tests/ requirements-dev.txt
git commit -m "feat: A2S server query module"
```

---

### Task 2: Discord signature verification and REST helpers

**Files:**
- Create: `bot/discord_api.py`
- Create: `tests/test_discord_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `verify_signature(public_key_hex: str, signature_hex: str, timestamp: str, body: bytes) -> bool`; `patch_original(app_id: str, interaction_token: str, content: str) -> dict`; `post_channel_message(bot_token: str, channel_id: str, content: str, components: list | None = None) -> dict`; `stop_button_components() -> list` (Discord components payload with `custom_id` `csgo_stop`). Module-level `_request(method, url, token=None, payload=None)` is the single HTTP chokepoint tests monkeypatch.

- [ ] **Step 1: Write the failing tests**

`tests/test_discord_api.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_discord_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_api'`

- [ ] **Step 3: Write the implementation**

`bot/discord_api.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_discord_api.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add bot/discord_api.py tests/test_discord_api.py
git commit -m "feat: Discord signature verification and REST helpers"
```

---

### Task 3: EC2 server control operations

**Files:**
- Create: `bot/server_control.py`
- Create: `tests/test_server_control.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `get_status(ec2, instance_id) -> dict` with keys `state` (str), `ip` (str or None), `launch_time` (datetime or None); `start_with_tags(ec2, instance_id, map_name, mode, tickrate) -> None`; `stop_instance(ec2, instance_id) -> None`; `runtime_hours(launch_time, now=None) -> float`; constant `MODE_SETTINGS = {"casual": ("0", "0"), "competitive": ("0", "1"), "deathmatch": ("1", "2")}`. All functions take the boto3 client as first argument so tests use botocore Stubber.

- [ ] **Step 1: Write the failing tests**

`tests/test_server_control.py`:
```python
import datetime
import boto3
from botocore.stub import Stubber, ANY
import server_control

IID = "i-0ec3d2d9276b31cd5"
LAUNCH = datetime.datetime(2026, 8, 27, 10, 0, tzinfo=datetime.timezone.utc)


def stubbed_ec2():
    ec2 = boto3.client("ec2", region_name="ap-south-1")
    return ec2, Stubber(ec2)


def describe_response(state, ip=None):
    inst = {"InstanceId": IID, "State": {"Name": state}, "LaunchTime": LAUNCH}
    if ip:
        inst["PublicIpAddress"] = ip
    return {"Reservations": [{"Instances": [inst]}]}


def test_get_status_running():
    ec2, stub = stubbed_ec2()
    stub.add_response("describe_instances", describe_response("running", "1.2.3.4"), {"InstanceIds": [IID]})
    with stub:
        st = server_control.get_status(ec2, IID)
    assert st == {"state": "running", "ip": "1.2.3.4", "launch_time": LAUNCH}


def test_get_status_stopped_has_no_ip():
    ec2, stub = stubbed_ec2()
    stub.add_response("describe_instances", describe_response("stopped"), {"InstanceIds": [IID]})
    with stub:
        st = server_control.get_status(ec2, IID)
    assert st["state"] == "stopped"
    assert st["ip"] is None


def test_start_with_tags_tags_then_starts():
    ec2, stub = stubbed_ec2()
    stub.add_response(
        "create_tags",
        {},
        {
            "Resources": [IID],
            "Tags": [
                {"Key": "csgo:map", "Value": "de_dust2"},
                {"Key": "csgo:mode", "Value": "casual"},
                {"Key": "csgo:tickrate", "Value": "128"},
            ],
        },
    )
    stub.add_response("start_instances", {}, {"InstanceIds": [IID]})
    with stub:
        server_control.start_with_tags(ec2, IID, "de_dust2", "casual", "128")


def test_stop_instance():
    ec2, stub = stubbed_ec2()
    stub.add_response("stop_instances", {}, {"InstanceIds": [IID]})
    with stub:
        server_control.stop_instance(ec2, IID)


def test_runtime_hours():
    now = LAUNCH + datetime.timedelta(hours=3, minutes=30)
    assert server_control.runtime_hours(LAUNCH, now=now) == 3.5


def test_mode_settings_table():
    assert server_control.MODE_SETTINGS["competitive"] == ("0", "1")
    assert server_control.MODE_SETTINGS["deathmatch"] == ("1", "2")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_server_control.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server_control'`

- [ ] **Step 3: Write the implementation**

`bot/server_control.py`:
```python
import datetime

MODE_SETTINGS = {
    "casual": ("0", "0"),
    "competitive": ("0", "1"),
    "deathmatch": ("1", "2"),
}


def get_status(ec2, instance_id):
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    inst = resp["Reservations"][0]["Instances"][0]
    return {
        "state": inst["State"]["Name"],
        "ip": inst.get("PublicIpAddress"),
        "launch_time": inst.get("LaunchTime"),
    }


def start_with_tags(ec2, instance_id, map_name, mode, tickrate):
    ec2.create_tags(
        Resources=[instance_id],
        Tags=[
            {"Key": "csgo:map", "Value": map_name},
            {"Key": "csgo:mode", "Value": mode},
            {"Key": "csgo:tickrate", "Value": tickrate},
        ],
    )
    ec2.start_instances(InstanceIds=[instance_id])


def stop_instance(ec2, instance_id):
    ec2.stop_instances(InstanceIds=[instance_id])


def runtime_hours(launch_time, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return round((now - launch_time).total_seconds() / 3600, 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_server_control.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add bot/server_control.py tests/test_server_control.py
git commit -m "feat: EC2 server control operations"
```

---

### Task 4: SSM live map change

**Files:**
- Modify: `bot/server_control.py` (append)
- Modify: `tests/test_server_control.py` (append)

**Interfaces:**
- Consumes: existing `server_control` module from Task 3.
- Produces: `change_map(ssm, instance_id, map_name) -> str` (the SSM CommandId); `command_result(ssm, instance_id, command_id) -> tuple[str, str]` (Status, StandardOutputContent); module constant `CHANGE_MAP_SCRIPT` (shell text with `{map}` placeholder).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server_control.py`:
```python
def stubbed_ssm():
    ssm = boto3.client("ssm", region_name="ap-south-1")
    return ssm, Stubber(ssm)


def test_change_map_sends_shell_command():
    ssm, stub = stubbed_ssm()
    stub.add_response(
        "send_command",
        {"Command": {"CommandId": "cmd-123"}},
        {
            "InstanceIds": [IID],
            "DocumentName": "AWS-RunShellScript",
            "Parameters": {"commands": [ANY]},
        },
    )
    with stub:
        cmd_id = server_control.change_map(ssm, IID, "de_inferno")
    assert cmd_id == "cmd-123"


def test_change_map_script_contains_map_and_fallback():
    script = server_control.CHANGE_MAP_SCRIPT.format(map="de_nuke")
    assert 'send "changelevel de_nuke"' in script
    assert "tmux" in script


def test_command_result():
    ssm, stub = stubbed_ssm()
    stub.add_response(
        "get_command_invocation",
        {"Status": "Success", "StandardOutputContent": "sent"},
        {"CommandId": "cmd-123", "InstanceId": IID},
    )
    with stub:
        status, out = server_control.command_result(ssm, IID, "cmd-123")
    assert status == "Success"
    assert out == "sent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_server_control.py -v`
Expected: 3 new FAIL with `AttributeError: module 'server_control' has no attribute 'change_map'`, 6 old PASS

- [ ] **Step 3: Write the implementation**

Append to `bot/server_control.py`:
```python
CHANGE_MAP_SCRIPT = """set -e
if sudo -u csgoserver -H /home/csgoserver/csgoserver send "changelevel {map}" 2>/dev/null; then
  echo sent
else
  SOCK=$(ls /tmp/tmux-$(id -u csgoserver)/ | head -1)
  sudo -u csgoserver -H tmux -L "$SOCK" send-keys -t csgoserver "changelevel {map}" Enter
  echo sent-tmux
fi
"""


def change_map(ssm, instance_id, map_name):
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [CHANGE_MAP_SCRIPT.format(map=map_name)]},
    )
    return resp["Command"]["CommandId"]


def command_result(ssm, instance_id, command_id):
    resp = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    return resp["Status"], resp.get("StandardOutputContent", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_server_control.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add bot/server_control.py tests/test_server_control.py
git commit -m "feat: SSM live map change"
```

---

### Task 5: Lambda handler plumbing, signature gate, and routing

**Files:**
- Create: `bot/lambda_function.py`
- Create: `tests/test_lambda_routing.py`

**Interfaces:**
- Consumes: `discord_api.verify_signature` (Task 2).
- Produces: `lambda_handler(event, context) -> dict`; `_env(name, default="") -> str` (call-time env reader); `msg(content: str) -> dict` (Function URL response wrapping Discord type-4 message); `_json_response(payload, status=200) -> dict`; `connect_line(ip: str) -> str`. Routing contract: events with `{"source": "hourly-check"}` go to `hourly_check()`, `{"source": "async-task"}` go to `run_async_task(event)`, everything else is treated as an HTTP interaction. Command dispatch calls `cmd_start(opts, body)`, `cmd_stop()`, `cmd_status()`, `cmd_map(opts, body)`, `handle_button(body)`, all defined in Task 6 (this task defines them as one-line stubs returning `msg("not implemented")` so routing is testable).

- [ ] **Step 1: Write the failing tests**

`tests/test_lambda_routing.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lambda_routing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lambda_function'`

- [ ] **Step 3: Write the implementation**

`bot/lambda_function.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lambda_routing.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add bot/lambda_function.py tests/test_lambda_routing.py
git commit -m "feat: Lambda handler plumbing and interaction routing"
```

---

### Task 6: Command handlers (start, stop, status, map, button)

**Files:**
- Modify: `bot/lambda_function.py` (replace the five stubs, add imports)
- Create: `tests/test_command_handlers.py`

**Interfaces:**
- Consumes: `server_control` (Tasks 3-4), `a2s.query` (Task 1), routing + helpers (Task 5).
- Produces: real `cmd_start(opts, body)`, `cmd_stop()`, `cmd_status()`, `cmd_map(opts, body)`, `handle_button(body)`. `cmd_start` and `cmd_map` return a deferred type-5 response after async-invoking the Lambda named by env `AWS_LAMBDA_FUNCTION_NAME` with payloads `{"source": "async-task", "task": "finish_start", "token": ..., "map": ...}` and `{"source": "async-task", "task": "finish_map", "token": ..., "map": ..., "command_id": ...}`. Task 7 implements those consumers.

- [ ] **Step 1: Write the failing tests**

`tests/test_command_handlers.py`:
```python
import datetime
import json
import lambda_function

LAUNCH = datetime.datetime(2026, 8, 27, 10, 0, tzinfo=datetime.timezone.utc)


class FakeLambdaClient:
    def __init__(self):
        self.invocations = []

    def invoke(self, **kw):
        self.invocations.append(kw)
        return {}


class FakeSSM:
    pass


def wire(monkeypatch, state, ip=None, a2s_info=None):
    fake_lambda = FakeLambdaClient()
    calls = {"start": [], "stop": [], "change_map": []}
    monkeypatch.setenv("INSTANCE_ID", "i-0ec3d2d9276b31cd5")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "csgo-discord-bot")
    monkeypatch.setenv("SV_PASSWORD", "")
    monkeypatch.setattr(lambda_function, "_client", lambda service: fake_lambda if service == "lambda" else FakeSSM())
    monkeypatch.setattr(
        lambda_function.server_control, "get_status",
        lambda ec2, iid: {"state": state, "ip": ip, "launch_time": LAUNCH},
    )
    monkeypatch.setattr(
        lambda_function.server_control, "start_with_tags",
        lambda ec2, iid, m, mo, t: calls["start"].append((m, mo, t)),
    )
    monkeypatch.setattr(
        lambda_function.server_control, "stop_instance",
        lambda ec2, iid: calls["stop"].append(iid),
    )
    monkeypatch.setattr(
        lambda_function.server_control, "change_map",
        lambda ssm, iid, m: calls["change_map"].append(m) or "cmd-1",
    )
    monkeypatch.setattr(lambda_function.a2s, "query", lambda ip_, **kw: a2s_info)
    return fake_lambda, calls


def content_of(resp):
    return json.loads(resp["body"])["data"]["content"]


def test_start_when_stopped_defers_and_invokes_async(monkeypatch):
    fake_lambda, calls = wire(monkeypatch, "stopped")
    resp = lambda_function.cmd_start({"map": "de_dust2"}, {"token": "tok1"})
    assert json.loads(resp["body"])["type"] == 5
    assert calls["start"] == [("de_dust2", "competitive", "64")]
    payload = json.loads(fake_lambda.invocations[0]["Payload"])
    assert payload == {"source": "async-task", "task": "finish_start", "token": "tok1", "map": "de_dust2"}
    assert fake_lambda.invocations[0]["InvocationType"] == "Event"


def test_start_when_running_returns_ip(monkeypatch):
    wire(monkeypatch, "running", ip="1.2.3.4")
    resp = lambda_function.cmd_start({}, {"token": "tok1"})
    assert "Already running" in content_of(resp)
    assert "connect 1.2.3.4:27015" in content_of(resp)


def test_start_when_stopping_asks_patience(monkeypatch):
    wire(monkeypatch, "stopping")
    resp = lambda_function.cmd_start({}, {"token": "tok1"})
    assert "stopping" in content_of(resp)


def test_stop_when_running(monkeypatch):
    _, calls = wire(monkeypatch, "running", ip="1.2.3.4")
    resp = lambda_function.cmd_stop()
    assert calls["stop"]
    assert "Stopping" in content_of(resp)


def test_stop_when_already_stopped(monkeypatch):
    _, calls = wire(monkeypatch, "stopped")
    resp = lambda_function.cmd_stop()
    assert calls["stop"] == []
    assert "Already stopped" in content_of(resp)


def test_status_running_with_players(monkeypatch):
    wire(monkeypatch, "running", ip="1.2.3.4", a2s_info={"name": "x", "map": "de_nuke", "players": 4, "max_players": 16})
    resp = lambda_function.cmd_status()
    text = content_of(resp)
    assert "de_nuke" in text and "4/16" in text and "connect 1.2.3.4:27015" in text


def test_status_running_but_srcds_down(monkeypatch):
    wire(monkeypatch, "running", ip="1.2.3.4", a2s_info=None)
    assert "not answering" in content_of(lambda_function.cmd_status())


def test_status_stopped(monkeypatch):
    wire(monkeypatch, "stopped")
    assert "stopped" in content_of(lambda_function.cmd_status())


def test_map_change_on_running_server_defers(monkeypatch):
    fake_lambda, calls = wire(monkeypatch, "running", ip="1.2.3.4")
    resp = lambda_function.cmd_map({"map": "de_inferno"}, {"token": "tok2"})
    assert json.loads(resp["body"])["type"] == 5
    assert calls["change_map"] == ["de_inferno"]
    payload = json.loads(fake_lambda.invocations[0]["Payload"])
    assert payload["task"] == "finish_map"
    assert payload["command_id"] == "cmd-1"


def test_map_change_when_stopped_redirects(monkeypatch):
    _, calls = wire(monkeypatch, "stopped")
    resp = lambda_function.cmd_map({"map": "de_inferno"}, {"token": "tok2"})
    assert calls["change_map"] == []
    assert "/csgo start" in content_of(resp)


def test_button_stop(monkeypatch):
    _, calls = wire(monkeypatch, "running", ip="1.2.3.4")
    resp = lambda_function.handle_button({"data": {"custom_id": "csgo_stop"}})
    assert calls["stop"]
    assert "Stopping" in content_of(resp)


def test_button_unknown(monkeypatch):
    wire(monkeypatch, "running", ip="1.2.3.4")
    resp = lambda_function.handle_button({"data": {"custom_id": "mystery"}})
    assert "Unknown" in content_of(resp)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_command_handlers.py -v`
Expected: FAIL (stubs return "not implemented", and `_client`/`server_control`/`a2s` attributes missing)

- [ ] **Step 3: Write the implementation**

In `bot/lambda_function.py`, add imports at the top:
```python
import boto3

import a2s
import server_control
```

Add after `_env`:
```python
def _client(service):
    return boto3.client(service)
```

Replace the five stub functions with:
```python
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
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS (12 new + all prior)

- [ ] **Step 5: Commit**

```bash
git add bot/lambda_function.py tests/test_command_handlers.py
git commit -m "feat: slash command and button handlers"
```

---

### Task 7: Async tasks and the hourly reminder

**Files:**
- Modify: `bot/lambda_function.py` (replace `hourly_check` and `run_async_task` stubs)
- Create: `tests/test_async_and_hourly.py`

**Interfaces:**
- Consumes: everything above; `discord_api.patch_original`, `discord_api.post_channel_message`, `discord_api.stop_button_components` (Task 2); `server_control.command_result` (Task 4).
- Produces: `run_async_task(event)` handling tasks `finish_start` and `finish_map`; `finish_start(event)`; `finish_map(event)`; `hourly_check()`. `finish_start` polls with `_sleep(seconds)` (module-level wrapper around `time.sleep`, monkeypatchable) and a 300-second deadline via `_now()` wrapper around `time.time`.

- [ ] **Step 1: Write the failing tests**

`tests/test_async_and_hourly.py`:
```python
import datetime
import lambda_function

LAUNCH = datetime.datetime(2026, 8, 27, 10, 0, tzinfo=datetime.timezone.utc)


def wire(monkeypatch, statuses, a2s_results, command_results=None):
    patched = []
    posted = []
    status_iter = iter(statuses)
    a2s_iter = iter(a2s_results)
    monkeypatch.setenv("INSTANCE_ID", "i-0ec3d2d9276b31cd5")
    monkeypatch.setenv("DISCORD_APP_ID", "app1")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bt")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "chan1")
    monkeypatch.setenv("SV_PASSWORD", "")
    monkeypatch.setattr(lambda_function, "_client", lambda service: object())
    monkeypatch.setattr(lambda_function, "_sleep", lambda s: None)
    monkeypatch.setattr(lambda_function.server_control, "get_status", lambda ec2, iid: next(status_iter))
    monkeypatch.setattr(lambda_function.a2s, "query", lambda ip, **kw: next(a2s_iter))
    monkeypatch.setattr(
        lambda_function.discord_api, "patch_original",
        lambda app_id, token, content: patched.append(content) or {},
    )
    monkeypatch.setattr(
        lambda_function.discord_api, "post_channel_message",
        lambda tok, chan, content, components=None: posted.append((content, components)) or {},
    )
    if command_results is not None:
        result_iter = iter(command_results)
        monkeypatch.setattr(
            lambda_function.server_control, "command_result",
            lambda ssm, iid, cid: next(result_iter),
        )
    return patched, posted


def test_finish_start_success_after_boot(monkeypatch):
    patched, _ = wire(
        monkeypatch,
        statuses=[
            {"state": "pending", "ip": None, "launch_time": LAUNCH},
            {"state": "running", "ip": "1.2.3.4", "launch_time": LAUNCH},
        ],
        a2s_results=[{"name": "x", "map": "de_dust2", "players": 0, "max_players": 16}],
    )
    result = lambda_function.finish_start({"task": "finish_start", "token": "tok", "map": "de_dust2"})
    assert result["ok"] is True
    assert "de_dust2" in patched[0]
    assert "connect 1.2.3.4:27015" in patched[0]


def test_finish_start_timeout_with_ip(monkeypatch):
    times = iter([0, 100, 400, 500])
    monkeypatch.setattr(lambda_function, "_now", lambda: next(times))
    patched, _ = wire(
        monkeypatch,
        statuses=[{"state": "running", "ip": "1.2.3.4", "launch_time": LAUNCH}] * 5,
        a2s_results=[None] * 5,
    )
    result = lambda_function.finish_start({"task": "finish_start", "token": "tok", "map": "de_dust2"})
    assert result["ok"] is False
    assert "not answering" in patched[0]


def test_finish_map_success(monkeypatch):
    patched, _ = wire(
        monkeypatch,
        statuses=[],
        a2s_results=[],
        command_results=[("InProgress", ""), ("Success", "sent")],
    )
    result = lambda_function.finish_map(
        {"task": "finish_map", "token": "tok", "map": "de_nuke", "command_id": "cmd-1"}
    )
    assert result["ok"] is True
    assert "de_nuke" in patched[0]


def test_finish_map_failure(monkeypatch):
    patched, _ = wire(
        monkeypatch,
        statuses=[],
        a2s_results=[],
        command_results=[("Failed", "boom")],
    )
    result = lambda_function.finish_map(
        {"task": "finish_map", "token": "tok", "map": "de_nuke", "command_id": "cmd-1"}
    )
    assert result["ok"] is False
    assert "failed" in patched[0].lower()


def test_hourly_skips_when_stopped(monkeypatch):
    _, posted = wire(
        monkeypatch,
        statuses=[{"state": "stopped", "ip": None, "launch_time": LAUNCH}],
        a2s_results=[],
    )
    result = lambda_function.hourly_check()
    assert result.get("skipped") is True
    assert posted == []


def test_hourly_posts_with_stop_button(monkeypatch):
    fixed_now = LAUNCH + datetime.timedelta(hours=2)

    class FakeDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(lambda_function.server_control.datetime, "datetime", FakeDT)
    _, posted = wire(
        monkeypatch,
        statuses=[{"state": "running", "ip": "1.2.3.4", "launch_time": LAUNCH}],
        a2s_results=[{"name": "x", "map": "de_train", "players": 2, "max_players": 16}],
    )
    lambda_function.hourly_check()
    content, components = posted[0]
    assert "2.0h" in content and "de_train" in content and "2 players" in content
    assert components[0]["components"][0]["custom_id"] == "csgo_stop"


def test_run_async_task_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr(lambda_function, "finish_start", lambda e: seen.append("start") or {"ok": True})
    monkeypatch.setattr(lambda_function, "finish_map", lambda e: seen.append("map") or {"ok": True})
    lambda_function.run_async_task({"task": "finish_start"})
    lambda_function.run_async_task({"task": "finish_map"})
    assert seen == ["start", "map"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_async_and_hourly.py -v`
Expected: FAIL with `AttributeError` on `finish_start` / `_sleep` / `_now`

- [ ] **Step 3: Write the implementation**

In `bot/lambda_function.py`, add `import time` at the top and replace the `hourly_check` and `run_async_task` stubs with:
```python
def _sleep(seconds):
    time.sleep(seconds)


def _now():
    return time.time()


def run_async_task(event):
    if event["task"] == "finish_start":
        return finish_start(event)
    if event["task"] == "finish_map":
        return finish_map(event)
    return {"ok": False}


def finish_start(event):
    ec2 = _client("ec2")
    deadline = _now() + 300
    ip = None
    while _now() < deadline:
        st = server_control.get_status(ec2, _env("INSTANCE_ID"))
        ip = st["ip"]
        if st["state"] == "running" and ip:
            if a2s.query(ip):
                discord_api.patch_original(
                    _env("DISCORD_APP_ID"),
                    event["token"],
                    f"Server up on **{event['map']}**. {connect_line(ip)}",
                )
                return {"ok": True}
        _sleep(10)
    if ip:
        content = f"Instance is up but srcds is not answering yet. Try /csgo status in a minute. {connect_line(ip)}"
    else:
        content = "Timed out waiting for the instance to start. Check the AWS console."
    discord_api.patch_original(_env("DISCORD_APP_ID"), event["token"], content)
    return {"ok": False}


def finish_map(event):
    ssm = _client("ssm")
    for _ in range(30):
        status, output = server_control.command_result(ssm, _env("INSTANCE_ID"), event["command_id"])
        if status == "Success":
            discord_api.patch_original(
                _env("DISCORD_APP_ID"), event["token"], f"Map changed to **{event['map']}**."
            )
            return {"ok": True}
        if status in ("Failed", "Cancelled", "TimedOut"):
            discord_api.patch_original(
                _env("DISCORD_APP_ID"), event["token"], f"Map change failed: {status}. {output}"[:1900]
            )
            return {"ok": False}
        _sleep(2)
    discord_api.patch_original(_env("DISCORD_APP_ID"), event["token"], "Map change timed out.")
    return {"ok": False}


def hourly_check():
    ec2 = _client("ec2")
    st = server_control.get_status(ec2, _env("INSTANCE_ID"))
    if st["state"] != "running" or not st["ip"]:
        return {"ok": True, "skipped": True}
    hours = server_control.runtime_hours(st["launch_time"])
    info = a2s.query(st["ip"])
    if info:
        content = (
            f"Server still running for {hours}h · **{info['map']}** · "
            f"{info['players']} players · {connect_line(st['ip'])}"
        )
    else:
        content = f"Instance still running for {hours}h (srcds not answering) · {connect_line(st['ip'])}"
    discord_api.post_channel_message(
        _env("DISCORD_BOT_TOKEN"), _env("DISCORD_CHANNEL_ID"), content, discord_api.stop_button_components()
    )
    return {"ok": True}
```

Also add `import discord_api` if not present from Task 5 (it is; verify).

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS (7 new + all prior)

- [ ] **Step 5: Commit**

```bash
git add bot/lambda_function.py tests/test_async_and_hourly.py
git commit -m "feat: async start/map completion and hourly reminder"
```

---

### Task 8: On-instance boot script

**Files:**
- Create: `bot/server/csgo-boot.sh`
- Create: `tests/test_boot_script.py`

**Interfaces:**
- Consumes: EC2 tags `csgo:map`, `csgo:mode`, `csgo:tickrate` via IMDSv2 (written by `server_control.start_with_tags`).
- Produces: patched LinuxGSM config and a started server on boot. Test seams: `CSGO_CFG` env var overrides the config path, `CSGO_TAG_CMD` overrides the tag-fetch command, `CSGO_SKIP_START=1` skips the server start.

- [ ] **Step 1: Write the failing test**

`tests/test_boot_script.py`:
```python
import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bot" / "server" / "csgo-boot.sh"


def run_script(tmp_path, tag_values):
    cfg = tmp_path / "csgoserver.cfg"
    cfg.write_text('gslt="TOKEN"\ndefaultmap="de_dust2"\n')
    tag_script = tmp_path / "tags.sh"
    lines = ["#!/bin/bash", 'case "$1" in']
    for key, value in tag_values.items():
        lines.append(f'  {key}) echo "{value}" ;;')
    lines.append("  *) exit 1 ;;")
    lines.append("esac")
    tag_script.write_text("\n".join(lines) + "\n")
    tag_script.chmod(0o755)
    env = {
        "CSGO_CFG": str(cfg),
        "CSGO_TAG_CMD": str(tag_script),
        "CSGO_SKIP_START": "1",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True)
    return cfg.read_text()


def test_applies_tags(tmp_path):
    out = run_script(tmp_path, {"map": "de_nuke", "mode": "deathmatch", "tickrate": "128"})
    assert 'defaultmap="de_nuke"' in out
    assert 'tickrate="128"' in out
    assert 'gametype="1"' in out
    assert 'gamemode="2"' in out
    assert out.count("defaultmap=") == 1
    assert 'gslt="TOKEN"' in out


def test_defaults_when_tags_missing(tmp_path):
    out = run_script(tmp_path, {})
    assert 'defaultmap="de_mirage"' in out
    assert 'tickrate="64"' in out
    assert 'gametype="0"' in out
    assert 'gamemode="1"' in out


def test_script_passes_bash_syntax_check():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_boot_script.py -v`
Expected: FAIL (script file does not exist)

- [ ] **Step 3: Write the implementation**

`bot/server/csgo-boot.sh`:
```bash
#!/usr/bin/env bash
set -u

CFG="${CSGO_CFG:-/home/csgoserver/lgsm/config-lgsm/csgoserver/csgoserver.cfg}"

imds_tag() {
  local token
  token=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 300") || return 1
  curl -sf -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/tags/instance/csgo:$1"
}

get_tag() {
  local value
  if [[ -n "${CSGO_TAG_CMD:-}" ]]; then
    value=$("$CSGO_TAG_CMD" "$1" 2>/dev/null)
  else
    value=$(imds_tag "$1")
  fi
  if [[ -n "$value" ]]; then
    echo "$value"
  else
    echo "$2"
  fi
}

MAP=$(get_tag map de_mirage)
MODE=$(get_tag mode competitive)
TICK=$(get_tag tickrate 64)

case "$MODE" in
  casual) GT=0; GM=0 ;;
  deathmatch) GT=1; GM=2 ;;
  *) GT=0; GM=1 ;;
esac

grep -vE '^(defaultmap|tickrate|gametype|gamemode)=' "$CFG" > "$CFG.tmp"
mv "$CFG.tmp" "$CFG"
{
  echo "defaultmap=\"$MAP\""
  echo "tickrate=\"$TICK\""
  echo "gametype=\"$GT\""
  echo "gamemode=\"$GM\""
} >> "$CFG"

if [[ "${CSGO_SKIP_START:-0}" != "1" ]]; then
  /home/csgoserver/csgoserver start
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_boot_script.py -v`
Expected: 3 PASS (the script uses grep + mv instead of sed -i so it behaves identically on macOS test runs and Ubuntu)

- [ ] **Step 5: Commit**

```bash
git add bot/server/csgo-boot.sh tests/test_boot_script.py
git commit -m "feat: on-instance boot script reading config from EC2 tags"
```

---

### Task 9: Slash command registration script

**Files:**
- Create: `bot/register_commands.py`
- Create: `tests/test_register_commands.py`

**Interfaces:**
- Consumes: `discord_api._request` (Task 2) for the PUT call.
- Produces: `build_commands() -> list` (the exact command tree JSON); `load_env(path) -> dict` (parses KEY=VALUE lines, ignores blanks and `#` comments); `main()` PUTs to `/applications/{app_id}/guilds/{guild_id}/commands`.

- [ ] **Step 1: Write the failing tests**

`tests/test_register_commands.py`:
```python
import register_commands


def test_command_tree_shape():
    cmds = register_commands.build_commands()
    assert len(cmds) == 1
    csgo = cmds[0]
    assert csgo["name"] == "csgo"
    subs = {o["name"]: o for o in csgo["options"]}
    assert set(subs) == {"start", "stop", "status", "map"}
    start_opts = {o["name"]: o for o in subs["start"]["options"]}
    assert set(start_opts) == {"map", "mode", "tickrate"}
    assert len(start_opts["map"]["choices"]) == 12
    assert {"name": "de_dust2", "value": "de_dust2"} in start_opts["map"]["choices"]
    assert [c["value"] for c in start_opts["tickrate"]["choices"]] == ["64", "128"]
    assert subs["map"]["options"][0]["required"] is True


def test_load_env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\n\n# comment\nB=two words\n")
    assert register_commands.load_env(f) == {"A": "1", "B": "two words"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_register_commands.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`bot/register_commands.py`:
```python
import pathlib
import sys

import discord_api

MAPS = [
    "de_dust2", "de_mirage", "de_inferno", "de_nuke", "de_train", "de_overpass",
    "de_vertigo", "de_ancient", "de_anubis", "de_cache", "cs_office", "cs_italy",
]
MODES = ["casual", "competitive", "deathmatch"]
TICKRATES = ["64", "128"]


def _choices(values):
    return [{"name": v, "value": v} for v in values]


def build_commands():
    return [
        {
            "name": "csgo",
            "description": "Control the CS:GO server",
            "options": [
                {
                    "type": 1,
                    "name": "start",
                    "description": "Start the server",
                    "options": [
                        {"type": 3, "name": "map", "description": "Starting map", "choices": _choices(MAPS)},
                        {"type": 3, "name": "mode", "description": "Game mode", "choices": _choices(MODES)},
                        {"type": 3, "name": "tickrate", "description": "Tick rate", "choices": _choices(TICKRATES)},
                    ],
                },
                {"type": 1, "name": "stop", "description": "Stop the server"},
                {"type": 1, "name": "status", "description": "Show server state, map and players"},
                {
                    "type": 1,
                    "name": "map",
                    "description": "Change map on the running server",
                    "options": [
                        {"type": 3, "name": "map", "description": "New map", "required": True, "choices": _choices(MAPS)},
                    ],
                },
            ],
        }
    ]


def load_env(path):
    env = {}
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def main():
    env = load_env(pathlib.Path(__file__).parent / ".env")
    url = (
        f"{discord_api.API}/applications/{env['DISCORD_APP_ID']}"
        f"/guilds/{env['DISCORD_GUILD_ID']}/commands"
    )
    result = discord_api._request("PUT", url, token=env["DISCORD_BOT_TOKEN"], payload=build_commands())
    print(f"Registered {len(result)} command(s).")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_register_commands.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add bot/register_commands.py tests/test_register_commands.py
git commit -m "feat: guild slash command registration script"
```

---

### Task 10: Deploy script and env template

**Files:**
- Create: `bot/deploy.sh`
- Create: `bot/.env.example`
- Modify: `.gitignore` (append `bot/.env` and `bot/build/`)

**Interfaces:**
- Consumes: all `bot/*.py` files; `bot/.env` for secrets.
- Produces: deployed Lambda `csgo-discord-bot` with Function URL, role `csgo-discord-bot-role`, EventBridge rule `csgo-hourly-reminder`; `--onboard-instance` flag performs the one-time instance setup. Prints the Function URL at the end.

- [ ] **Step 1: Write the files**

`bot/.env.example`:
```
DISCORD_PUBLIC_KEY=
DISCORD_BOT_TOKEN=
DISCORD_APP_ID=
DISCORD_GUILD_ID=
DISCORD_CHANNEL_ID=
INSTANCE_ID=i-0ec3d2d9276b31cd5
SV_PASSWORD=
AWS_PROFILE=personal
AWS_REGION=ap-south-1
```

Append to `.gitignore`:
```
bot/.env
bot/build/
```

`bot/deploy.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
[[ -f .env ]] || { echo "Copy .env.example to .env and fill it in first."; exit 1; }
set -a; source .env; set +a

PROFILE="${AWS_PROFILE:-personal}"
REGION="${AWS_REGION:-ap-south-1}"
AWS=(aws --profile "$PROFILE" --region "$REGION")
ACCOUNT=$("${AWS[@]}" sts get-caller-identity --query Account --output text)
FUNC=csgo-discord-bot
ROLE=csgo-discord-bot-role
RULE=csgo-hourly-reminder
INSTANCE_ARN="arn:aws:ec2:$REGION:$ACCOUNT:instance/$INSTANCE_ID"

build_zip() {
  rm -rf build function.zip
  mkdir -p build
  pip3 install pynacl -t build/ --platform manylinux2014_x86_64 \
    --only-binary=:all: --python-version 3.12 --quiet
  cp a2s.py discord_api.py server_control.py lambda_function.py build/
  (cd build && zip -qr ../function.zip .)
}

ensure_role() {
  if ! "${AWS[@]}" iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    "${AWS[@]}" iam create-role --role-name "$ROLE" --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
    }' >/dev/null
    sleep 10
  fi
  "${AWS[@]}" iam put-role-policy --role-name "$ROLE" --policy-name csgo-bot-policy \
    --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [
        {\"Effect\": \"Allow\", \"Action\": [\"ec2:StartInstances\", \"ec2:StopInstances\", \"ec2:CreateTags\"], \"Resource\": \"$INSTANCE_ARN\"},
        {\"Effect\": \"Allow\", \"Action\": \"ec2:DescribeInstances\", \"Resource\": \"*\"},
        {\"Effect\": \"Allow\", \"Action\": \"ssm:SendCommand\", \"Resource\": [\"$INSTANCE_ARN\", \"arn:aws:ssm:$REGION::document/AWS-RunShellScript\"]},
        {\"Effect\": \"Allow\", \"Action\": \"ssm:GetCommandInvocation\", \"Resource\": \"*\"},
        {\"Effect\": \"Allow\", \"Action\": \"lambda:InvokeFunction\", \"Resource\": \"arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNC\"},
        {\"Effect\": \"Allow\", \"Action\": [\"logs:CreateLogGroup\", \"logs:CreateLogStream\", \"logs:PutLogEvents\"], \"Resource\": \"*\"}
      ]
    }"
}

ENV_VARS="Variables={DISCORD_PUBLIC_KEY=$DISCORD_PUBLIC_KEY,DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN,DISCORD_APP_ID=$DISCORD_APP_ID,DISCORD_CHANNEL_ID=$DISCORD_CHANNEL_ID,INSTANCE_ID=$INSTANCE_ID,SV_PASSWORD=$SV_PASSWORD}"

deploy_lambda() {
  if "${AWS[@]}" lambda get-function --function-name "$FUNC" >/dev/null 2>&1; then
    "${AWS[@]}" lambda update-function-code --function-name "$FUNC" \
      --zip-file fileb://function.zip >/dev/null
    "${AWS[@]}" lambda wait function-updated --function-name "$FUNC"
    "${AWS[@]}" lambda update-function-configuration --function-name "$FUNC" \
      --timeout 420 --memory-size 512 --environment "$ENV_VARS" >/dev/null
  else
    "${AWS[@]}" lambda create-function --function-name "$FUNC" \
      --runtime python3.12 --handler lambda_function.lambda_handler \
      --role "arn:aws:iam::$ACCOUNT:role/$ROLE" \
      --zip-file fileb://function.zip \
      --timeout 420 --memory-size 512 --environment "$ENV_VARS" >/dev/null
  fi
  "${AWS[@]}" lambda wait function-active --function-name "$FUNC"
}

ensure_url() {
  if ! "${AWS[@]}" lambda get-function-url-config --function-name "$FUNC" >/dev/null 2>&1; then
    "${AWS[@]}" lambda create-function-url-config --function-name "$FUNC" \
      --auth-type NONE >/dev/null
    "${AWS[@]}" lambda add-permission --function-name "$FUNC" \
      --statement-id FunctionURLAllowPublicAccess \
      --action lambda:InvokeFunctionUrl --principal "*" \
      --function-url-auth-type NONE >/dev/null
  fi
}

ensure_schedule() {
  "${AWS[@]}" events put-rule --name "$RULE" --schedule-expression "rate(1 hour)" >/dev/null
  "${AWS[@]}" events put-targets --rule "$RULE" --targets \
    "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCOUNT:function:$FUNC\",\"Input\":\"{\\\"source\\\":\\\"hourly-check\\\"}\"}]" >/dev/null
  "${AWS[@]}" lambda add-permission --function-name "$FUNC" \
    --statement-id EventBridgeHourly --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:$REGION:$ACCOUNT:rule/$RULE" >/dev/null 2>&1 || true
}

onboard_instance() {
  local IROLE=csgo-server-ssm
  if ! "${AWS[@]}" iam get-role --role-name "$IROLE" >/dev/null 2>&1; then
    "${AWS[@]}" iam create-role --role-name "$IROLE" --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
    }' >/dev/null
    "${AWS[@]}" iam attach-role-policy --role-name "$IROLE" \
      --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
    "${AWS[@]}" iam create-instance-profile --instance-profile-name "$IROLE" >/dev/null
    "${AWS[@]}" iam add-role-to-instance-profile --instance-profile-name "$IROLE" --role-name "$IROLE"
    sleep 10
  fi
  if ! "${AWS[@]}" ec2 describe-iam-instance-profile-associations \
      --filters "Name=instance-id,Values=$INSTANCE_ID" \
      --query 'IamInstanceProfileAssociations[0]' --output text | grep -q "$IROLE"; then
    "${AWS[@]}" ec2 associate-iam-instance-profile --instance-id "$INSTANCE_ID" \
      --iam-instance-profile "Name=$IROLE" >/dev/null
  fi
  "${AWS[@]}" ec2 modify-instance-metadata-options --instance-id "$INSTANCE_ID" \
    --instance-metadata-tags enabled >/dev/null
  echo "Instance role and metadata tags configured."
  echo "Start the instance, wait for SSM to register, then run: $0 --install-boot-script"
}

install_boot_script() {
  local B64
  B64=$(base64 < server/csgo-boot.sh | tr -d '\n')
  "${AWS[@]}" ssm send-command --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --parameters "commands=[
      \"echo $B64 | base64 -d > /home/csgoserver/csgo-boot.sh\",
      \"chmod +x /home/csgoserver/csgo-boot.sh\",
      \"chown csgoserver:csgoserver /home/csgoserver/csgo-boot.sh\",
      \"sudo -u csgoserver bash -c 'crontab -l 2>/dev/null | grep -v csgo-boot; echo \\\"@reboot /home/csgoserver/csgo-boot.sh >> /home/csgoserver/boot.log 2>&1\\\"' | sudo -u csgoserver crontab -\"
    ]" \
    --query 'Command.CommandId' --output text
}

case "${1:-deploy}" in
  --onboard-instance) onboard_instance ;;
  --install-boot-script) install_boot_script ;;
  deploy)
    build_zip
    ensure_role
    deploy_lambda
    ensure_url
    ensure_schedule
    "${AWS[@]}" lambda get-function-url-config --function-name "$FUNC" \
      --query FunctionUrl --output text
    ;;
  *) echo "Usage: $0 [deploy | --onboard-instance | --install-boot-script]"; exit 1 ;;
esac
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n bot/deploy.sh && echo OK`
Expected: OK

Run: `chmod +x bot/deploy.sh bot/server/csgo-boot.sh`

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
git add bot/deploy.sh bot/.env.example .gitignore
git commit -m "feat: deploy script, env template, gitignore entries"
```

---

### Task 11: Documentation

**Files:**
- Create: `bot/README.md`
- Modify: `CLAUDE.md` (rewrite pending work item 2)

**Interfaces:**
- Consumes: everything above; documents the exact commands and flags from Tasks 9-10.

- [ ] **Step 1: Write bot/README.md**

Content requirements (write real prose, no placeholders): what the bot does (the four commands and the hourly reminder); Discord setup walkthrough (create app at discord.com/developers, copy Application ID and Public Key from General Information, create bot token under Bot, invite via OAuth2 URL generator with `bot` scope + Send Messages permission, enable Developer Mode in Discord to copy the guild and channel IDs); `cp .env.example .env` and fill in; `./deploy.sh` and paste the printed Function URL into Interactions Endpoint URL; `python3 register_commands.py`; `./deploy.sh --onboard-instance` then start the instance once and `./deploy.sh --install-boot-script`; costs (effectively zero); troubleshooting (Discord endpoint verification fails means wrong public key; commands not appearing means wrong guild ID; `/csgo map` failing means SSM agent not registered, check `aws ssm describe-instance-information`).

- [ ] **Step 2: Update CLAUDE.md pending work**

Replace item 2 of "Pending work" (the Lambda + Function URL + static page paragraph) with:
```markdown
2. Discord bot (branch `discord-bot`): serverless slash commands /csgo start|stop|status|map via Lambda Function URL as Discord Interactions Endpoint, hourly still-running reminder with Stop button via EventBridge, boot-time config via EC2 tags + IMDS, live map change via SSM. Design: docs/superpowers/specs/2026-08-27-discord-bot-design.md. Plan: docs/superpowers/plans/2026-08-27-discord-bot.md.
```

- [ ] **Step 3: Commit**

```bash
git add bot/README.md CLAUDE.md
git commit -m "docs: bot setup walkthrough and CLAUDE.md pending work update"
```

---

### Task 12: Live deployment and end-to-end verification

This task is manual/interactive and requires the user's Discord app credentials. Execute with the user present.

**Files:**
- Create: `bot/.env` (locally only, never committed)

- [ ] **Step 1: User creates the Discord application** (they follow bot/README.md; collect public key, app ID, bot token, guild ID, channel ID into `bot/.env`)

- [ ] **Step 2: Deploy**

Run: `bot/deploy.sh`
Expected: prints a Function URL. Then run `python3 -m pytest tests/ -v` one final time: all PASS.

- [ ] **Step 3: Wire Discord**

Paste the Function URL into the app's Interactions Endpoint URL field. Discord sends a signed PING; the field saves only if the Lambda answers PONG correctly.
Run: `python3 bot/register_commands.py`
Expected: `Registered 1 command(s).`

- [ ] **Step 4: Onboard the instance**

Run: `bot/deploy.sh --onboard-instance`
Then `/csgo start` in Discord (this both tests start and boots the instance for SSM registration).
Wait, then run: `aws ssm describe-instance-information --profile personal --region ap-south-1`
Expected: the instance listed with `PingStatus: Online`.
Run: `bot/deploy.sh --install-boot-script`

- [ ] **Step 5: Full command pass in Discord**

`/csgo status` shows state and players. `/csgo map de_dust2` changes the map (verify with `/csgo status`). `/csgo stop` stops. `/csgo start map:de_inferno mode:casual tickrate:128` starts with options; when the reply arrives with the IP, verify with `/csgo status` that the map is de_inferno; join from the game client to confirm. Wait for one hourly reminder (or trigger manually: `aws lambda invoke --function-name csgo-discord-bot --payload '{"source":"hourly-check"}' --cli-binary-format raw-in-base64-out --profile personal --region ap-south-1 /tmp/out.json`), press its Stop Server button, confirm the instance stops.

- [ ] **Step 6: Final commit and push**

```bash
git push -u origin discord-bot
```
