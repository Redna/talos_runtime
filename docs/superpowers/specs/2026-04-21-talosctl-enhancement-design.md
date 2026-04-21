# Design: talosctl Operations Enhancement

**Date:** 2026-04-21
**Scope:** Add `pause`, `resume`, `step`, `events`, and `reset` commands to `talosctl`.

## Motivation
Users currently need low-level `docker` commands, `curl` API calls, and manual file manipulation to control the Talos agent's lifecycle in step mode. A unified CLI (`talosctl`) should hide these details.

## Architecture

### Command Routing
- `pause`, `resume`, `step` → **X-Ray REST API** (`POST /api/command`)
- `events` → **Direct Docker** (`docker exec talos_agent tail /spine/events/...`)
- `reset` → **Direct Docker Compose** (`docker compose down/up --build`)

### Why Hybrid?
- **API-first** for commands that modify agent state. This ensures X-Ray is the single source of truth and avoids sentinel-file race conditions.
- **Direct Docker** for read-only / infrastructure operations. `events` is a simple log tail; building a streaming endpoint in X-Ray would be overkill.

## Subcommands

### `talosctl pause`
- **Action:** POST `{"command":"pause"}` to `http://localhost:4040/api/command`
- **Output:** `Agent paused.` or `Error: X-Ray unreachable at localhost:4040`
- **Guard:** None (idempotent)

### `talosctl resume`
- **Action:** POST `{"command":"resume"}` to `http://localhost:4040/api/command`
- **Output:** `Agent resumed.` or `Error: X-Ray unreachable`
- **Guard:** None (idempotent)

### `talosctl step`
- **Action:** POST `{"command":"step"}` to `http://localhost:4040/api/command`
- **Output:** `Step triggered. Turn will advance when gate returns.` or `Error: X-Ray unreachable`
- **Guard:** None. The gate latency is variable; the CLI does not wait.

### `talosctl events [--tail N]`
- **Action:**
  1. `docker exec talos_agent ls -t /spine/events/*.jsonl`
  2. `docker exec talos_agent tail -n N /spine/events/<latest>`
- **Default:** `--tail 50`
- **Output:** Pretty-printed JSONL (one event per line) or streaming if `-f` flag added
- **Guard:** Warn if container not running

### `talosctl reset [--hard]`
- **Action:**
  1. `docker compose down`
  2. Wipe `spine_observability` volume
  3. `rm -rf ./xray_data ./llm_logs`
  4. `docker compose up -d --build`
  5. With `--hard`: also `rm -rf ./memory/*`
- **Guard:** If `--hard` not given and `./memory/` is non-empty, prompt: `"Memory dir contains files. Use --hard to wipe, or --preserve to keep."`
- **Output:** `Reset complete. Agent started from turn 0.`

## Dependencies
- X-Ray container must be healthy on port 4040 for `pause`/`resume`/`step`
- Docker daemon must be reachable for `events` and `reset`
- `docker compose` must be available for `reset`

## Error Handling
- Any subprocess failure prints stderr and exits non-zero
- API failures (X-Ray down) are treated as non-fatal warnings for `events`, fatal for `pause`/`resume`/`step`

## Future Work
- `talosctl focus "new objective"` (inject focus via API)
- `talosctl push` (commit and push `feat/talos`)
- `talosctl logs --service gate` (filtered log tailing)
