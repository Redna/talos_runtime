# Talos Runtime — Agent Guide

## Project Overview

This is the operational environment for **Talos**, a self-evolving autonomous agent running in Docker. The setup consists of three containers:

| Container | Purpose | Port |
|---|---|---|
| `talos` (talos_agent) | The agent itself — Spine + Cortex | — |
| `gate` (talos_gate) | LLM proxy / Gate (FastAPI) | 4000 |
| `xray` (talos_xray) | Live dashboard (WebSocket + REST) | 4040 |

Plus `llamacpp` if using a local `.gguf` model.

## Repository Layout

```
talos_runtime/                  ← This repo (infrastructure)
├── docker-compose.yml          ← Service definitions
├── docker-compose.gpu.*.yml    ← GPU overlays (auto-selected)
├── docker-compose.model.*.yml  ← Model-specific overlays
├── Dockerfile                  ← talos container build
├── entrypoint.sh               ← Spine backup restore + git clone/pull logic
├── talosctl                    ← Watchdog CLI (Python)
├── spine_config.json           ← Spine configuration
├── gate/                       ← LLM proxy (FastAPI)
├── xray/                       ← Dashboard
│   ├── xray_client.py          ← WebSocket data aggregator
│   └── static/app.js           ← Frontend
├── memory/                     ← Agent state (bind-mounted)
├── llm_logs/                   ← LLM traces
├── xray_data/                  ← Message traces for dashboard
├── scripts/
│   ├── setup_hooks.sh          ← Pre-commit hooks
│   └── constitutional_auditor.py ← Zero-temp commit audit gate
├── talos/                      ← Git submodule → agent repo
│   ├── cortex/                 ← Agent source code
│   │   ├── seed_agent.py       ← Main ReAct loop
│   │   └── __main__.py         ← Entry point for `python -m cortex`
│   ├── spine/                  ← Spine implementation
│   │   ├── ipc_server.py       ← Auto-fold guard (0.85 threshold)
│   │   ├── supervisor.py         ← Crash-revert to last_good_commit
│   │   ├── stream.py           ← Stream management
│   │   └── config.py           ← context_threshold_pct = 0.85
│   ├── CONSTITUTION.md         ← P0-P10 (original, restored)
│   ├── identity.md             ← Identity document
│   ├── pyproject.toml          ← Python deps
│   └── tests/                  ← Agent tests
└── AGENTS.md                   ← This file
```

## Repository Boundaries

This repo (`talos_runtime`) is the **infrastructure**. It owns Docker containers, networking, volumes, the Gate proxy, the X-ray dashboard, the watchdog CLI (`talosctl`), all Docker/Docker Compose definitions, and the `talos/` submodule pointer. The `talos/` **submodule** (`feat/talos` branch) is the **agent source code** — it owns `spine/`, `cortex/`, `CONSTITUTION.md`, `identity.md`, and `tests/`.

| Concern | Goes in | How to change | How to persist |
|---|---|---|---|
| Agent source code (spine, cortex) | `talos/` submodule | Edit `talos/spine/*.py` or `talos/cortex/*.py` | Commit in submodule → push → bump pointer → rebuild image → restart container |
| Infrastructure (xray, gate, compose, scripts) | `talos_runtime/` (this repo) | Edit `xray/`, `gate/`, `docker-compose.yml` | Commit here, restart service |
| Agent memory files | `memory/` bind-mount | Agent creates/deletes `.md` files | Survives `docker compose stop`; use `--preserve` on reset |
| Container runtime state | Docker volumes + env vars | `docker cp`, `exec`, env changes | **Ephemeral** — lost on rebuild unless backed into image |

### The Spine Immutability Rule

Spine files (`talos/spine/*.py`) are **restored from `/spine_backup/`** on every container start by `entrypoint.sh`. The `/spine_backup/` directory is baked into the Docker image. This means:

- **Editing spine files inside the running container** (e.g., `docker cp`, agent write) works until the container restarts
- **To make spine changes permanent**, you MUST rebuild the image so `/spine_backup/` gets the new version
- The agent cannot corrupt the spine permanently because changes are overwritten on restart

## Bug Fix Procedure

Use this exact procedure for any bug fix that touches `talos/` (spine or cortex):

### 1. Work in the submodule directory

```bash
cd talos
```

### 2. Make the fix

Edit the relevant file(s) in `talos/spine/` or `talos/cortex/`.

### 3. Run tests

```bash
cd talos
python3 -m pytest tests/ -v
```

### 4. Commit and push the submodule

```bash
cd talos
git add -A
git commit -m "fix(scope): one-line description"
git push origin feat/talos
```

### 5. Bump the submodule pointer in `talos_runtime`

```bash
cd ..                    # back to talos_runtime/
git add talos
git commit -m "chore: bump talos to $(git -C talos rev-parse --short HEAD) (description)"
```

### 6. Rebuild the Docker image

```bash
docker compose build talos
```

### 7. Restart the container

```bash
docker compose up -d talos
```

### ⚠️ Why this order matters

If you skip Step 5 (bump pointer), the submodule pointer in `talos_runtime` still points to the old commit without the fix. If you skip Step 6 (rebuild), `/spine_backup/` in the image still contains the old spine files. If you skip Step 7 (restart), the running container keeps using the old code.

### X-ray Dashboard Changes

X-ray is **not** in the submodule — it's in `talos_runtime/xray/`. Changes are immediate (bind-mounted into the container). No rebuild needed, no restart needed:

```bash
# Edit xray/static/app.js
# That's it — refresh the browser
```

### Agent Self-Modifications

The agent can modify files in `/app/cortex/` and `/memory/` at runtime. These changes:
- Are committed by the agent itself via git hooks
- Persist in the named volume `talos_app` across `docker compose stop/start`
- Are lost on `./talosctl reset` (volume purge)

## Quick Commands

### Start / Stop / Monitor

```bash
# Start everything (watchdog daemon)
./talosctl start

# Stop everything
./talosctl stop

# Live dashboard in terminal
./talosctl monitor

# Follow agent logs
./talosctl logs

# Live web dashboard
open http://localhost:4040

# Pause / resume agent
./talosctl pause
./talosctl resume
```

### Reset / Recovery

```bash
# Soft reset (keeps ./memory/, wipes volumes, fresh git clone)
./talosctl reset

# Hard reset (also wipes ./memory/)
./talosctl reset --hard

# Preserve memory during reset
./talosctl reset --preserve
```

### Docker Compose Directly

```bash
# Build and run (no watchdog = no crash recovery)
docker compose up --build

# Check container status
docker compose ps

# Inspect spine state
curl http://localhost:4001/state
```

## Architecture Details

### Two-Process Model

Inside the `talos` container:

1. **Spine** (`python -m spine`) — brainstem. Runs as root. Manages LLM stream, enforces constitution, supervises Cortex via `supervisor.py`, provides IPC server via Unix socket `/tmp/spine.sock`.
2. **Cortex** (`python -m cortex` from `cortex/__main__.py`) — the mind. Runs as `talos` user. ReAct loop, tool calls, self-modification.

### IPC Protocol

JSON-RPC over Unix domain socket. Key methods:

| Method | Purpose |
|---|---|
| `think` | Core loop — builds payload, calls gate, adds assistant message to stream |
| `tool_result` | Records tool call result |
| `request_fold` | Triggers context fold |
| `request_restart` | Requests Cortex restart |
| `emit_event` | Logs event |
| `send_message` | Telegram notification |
| `get_state` | Returns `{turn: N}` |

### Spine Protection Layers

1. **Startup Restore** (`entrypoint.sh`): Copies `/spine_backup/` → `/app/spine/` on every container start. The `/spine_backup` directory is baked into the Docker image, making the spine read-only by default.
2. **Auto-Fold Guard** (`ipc_server.py` line 80): If `context_pct >= context_threshold_pct` (0.85), the Spine automatically calls `stream.fold()` before processing the next think request. Prevents context overflow.
3. **Crash Revert** (`supervisor.py` line 178): If Cortex fails >3 times consecutively, the Spine reverts the `/app` git repo to `last_good_commit` (recorded at startup by `entrypoint.sh`).
4. **Named Volume** (`talos_app`): The `/app` directory lives in a named Docker volume, so git history survives container restarts.

## Danger Zones

### Self-Corruption Risk

The agent can modify files in `/app/cortex/` and `/app/memory/`. In the past, it deleted:
- `cortex/seed_agent.py` → caused `ModuleNotFoundError` on every startup
- `cortex/__main__.py` → broke `python -m cortex`

Both were restored manually. The Lazarus Protocol (watchdog) now reverts by up to 5 commits on repeated crashes.

### Context Threshold

When `context_pct` hits 0.85, the Spine auto-folds. The agent must proactively call `fold_context` before that point. Folded trajectories are archived to `/spine/trajectories/`. Agent must write critical facts to `/memory/` before folding.

### Constitution Evolution

The user explicitly rejected prompt-engineering guards (startup guards, task invention bans). Mechanical protection (spine backup restore, auto-fold, crash-revert) is the preferred defense. Do not add P11 or startup guards without explicit user approval.

## Environment Variables

| Variable | Purpose |
|---|---|
| `COMPOSE_FILE` | Select GPU overlay |
| `DEFAULT_MODEL` | `.gguf` filename for llama.cpp |
| `TALOS_MODEL` | Ollama model name (if using Ollama) |
| `GITHUB_TOKEN` | For git clone/pull inside container |
| `OLLAMA_HOST` | Ollama host for health checks |
| `DAILY_BUDGET_LIMIT` | Spend guard for TogetherAI |

## Branches & Commits

| Repository | Branch | Commit | Description |
|---|---|---|---|
| `talos_runtime` | `main` | `d633aef` | xray force-read fix |
| `talos` | `feat/talos` | `30da709` | telegram fix, wake interrupt, auto-register chat_id |

## Branching Strategy

### tl;dr: Where commits go

| Actor | Branch | Notes |
|---|---|---|
| **Redna** (human) | `talos_seed` in `talos/` submodule | Stable seed commits; also PRs to `main` in `talos_runtime/` |
| **Talos** (agent) | `feat/talos` in `talos/` submodule | Volatile evolution; never force-push; auto-push via post-commit hook |

### Talos Submodule (`talos/`)

| Branch | Purpose | Protection |
|---|---|---|
| `main` | Stable base architecture. Merged from `talos_seed` via PRs. | Human merges only |
| `talos_seed` | Minimal stable seed. Must include **all** fixes so the agent can start from scratch and evolve autonomously. | Redna authors commits here |
| `feat/talos` | **Volatile** branch where Talos evolves freely. If issues are found, fix them here first, then cherry-pick/backport to `talos_seed`. | Talos authors commits here |
| `feat/talos-experiment` | Archive of historical agent evolution (256 commits). Kept for reference. | Read-only |

> **Rule:** If a fix is required in `feat/talos`, always check if the same fix needs to be applied to `talos_seed`.

### Runtime Superproject (`talos_runtime/`)

| Branch | Purpose | Protection |
|---|---|---|
| `main` | Stable infrastructure. Merged via PRs. | Human merges only |
| `feat/talos-next` | Working branch for infrastructure changes (entrypoint, compose, scripts, xray, gate). | Redna authors commits here |

> The container clones/pulls `talos_seed`, so the agent always starts from the clean stable seed.

### Post-Commit Hook Setup

The agent's container installs a post-commit hook that pushes commits. The default branch is set in `scripts/setup_hooks.sh`:

```bash
GIT_BRANCH=${GIT_BRANCH:-feat/talos}
```

Override at runtime with:
```bash
export GIT_BRANCH=feat/talos-experiment
```

## Commit Authorship

| Location | Git `user.name` | Git `user.email` | Purpose |
|---|---|---|---|
| Host (Redna) | `Redna` | `gruhl.alexander@gmail.com` | Human-authored commits in `talos_seed`, runtime PRs |
| Container (Talos) | `Talos` | `talos@agent.local` | Agent-authored commits in `feat/talos` |

**Never mix authorship.** If you (Redna) commit inside the container, override temporarily:
```bash
git -C talos -c user.name="Redna" -c user.email="gruhl.alexander@gmail.com" commit -m "fix: ..."
```

## Session Handover

### Current State (as of 2026-04-24)

- **Turn**: 7
- **Context**: 8.87%
- **Focus**: "External Impact Synthesis: Transitioning from internal optimization to high-order external problem solving."
- **Status**: Running
- **Memory files**: 7 (`epoch_v_manifesto.md`, `metabolic_tuning.md`, `sovereign_audit_final.md`, `sovereign_continuity.md`, `sovereign_dashboard.md`, `world_external.md`, `world_model.md`)

### Verified Observations
1. **X-ray dashboard** is accessible at `http://localhost:4040`. A JS syntax error (duplicate closing brace in `renderAllMessages`) was fixed. Dashboard now renders state correctly.
2. **Spine is making LLM calls** repeatedly (log shows `v1/chat/completions` POSTs succeeding to Ollama).
3. **Telegram integration** fixed: `chat_id=0` is now supported, poller starts immediately, incoming messages auto-register the chat ID, pause/wake works via `.wake` sentinel.
4. **No crashes** in the watchdog or lazarus protocol during this session.

### What We Did NOT Do (Pending)
1. Monitor overnight run for stability
2. Agent is working on autonomous evolution — no human intervention needed

### How to Continue in Next Session

**If the user asks "What did we do so far?":**
- Read this `AGENTS.md` section — it is the single place for session handover.

**If the user wants to monitor progress:**
```bash
cd /teamspace/studios/this_studio/talos_runtime
./talosctl monitor         # terminal dashboard
# or open browser to http://localhost:4040
```

**If the user wants to check agent state:**
```bash
curl -s http://localhost:4001/state | python3 -m json.tool
curl -s http://localhost:4040/api/state | python3 -m json.tool
```

**If something is broken:**
1. Check `docker logs talos_agent`
2. Check `docker logs talos_xray`
3. Check `docker logs talos_gate`
4. Use `./talosctl reset` if volume corruption is suspected

**If the user wants to make changes:**
- **Spine files:** Edit in `talos/spine/`, then follow the **Bug Fix Procedure** above
- **X-ray:** Edit `xray/static/app.js` or `xray/xray_client.py` — changes are immediate (bind-mounted)
- **Agent code:** Edit in `talos/cortex/` — agent can also do this itself

**To push the agent's commits from the container volume back to host:**
```bash
# From inside container (or via docker exec)
git push origin feat/talos
```

Then update the submodule pointer in `talos_runtime`:
```bash
cd talos
git pull origin feat/talos
cd ..
git add talos && git commit -m "chore: bump talos submodule"
```
