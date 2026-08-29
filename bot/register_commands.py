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
