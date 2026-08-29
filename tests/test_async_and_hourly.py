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
    monkeypatch.setattr(lambda_function.server_control, "start_with_tags", lambda ec2, iid, m, mo, t: None)
    monkeypatch.setattr(lambda_function.server_control, "change_map", lambda ssm, iid, m: "cmd-1")
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
    result = lambda_function.finish_start(
        {"task": "finish_start", "token": "tok", "map": "de_dust2", "mode": "casual", "tickrate": "128"}
    )
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
    result = lambda_function.finish_start(
        {"task": "finish_start", "token": "tok", "map": "de_dust2", "mode": "competitive", "tickrate": "64"}
    )
    assert result["ok"] is False
    assert "not answering" in patched[0]


def test_finish_map_success(monkeypatch):
    patched, _ = wire(
        monkeypatch,
        statuses=[],
        a2s_results=[],
        command_results=[("InProgress", ""), ("Success", "sent")],
    )
    result = lambda_function.finish_map({"task": "finish_map", "token": "tok", "map": "de_nuke"})
    assert result["ok"] is True
    assert "de_nuke" in patched[0]


def test_finish_map_failure(monkeypatch):
    patched, _ = wire(
        monkeypatch,
        statuses=[],
        a2s_results=[],
        command_results=[("Failed", "boom")],
    )
    result = lambda_function.finish_map({"task": "finish_map", "token": "tok", "map": "de_nuke"})
    assert result["ok"] is False
    assert "failed" in patched[0].lower()


def test_finish_map_reports_ssm_offline(monkeypatch):
    from botocore.exceptions import ClientError

    patched, _ = wire(monkeypatch, statuses=[], a2s_results=[])

    def raise_client_error(ssm, iid, m):
        raise ClientError({"Error": {"Code": "InvalidInstanceId"}}, "SendCommand")

    monkeypatch.setattr(lambda_function.server_control, "change_map", raise_client_error)
    result = lambda_function.finish_map({"task": "finish_map", "token": "tok", "map": "de_nuke"})
    assert result["ok"] is False
    assert "SSM" in patched[0]
    assert "onboard" in patched[0]


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
