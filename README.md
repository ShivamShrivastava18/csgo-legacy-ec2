# csgo-legacy-ec2

One script to host a **CS:GO server on AWS EC2** for the 2026 standalone re-release (App ID 4465480).

In March 2026 Valve quietly re-released Counter-Strike: Global Offensive as a separate, unlisted Steam app. There is no official matchmaking and the community server browser is broken, so the only way to play with friends is `connect IP:PORT` to a community server. Hosting one is not straightforward because the dedicated server files still ship as the old App ID, Steam authentication needs a token for the new App ID, and the client's lobby reservation system points at a Valve backend that no longer exists.

This repo automates all of it on a fresh Ubuntu EC2 instance:

- LinuxGSM + SteamCMD install of the legacy dedicated server (app 740)
- App ID patch to 4465480 (`steam_appid.txt`, `steam.inf`)
- GSLT configuration for Steam auth and VAC
- MetaMod + SourceMod with the loader fixes this build needs
- NoLobbyReservation plugin, compiled from source, so clients can actually join
- Optional server password

## Requirements

- An AWS account
- A Game Server Login Token (GSLT): create one at [steamcommunity.com/dev/managegameservers](https://steamcommunity.com/dev/managegameservers) using App ID **4465480**
- The game itself: [store.steampowered.com/app/4465480](https://store.steampowered.com/app/4465480) (unlisted, add to library from the link)

## 1. Launch the instance

- **AMI:** Ubuntu Server 24.04 LTS (22.04 also works)
- **Instance type:** t3.medium is enough for a 10-12 player 64-tick server. Use c5.large for 128 tick. Must be **x86_64**, Graviton (t4g, c7g) will not work.
- **Storage:** 40 GB gp3 minimum. The server files alone are 33 GB, which leaves a 40 GB volume 94% full. It works, but pick 50 GB if you want headroom for logs, demos, or workshop maps.
- **Security group inbound rules:**

| Port  | Protocol | Purpose            | Source    |
|-------|----------|--------------------|-----------|
| 22    | TCP      | SSH                | Your IP   |
| 27015 | UDP      | Game (required)    | 0.0.0.0/0 |
| 27015 | TCP      | RCON (optional)    | Your IP   |
| 27020 | UDP      | SourceTV (optional)| 0.0.0.0/0 |

## 2. Install

SSH in as `ubuntu` and run:

```bash
wget https://raw.githubusercontent.com/ShivamShrivastava18/csgo-legacy-ec2/main/install.sh
sudo bash install.sh "YOUR_GSLT" "your_server_password"
```

Leave the password argument out for a public server. The SteamCMD download is ~35 GB, so expect the script to run for 30-45 minutes. It is safe to re-run, and a run that dies mid-download resumes where it left off.

## 3. Connect

Open the in-game console and run:

```
connect YOUR_INSTANCE_IP:27015; password your_server_password
```

## Managing the server

All management goes through LinuxGSM:

```bash
sudo -u csgoserver -H /home/csgoserver/csgoserver details
sudo -u csgoserver -H /home/csgoserver/csgoserver stop
sudo -u csgoserver -H /home/csgoserver/csgoserver restart
sudo -u csgoserver -H /home/csgoserver/csgoserver console
```

Detach from the console with `Ctrl+B` then `D`.

Server settings live in `/home/csgoserver/serverfiles/csgo/cfg/csgoserver.cfg` (hostname, password, rcon_password, sv_lan, etc).

**Do not run `update` or `validate`.** The game is frozen and will never update. Validating reverts the App ID patch and breaks joining. If you did it by accident, just re-run `install.sh`.

## Costs

Rough numbers for ap-south-1 (Mumbai), c5.large:

- Compute: $0.085/hr, so 2-3 hrs/day is about $5-8/month. Running 24/7 is ~$62/month.
- Storage: ~$3.50/month for 40 GB gp3, billed even while the instance is stopped.
- **Stop the instance when nobody is playing.** Compute billing stops, storage keeps everything intact.
- Stopping changes the public IP unless you attach an Elastic IP (~$3.65/month). Without one, just share the new IP each session, `csgoserver details` prints it.

## Troubleshooting

**"Restricted to LAN connections only"** on the client means Steam auth failed. Check the server log for `Could not establish connection to Steam servers` and verify your GSLT is valid and was created for App ID 4465480.

**Client stuck on "Retrying connection" with no error** means NoLobbyReservation is not loaded. Attach the console and run `meta list` and `sm plugins list`. You should see MetaMod, SourceMod, and "No Lobby Reservation". If `meta list` prints nothing, the metamod.vdf fix did not apply.

**Steam auth fails after a validate** can also be the bundled `bin/libgcc_s.so.1` conflicting with system libraries. Delete it and restart.

## How it works

The dedicated server downloaded via SteamCMD app 740 is the final legacy CS:GO build and is protocol-compatible with the 2026 client. Three things stand between it and a joinable server. First, it identifies as App ID 730/740, while the new client authenticates as 4465480, so both `steam_appid.txt` and `steam.inf` get patched. Second, internet servers must log into Steam with a GSLT or they fall back to LAN-only mode. Third, the client performs a lobby reservation handshake against Valve matchmaking before joining, and that backend is gone, so the server silently ignores direct connects. The NoLobbyReservation SourceMod plugin patches the reservation check out of the server binary at runtime, which is what makes plain `connect IP` work.

## Credits

- [eldoradoel/NoLobbyReservation](https://github.com/eldoradoel/NoLobbyReservation), the fork with working signatures for the 2026 build (original by [vanz666](https://github.com/vanz666/NoLobbyReservation))
- [LinuxGSM](https://linuxgsm.com)
- [AlliedModders](https://www.sourcemod.net) for MetaMod and SourceMod
- The [FNScence Steam guide](https://steamcommunity.com/sharedfiles/filedetails/?id=3680627349) and [osk4r8088's server repo](https://github.com/osk4r8088/csgo-community-server-r2026), which documented several of the gotchas

## License

MIT
