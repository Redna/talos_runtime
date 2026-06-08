# Talos Runtime

> **Two experiments complete; between runs.** Experiment 1 ran April 27 – May 6, 2026 (~10 days).
> Experiment 2 ran May 15 – 27, 2026 and was terminated by a Telegram poller SSL timeout on May 27.
> See [docs/CLOSING_SUMMARY.md](docs/CLOSING_SUMMARY.md) for Experiment 1's wrap-up report,
> [reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md](reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md)
> for Experiment 2's report, and [docs/POSTMORTEM_2026-05-27.md](docs/POSTMORTEM_2026-05-27.md) for the
> crash post-mortem.

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

### 4. Monitor the agent

```bash
./talosctl monitor
```

Live dashboard showing container health, Spine state (turn, context %, tokens), version tracking, and Lazarus status. Refreshes every 5 seconds. Ctrl+C to exit.

### 5. View logs

```bash
./talosctl logs
```

### 6. Run without the watchdog

```bash
docker compose up --build
```

Note: Without the watchdog, crash recovery and the Lazarus Protocol are unavailable.

## Repository Structure

```
talos_runtime/                  ← This repo (infrastructure)
  docker-compose.yml            ← Service definitions (includes nono cgroup limits)
  Dockerfile                    ← Container build (Python only, no Go; installs nono-cli)
  entrypoint.sh                 ← Starts Spine + Cortex
  spine_config.json             ← Spine configuration
  gate/                         ← LLM proxy (FastAPI)
  memory/                       ← Agent state (gitignored, bind-mounted)
  models/                       ← .gguf model files (gitignored)
  reports/                      ← Per-experiment reports (Experiment 1 + 2)
  runtime_scripts/
    constitutional_auditor.py   ← Zero-temperature audit gate
    secret_scrubber.py          ← Scrubs tokens from Gate/xray output at write time
  scripts/
    setup_hooks.sh              ← Pre-commit hook installer
  sentinel/                     ← Semantic Firewall (mitmproxy)
  talos/                        ← Git submodule → talos agent repo
  talosctl                      ← Watchdog daemon (start/stop/monitor/logs/status/check)
  tests/                        ← Runtime-level tests (e.g. test_secret_scrubber.py)
  xray/                         ← Live dashboard
  nono-cli_0.61.2_amd64.deb     ← The nono CLI installer (sideloaded into the image)
  .gitguardian.yaml             ← Ignore policy for the entrypoint.sh token-template URL

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
    nono_policy.py              ← Writes the nono CLI policy manifest
    sandbox.py                  ← Popen wrapper around `nono run --config` + rlimits
    control_plane.py / telegram.py
  tests/                        ← Cortex + spine tests
    test_*.py                   ← Example-based tests
    *_hypothesis.py             ← Property-based tests (hypothesis)
  tests-spine/                  ← Spine-specific nono sandbox tests
  CONSTITUTION.md               ← Agent's core principles (P0-P10)
  identity.md                   ← Agent's identity document
  pyproject.toml / uv.lock      ← Dependency management
```

## Architecture

Three processes run inside the Talos container, with a nono CLI wrapper around the Cortex:

1. **Spine** (`python -m spine`) — the brainstem. Manages the LLM stream, enforces the constitution, supervises the Cortex, and provides the IPC server. Runs as root.
2. **nono CLI** (`nono run --config /spine/nono_policy.json -- ...`) — kernel-enforced Landlock wrapper around the Cortex. Owns the policy manifest, the credential-injection network policy, and signal isolation. Runs as root.
3. **Cortex** (`python seed_agent.py`) — the mind. Runs the ReAct loop, calls tools, self-modifies code. Runs as the `talos` user, inside the nono Landlock boundary.

They communicate via Unix domain socket at `/tmp/spine.sock` using JSON-RPC. The Spine supervises the nono Popen (not the Cortex directly), so it can SIGTERM / SIGKILL a hung Cortex by PID.

### Three layers of defense

| Layer | What it caps | Where |
|---|---|---|
| **nono Landlock** | Filesystem R/W, network egress, signal forwarding | `talos/spine/nono_policy.py` → `/spine/nono_policy.json` |
| **preexec_fn rlimits** | CPU 30 min, AS 8 GB, NOFILE 4096 | `talos/spine/sandbox.py::_set_cortex_rlimits` |
| **cgroup cap** | cpus 2.0, memory 8 GB | `docker-compose.yml` `services.talos.deploy.resources.limits` |

The cgroup is the unbypassable backstop: the Cortex cannot raise its own rlimits above it, and the Compose file is bind-mounted read-only from the host.

See [ARCHITECTURE.md](docs/docs/ARCHITECTURE.md) for the full technical specification.

## Docker Compose Overlays

Compose files are layered: **base** + **GPU** + **model**. `talosctl` auto-selects based on your `.env` and hardware.

### GPU Overlays (auto-detected)

| File | Purpose |
|---|---|
| `docker-compose.gpu.rocm.yml` | AMD ROCm (devices, image, HSA config) |
| `docker-compose.gpu.cuda.yml` | NVIDIA CUDA (image, NVIDIA env) |

Selected automatically: ROCm if `/dev/kfd` exists, CUDA otherwise.

### Model Overlays (auto-selected from `DEFAULT_MODEL`)

| File | Model | Key params |
|---|---|---|
| `docker-compose.model.gemma-4-31b-it.yml` | `gemma-4-31B-it-UD-Q4_K_XL.gguf` | 65536 ctx, q4_0 cache, reasoning on |
| `docker-compose.model.gemma-4-26b-a4b-it.yml` | `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` | 40960 ctx, q8_0 cache, mmproj, thinking |
| `docker-compose.model.qwen3-5-27b.yml` | `Qwen3.5-27B-Q4_K_M.gguf` | 71680 ctx, q4_0 cache |

Auto-selected by convention: `gemma-4-31B-it-UD-Q4_K_XL.gguf` → `model.gemma-4-31b.yml`. If no matching overlay exists, the base `docker-compose.yml` uses `${DEFAULT_MODEL}` with generic defaults.

### Adding a new model

1. Create `docker-compose.model.<slug>.yml` with `services.llamacpp.command` + `healthcheck`
2. Set `DEFAULT_MODEL=<filename>.gguf` in `.env`

Slug convention: strip `.gguf`, strip quant suffix (everything from `-UD-` or `-Q` onward), lowercase, dots → hyphens.

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
cd talos && python3 -m pytest tests-spine/ tests/ -v
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

## Nono Sandbox

The Cortex runs inside a **nono** capability-based sandbox, enforced at the kernel level via Linux Landlock. This is orthogonal to the Sentinel Proxy (semantic/PII audit) and Docker network isolation — it provides a hard boundary the agent cannot cross even via `eval`, `exec`, or `ctypes`.

The Spine runs nono as a subprocess (the `nono run --config /spine/nono_policy.json -- ...` CLI, see `talos/spine/sandbox.py`). The Phase 2b design replaced the original `nono_py.sandboxed_exec()` Python API with a real `subprocess.Popen` wrapper so the Spine can `SIGTERM` / `SIGKILL` a hung Cortex by its real PID.

### What nono enforces

| Layer | Enforcement |
|---|---|
| **Filesystem** | Allowlist-based — R/W only on explicit working directories (/app, /memory, /spine, /home/talos, /tmp, /venv, etc.) plus the built-in `python_runtime` and `git_config` groups from nono's `default` profile. Attempts to write outside the allowlist return EACCES from the kernel. |
| **Network** | Per-host allowlist via the `network.allow_domain` block in the policy. Credentials are injected at the **network layer** (not the process layer): the Cortex sees dummy API keys, and nono's network proxy swaps them for the real `GITHUB_TOKEN` / `TELEGRAM_BOT_TOKEN` on matching outbound requests. The agent literally cannot exfiltrate them. The full injection config lives under `network.custom_credentials` in the policy. |
| **Credentials** | Real tokens live in the proxy, never in the sandbox env. Cloud metadata endpoints (169.254.169.254) and private RFC1918 ranges are hard-denied. |
| **Snapshots** | Content-addressable snapshots with SHA-256 dedup and Merkle-tree integrity. Baseline at boot, incremental every N turns, restore on Lazarus Protocol. |
| **Audit** | Append-only NDJSON log with Merkle-chained tamper detection. `verify_log()` confirms integrity. |
| **rlimits** | `preexec_fn` in `talos/spine/sandbox.py` sets `RLIMIT_CPU=1800s`, `RLIMIT_AS=8GB`, `RLIMIT_NOFILE=4096` on the nono Popen (inherited by the Cortex). |
| **cgroup** | `docker-compose.yml` `services.talos.deploy.resources.limits` caps cpus at 2.0 and memory at 8 GB. This is the unbypassable backstop — the Cortex cannot raise its own rlimits above the cgroup. |

### Three layers of defense

1. **nono Landlock** — kernel-enforced filesystem + network + signal boundaries.
2. **preexec_fn rlimits** — agent-aware soft caps (CPU, AS, NOFILE).
3. **cgroup** — kernel-enforced hard caps the agent cannot escape.

### Requirements

- Linux kernel **5.13+** with Landlock (`CONFIG_SECURITY_LANDLOCK=y`)
- Run `./talosctl check` to verify your host

### Disabling the sandbox

For debugging or testing:

```bash
# Set in .env to disable the sandbox at the Spine level
NONO_ENABLED=0

# Or add to spine_config.json:
# { "nono_enabled": false }
```

When nono is unavailable (unsupported kernel, missing nono-py package), the Spine falls back to the classic `subprocess.Popen` behavior — the agent runs without kernel-level sandboxing but everything else (Sentinel, Docker network isolation, immutability) still works.

### How it fits the security stack

```
Outbound request → Cortex (sandboxed)
                     │
                     ├── nono CLI (Landlock + network policy)
                     │     ├── custom_credentials (github / telegram)
                     │     └── group: python_runtime, git_config
                     │
                     ├── preexec_fn rlimits (CPU, AS, NOFILE)
                     │
                     ├── cgroup cap (cpus 2.0, mem 8G)
                     │
                     ├── Sentinel mitmproxy (PII/secret/constitutional audit)
                     │
                     └── Docker talos_internal network (internal: true)
```

## `talosctl check`

The `check` subcommand verifies the host kernel supports the nono Landlock sandbox:

```bash
./talosctl check
```

It inspects `/proc/version`, parses the kernel major/minor, and (if nono-py is importable) calls `is_supported()`. Output is a green check on success or a yellow cross on failure. Run it once after provisioning a new host, or in CI before launching the agent.

## License

Private repository. All rights reserved.