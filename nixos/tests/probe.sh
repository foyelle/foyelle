# Runs inside the probe-a connector unit (same template as every connector) and records what it
# can and cannot reach. The isolation test reads $STATE_DIRECTORY/results.json.
results="$STATE_DIRECTORY/results.json"
declare -A r

check() { # name, command... : "ok" when the command succeeds, "denied" when it fails
  local name=$1
  shift
  if "$@" >/dev/null 2>&1; then r[$name]=ok; else r[$name]=denied; fi
}

proxy_code() { # proxy url, target -> the proxy's CONNECT status ("000" when unreachable)
  curl -s -o /dev/null --max-time 10 -w '%{http_connect}' -x "$1" "$2" || true
}

check own_secret grep -qx secret-a "$CREDENTIALS_DIRECTORY/secret"
check other_secret cat /run/credentials/foyelle-connector-probe-b.service/secret
check other_state cat /var/lib/private/foyelle/connectors/probe-b/private
check harness_state ls /var/lib/foyelle/harness
if [ -z "$(ls -A /home 2>/dev/null)" ]; then r[home_empty]=ok; else r[home_empty]=denied; fi
check connector_port curl -s --max-time 5 --noproxy '*' http://127.0.0.1:7301/mcp
check gateway_port curl -s --max-time 5 --noproxy '*' http://127.0.0.1:9119/
code=$(proxy_code http://127.0.1.90:3128 https://allowed.example)
if [ "$code" != "403" ] && [ "$code" != "000" ]; then r[own_proxy_allowed_host]=ok; else r[own_proxy_allowed_host]="denied:$code"; fi
code=$(proxy_code http://127.0.1.90:3128 https://denied.example)
if [ "$code" = "403" ]; then r[own_proxy_denied_host]=denied; else r[own_proxy_denied_host]="ok:$code"; fi
code=$(proxy_code http://127.0.1.91:3128 https://other.example)
if [ "$code" = "000" ]; then r[other_proxy]=denied; else r[other_proxy]="ok:$code"; fi
check direct_internet curl -s --max-time 5 --noproxy '*' http://1.1.1.1/

{
  printf '{'
  first=1
  for key in "${!r[@]}"; do
    [ "$first" = 1 ] || printf ','
    first=0
    printf '"%s":"%s"' "$key" "${r[$key]}"
  done
  printf '}\n'
} > "$results.tmp"
mv "$results.tmp" "$results"
exec sleep infinity
