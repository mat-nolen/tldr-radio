#!/usr/bin/env bash
# Preflight for TLDR Radio. Everything checked here is something that otherwise fails
# *after* a ~5 GB image pull, as a wall of compose output. Read-only: changes nothing.
set -uo pipefail

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
fail=0; warn=0

ok()    { printf "  ${GREEN}✓${OFF} %s\n" "$1"; }
bad()   { printf "  ${RED}✗${OFF} %s\n     ${DIM}%s${OFF}\n" "$1" "$2"; fail=$((fail + 1)); }
note()  { printf "  ${YELLOW}!${OFF} %s\n     ${DIM}%s${OFF}\n" "$1" "$2"; warn=$((warn + 1)); }

echo ""
echo "TLDR Radio — preflight"
echo ""

# --- Docker ---------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  bad "Docker not found" "Install Docker Desktop: https://docs.docker.com/get-docker/"
elif ! docker info >/dev/null 2>&1; then
  bad "Docker is installed but not running" "Start Docker Desktop and re-run this."
else
  ok "Docker is running ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'))"

  # Compose v2 — `docker-compose` v1 is EOL and does not understand this file.
  if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose v2 ($(docker compose version --short 2>/dev/null || echo '?'))"
  else
    bad "Docker Compose v2 not available" \
        "This project needs 'docker compose' (v2), not the old 'docker-compose'. Update Docker Desktop."
  fi
fi

# --- Disk -----------------------------------------------------------------
# The Kokoro image alone is ~5 GB; episodes and cached pages land in ./data.
need_gb=8
avail_gb=$(df -Pk . 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')
if [ -z "${avail_gb:-}" ]; then
  note "Could not measure free disk space" "Make sure you have ~${need_gb} GB free."
elif [ "$avail_gb" -lt "$need_gb" ]; then
  bad "Only ${avail_gb} GB free here" "Need ~${need_gb} GB: the TTS image is ~5 GB, plus audio and cache."
else
  ok "Disk space: ${avail_gb} GB free (need ~${need_gb} GB)"
fi

# --- Port -----------------------------------------------------------------
port=${APP_PORT:-7777}
if command -v lsof >/dev/null 2>&1 && lsof -i:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  bad "Port ${port} is already in use" \
      "Stop whatever is on it, or set APP_PORT and update the ports: mapping in docker-compose.yml."
else
  ok "Port ${port} is free"
fi

# --- Config ---------------------------------------------------------------
# Not required: docker-compose.yml supplies a default for every variable.
if [ -f .env ]; then
  ok ".env found"
else
  note "No .env file (that's fine)" \
       "It boots on defaults. Copy .env.example to .env to set TZ or the overnight schedule."
fi

if [ -n "${TZ:-}" ] || grep -qE '^TZ=.+' .env 2>/dev/null; then
  ok "Timezone configured"
else
  note "TZ not set — the overnight broadcast would use UTC" \
       "Set TZ in .env (e.g. America/Chicago) if you plan to use AUTO_BROADCAST_TIME."
fi

echo ""
if [ "$fail" -gt 0 ]; then
  printf "${RED}%d problem(s) to fix before 'make up'.${OFF}\n\n" "$fail"
  exit 1
fi
if [ "$warn" -gt 0 ]; then
  printf "${GREEN}Good to go${OFF} (%d note(s) above — none blocking).\n" "$warn"
else
  printf "${GREEN}Good to go.${OFF}\n"
fi
echo ""
echo "  Next:  make up      # first run pulls ~5 GB and warms the model — allow 5-20 min"
echo "         then open http://localhost:${port}"
echo ""
