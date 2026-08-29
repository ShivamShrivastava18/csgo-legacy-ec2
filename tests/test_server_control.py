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


def stubbed_ssm():
    ssm = boto3.client("ssm", region_name="ap-south-1")
    return ssm, Stubber(ssm)


def test_change_map_sends_shell_command():
    ssm, stub = stubbed_ssm()
    stub.add_response(
        "send_command",
        {"Command": {"CommandId": "cmd-12345678-1234-1234-1234-123456789012"}},
        {
            "InstanceIds": [IID],
            "DocumentName": "AWS-RunShellScript",
            "Parameters": {"commands": [ANY]},
        },
    )
    with stub:
        cmd_id = server_control.change_map(ssm, IID, "de_inferno")
    assert cmd_id == "cmd-12345678-1234-1234-1234-123456789012"


def test_change_map_script_contains_map_and_fallback():
    script = server_control.CHANGE_MAP_SCRIPT.format(map="de_nuke")
    assert 'send "changelevel de_nuke"' in script
    assert "tmux" in script


def test_command_result():
    ssm, stub = stubbed_ssm()
    stub.add_response(
        "get_command_invocation",
        {"Status": "Success", "StandardOutputContent": "sent"},
        {"CommandId": "cmd-12345678-1234-1234-1234-123456789012", "InstanceId": IID},
    )
    with stub:
        status, out = server_control.command_result(ssm, IID, "cmd-12345678-1234-1234-1234-123456789012")
    assert status == "Success"
    assert out == "sent"
