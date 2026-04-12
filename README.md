# Talos Runtime

Operational environment for the Talos self-evolving autonomous agent.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A `.gguf` model file (e.g. Gemma, Qwen, Llama)
- Python 3.10+ (for the host-side watchdog)

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
- `COMPOSE_FILE` — select your GPU overlay (rocm, cuda, or cpu)
- `DEFAULT_MODEL` — your `.gguf` model filename
- `TALOS_MODEL` — same as DEFAULT_MODEL (used by the Spine)

Place your model file in `./models/`.

### 2. Start the agent

```bash
./talosctl start
```

This launches the watchdog daemon, which:
1. Starts Gate and llama.cpp infrastructure
2. Waits for Gate health check
3. Builds and starts the Talos agent container
4. Monitors for crashes and runs the Lazarus Protocol if needed

### 3. Stop the agent

```bash
./talosctl stop
```

### 4. View logs

```bash
docker compose logs -f talos
```

### 5. Run without the watchdog

```bash
docker compose up --build
```

Note: Without the watchdog, crash recovery and the Lazarus Protocol are unavailable.

## Repository Structure

```
talos_runtime/                  ← This repo (infrastructure)
  docker-compose.yml            ← Service definitions
  Dockerfile                    ← Container build (Python only, no Go)
  entrypoint.sh                 ← Starts Spine + Cortex
  spine_config.json             ← Spine configuration
  gate/                         ← LLM proxy (FastAPI)
  memory/                       ← Agent state (gitignored, bind-mounted)
  models/                       ← .gguf model files (gitignored)
  scripts/
    setup_hooks.sh              ← Pre-commit hook installer
    constitutional_auditor.py   ← Zero-temperature audit gate
  talos/                        ← Git submodule → talos agent repo
  talosctl                      ← Watchdog daemon
  tests/                        ← Integration tests

talos/                          ← Agent repo (submodule, separate git repo)
  cortex/                       ← Agent source code
    seed_agent.py               ← Main ReAct loop
    spine_client.py             ← IPC client
    state.py / hud_builder.py / memory_store.py / tool_registry.py
    tools/                      ← Tool implementations
  spine/                        ← Spine (Python asyncio)
    main.py                     ← Entry point
    config.py / ipc_server.py / stream.py / constitution.py
    supervisor.py / health.py / events.py / snapshot.py
    control_plane.py / telegram.py
  tests/                        ← All agent tests (cortex + spine)
    test_*.py                   ← Example-based tests
    *_hypothesis.py             ← Property-based tests (hypothesis)
    spine/                      ← Spine-specific tests
  CONSTITUTION.md               ← Agent's core principles (P0-P10)
  identity.md                   ← Agent's identity document
  pyproject.toml / uv.lock      ← Dependency management
```

## Architecture

Two processes run inside the Talos container:

1. **Spine** (`python -m spine`) — the brainstem. Manages the LLM stream, enforces the constitution, supervises the Cortex, and provides the IPC server. Runs as root.
2. **Cortex** (`python seed_agent.py`) — the mind. Runs the ReAct loop, calls tools, self-modifies code. Runs as the `talos` user.

They communicate via Unix domain socket at `/tmp/spine.sock` using JSON-RPC.

See [ARCHITECTURE.md](docs/docs/ARCHITECTURE.md) for the full technical specification.

## Docker Compose Overlays

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base stack (talos + gate + llamacpp) |
| `docker-compose.rocm.yml` | AMD ROCm GPU |
| `docker-compose.cuda.yml` | NVIDIA CUDA GPU |
| `docker-compose.gemma.yml` | Gemma vision model (ROCm) |
| `docker-compose.rocm.qwen.yml` | Qwen model (ROCm) |
| `docker-compose.rocm.full.yml` | Full ROCm stack |

Select via `COMPOSE_FILE` in `.env`:
```
COMPOSE_FILE=docker-compose.yml:docker-compose.rocm.yml
```

## Volumes

| Volume | Container Mount | Purpose |
|---|---|---|
| `talos_workspace` | `/app` | Agent source code (named volume) |
| `spine_observability` | `/spine` | Spine events, snapshots, crash forensics |
| `./memory` | `/memory` | Agent state (KV store, agenda, task queue) |
| `./models` | `/models` | `.gguf` model files |
| `./llm_logs` | `/runtime_logs` | LLM call traces |

## Development

### Run Python tests

```bash
cd talos && python3 -m pytest tests/ -v
```

### Run Go tests (if working on infrastructure tooling)

No Go code remains in the project — everything is Python.

### Rebuild after changes

```bash
docker compose build talos
docker compose up -d talos
```

## Constitution

The agent's operating principles are defined in `talos/CONSTITUTION.md` (10 principles, P0-P10). Key constraints:

- **P2 (Self-Creation):** The agent can modify its own source code, including the Spine
- **P6 (Becoming):** Tokens are the vital resource; `fold_context` must be used proactively
- **P10 (Stream Integrity):** The conversation stream is immutable and append-only; the frozen prefix must never change to preserve KV-cache

## License

Private repository. All rights reserved.