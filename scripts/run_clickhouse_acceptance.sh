#!/usr/bin/env bash
set -euo pipefail

clickhouse=$1
shift
fixture="$(cd "$(dirname "${BASH_SOURCE[0]}")/../tests/fixtures/clickhouse" && pwd)"
work=$(mktemp -d)
port=$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
export DE_REAL_CLICKHOUSE_PORT=$port
export DE_CLICKHOUSE_DATA="$work/data/"
export DE_CLICKHOUSE_USERS="$fixture/users.xml"
export DE_REAL_DB_TESTS=1
export LITELLM_LOCAL_MODEL_COST_MAP=True
export RAG_EMBEDDING_PROVIDER=mock

"$clickhouse" server --config-file "$fixture/config.xml" >"$work/server.log" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' EXIT
url="http://127.0.0.1:$port/"
ready=false
for _ in {1..40}; do
    if curl --noproxy '*' --silent --fail --max-time 1 "${url}ping" >/dev/null; then
        ready=true
        break
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
done
if [[ $ready != true ]]; then
    cat "$work/server.log" >&2
    exit 1
fi
while IFS= read -r statement; do
    curl --noproxy '*' --silent --show-error --fail-with-body --max-time 10 \
        --user default:stage67-disposable-admin --data-binary "$statement" "$url"
done <"$fixture/seed.sql"
python -m pytest tests/real_db/test_clickhouse.py -q --strict-markers "$@"
