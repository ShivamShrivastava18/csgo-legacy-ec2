# Discord bot for CS:GO EC2 server control

Date: 2026-08-27. Branch: `discord-bot`. Status: approved design.

## Goal

Control the CS:GO server instance (`i-0ec3d2d9276b31cd5`, c5.large, ap-south-1, account 403141583853) from Discord, so friends can start it, get the join IP, and nobody forgets it running. Replaces the Lambda + static status page idea from CLAUDE.md pending work.

## Commands

| Command | Effect |
|---|---|
| `/csgo start [map] [mode] [tickrate]` | Tag instance with choices, start it, reply with `connect IP:27015; password ...` once srcds answers queries |
| `/csgo stop` | Stop the instance, confirm with hours it ran |
| `/csgo status` | State, IP, current map, player count |
| `/csgo map <name>` | Live map change on a running server via SSM |

Option choices, registered with the commands: maps de_dust2, de_mirage, de_inferno, de_nuke, de_train, de_overpass, de_vertigo, de_ancient, de_anubis, de_cache, cs_office, cs_italy; mode casual, competitive, deathmatch; tickrate 64, 128. Defaults when omitted: de_mirage, competitive, 64.

Anyone in the (private) Discord server can run commands. No role gating.

## Architecture

One Python 3.12 Lambda (`csgo-discord-bot`) with a Function URL, invoked three ways:

1. **Discord interaction** (HTTPS POST to the Function URL): slash commands and button presses. Every request is verified with Ed25519 against the Discord app public key before any processing; invalid signature returns 401. The Function URL itself has auth NONE; the signature is the auth. PING interactions (type 1) get PONG.
2. **EventBridge hourly** (`csgo-hourly-reminder`, rate 1 hour, payload `{"source": "hourly-check"}`): the reminder path.
3. **Async self-invocation**: slow work continues after the 3-second Discord deadline.

### /csgo start flow

1. Sync invocation: verify signature, write tags `csgo:map`, `csgo:mode`, `csgo:tickrate` on the instance, call `StartInstances`, async-invoke self with the interaction token, return deferred response (type 5) within 3 seconds.
2. Async invocation: poll until the instance is running and has a public IP, then poll A2S until srcds responds (timeout 5 minutes total), then PATCH `https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original` with the connect line. On timeout, PATCH an error with whatever state was reached.
3. Already running: reply immediately with the current IP, no restart.

### /csgo stop

`StopInstances`, reply with runtime computed from the instance LaunchTime. Already stopped: say so. The Stop button on reminder messages (`custom_id: csgo_stop`) routes to the same code; button interactions get a deferred update + followup if stopping is slow.

### /csgo map

Running check, then SSM SendCommand (AWS-RunShellScript) on the instance: `sudo -u csgoserver -H /home/csgoserver/csgoserver send "changelevel <map>"`. Fallback inside the same shell script if `send` is unavailable: discover the LinuxGSM tmux socket under `/tmp/tmux-1001/` and use `tmux -L <socket> send-keys`. Poll GetCommandInvocation, confirm in Discord. Server stopped: tell the user to `/csgo start` instead.

### Hourly reminder

If the instance is stopped: do nothing. If running: A2S query for map and player count, then POST to `DISCORD_CHANNEL_ID` via the bot token: "Still running for {h}h · {map} · {n} players · `connect {ip}:27015`" with a Stop Server button (components require the bot-token message API, which is why the reminder does not use a plain webhook).

### A2S

`bot/a2s.py` implements A2S_INFO with the challenge handshake (0x41 reply resend), 3-second timeout, returns map, player count, max players. Same protocol validated against the test instance on 2026-08-27. Lambda runs outside any VPC; outbound UDP to the instance public IP works.

## Server-side boot flow

The instance gets `instance-metadata-tags` enabled (one-time `modify-instance-metadata-options`). A boot script `/home/csgoserver/csgo-boot.sh` (from `bot/server/csgo-boot.sh`), run by `@reboot` in the csgoserver crontab:

1. Read `csgo:map`, `csgo:mode`, `csgo:tickrate` from IMDSv2 (`/latest/meta-data/tags/instance/...`). Missing tags fall back to defaults, so a console start without the bot still works.
2. Patch the LinuxGSM config (`lgsm/config-lgsm/csgoserver/csgoserver.cfg`): `defaultmap`, `tickrate`, and the gametype/gamemode pair for the mode (casual 0/0, competitive 0/1, deathmatch 1/2).
3. `/home/csgoserver/csgoserver start`.

No AWS credentials on the instance for this path; metadata tags are read-only.

## AWS resources

Created by `bot/deploy.sh` (bash + aws cli, idempotent, profile/region as env or flags):

- Lambda `csgo-discord-bot`, Python 3.12, 512 MB, timeout 420s (poll budget is 5 minutes; the extra headroom lets a timed-out start still PATCH its error message), Function URL auth NONE.
- Role `csgo-discord-bot-role`: `ec2:StartInstances`, `ec2:StopInstances`, `ec2:CreateTags` scoped to the instance ARN; `ec2:DescribeInstances` (unscoped, describe does not support resource ARNs); `ssm:SendCommand` scoped to the instance ARN and the AWS-RunShellScript document; `ssm:GetCommandInvocation`; `lambda:InvokeFunction` on itself; CloudWatch Logs.
- EventBridge rule `csgo-hourly-reminder` + lambda permission.
- Instance role/profile `csgo-server-ssm` with `AmazonSSMManagedInstanceCore`, associated to the instance.
- Deploy zip built with `pip install --platform manylinux2014_x86_64 --only-binary=:all:` for PyNaCl.

Lambda env vars: `DISCORD_PUBLIC_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_APP_ID`, `DISCORD_CHANNEL_ID`, `INSTANCE_ID`, `SV_PASSWORD` (shown in the connect line; empty means no password suffix).

Cost: Lambda and EventBridge free tier, no VPC, no NAT. Effectively zero.

## One-time setup

1. Discord developer portal: create application "CSGO Server", grab public key, app ID, bot token; invite bot with `bot` scope + Send Messages; pick the channel ID for reminders. Documented step by step in `bot/README.md`.
2. `bot/deploy.sh` to create AWS resources, prints the Function URL.
3. Paste the Function URL into the app's Interactions Endpoint URL field (Discord sends a PING to verify).
4. `bot/register_commands.py`: registers the `/csgo` command tree guild-scoped (instant availability) using the bot token and guild ID.
5. Instance onboarding, via SSM once the profile attaches (Ubuntu AMIs ship the SSM agent): copy `csgo-boot.sh`, add the `@reboot` crontab line, enable metadata tags. Scripted as `deploy.sh --onboard-instance`.

## Error handling

- Invalid signature: 401. Unknown command or custom_id: ephemeral "unknown command".
- Start when running / stop when stopped: friendly no-op replies.
- A2S timeout while instance runs: report "instance up, server still booting".
- SSM offline (agent not registered): report it and suggest checking onboarding.
- Discord API failures on followup: logged; EventBridge path retries next hour by design.

## Testing

- pytest for command routing, signature verification (known-key fixtures), option parsing, A2S packet parsing (captured real responses), and tag/config mapping. boto3 stubbed with botocore Stubber.
- Manual end to end: deploy, register to the real guild, run each command against the real instance, watch one hourly cycle, press the Stop button.

## Risks

- No SSH key for the c5.large exists on this machine; onboarding depends on the SSM agent being present in its Ubuntu install (default in the AMI). Fallback: one-boot user-data script while stopped.
- LinuxGSM `send` availability is assumed; tmux fallback is in the same SSM script.
- Discord bot token in Lambda env vars is encrypted at rest and acceptable for this threat model.

## Out of scope

Per-session passwords, auto-stop on empty server, any web UI, multi-instance support. CLAUDE.md pending work item 2 gets rewritten to this design when the branch merges.
