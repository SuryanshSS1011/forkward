#!/usr/bin/env bash
# Ship the committed tree to the deploy host, build the frontend, and restart every service.
# Usage: deploy/deploy.sh user@HOST [https://your-domain] [--runs] [ssh-key]
#   https://your-domain  the public origin Caddy serves; without it, Caddy uses <ip>.sslip.io
#   --runs               also sync run logs for the board, without attempts.jsonl (full prompts stay local)
# Works on any Linux host with systemd, Caddy, uv and Node: the units are installed for whichever
# user the SSH login is, so a Vultr, EC2 or other VM only differs in the login you pass.
set -euo pipefail
host="$1"; shift
sync_runs=0
domain=""
key="$HOME/.ssh/deploy.pem"
for a in "$@"; do
  case "$a" in
    --runs) sync_runs=1 ;;
    https://*) domain="${a#https://}"; domain="${domain%%/*}" ;;
    *) key="$a" ;;
  esac
done
if [ -n "$domain" ] && ! [[ "$domain" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Not a hostname: $domain" >&2; exit 1
fi
# The host key must already be in known_hosts (CI writes it from the DEPLOY_KNOWN_HOSTS secret).
# Keepalives make a dropped connection fail within a minute instead of hanging the deploy.
ssh_() { ssh -i "$key" -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=yes "$host" "$@"; }
# Upload the archive as a file: a long stream piped through ssh stalled on unreliable networks.
tarball=$(mktemp -t forkward.XXXXXX)
trap 'rm -f "$tarball"' EXIT
git archive HEAD -o "$tarball"
scp -q -i "$key" -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -o StrictHostKeyChecking=yes "$tarball" "$host:forkward.tar"
ssh_ 'rm -rf ~/forkward.new && mkdir -p ~/forkward.new && tar -x -C ~/forkward.new -f ~/forkward.tar && rm ~/forkward.tar'
ssh_ 'set -e
  [ -f ~/forkward/.env ] && cp ~/forkward/.env ~/forkward.new/.env
  [ -d ~/forkward/runs ] && mv ~/forkward/runs ~/forkward.new/runs || mkdir -p ~/forkward.new/runs
  [ -d ~/forkward/frontend/node_modules ] && mv ~/forkward/frontend/node_modules ~/forkward.new/frontend/node_modules || true
  rm -rf ~/forkward.old; [ -d ~/forkward ] && mv ~/forkward ~/forkward.old; mv ~/forkward.new ~/forkward
  [ -f ~/forkward/.env ] || { echo "Create ~/forkward/.env from .env.example first" >&2; exit 1; }
  cd ~/forkward && ~/.local/bin/uv sync --frozen --no-dev -q
  set -a; . ./.env; set +a
  if [ -n "${DATABASE_URL:-}" ]; then ~/.local/bin/uv run --frozen --no-dev python -m forkward db migrate; fi'
if [ "$sync_runs" = 1 ]; then
  # tar instead of rsync: macOS ships an rsync whose filter protocol the server's rsync rejects.
  find runs -type f \( -name '*.jsonl' -o -name '*.json' \) ! -name attempts.jsonl -print \
    | tar -czf - -T - | ssh_ 'mkdir -p ~/forkward/runs && tar -xzf - -C ~/forkward'
fi
# DOMAIN is checked against a hostname pattern above, so it is safe to splice into the command.
ssh_ "DOMAIN='$domain'; "'set -e
  grep -q "^BETTER_AUTH_SECRET=." ~/forkward/.env || { echo "Set BETTER_AUTH_SECRET in ~/forkward/.env" >&2; exit 1; }
  cd ~/forkward/frontend
  npm ci --no-audit --no-fund --ignore-scripts >/dev/null
  NEXT_TELEMETRY_DISABLED=1 npm run build >/dev/null
  # Units are templates: __USER__ and __HOME__ become the login this script connected as.
  for u in forkward-mcp.service forkward-board.service forkward-web.service forkward-worker.service \
           forkward-billing.service forkward-billing.timer; do
    sed "s#__HOME__#$HOME#g; s#__USER__#$(id -un)#g" ~/forkward/deploy/$u | sudo tee /etc/systemd/system/$u >/dev/null
  done
  sudo install -m 644 ~/forkward/deploy/caddy.service /etc/systemd/system/caddy.service
  mkdir -p ~/.forkward
  site="${DOMAIN:-$(curl -fsS https://checkip.amazonaws.com | tr . -).sslip.io}"
  sudo sed "s/{\$HOSTNAME}/$site/" ~/forkward/deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable -q forkward-mcp forkward-board forkward-web forkward-worker caddy
  if grep -q "^STRIPE_SECRET_KEY=." ~/forkward/.env; then sudo systemctl enable -q --now forkward-billing.timer; fi
  sudo systemctl restart forkward-mcp forkward-board forkward-web forkward-worker caddy
  sleep 5 && systemctl is-active forkward-mcp forkward-board forkward-web forkward-worker caddy'
