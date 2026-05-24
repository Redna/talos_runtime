# Talos Runtime — Agent Guide

## Project Overview

This is the operational environment for **Talos**, a self-evolving autonomous agent running in Docker. The setup consists of four primary containers:

| Container | Purpose | Port |
|---|---|---|
| `talos` (talos_agent) | The agent itself — Spine + Cortex | — |
| `sentinel` (talos_sentinel) | Security Proxy & Semantic Firewall | 8080 |
| `gate` (talos_gate) | Pure LLM proxy / Router | 4000 |
| `xray` (talos_xray) | Live dashboard (WebSocket + REST) | 4040 |

## Repository Layout

```
talos_runtime/                  ← Infrastructure Repository
├── docker-compose.yml          ← Service and Network definitions
├── Dockerfile                  ← Agent container build (with dependencies)
├── entrypoint.sh               ← Spine protection & Dynamic branch setup
├── talosctl                    ← Health Watchdog & Lifecycle CLI
├── sentinel/                   ← Semantic Firewall (mitmproxy)
│   └── mitm_audit.py           ← Out-of-band PII/Secret/Audit logic
├── gate/                       ← LLM proxy (FastAPI)
├── xray/                       ← Live Dashboard
├── talos/                      ← Git submodule → Agent Repository
│   ├── cortex/                 ← Mind: ReAct loop & Tools
│   ├── spine/                  ← Brainstem: Stream & Constitution
│   ├── memory/                 ← Biography: Persistent state (Git-tracked)
│   ├── CONSTITUTION.md         ← Core Directives (P0-P11)
│   └── identity.md             ← Agent Identity
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
```

## Session Handover

### Current State
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
