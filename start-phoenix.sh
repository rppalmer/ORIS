#!/bin/sh

set -eu

project_root=$(CDPATH= cd "$(dirname "$0")" && pwd)
# Honour an exported PHOENIX_WORKING_DIR: ORIS reads the same variable to find
# the traces, and the two pointing at different directories is a silent failure.
# The fallback must stay in step with `ORIS_HOME` in src/oris/config.py for the
# same reason — this is the one place the default is written twice.
trace_directory="${PHOENIX_WORKING_DIR:-$HOME/.oris/traces/phoenix}"

mkdir -p "$trace_directory"
cd "$project_root"

exec env \
    PHOENIX_HOST=127.0.0.1 \
    PHOENIX_PORT=6006 \
    PHOENIX_WORKING_DIR="$trace_directory" \
    PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=14 \
    PHOENIX_TELEMETRY_ENABLED=false \
    PHOENIX_ALLOW_EXTERNAL_RESOURCES=false \
    PHOENIX_ENABLE_MCP_SERVER=false \
    PHOENIX_ALLOWED_PROVIDERS=NONE \
    PHOENIX_ALLOWED_SANDBOX_PROVIDERS=NONE \
    uvx --from "arize-phoenix==19.6.0" phoenix serve
