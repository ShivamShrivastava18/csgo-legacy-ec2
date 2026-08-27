#!/usr/bin/env bash
set -euo pipefail

GSLT="${1:-${GSLT:-}}"
SV_PASSWORD="${2:-${SV_PASSWORD:-}}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh <GSLT> [server_password]"
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "This server only runs on x86_64. Use a t3/c5/m5 instance, not Graviton (t4g/c7g)."
  exit 1
fi

if [[ -z "$GSLT" && -t 0 ]]; then
  read -rp "Paste your GSLT for App ID 4465480 (from steamcommunity.com/dev/managegameservers): " GSLT
fi
if [[ -z "$GSLT" ]]; then
  echo "A GSLT is required. Usage: sudo bash install.sh <GSLT> [server_password]"
  exit 1
fi
if [[ -z "$SV_PASSWORD" && -t 0 ]]; then
  read -rp "Set a server password (leave blank for a public server): " SV_PASSWORD
fi

echo "[1/8] Installing dependencies"
export DEBIAN_FRONTEND=noninteractive
add-apt-repository -y multiverse >/dev/null 2>&1 || true
dpkg --add-architecture i386
apt-get update -y
echo steam steam/question select "I AGREE" | debconf-set-selections
echo steam steam/license note '' | debconf-set-selections
apt-get install -y bc binutils bsdmainutils bzip2 ca-certificates cpio curl file gzip jq lib32gcc-s1 lib32stdc++6 libsdl2-2.0-0:i386 netcat-openbsd pigz tar tmux unzip util-linux wget xz-utils
apt-get install -y steamcmd || echo "Distro steamcmd unavailable, LinuxGSM will fetch its own copy."

echo "[2/8] Creating csgoserver user"
id csgoserver &>/dev/null || adduser --disabled-password --gecos "" csgoserver

echo "[3/8] Installing LinuxGSM"
sudo -u csgoserver -H bash -c '
  set -e
  cd "$HOME"
  if [[ ! -f linuxgsm.sh ]]; then
    wget -qO linuxgsm.sh https://linuxgsm.sh
    chmod +x linuxgsm.sh
  fi
  [[ -f csgoserver ]] || ./linuxgsm.sh csgoserver
'

echo "[4/8] Downloading server files via SteamCMD (~35GB, this takes a while)"
sudo -u csgoserver -H bash -c '
  set -e
  cd "$HOME"
  ACF=serverfiles/steamapps/appmanifest_740.acf
  if [[ ! -f "$ACF" ]]; then
    ./csgoserver auto-install
  elif ! grep -q "\"StateFlags\"[[:space:]]*\"4\"" "$ACF"; then
    echo "Previous download incomplete, resuming"
    ./csgoserver update
  fi
'

echo "[5/8] Patching App ID to 4465480"
SF=/home/csgoserver/serverfiles
echo "4465480" > "$SF/steam_appid.txt"
chown csgoserver:csgoserver "$SF/steam_appid.txt"
sed -i 's/^appID=.*/appID=4465480/' "$SF/csgo/steam.inf"

echo "[6/8] Installing MetaMod + SourceMod"
sudo -u csgoserver -H bash -c '
  set -e
  cd "$HOME/serverfiles/csgo"
  if [[ ! -f addons/metamod/bin/server.so || ! -f addons/sourcemod/scripting/spcomp ]]; then
    MM=$(curl -fsSL https://mms.alliedmods.net/mmsdrop/1.12/mmsource-latest-linux)
    SM=$(curl -fsSL https://sm.alliedmods.net/smdrop/1.12/sourcemod-latest-linux)
    wget -q "https://mms.alliedmods.net/mmsdrop/1.12/$MM"
    wget -q "https://sm.alliedmods.net/smdrop/1.12/$SM"
    tar -xzf "$MM"
    tar -xzf "$SM"
    rm -f "$MM" "$SM"
  fi
  cat > addons/metamod.vdf <<VDF
"Plugin"
{
  "file"  "../csgo/addons/metamod/bin/server"
}
VDF
  rm -f addons/metamod/bin/linux64/server.so
'

echo "[7/8] Installing NoLobbyReservation plugin"
sudo -u csgoserver -H bash -c '
  set -e
  cd "$HOME/serverfiles/csgo/addons/sourcemod"
  wget -qO nolobby.zip https://github.com/eldoradoel/NoLobbyReservation/archive/refs/heads/master.zip
  unzip -oq nolobby.zip
  cp NoLobbyReservation-master/csgo/addons/sourcemod/gamedata/nolobbyreservation.games.txt gamedata/
  cp NoLobbyReservation-master/csgo/addons/sourcemod/scripting/nolobbyreservation.sp scripting/NoLobbyReservation.sp
  cd scripting
  chmod +x spcomp
  ./spcomp NoLobbyReservation.sp -o ../plugins/NoLobbyReservation.smx
  cd ..
  rm -rf nolobby.zip NoLobbyReservation-master
'

echo "[8/8] Writing configuration"
LGSM_CFG=/home/csgoserver/lgsm/config-lgsm/csgoserver/csgoserver.cfg
touch "$LGSM_CFG"
chown csgoserver:csgoserver "$LGSM_CFG"
sed -i '/^gslt=/d' "$LGSM_CFG"
printf 'gslt="%s"\n' "$GSLT" >> "$LGSM_CFG"

SRV_CFG=/home/csgoserver/serverfiles/csgo/cfg/csgoserver.cfg
touch "$SRV_CFG"
chown csgoserver:csgoserver "$SRV_CFG"
if [[ -n "$SV_PASSWORD" ]]; then
  sed -i '/^sv_password/d' "$SRV_CFG"
  printf 'sv_password "%s"\n' "$SV_PASSWORD" >> "$SRV_CFG"
fi

echo "Starting server"
sudo -u csgoserver -H bash -c 'cd "$HOME" && ./csgoserver start'

IP=$(curl -fsSL https://checkip.amazonaws.com 2>/dev/null || echo "<your-instance-public-ip>")
echo ""
echo "=============================================="
echo " Done. Join from the CS:GO console:"
if [[ -n "$SV_PASSWORD" ]]; then
  echo "   connect $IP:27015; password $SV_PASSWORD"
else
  echo "   connect $IP:27015"
fi
echo ""
echo " Manage the server:"
echo "   sudo -u csgoserver -H /home/csgoserver/csgoserver details"
echo "   sudo -u csgoserver -H /home/csgoserver/csgoserver stop"
echo "   sudo -u csgoserver -H /home/csgoserver/csgoserver restart"
echo "   sudo -u csgoserver -H /home/csgoserver/csgoserver console"
echo "=============================================="
