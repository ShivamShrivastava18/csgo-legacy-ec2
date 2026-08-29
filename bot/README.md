# csgo-discord-bot

A Discord slash-command bot that starts, stops, and manages the CS:GO EC2 instance from `csgo-legacy-ec2`, so friends can spin the server up and down without touching AWS.

It runs as a single Lambda function behind a Function URL, which Discord calls directly as the app's Interactions Endpoint. No server to host, no polling gateway connection.

## Commands

| Command | Description |
|---|---|
| `/csgo start [map] [mode] [tickrate]` | Starts the instance. `map`, `mode` (casual, competitive, deathmatch), and `tickrate` (64, 128) are optional dropdowns; defaults are `de_mirage`, `competitive`, `64`. Replies once the instance is up and srcds is answering, with a ready-to-paste `connect` line. |
| `/csgo stop` | Stops the instance and reports how long it ran this session. |
| `/csgo status` | Shows current state, map, and player count if the server is running. |
| `/csgo map <name>` | Changes the map on the running server via SSM, no restart needed. |

Every hour, EventBridge triggers the bot to post a reminder to the configured channel if the instance is still running: current map, player count, and a **Stop Server** button. This is what keeps the instance from being left running (and billing) after everyone logs off.

## Prerequisites

- The EC2 instance from the root of this repo, already set up with `install.sh`.
- AWS CLI installed and configured with a profile that has permission to create IAM roles, Lambda functions, EventBridge rules, and SSM commands.
- Python 3.12 and `pip3` locally (used to build the Lambda deployment zip).
- A Discord account and a server (guild) you can add bots to.

## 1. Create the Discord app

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and click **New Application**. Name it whatever you want, e.g. "CS:GO Server".
2. On the **General Information** page, copy the **Application ID** and the **Public Key**. You'll need both for `.env`.
3. Open the **Bot** tab. Click **Reset Token** (or **Copy** if a token is already shown) to get the bot token. Save it, Discord only shows it once.
4. Under the same **Bot** tab, make sure **Public Bot** is off if you don't want strangers adding it to their own servers.
5. Open the **OAuth2 → URL Generator** tab. Under **Scopes**, check `bot`. Under **Bot Permissions**, check **Send Messages** (add **Use Slash Commands** too if it's not implied). Copy the generated URL at the bottom, open it in a browser, and add the bot to your server.
6. In Discord itself, go to **User Settings → Advanced** and turn on **Developer Mode**. This lets you right-click any server or channel and choose **Copy ID**.
7. Right-click your server's icon and **Copy Server ID**, this is `DISCORD_GUILD_ID`.
8. Right-click the channel where you want the hourly reminders posted and **Copy Channel ID**, this is `DISCORD_CHANNEL_ID`.

## 2. Configure

```bash
cd bot
cp .env.example .env
```

Fill in `.env`:

- `DISCORD_PUBLIC_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_APP_ID` from step 1.
- `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID` from step 1.
- `INSTANCE_ID`: the EC2 instance ID of your CS:GO server.
- `SV_PASSWORD`: only if the server has one set, it gets appended to the `connect` lines the bot prints.
- `AWS_PROFILE`, `AWS_REGION`: the profile and region to deploy into.

## 3. Deploy the Lambda

```bash
./deploy.sh
```

This builds the deployment zip, creates the IAM role and Lambda function (or updates them if they already exist), creates the Function URL, and sets up the hourly EventBridge rule. It prints the Function URL at the end.

Copy that URL and paste it into the Discord app's **General Information → Interactions Endpoint URL** field, then click **Save Changes**. Discord immediately sends a signed PING to the URL to verify it. If the bot's signature check passes, Discord accepts the endpoint. If it fails, double check `DISCORD_PUBLIC_KEY` in `.env` matches the Public Key on the General Information page and redeploy.

## 4. Register the slash commands

```bash
python3 register_commands.py
```

This registers `/csgo` and its subcommands as guild commands for `DISCORD_GUILD_ID`. Guild commands show up immediately, unlike global commands which can take up to an hour to propagate. Re-run this any time the command definitions in `register_commands.py` change.

## 5. Onboard the instance

The bot changes maps over SSM and reads the boot-time map/mode/tickrate from EC2 tags, both of which need the instance to have an IAM role with SSM access and IMDS tag access enabled. Set that up once:

```bash
./deploy.sh --onboard-instance
```

This creates an instance profile with the `AmazonSSMManagedInstanceCore` policy, attaches it to the instance, and enables instance metadata tags.

Start the instance once so the SSM agent registers with Systems Manager:

```bash
aws ec2 start-instances --instance-ids <INSTANCE_ID> --profile personal --region ap-south-1
```

Wait a minute or two, then confirm the agent is online:

```bash
aws ssm describe-instance-information --profile personal --region ap-south-1
```

Once the instance shows up in that output, install the boot script that reads the `csgo:map`, `csgo:mode`, and `csgo:tickrate` tags on startup and writes them into the LinuxGSM config before launching srcds:

```bash
./deploy.sh --install-boot-script
```

From here, `/csgo start map mode tickrate` sets the tags before starting the instance, and the boot script picks them up on every boot.

## Costs

The bot itself costs effectively nothing. Lambda's free tier covers this workload many times over (a handful of invocations per session plus 24 hourly checks a day), and EventBridge's rate-based rule is also free tier. The only cost is the EC2 instance itself, covered in the root README.

## Troubleshooting

**Discord endpoint verification fails when saving the Interactions Endpoint URL.** `DISCORD_PUBLIC_KEY` in `.env` doesn't match the app's actual Public Key, or the Lambda wasn't redeployed after changing it. Check the value on the General Information page, fix `.env`, and run `./deploy.sh` again.

**Slash commands don't show up in Discord.** Either `DISCORD_GUILD_ID` in `.env` is wrong (commands were registered to the wrong server) or `register_commands.py` hasn't been run since the bot was added. Re-check the guild ID and re-run `python3 register_commands.py`. It can also take a minute for Discord's client to refresh the command list, try restarting Discord.

**`/csgo map` fails or times out.** The SSM agent on the instance isn't registered. Run `aws ssm describe-instance-information --profile personal --region ap-south-1` and confirm the instance appears; if it doesn't, the instance profile from `./deploy.sh --onboard-instance` may not be attached, or the instance needs a reboot to pick it up.

**The hourly reminder never posts.** `DISCORD_CHANNEL_ID` in `.env` is wrong, or the bot doesn't have Send Messages permission in that specific channel (channel-level permission overrides can block a bot even if the server-wide invite granted it). Check the channel's permissions for the bot's role.
