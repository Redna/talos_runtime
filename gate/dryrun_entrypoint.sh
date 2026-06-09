#!/bin/bash
set -e

# Dry-run gate entrypoint. Reads DRYRUN_SCENARIO (happy | crash | stall)
# and DRYRUN_SCRIPT (override path). Defaults to the happy script.

SCENARIO="${DRYRUN_SCENARIO:-happy}"
SCRIPT_FILE="/gate/dryrun_script_${SCENARIO}.json"
if [ -n "${DRYRUN_SCRIPT:-}" ]; then
    SCRIPT_FILE="${DRYRUN_SCRIPT}"
fi
export DRYRUN_SCRIPT="$SCRIPT_FILE"
export DRYRUN_SCENARIO="$SCENARIO"
export DRYRUN_LOG="/gate/dryrun_log.jsonl"

echo "[Dry-Run Gate] scenario=$SCENARIO script=$SCRIPT_FILE"
exec python dryrun_app.py
