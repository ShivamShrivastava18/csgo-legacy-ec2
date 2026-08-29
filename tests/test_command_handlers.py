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
