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
