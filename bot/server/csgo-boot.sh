#!/usr/bin/env bash
set -u

CFG="${CSGO_CFG:-/home/csgoserver/lgsm/config-lgsm/csgoserver/csgoserver.cfg}"
[[ -f "$CFG" ]] || exit 1

imds_tag() {
  local token
  token=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 300") || return 1
  curl -sf -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/tags/instance/csgo:$1"
}

get_tag() {
  local value
  if [[ -n "${CSGO_TAG_CMD:-}" ]]; then
    value=$("$CSGO_TAG_CMD" "$1" 2>/dev/null)
  else
    value=$(imds_tag "$1")
  fi
  if [[ -n "$value" ]]; then
    echo "$value"
  else
    echo "$2"
  fi
}

MAP=$(get_tag map de_mirage)
MODE=$(get_tag mode competitive)
TICK=$(get_tag tickrate 64)

case "$MODE" in
  casual) GT=0; GM=0 ;;
  deathmatch) GT=1; GM=2 ;;
  *) GT=0; GM=1 ;;
esac

grep -vE '^(defaultmap|tickrate|gametype|gamemode)=' "$CFG" > "$CFG.tmp"
mv "$CFG.tmp" "$CFG"
{
  echo "defaultmap=\"$MAP\""
  echo "tickrate=\"$TICK\""
  echo "gametype=\"$GT\""
  echo "gamemode=\"$GM\""
} >> "$CFG"

if [[ "${CSGO_SKIP_START:-0}" != "1" ]]; then
  /home/csgoserver/csgoserver start
fi
