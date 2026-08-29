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
