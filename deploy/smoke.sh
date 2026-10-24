#!/usr/bin/env bash
# Check that the deployed site, OAuth discovery, the private board API and the MCP endpoint answer
# over HTTPS as expected.
# Usage: deploy/smoke.sh https://HOST
set -euo pipefail
base="${1%/}"
fail=0
# check <expected status> <curl args...>
check() {
  want="$1"; shift
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$@") || code=000
  echo "$code ${*: -1}"
  [ "$code" = "$want" ] || fail=1
}
check 200 "$base/"
check 200 "$base/.well-known/oauth-authorization-server"
check 200 "$base/.well-known/oauth-protected-resource/mcp"
check 200 "$base/api/auth/jwks"
# Customer data needs a session: signed out, the repositories API answers 401.
check 401 "$base/api/repos"
# The MCP endpoint needs a bearer token; without one it answers 401 and points to OAuth discovery.
check 401 -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  "$base/mcp"
exit $fail
