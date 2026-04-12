# Repository Restructuring Design

**Date:** 2026-04-12
**Status:** Draft
**Scope:** Restructure `talos_runtime/` and `talos/` repositories to enforce clean boundaries between the self-evolving agent and its runtime infrastructure, and separate tests from source code.

---

## 1. Problem Statement

The current repository structure has two issues that undermine the agent's self-evolution capability:

1. **No boundary between agent and infrastructure.** Spine source code, Cortex source code, Gate, docker-compose, talosctl, and integration tests all live at the same level under `talos_runtime/`. A self-evolving agent whose world is `/app` has no clear separation between what it owns and what is infrastructure it should never see or modify.

2. **Tests mixed with source.** In both `spine/` and `cortex/`, test files sit alongside source files. For the Spine (Go), this is standard convention and harmless since the agent never sees Spine source. For the Cortex (Python), this is problematic: the agent reads and modifies files in `/app`, and a clutter of `*_test.py` files adds cognitive noise that could confuse a self-evolving agent.

Additionally:
- `CONSTITUTION.md` and `identity.md` don't exist yet — they need a home.
- The `memory/` directory is gitignored but lives outside the repo (`../talos_memory/`), scattering state.
- The `cortex/` directory at the runtime level blurs the agent/infrastructure boundary.

---

## 2. Design Principles

1. **The agent's world is `/app` and `/memory` — nothing else.** Everything the agent can read, write, or modify lives in these two paths. All infrastructure (Docker, Gate, Spine binary, talosctl, runtime scripts) is outside this scope.

2. **`talos/` is the agent's own repository.** It contains everything the agent is: its source code, its tests, its constitution, its identity. The agent has full read/write access here. This repo is versioned independently.

3. **`talos_runtime/` is the operational environment.** It contains everything needed to run the agent but that the agent should never see or modify: Docker configuration, the Gate, the Spine source, the watchdog, runtime scripts.

4. **Tests are separated from source in `talos/`.** The agent can evolve its own tests (write new ones, modify existing ones), but they live in a dedicated `tests/` directory to keep the source tree clean for the agent's working context.

5. **Spine tests follow Go convention.** Since the agent never sees Spine source, there's no reason to move Go tests away from their standard co-location pattern (`foo_test.go` alongside `foo.go`).

---

## 3. Repository Architecture

### 3.1 `talos/` — The Agent (separate git repo)

Everything the agent can see, read, write, and evolve. Deployed into `/app` inside the Docker container.

```
talos/                              ← Separate git repo (submodule in talos_runtime/)
  cortex/
    seed_agent.py                   ← Main ReAct loop
    spine_client.py                 ← IPC client for Spine communication
    state.py                        ← Focus, task queue, cognitive parameters
    hud_builder.py                  ← HUD data construction for think() calls
    memory_store.py                 ← KV store operations on /memory/
    tool_registry.py               ← Tool decorator and registry with OpenAI JSON Schema
    tools/
      __init__.py
      executive.py                 ← set_focus, resolve_focus, fold_context, reflect
      code_surgery.py              ← generate_repo_map, replace_symbol, write_file, read_file, patch_file
      memory.py                    ← store_fact, recall_fact, list_memory_keys, search_memory
      physical.py                  ← bash_command, send_message, request_restart
      git_operations.py            ← git_commit, git_push, git_diff
  tests/                            ← Agent-evolvable tests, separate from source
    test_spine_client.py
    test_tool_registry.py
    test_state.py
    test_memory_store.py
    test_hud_builder.py
    tools/
      test_executive.py
      test_code_surgery.py
      test_memory.py
      test_physical.py
      test_git_operations.py
  CONSTITUTION.md                   ← Agent's core principles (read/writable by agent)
  identity.md                       ← Agent's identity (read/writable by agent)
  requirements.txt
  pyproject.toml
  uv.lock
  .gitignore
  README.md
```

### 3.2 `talos_runtime/` — The Infrastructure

Operational environment that runs the agent. The agent never sees or modifies this code.

```
talos_runtime/                      ← Main git repo
  spine/                            ← Spine source (Go)
    main.go                         ← Entry point, config loading, signal handling
    config.go                       ← Configuration struct and loading
    ipc.go                          ← Unix domain socket server (JSON-RPC)
    ipc_types.go                    ← Request/response types for IPC
    stream.go                       ← Stream construction, shedding, folding, HUD injection
    stream_test.go                  ← Go tests co-located (agent never sees these)
    supervisor.go                   ← Cortex process lifecycle management
    health.go                       ← Stall, crash, and startup failure detection
    events.go                       ← Structured event emission to /spine/events/
    events_test.go
    snapshot.go                     ← State snapshot save/restore
    snapshot_test.go
    control_plane.go               ← HTTP API on port 4001
    control_plane_test.go
    constitution.go                 ← Constitution loading and hash tracking
    constitution_test.go
    telegram.go                     ← Minimal Telegram bot for essential notifications
    integration_test.go             ← Integration tests
    go.mod / go.sum
    Dockerfile                      ← Build stage only (used in multi-stage build)
    spine_config.json
  gate/                             ← Gate (FastAPI LLM proxy)
  talos/                            ← Git submodule pointing to talos repo
  memory/                           ← Agent state (gitignored, host bind mount source)
    .gitkeep
  folds/                            ← Context fold history (gitignored)
  scripts/
    setup_hooks.sh
  tests/
    integration/                    ← Infrastructure-level integration tests
      test_e2e.py
  docker-compose.yml
  Dockerfile
  entrypoint.sh
  .env.example
  .gitignore
  talosctl                          ← Watchdog daemon
  docs/                             ← Architecture docs, specs, plans
```

---

## 4. Container Architecture

### 4.1 Single-Container Model

The Spine binary runs **inside the talos container**, not as a separate service. It starts as a background process via `entrypoint.sh` before the Cortex process launches.

```
talos container:
  /usr/local/bin/spine          ← Spine binary (root-owned, immutable, from multi-stage Go build)
  /spine/spine_config.json      ← Spine configuration
  /tmp/spine.sock               ← Unix domain socket (local to container, no cross-container sharing)
  /app/                         ← Cortex source (named volume: talos_workspace)
    CONSTITUTION.md
    identity.md
    cortex/
    tests/
  /memory/                      ← Agent state (host bind mount from talos_runtime/memory/)
  /spine/                       ← Spine observability (named volume: spine_observability)
  /runtime_scripts/              ← Git hooks (root-owned, 755)
  /venv/                        ← Python virtual environment
```

### 4.2 Volume Mapping

| Host Source | Container Mount | Type | Managed By | Agent Access |
|---|---|---|---|---|
| `talos/` (via build) | `/app` | Named volume: `talos_workspace` | Cortex (r/w), Spine (r/o for constitution) | Source + tests + constitution + identity |
| `talos_runtime/memory/` | `/memory` | Host bind mount | Cortex (r/w), Gate (r/w) | KV store, agenda, task queue |
| — | `/spine` | Named volume: `spine_observability` | Spine (r/w), Cortex (r/o) | Event logs, snapshots, crash forensics |
| `talos_runtime/llm_logs/` | `/runtime_logs` | Host bind mount | Gate (r/w) | LLM call traces |
| `talos_runtime/models/` | `/models` | Host bind mount | llama.cpp | .gguf model files |

### 4.3 Docker Compose Services

```yaml
services:
  talos:      # Agent container: runs spine binary + cortex python
  gate:       # LLM proxy + constitutional auditor
  llamacpp:   # Local inference engine

volumes:
  talos_workspace:       # /app — agent source
  spine_observability:   # /spine — event logs, snapshots, crash forensics
```

The separate `spine` service is removed from docker-compose.yml. The Spine binary runs inside the `talos` container.

### 4.4 Build Flow

```
Dockerfile (in talos_runtime/):
  Stage 1: Go builder
    - COPY talos_runtime/spine/go.mod go.sum
    - COPY talos_runtime/spine/*.go
    - Build: CGO_ENABLED=0 go build -o spine

  Stage 2: Python image
    - Install system deps (git, curl, gosu, gh, uv)
    - COPY talos/ → /app (from talos/ submodule)
    - Install Python deps via uv
    - COPY runtime scripts → /runtime_scripts (root-owned, 555)
    - COPY entrypoint.sh → /usr/local/bin/entrypoint.sh
    - COPY --from=spine-builder /build/spine → /usr/local/bin/spine
    - COPY spine config → /spine/spine_config.json
```

### 4.5 Entrypoint Sequence

```bash
1. Create talos user (PUID/PGID)
2. chown /app to talos user
3. Configure git (user.name, user.email, safe.directory)
4. Install git hooks (setup_hooks.sh)
5. Lock down: chown root /runtime_scripts and /app/.git/hooks, chmod 755
6. Start Spine: /usr/local/bin/spine /spine/spine_config.json &
7. Wait for /tmp/spine.sock to appear (30s timeout)
8. exec gosu talos python seed_agent.py
```

---

## 5. Constitution and Identity

`CONSTITUTION.md` and `identity.md` live at the root of the `talos/` repo, which maps to `/app/` in the container.

- **Agent access:** Read and write. The agent can self-modify these files using `write_file` or `patch_file`.
- **Spine access:** The Spine runs in the same container and reads `CONSTITUTION_PATH=/app/CONSTITUTION.md` and `IDENTITY_PATH=/app/identity.md` directly. It tracks their SHA-256 hash and reloads on change. The Spine process runs as root and can read all files regardless of ownership.
- **Enforcement:** The Spine refuses to construct an LLM call if the Constitution is empty or missing. The Constitutional Auditor (in the Gate) validates that changes are additive, not destructive.

---

## 6. Test Architecture

### 6.1 Cortex Tests (`talos/tests/`)

Located at `/app/tests/` inside the container. The agent can run, modify, and create tests here.

```
talos/tests/
  test_spine_client.py         ← Mock server, IPC protocol tests
  test_tool_registry.py        ← Registry, schema generation
  test_state.py                ← Focus, error streak, persistence
  test_memory_store.py         ← KV store, capacity, partial match
  test_hud_builder.py           ← HUD data construction
  tools/
    test_executive.py           ← set_focus, resolve_focus, fold_context
    test_code_surgery.py        ← file ops, symbol replacement
    test_memory.py              ← store/recall/forget tools
    test_physical.py            ← bash_command, send_message
    test_git_operations.py      ← commit, push, diff
```

Properties:
- Pre-commit hook runs `pytest tests/` — part of the self-modification pipeline
- The agent can write new tests here as it evolves
- Separated from `cortex/` source to keep the agent's working context clean

### 6.2 Spine Tests (`talos_runtime/spine/`)

Go convention: `_test.go` files co-located with source. This is safe because the agent never sees Spine source.

### 6.3 Integration Tests (`talos_runtime/tests/integration/`)

Infrastructure-level tests that validate cross-component behavior (Spine ↔ Cortex ↔ Gate). Run outside the agent's context.

---

## 7. Agent's View of the World

What the agent sees and can interact with inside the container:

```
/app/                              ← Agent's entire writable world
  CONSTITUTION.md                   ← Can read/write (Spine watches hash)
  identity.md                       ← Can read/write (Spine watches hash)
  cortex/                           ← Can self-modify source
  tests/                            ← Can write/evolve tests
  requirements.txt / pyproject.toml ← Can read (but modifying deps requires restart)
  .git/                             ← Git repo (hooks root-owned, agent can commit)

/memory/                           ← Agent state (bind mount)
  agent_memory.json                 ← KV store
  agenda.md                         ← Task priorities
  task_queue.json                   ← Prioritized task queue
  .agent_state.json                 ← Focus, error streak, token count
  folds/                            ← Context fold synthesis history

/spine/                            ← Spine observability (read-only)
  events/                           ← Structured event log
  snapshots/                        ← State snapshots for crash recovery
  crashes/                          ← Crash forensics bundles
```

What the agent does **not** see:
- Spine source code (`.go` files)
- Spine binary source (only the compiled binary at `/usr/local/bin/spine`)
- `docker-compose.yml`
- `Dockerfile`
- `gate/` code
- `talosctl`
- Runtime scripts (can execute, not read their source after lockdown)
- Spine configuration (owned by root)

---

## 8. Migration Plan Summary

### File Moves

| From | To | Notes |
|---|---|---|
| `cortex/` (all `.py` source) | `talos/cortex/` | Agent source moves to agent repo |
| `cortex/*_test.py` | `talos/tests/` | Tests separated from source |
| `cortex/requirements.txt` | `talos/requirements.txt` | Python deps for agent repo |
| (new) | `talos/CONSTITUTION.md` | Agent's constitution |
| (new) | `talos/identity.md` | Agent's identity |
| `spine/` | stays in `talos_runtime/spine/` | Infrastructure, no move |
| `gate/` | stays in `talos_runtime/gate/` | Infrastructure, no move |
| `../talos_memory/` | `talos_runtime/memory/` | Consolidated into repo (gitignored) |
| `memory/` (current empty) | `talos_runtime/memory/` | Becomes the bind mount source |
| `folds/` | `talos_runtime/folds/` | Stays, gitignored |
| `tests/integration/` | stays in `talos_runtime/tests/` | Infrastructure tests |

### Docker Changes

- **Remove `spine` service** from docker-compose.yml
- **Update volume mounts**: `../talos_memory` → `./memory` (in-repo path)
- **Remove cross-container socket mount**: `/tmp/talos_spine.sock` no longer needed since Spine runs in-agent
- **Update Dockerfile**: `COPY talos/` replaces `COPY cortex/` build steps
- **Dockerfile multi-stage build**: Spine built in stage 1, copied into stage 2

### What Gets Deleted

- `cortex/` directory at `talos_runtime/` level (moved to `talos/`)
- `../talos_memory/` as a separate host directory (consolidated into `memory/`)