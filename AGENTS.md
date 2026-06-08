# Talos Runtime — Agent Guide

## Project Overview

This is the operational environment for **Talos**, a self-evolving autonomous agent running in Docker. The setup consists of four primary containers plus a kernel-enforced sandbox:

| Container / subsystem | Purpose | Port |
|---|---|---|
| `talos` (talos_agent) | The agent itself — Spine + Cortex (the Cortex runs inside a nono Landlock sandbox) | — |
| `sentinel` (talos_sentinel) | Security Proxy & Semantic Firewall | 8080 |
| `gate` (talos_gate) | Pure LLM proxy / Router | 4000 |
| `xray` (talos_xray) | Live dashboard (WebSocket + REST) | 4040 |
| `nono` subsystem | Kernel-enforced Landlock sandbox (`/usr/bin/nono`), policy at `/spine/nono_policy.json` (`talos/spine/nono_policy.py`), rlimits set via `preexec_fn` in `talos/spine/sandbox.py`, cgroup cap in `docker-compose.yml` | — |

## Repository Layout

```
talos_runtime/                  ← Infrastructure Repository
├── docker-compose.yml          ← Service and Network definitions (nono cgroup limits)
├── Dockerfile                  ← Agent container build (with nono-cli installed)
├── entrypoint.sh               ← Spine protection & Dynamic branch setup
├── nono-cli_0.61.2_amd64.deb   ← Nono CLI installer (sideloaded into the image)
├── talosctl                    ← Health Watchdog & Lifecycle CLI (incl. `check` subcommand)
├── .gitguardian.yaml           ← Ignore policy for the entrypoint.sh token-template URL
├── sentinel/                   ← Semantic Firewall (mitmproxy)
│   └── mitm_audit.py           ← Out-of-band PII/Secret/Audit logic
├── gate/                       ← LLM proxy (FastAPI)
├── xray/                       ← Live Dashboard
├── runtime_scripts/
│   ├── constitutional_auditor.py   ← Zero-temperature audit gate
│   └── secret_scrubber.py          ← Scrubs tokens from Gate/xray output at write time
├── tests/                      ← Runtime-level tests (test_secret_scrubber.py)
├── reports/                    ← Per-experiment reports (Experiment 1 + 2)
├── talos/                      ← Git submodule → Agent Repository
│   ├── cortex/                 ← Mind: ReAct loop & Tools
│   ├── spine/                  ← Brainstem: Stream & Constitution
│   │   ├── nono_policy.py      ← nono CLI manifest writer (real nono schema)
│   │   └── sandbox.py          ← Popen wrapper around `nono run --config` + rlimits
│   ├── memory/                 ← Biography: Persistent state (Git-tracked)
│   ├── CONSTITUTION.md         ← Core Directives (P0-P10)
│   ├── identity.md             ← Agent Identity
│   ├── tests/                  ← Cortex + spine tests
│   └── tests-spine/            ← Spine-specific nono sandbox tests
└── AGENTS.md                   ← This file
```

## Repository Boundaries

This repo (`talos_runtime`) is the **infrastructure**. It owns Docker orchestration, network isolation, the Sentinel Proxy, and the host-side watchdog (`talosctl`). The `talos/` **submodule** is the **agent source code** — it owns the mind, the brainstem, and the persistent memory.

| Component | Protection | Persistence |
|---|---|---|
| **Spine** | **Immutable** (chattr +i, Watchdog) | Rebuild image to change |
| **Cortex / Memory** | Mutable, Audited on Push | Persists in Git history |
| **Infrastructure** | Host-only / sidecar | Direct edit (no agent access) |

### The Spine Immutability Rule
Spine files are restored from an authoritative `/spine_backup/` and locked with the Linux **Immutable Bit (`chattr +i`)** on startup. The agent cannot permanently alter its core nervous system. To change the Spine, a human must rebuild the Docker image.

## Operational Workflow

### Branching Strategy
The agent operates on a dynamic **`experiment`** branch.
- **`talos_seed` (Submodule):** The stable source of truth. Human fixes land here.
- **`experiment` (Submodule):** The agent's volatile evolution branch. 
- **Push Mechanism:** The agent pushes to **`HEAD`**. This allows it to work on any branch while ensuring all work is securely backed up to the remote repository.

### Persistence & Survival
**`git push` is survival-critical.** Only work pushed to the remote repository survives a server incident or a forceful `Lazarus Reset`. The agent is instructed to commit and push its "biography" (code + memory) frequently.

## Out-of-Band Security (The Sentinel)

Security is no longer enforced inside the agent's container. The **Sentinel Proxy** sidecar provides a "Hard Boundary" that the agent cannot bypass.
1.  **Zero Overhead:** No blocking local hooks.
2.  **Intercepted Push:** When the agent pushes, the Sentinel extracts the diff and performs a file-level audit for PII, Secrets, and Constitutional alignment.
3.  **Network Isolation:** Direct internet access is physically blocked. All traffic must pass through the Sentinel's audit gate.

## Quick Commands

### Lifecycle Management
```bash
# Start the infrastructure (with health watchdog)
./talosctl start

# Wipe everything and start fresh (clean clone from seed)
./talosctl start --fresh

# Monitor the agent's live reasoning
./talosctl monitor

# View agent logs
./talosctl logs
```

### Reset & Recovery
```bash
# Forceful reset to last good commit (Lazarus Protocol)
./talosctl reset --hard

# Verify the host kernel supports the nono Landlock sandbox
./talosctl check
```

## Nono Sandbox

The Cortex runs inside a kernel-enforced **nono** Landlock sandbox. This is the second line of defense after Docker network isolation; the agent cannot bypass it via `eval`, `exec`, or `ctypes`.

### Why a CLI wrapper, not the Python API

The Spine launches the Cortex via the `nono` CLI as a real `subprocess.Popen`:

```
Popen(['nono', 'run', '--config', '/spine/nono_policy.json', '--', sys.executable, '-m', 'cortex'])
```

This was a Phase 2b change. The original Python `nono_py.sandboxed_exec()` API ran the Cortex on a background thread and returned a thread-wrapped handle with a no-op `.kill()`, so the Spine could not `SIGTERM` a hung Cortex. The CLI wrapper gives the Supervisor a real Popen with a real PID, which can be SIGKILL'd.

### Three layers of defense

| Layer | What it caps | Code |
|---|---|---|
| **nono Landlock** | Filesystem R/W, network egress, signal forwarding | `talos/spine/nono_policy.py` → `/spine/nono_policy.json` |
| **preexec_fn rlimits** | CPU 30 min, AS 8 GB, NOFILE 4096 | `talos/spine/sandbox.py::_set_cortex_rlimits` |
| **cgroup cap** | cpus 2.0, memory 8 GB | `docker-compose.yml` `services.talos.deploy.resources.limits` |

The cgroup is the unbypassable backstop: the Cortex cannot raise its own rlimits above it, and the Compose file is bind-mounted read-only from the host.

### Disabling the sandbox

```bash
# In .env
NONO_ENABLED=0

# Or in spine_config.json
{ "nono_enabled": false }
```

When nono is unavailable (unsupported kernel, missing nono-py package) or disabled, the Spine falls back to plain `subprocess.Popen` — the agent runs without kernel-level sandboxing but everything else (Sentinel, Docker network isolation, immutability) still works.

## Branch topology

```
talos_seed           ← the clean Spine + nono baseline (canonical source of truth)
experiment           ← the agent's last 11 commits before the May 27 crash (archived at experiment-2-final-2026-05-27)
main                 ← the April 29 release (historical, no nono)
```

Archive tags on the talos submodule:
- `experiment-2-final-2026-05-27` — agent's last 11 commits on `experiment`
- `experiment-2-parallel-final` — parallel re-do on `experiment` from May 27
- `experiment-1-wave-5-6-archive` — Experiment 1 archive

The runtime `main` branch is the only live integration branch. Submodule test branches (`test-*`, `feat/*`) were deleted in the post-Experiment-2 cleanup; their work either landed on `main` via merge or is preserved in archive tags.

## Session Handover

### Current State
- **Nono integration (Phases 1-8):** kernel-enforced Landlock sandbox around the Cortex, real-schema policy at `/spine/nono_policy.json`, content-addressable snapshots with Merkle integrity, Merkle-chained NDJSON audit.
- **Phase 2b (just done):** `nono` CLI Popen wrapper replaces `nono_py.sandboxed_exec()` so the Spine can SIGKILL the Cortex; container cgroup limits (`cpus: '2.0'`, `memory: 8G`) added to `docker-compose.yml` as the unbypassable backstop.
- **Secret scrubber:** `runtime_scripts/secret_scrubber.py` scrubs tokens from Gate and xray output at write time so on-disk `llm_logs/` and `xray_data/messages/*.jsonl` never contain raw credentials.
- **`.gitguardian.yaml`:** ignore policy for the `entrypoint.sh` `x-access-token:${GITHUB_TOKEN}@github.com` URL pattern (the token is a shell variable, not a real credential).
- **Experiment 2 report:** `reports/EXPERIMENT_2_REPORT_2026-05-15-to-2026-05-27.md` documents the May 15-27 experiment in full.
- **Hardened Architecture:** Sandbox network isolation and Sentinel Proxy fully implemented.
- **Consolidated Audit:** All security logic (PII, Secrets, Constitution) moved out-of-band to the Sentinel.
- **Unified Memory:** Memory moved into the Git repo (`/app/memory/`) for atomic versioning.
- **Modernized Spine:** Refactored to use the official OpenAI SDK for LLM communication.
- **Branching:** Default branch moved from `feat/talos` to `experiment`.

### Bug Fix Procedure
1.  Apply fixes to the `talos/` submodule on the **`talos_seed`** branch.
2.  Push to submodule remote.
3.  Bump the submodule pointer in `talos_runtime`.
4.  Run `./talosctl start --fresh` to propagate the fix to the agent.
5.  For nono-related issues, also check `talos/spine/sandbox.py` (Popen wrapper + rlimits), `talos/spine/nono_policy.py` (manifest writer), and `docker-compose.yml` (cgroup cap). The nono policy manifest on disk is `/spine/nono_policy.json` — re-read it after restart to confirm the writer emitted what you expected.
