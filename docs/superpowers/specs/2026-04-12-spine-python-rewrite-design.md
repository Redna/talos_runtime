# Spine Python Rewrite Design

**Date:** 2026-04-12
**Status:** Draft
**Scope:** Rewrite the Spine from Go to Python, moving it into the `talos/` repository alongside Cortex. Add P10 (Stream Integrity) to Constitution. Update watchdog Lazarus for targeted spine reverts.

---

## 1. Problem Statement

The Spine is written in Go, which creates two problems:

1. **Accessibility.** The Spine codebase is unreadable to the operator who maintains Talos. Go knowledge is required to understand, modify, or debug the system's most critical component — the process supervisor and stream manager that keeps the agent alive.

2. **Architectural inconsistency.** The Spine is part of the agent (it manages the LLM stream, enforces the constitution, and supervises the Cortex), but it lives outside the agent's repository. P2 (Self-Creation) says the agent can restructure its own code, yet the brainstem is in a different language and repo.

Additionally, the current architecture has no mechanism for the agent to evolve the Spine, and the Lazarus Protocol reverts the entire `/app/` workspace on failure — indiscriminately wiping both cortex and spine commits.

## 2. Design Principles

1. **One language for the agent.** Spine + Cortex are both Python, both in `talos/`. The operator needs only Python to understand the full system.

2. **Process isolation preserved.** Spine and Cortex are separate Python processes. Spine starts first (as root), Cortex starts second (as `talos` user). They communicate via the same Unix domain socket JSON-RPC protocol.

3. **The Spine source is evolvable.** The agent can read and modify `/app/spine/`. Safety comes from the pre-commit hook (non-bypassable via `bash_command`) and Lazarus Protocol (targeted reverts), not from file permissions.

4. **IPC protocol unchanged.** `spine_client.py` in Cortex stays exactly the same. The rewrite is transparent to the Cortex.

5. **Stable version tracking.** The system tracks a `last_stable_commit` SHA — promoted from candidate only after the spine processes a `think()` request successfully. Reverts use `git checkout <sha> -- spine/`, preserving cortex commit history.

6. **P10: Stream Integrity.** The conversation stream is an immutable, append-only ledger. The frozen prefix must never change between requests. Dynamic data is piggybacked onto the last message.

## 3. Repository Architecture

### New files: `talos/spine/`

```
talos/spine/
  __init__.py
  main.py               ← asyncio entry point
  config.py              ← Load spine_config.json (dataclass)
  ipc_server.py          ← asyncio Unix socket JSON-RPC server
  ipc_types.py           ← Request/response dataclasses
  stream.py              ← Message stream, shedding, folding, HUD, Gate client
  constitution.py        ← Constitution + identity loading, SHA-256 hash tracking
  supervisor.py          ← Cortex subprocess lifecycle + Lazarus Protocol
  health.py              ← Stall/startup-failure detection
  events.py              ← JSONL event logger
  snapshot.py            ← State snapshot save/restore
  control_plane.py       ← aiohttp HTTP server on :4001
  telegram.py            ← Outbound Telegram notifications (httpx)
```

### New test files: `talos/tests/spine/`

```
talos/tests/spine/
  test_config.py
  test_ipc_server.py
  test_stream.py
  test_constitution.py
  test_supervisor.py
  test_health.py
  test_events.py
  test_snapshot.py
  test_control_plane.py
```

### Modified files

| File | Change |
|---|---|
| `entrypoint.sh` | Spine start: `/usr/local/bin/spine` → `python -m spine` |
| `Dockerfile` | Remove Go build stage; spine installs via uv |
| `talos/pyproject.toml` | Add `aiohttp` dependency |
| `scripts/setup_hooks.sh` | Write `last_candidate_commit` after hook passes |
| `talosctl` | Spine-specific Lazarus: `git checkout <sha> -- spine/` + spine health detection branching |

### Deleted files

| Item | Reason |
|---|---|
| `talos_runtime/spine/` (entire Go codebase) | Migrated to `talos/spine/` (Python) |
| `spine/Dockerfile` | Go build stage no longer needed |

### Unchanged files

| File | Reason |
|---|---|
| `talos/cortex/spine_client.py` | IPC protocol unchanged |
| `spine_config.json` | Same format, same fields |
| `gate/` | Infrastructure, not affected |
| `docker-compose.yml` | Spine still runs in-agent |

## 4. Process Architecture

```
entrypoint.sh (runs as root):
  1. Create directories /spine/events, /spine/snapshots, /spine/crashes
  2. Start Spine: python -m spine /spine/spine_config.json &
  3. Wait for /tmp/spine.sock (30s timeout)
  4. Write current HEAD to /spine/last_candidate_commit
  5. exec gosu talos python seed_agent.py

talos container (two processes):
  ├── Spine (Python asyncio, root-owned PID)
  │     Listens on /tmp/spine.sock (JSON-RPC)
  │     Runs HTTP control plane on :4001
  │     Supervises Cortex process (start, watch, restart)
  │     Watches /app/CONSTITUTION.md and /app/identity.md for changes
  │     On first successful think(): promote candidate → stable
  │
  └── Cortex (Python, talos user, supervised by Spine)
        seed_agent.py
        spine_client.py → IPC via /tmp/spine.sock
```

## 5. IPC Protocol (Unchanged)

Same JSON-RPC over Unix domain socket. Same methods. Same request/response types. `spine_client.py` requires zero changes.

| Method | Direction | Purpose |
|---|---|---|
| `think` | Cortex → Spine | LLM call with focus, tools, HUD |
| `tool_result` | Cortex → Spine | Return tool execution result |
| `request_fold` | Cortex → Spine | Request context compression |
| `request_restart` | Cortex → Spine | Restart Cortex process |
| `send_message` | Cortex → Spine | Telegram notification |
| `emit_event` | Cortex → Spine | Log custom event |
| `get_state` | Cortex → Spine | Query Spine's state |

## 6. Stable Version Tracking

### Two tracking files

| File | Location | Updated When |
|---|---|---|
| `last_stable_commit` | `/spine/last_stable_commit` | After Spine processes 1+ successful `think()` requests |
| `last_candidate_commit` | `/spine/last_candidate_commit` | After pre-commit hook passes (before runtime verification) |

### Flow

1. Agent commits code → pre-commit hook passes → writes `last_candidate_commit`
2. Spine starts, handles first `think()` successfully → promotes candidate to `last_stable_commit`
3. If Spine crashes before first successful think → revert to `last_stable_commit` (candidate was never promoted)

### Recovery rules

| Crash type | Detected by | Recovery |
|---|---|---|
| Cortex crash | Spine Supervisor (in-process) | `git reset --hard HEAD~{depth}` (full workspace revert) |
| Spine crash | Watchdog (talosctl, outside container) | `git checkout {last_stable_commit} -- spine/` (targeted revert, cortex untouched) |

### Agent notification

- **Cortex crash:** Spine queues system notice before restart → agent sees it immediately
- **Spine crash:** Watchdog writes to `/memory/pending_system_notices.json` → Spine reads on next startup → injects into stream → agent sees it on next think()

## 7. Docker Changes

### Dockerfile

The Go build stage is removed entirely. The Spine now installs as a Python package alongside Cortex:

```dockerfile
# Single stage (no Go builder)
FROM python:3.13-slim

# Install system deps
RUN apt-get update && apt-get install -y git curl gosu ...

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh ...

# Copy entire talos/ (both cortex/ and spine/) and install deps
COPY talos/pyproject.toml talos/uv.lock ./
RUN uv sync --frozen --no-dev --no-progress
COPY talos/ .

# Copy runtime scripts (root-owned, 555)
COPY scripts/ /runtime_scripts/

# Copy entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# Copy spine config (root-owned, not writable by agent)
# Config lives in talos_runtime/spine_config.json (moved from deleted spine/ dir)
COPY spine_config.json /spine/spine_config.json
```

### entrypoint.sh changes

```bash
# Before:
/usr/local/bin/spine /spine/spine_config.json &

# After:
python -m spine /spine/spine_config.json &
```

## 8. Dependencies

Added to `talos/pyproject.toml`:

```toml
[project]
dependencies = [
    "aiohttp>=3.9",     # Control plane HTTP server + Gate HTTP client
]
```

All other deps (standard library: `asyncio`, `json`, `hashlib`, `subprocess`, `pathlib`, `time`, `signal`, `os`, `struct`) are built-in.

## 9. Constitution Update

P10 (Stream Integrity) added:

> **P10: Stream Integrity.** The conversation stream is an immutable, append-only ledger. The system prompt, initialization, and all prior messages form a frozen prefix that must never change between requests — this is what enables KV-cache reuse. Dynamic data (HUD, context percentage, turn number) is piggybacked onto the last message, never injected as a new system message or mutated into the prefix. Changing the prefix invalidates the cache and forces the model to re-process every token from scratch, wasting budget and slowing reasoning. Append-only reads and recalls are fine — re-reading what is already in the stream preserves the cache. The constraint is: never modify what has already been sent.

## 10. Module-by-Module Port Map

| Go File | Lines | Python File | Key Changes |
|---|---|---|---|
| `main.go` | 79 | `main.py` | asyncio event loop, same startup sequence |
| `config.go` | 86 | `config.py` | dataclass instead of struct, same JSON loading |
| `ipc.go` | 200 | `ipc_server.py` | asyncio streams instead of goroutines, same JSON-RPC |
| `ipc_types.go` | 90 | `ipc_types.py` | Python dataclasses, identical fields |
| `stream.go` | 520 | `stream.py` | asyncio httpx for Gate, same shedding/folding logic |
| `constitution.go` | 92 | `constitution.py` | hashlib.sha256, same file loading and hash tracking |
| `supervisor.go` | 182 | `supervisor.py` | asyncio.create_subprocess_exec for Cortex, same Lazarus |
| `health.go` | 48 | `health.py` | asyncio time tracking, same stall/startup detection |
| `events.go` | 52 | `events.py` | Same JSONL append, pathlib instead of filepath |
| `snapshot.go` | 61 | `snapshot.py` | Same JSON save/load, pathlib |
| `control_plane.go` | 124 | `control_plane.py` | aiohttp.web instead of net/http, same endpoints |
| `telegram.go` | 43 | `telegram.py` | httpx instead of net/http, same outbound only |

Estimated total: ~1,200-1,400 lines Python (from ~1,477 lines Go).

## 11. Testing Strategy

All spine tests in `talos/tests/spine/`, following the same pattern as cortex tests.

- **Hypothesis property-based tests** mandated for all new logic (shedding thresholds, fold behavior, hash comparison, etc.)
- **IPC testing:** Mock server (same pattern as existing `test_spine_client.py`)
- **Supervisor testing:** Mock subprocess, test Lazarus depth calculation
- **Control plane testing:** `aiohttp.test_utils.TestClient` for HTTP endpoints
- **Stream testing:** Mock Gate, verify shedding/folding/HUD construction

Pre-commit hook (`setup_hooks.sh`) update: write `last_candidate_commit` after tests pass.

## 12. What Is NOT Changing

- `talos/cortex/spine_client.py` — zero changes
- `spine_config.json` — same format, same fields
- `gate/` — not touched
- `docker-compose.yml` — same services, same volumes
- IPC protocol — same JSON-RPC methods
- The watchdog pattern in `talosctl` — same, just updated logic for spine-specific revert