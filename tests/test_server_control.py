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
