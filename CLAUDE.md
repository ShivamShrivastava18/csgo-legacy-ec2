# CLAUDE.md

## Project

`csgo-legacy-ec2`: a one-script installer that sets up a dedicated server on AWS EC2 for the 2026 standalone CS:GO re-release (Steam App ID 4465480), plus a Medium article draft documenting the setup.

Valve re-released legacy CS:GO in March 2026 as an unlisted app. No official matchmaking exists, so players join community servers by IP. This repo automates hosting one.

## Files

- `install.sh`: the deliverable. Run as root on fresh Ubuntu 22.04/24.04 EC2, takes GSLT and optional server password as args. Installs LinuxGSM, downloads the server, applies all fixes, starts it.
- `README.md`: repo front page. EC2 launch specs, security group rules, costs, troubleshooting.
- `article.md`: Medium article draft (clean tutorial format). Not part of the public repo, remove or branch it before pushing.

## Current state

The full setup was validated manually, step by step, on a live c5.large in ap-south-1 and works end to end (client connects, VAC on, plugins loaded). install.sh was then tested on a fresh t3.medium in ap-south-1 on 2026-08-27 and passed fully: all 8 steps ran unattended, Steam auth and VAC came up, MetaMod/SourceMod/NoLobbyReservation loaded, the server answered A2S queries from the internet, and a real client joined from the standalone CS:GO client. The test instance was terminated afterward. The repo is ready to publish.

## Technical facts (verified, do not re-derive)

- The dedicated server from SteamCMD `app_update 740` is the final legacy CS:GO build and is protocol-compatible with the 4465480 client. Do not look for a different server app.
- Three fixes make it joinable:
  1. App ID patch: `serverfiles/steam_appid.txt` set to `4465480`, `serverfiles/csgo/steam.inf` line `appID=` set to `4465480`.
  2. GSLT created for App ID 4465480 (not 730) in the LinuxGSM config (`gslt="..."`), otherwise the server falls back to LAN-only mode.
  3. NoLobbyReservation SourceMod plugin. Without it clients get infinite silent "Retrying connection" because the client's lobby reservation handshake targets a dead Valve backend and the server ignores direct connects. Use the eldoradoel fork (github.com/eldoradoel/NoLobbyReservation), its gamedata signatures match the 2026 build. Compile the .sp with spcomp.
- MetaMod/SourceMod: use 1.12 Source 1 Linux builds from alliedmods. The `-latest-linux` URLs are pointer files containing the real tarball filename, not tarballs. Two silent loader failures must be fixed: rewrite `addons/metamod.vdf` to `"file" "../csgo/addons/metamod/bin/server"`, and delete `addons/metamod/bin/linux64/server.so` (engine prefers it but srcds is 32-bit).
- SteamCMD `validate`/`update` reverts the App ID patch and restores `bin/libgcc_s.so.1` (which can break Steam auth on some systems). The game never updates, so never run them. install.sh is safe to re-run to re-patch.
- x86_64 only, Graviton will not work. 40 GB disk minimum (server files are 33 GB, measured; a 40 GB volume ends 94% full, 50 GB is comfortable). SteamCMD reports the download as ~35 GB.
- Security group: UDP 27015 required, TCP 27015 for RCON, UDP 27020 SourceTV.

## Shipped

- Repo and article published (install.sh fresh-instance test passed 2026-08-27). gslt.txt and article.md excluded, see .gitignore.
- Discord bot in `bot/`: serverless slash commands /csgo start|stop|status|map via Lambda `csgo-discord-bot` Function URL as Discord Interactions Endpoint, hourly still-running reminder with Stop button via EventBridge rule `csgo-hourly-reminder`, boot-time config via EC2 tags + IMDS (`bot/server/csgo-boot.sh` on the instance, @reboot crontab), live map change via SSM. Live end-to-end validated 2026-08-29 (start/stop/status/map all worked from Discord). Design and plan under docs/superpowers/. Setup and redeploy: `bot/README.md`. Instance has role `csgo-server-ssm`; account secrets live only in Lambda env and `bot/.env` (gitignored).

## Live-deploy gotchas (verified 2026-08-29, already fixed in code)

- Discord's Cloudflare rejects Python's default urllib User-Agent with error 1010; `discord_api._request` sends a `DiscordBot (...)` User-Agent.
- A public Lambda Function URL (auth NONE) needs BOTH `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` granted to principal `*`, or Discord endpoint verification gets 403 AccessDeniedException. deploy.sh adds both and self-heals on rerun.
- Slow AWS work must stay off the sync interaction path: cmd_start/cmd_map return the type-5 ack after one describe + one self-invoke; StartInstances and SSM SendCommand run in the async finishers, or the 3-second Discord deadline is missed from ap-south-1.

## Pending work

- None open. Possible later addition: scheduled auto-stop Lambda when the server is empty (spec explicitly deferred it).

## Style

- No em dashes anywhere, including the article.
- Plain direct language, no filler. Code without comments.
- Article voice: first person, practical, no AI-sounding phrasing.
