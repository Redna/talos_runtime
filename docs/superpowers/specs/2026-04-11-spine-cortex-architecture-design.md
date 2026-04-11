# Talos V2: Spine-Cortex Architecture Design

**Date:** 2026-04-11  
**Status:** Draft  
**Scope:** Agent core redesign (Cortex + Spine). Runtime infrastructure (talosctl, Gate, Docker Compose, llama.cpp) is preserved.

---

## 1. Problem Statement

The current agent (Ouroboros) has accumulated architectural debt that makes it hard to control and observe:

1. **Silent failures.** When the agent crashes, it's unclear why. The watchdog itself fails. Recovery is unreliable.
2. **Cache invalidation.** Scattered prompt fragments in the codebase cause KV cache misses, broken message formats, and LLM API errors.
3. **No observability.** The agent's reasoning is stripped from historical turns. There's no structured log of decisions. Debugging requires reading raw JSONL.
4. **Uncontrolled spirals.** Error streaks adjust temperature but don't change strategy. The agent can get stuck in loops with no effective intervention.
5. **Incomplete tool error handling.** Tool crashes leave orphaned tool calls in the stream, causing 400 API errors on the next request.

## 2. Design Principles

1. **Spine owns the stream.** The Cortex never constructs LLM messages directly. All message construction, shedding, and HUD injection go through the Spine.
2. **Spine is immutable from the agent's perspective.** Written in Go, compiled to a static binary. The Cortex cannot modify it.
3. **Every state transition is a structured event.** Not just JSONL chat logs — a separate event stream for observability, crash forensics, and control.
4. **Fixed-window shedding with deterministic rules.** The same shed logic every turn. No variable tiers. The prefix is always frozen, the active window is always the last M turns, everything else is shed identically.
5. **Constitutional evolution is additive, not destructive.** The agent can modify its Constitution, but only through clarification and expansion. The Constitutional Auditor (in the Gate) enforces this.
6. **Crashes are loud, never silent.** Every crash produces a forensics bundle. Every orphaned tool call gets a synthetic result. Every API error is handled with retry or clear fallback.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│  Host (talosctl)                                         │
│  - Manages Docker Compose lifecycle                       │
│  - Lazarus Protocol (crash recovery for container-level)  │
└──────────────┬───────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────┐
│  Docker Stack                                            │
│                                                          │
│  ┌─────────────────────┐  ┌───────────────────────────┐  │
│  │  SPINE (Go)         │  │  Gate (FastAPI)            │  │
│  │  - Event bus        │  │  - LLM proxy/routing       │  │
│  │  - Stream owner     │  │  - Budget enforcement      │  │
│  │  - State snapshots  │  │  - Trace logging            │  │
│  │  - Health monitor   │  │  - Constitutional audit     │  │
│  │  - HUD injection    │  │                             │  │
│  │  - Control plane    │  │                             │  │
│  │  - Fold enforcement │  │                             │  │
│  │  - Essential notifs │  │                             │  │
│  │    (Telegram, API)  │  │                             │  │
│  └───────┬─────────────┘  └───────────▲─────────────────┘  │
│          │ Unix socket                │ HTTP                │
│          │ (IPC)                      │                     │
│  ┌───────▼────────────────────────────┴─────────────────┐  │
│  │  CORTEX (Python)                                     │  │
│  │  - LLM decision loop (ReAct)                        │  │
│  │  - Tool registry & execution                        │  │
│  │  - Self-modifiable source                           │  │
│  │  - Memory operations (direct /memory access)       │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  llama.cpp (Inference)                              │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                          │
│  Volumes:                                                │
│  /memory          → Agent state (Cortex reads/writes)    │
│  /spine           → Spine observability (Cortex read-only)│
│  /app             → Cortex source (named volume)          │
│  /runtime_logs    → LLM traces (Gate)                    │
│  /models          → .gguf files                          │
└──────────────────────────────────────────────────────────┘
```

---

## 4. The Spine

The Spine is a thin, deterministic state machine written in Go. It owns everything the Cortex should not be able to modify.

### 4.1 Responsibilities

| Responsibility | Description |
|---|---|
| **Stream construction** | Builds every LLM API call: system prompt + stream + HUD. The Cortex never touches message construction. |
| **Shedding** | Applies fixed-window shedding every turn. Last M turns at full fidelity, everything else shed with deterministic rules. |
| **Fold enforcement** | When context > threshold, overrides tool list to only `fold_context` with `tool_choice="required"`. |
| **Event logging** | Every state transition is emitted as a structured event. The event log is the source of truth for observability. |
| **State snapshots** | Periodically saves full Cortex state to `/spine/snapshots/`. On crash recovery, restores the last good state. |
| **Health monitoring** | Watches for Cortex stalls (no events within timeout), crashes (process exit), and startup failures (exit within 30s). |
| **Cortex supervision** | Starts, stops, and restarts the Cortex process. Manages the lifecycle. |
| **Tool validation** | Validates tool schemas included in each `think()` call on-the-fly (standard OpenAI JSON Schema format). Validates tool calls at runtime. Rejects malformed schemas and calls. |
| **Control plane** | Exposes HTTP API on port 4001 for external observation and control. |
| **Error synthesis** | Generates synthetic tool results for orphaned tool calls, timeouts, and LLM API errors. Never lets a malformed payload reach the LLM. |
| **Essential notifications** | Hardcoded Telegram adapter and Control Plane API for crash alerts, health status, and essential notifications. Works even when the Cortex is down. |

### 4.2 The Spine is NOT

- An LLM. It contains zero inference calls.
- A decision-maker. It follows deterministic rules, not judgments.
- Modifiable by the Cortex. It's a compiled Go binary outside the Cortex's writable directories.
- A replacement for the Gate. The Gate handles LLM proxying, routing, budget enforcement, and constitutional auditing. The Spine handles stream construction, shedding, HUD injection, and observability. The Spine sends LLM calls through the Gate (which routes to local llama.cpp or cloud APIs), not directly to the inference backend.

### 4.3 Spine Determinism

The Spine is mostly deterministic:
- **Deterministic:** Message construction, shedding, HUD formatting, event logging, state snapshots, tool validation. Given the same inputs, always produces the same outputs.
- **Wall-clock dependent:** Health check timeouts, control plane message routing, crash recovery decisions. These are simple if/else logic, not LLM-driven.

---

## 5. The Cortex

The Cortex is the self-evolving Python agent. It's the decision-maker.

### 5.1 Responsibilities

| Responsibility | Description |
|---|---|
| **Decision loop** | The ReAct loop: observe → think → act → observe. Calls `spine.think()` to reason, executes tools directly. |
| **Tool execution** | Owns the tool registry. Executes tools and returns results to the Spine. |
| **Self-modification** | Can write to its own Python source files within `/app`. Commits via git. Requests restart via `spine.request_restart()`. |
| **Memory management** | Direct filesystem access to `/memory` for KV store, agenda, task queue, folds. The Cortex reads and writes its own data without going through the Spine. |
| **Communication tools** | The Cortex can create any communication tool (Discord, Slack, email, etc.) as regular Python tools. These are just tools — they call external APIs directly. No Spine involvement needed beyond the standard IPC. |

### 5.2 What the Cortex Cannot Do

- Construct LLM messages directly. It calls `spine.think()` and receives the LLM's response.
- Modify the Spine binary or its configuration.
- Modify the system prompt construction pipeline. CONSTITUTION.md and identity.md are writable files on disk — the Cortex can modify them via `write_file`/`patch_file`. However, any commit touching these files is validated by the Constitutional Auditor (pre-commit hook), and the Spine refuses to construct an LLM call with an empty or missing Constitution.
- Silence or modify event logging.
- Bypass the Constitutional Auditor (it's in the Gate, outside the Cortex's control).
- Inject content directly into the stream (only the Spine appends messages).

### 5.3 Cortex ↔ Spine IPC Protocol

Communication via Unix domain socket with JSON-RPC-like protocol.

**Cortex → Spine (requests):**

| Method | Purpose | Parameters |
|--------|---------|------------|
| `think` | Call the LLM with current stream and tool definitions | `{focus: string, tools: [{name, description, parameters}]}` — tools use standard OpenAI JSON Schema format |
| `tool_result` | Return tool execution result | `{tool_call_id: string, output: string, success: bool}` |
| `request_fold` | Request a context fold | `{synthesis: string}` |
| `request_restart` | Request a clean restart | `{reason: string}` |
| `emit_event` | Log a custom event | `{type: string, payload: object}` |
| `get_state` | Query the Spine's view of agent state | `{keys: [string]}` — returns authoritative values for context_pct, turn, tokens_consumed, fold_pending, etc. |

**Note on `think` and tools:** The Cortex includes its current tool definitions (in standard OpenAI JSON Schema format) with every `think()` call. The Spine validates them on-the-fly — no separate registration step. If a tool schema is invalid, the Spine rejects that specific tool and logs a warning, but continues with the valid ones. This is aligned with P5 (Minimalism) and P3 (LLM-First) — no extra registration protocol, just the standard OpenAI convention.

**Note on `get_state`:** The Spine maintains an authoritative view of agent state (context window usage, turn count, total tokens, whether a fold is pending, etc.). The Cortex can query this to get accurate values that may differ from its own stale context after a fold or restart.

**Spine → Cortex (push notifications):**

| Event | Purpose | When |
|-------|---------|------|
| `force_fold` | Context threshold exceeded | Context > 85% |
| `system_notice` | External message or system event | Telegram message, crash recovery, etc. |
| `tool_timeout` | A tool exceeded its time limit | After configurable timeout |
| `pause` | Stop the Cortex loop | Operator command |
| `resume` | Resume the Cortex loop | Operator command |

---

## 6. Stream Management

### 6.1 The Frozen Stream Invariant

The Spine constructs every LLM API call. The stream structure is:

```
Message 0: System prompt (CONSTITUTION.md + identity.md)   [FROZEN — never changes within a session]
Message 1: Initial context / agenda                        [FROZEN — set at session start]
Message 2: Fold synthesis (if any folds occurred)          [FROZEN after fold]
Messages 3 to 3+M: Last M turns at full fidelity             [ACTIVE WINDOW]
Messages beyond 3+M: Shed turns                             [SHED]
```

**What's frozen:** Messages 0 and 1 are constructed once at session start and never modified. This guarantees KV cache hits for the prefix.

**What's append-only:** New turns are appended to the end of the stream. The Spine never mutates a message that's already been sent.

**What's NOT in the stream:** No dynamic HUD as a separate message. No timestamp updates to old messages. No state injections into the system prompt. The HUD is piggybacked as a suffix on the last tool output.

### 6.2 Shedding

Shedding happens **every turn** during stream construction. It's not a special event — it's how the Spine always builds the LLM payload.

**The shed rule is always the same:**

For all turns outside the active window (messages beyond the last M turns):
- Assistant messages: Keep the first line (the decision). Strip the rest.
- Tool call parameters: Strip entirely, replaced with `[args: {tool_name}]`
- Tool outputs: Truncate to 500 chars with `[… N chars archived]`

The window parameter `M` (default: 5) is the number of turns kept at full fidelity.

### 6.3 Folding

Folding is a **context reset** that happens when context usage exceeds a threshold (default ~85%).

**How folding is enforced:**

1. The Spine detects context > 85% before constructing the next `think()` call.
2. The Spine overrides the `tools_available` parameter to only include `fold_context`.
3. The Spine sets `tool_choice: "required"`, forcing the LLM to call the one available tool.
4. The Spine injects a system notice: `[SYSTEM | Context at X%. You MUST use fold_context immediately.]`
5. The LLM calls `fold_context(synthesis=...)` using the DELTA pattern (State Delta, Negative Knowledge, Handoff).
6. The Spine replaces the entire stream with: Message 0 + Message 1 + fold synthesis.
7. The Cortex continues with a clean context window and the full tool list restored.

**Structural guarantee:** The Cortex cannot skip a fold. It doesn't control the tool list — the Spine does. With `tool_choice: "required"` and only `fold_context` available, the LLM has no choice but to comply.

### 6.4 Fold History

Each fold's synthesis is preserved in `/memory/folds/` as a separate file (managed by the Cortex). The agent can `recall_memory("fold_N")` to recover reasoning from a previous fold. Folding moves information from hot context to cold storage, not into the void.

---

## 7. The Event System

### 7.1 Event Types

Every state transition is a structured event with a type, timestamp, and context:

```json
{"type": "cortex.think", "ts": "...", "turn": 42, "focus": "fix_auth_bug", "tokens_used": 1847}
{"type": "cortex.tool_call", "ts": "...", "tool": "replace_symbol", "args_summary": "patch_auth_validator", "duration_ms": 320}
{"type": "cortex.tool_result", "ts": "...", "tool": "replace_symbol", "success": true, "output_chars": 142}
{"type": "cortex.focus_set", "ts": "...", "from": null, "to": "fix_auth_bug"}
{"type": "cortex.focus_resolved", "ts": "...", "focus": "fix_auth_bug", "synthesis": "Auth validator patched"}
{"type": "spine.context_fold", "ts": "...", "reason": "threshold_85pct", "turns_shed": 12, "synthesis_chars": 580}
{"type": "spine.hud_inject", "ts": "...", "urgency": "nominal", "context_pct": 62, "turn": 43}
{"type": "spine.heartbeat", "ts": "...", "cortex_pid": 12345, "uptime_sec": 3600}
{"type": "spine.stall_detected", "ts": "...", "last_event_age_sec": 620}
{"type": "spine.cortex_restart", "ts": "...", "reason": "stall_detected", "last_focus": "fix_auth_bug"}
{"type": "cortex.self_modify", "ts": "...", "files_changed": ["seed_agent.py"], "commit": "abc1234"}
{"type": "gate.audit_result", "ts": "...", "rejected": false, "reason": "No violations"}
{"type": "spine.cortex_crash", "ts": "...", "exit_code": 1, "last_event_age_sec": 0, "state_snapshot": "/spine/snapshots/2026-04-11T14:30:00.json"}
```

### 7.2 Crash Forensics

When the Cortex crashes, the Spine produces a forensics bundle in `/spine/crashes/`:

- `last_100_events.jsonl` — the last 100 events before the crash
- `state_snapshot.json` — the last saved state
- `crash_summary.md` — human-readable summary

On recovery, the Spine injects a system notice containing:
- What was being worked on (last focus)
- What code changed (commit diff)
- Whether the restart was intentional or a crash
- The crash forensics summary

---

## 8. Error Handling

### 8.1 Incomplete Tool Executions

If a tool crashes mid-execution (no tool result in the stream), the Spine synthesizes a result:

```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "[SYSTEM | Tool execution failed due to process crash. The tool '{tool_name}' did not produce output. Approach this task differently.]"
}
```

This ensures the message sequence is always well-formed when sent to the LLM.

### 8.2 Tool Execution Timeouts

Each tool call has a configurable timeout (default 120s). If the tool doesn't return within the timeout, the Spine sends a synthetic result: `[SYSTEM | Tool '{tool_name}' timed out after {timeout}s. Consider a different approach.]`

### 8.3 LLM API Errors

- **400 (Bad Request):** The Spine validates the payload structure and attempts repair (remove orphaned tool calls, fix message ordering). If repair fails, force a context fold to start fresh.
- **429 (Rate Limit):** Exponential backoff retry (1s, 2s, 4s, 8s, max 60s).
- **500/502/503 (Server Error):** Retry with backoff, up to 3 attempts. If all fail, inject synthetic response: `[SYSTEM | LLM backend unavailable. Wait and retry.]`

### 8.4 Startup Failure Detection

If the Cortex process exits within the first 30 seconds (before the first successful `think()`), the Spine:
1. Automatically reverts the last commit (`git reset --hard HEAD~1`)
2. Restarts the Cortex with pre-modification code
3. Injects system notice: "Your last modification broke startup. It has been reverted. Try a different approach."

This is a startup-only safeguard. Runtime failures are handled by the Lazarus Protocol.

---

## 9. Crash Recovery (Lazarus Protocol)

### 9.1 Layered Recovery

| Layer | Handles | Mechanism |
|-------|---------|-----------|
| **Spine** | Cortex process crashes | Detects exit code, restarts process, injects crash forensics |
| **Docker** | Container-level crashes (OOM, segfault) | Docker restart policy, Spine reads state snapshot on startup |
| **talosctl** | Spine/container can't start | Captures `docker compose logs`, reverts commits on named volume |

### 9.2 Lazarus Protocol (Spine-managed)

The Spine tracks consecutive failures on the same focus:

| Failure Count | Action |
|---|---|
| 1 | Restart with notice |
| 2 | Restart with notice + 1-commit revert |
| 3 | Restart with notice + 2-commit revert |
| 4 | Restart with notice + 3-commit revert |
| 5 | System override — force the agent to abandon this approach |

### 9.3 Proactive State Snapshots

The Spine saves state snapshots periodically (every N successful turns, configurable). This means even an OOM kill has a recent snapshot to recover from. Snapshots include:
- Current focus and task queue
- Memory state (KV store)
- Cognitive parameters (temperature, error streak)
- Last N events

---

## 10. Self-Modification Pipeline

### 10.1 Flow

```
Cortex decides to modify code
        │
        ▼
Cortex calls write_file / patch_file / replace_symbol
        │
        ▼
Tool executes: write to filesystem + ast.parse() validation
        │
        ▼ (syntax OK)
Cortex calls git_commit
        │
        ▼
Pre-commit hooks run:
  1. mypy (type check)
  2. py_compile (syntax)
  3. pytest (logic verification)
  4. Constitutional Auditor (semantic review via Gate)
        │
        ▼ (all pass)
Commit succeeds
        │
        ▼
Cortex calls spine.request_restart(reason)
        │
        ▼
Spine takes a state snapshot (focus, memory, queue, last N events)
        │
        ▼
Spine sends SIGTERM to Cortex process
        │
        ▼
Spine starts new Cortex process with updated code
        │
        ▼
New Cortex awakens → Spine injects system notice about the restart
```

### 10.2 Constitution and Identity Evolution

- `CONSTITUTION.md` and `identity.md` are **writable files on disk**. The Cortex can modify them using `write_file` or `patch_file`, just like any other file.
- Any commit that touches these files triggers the **Constitutional Auditor** (pre-commit hook), which evaluates whether changes are clarifications or expansions (allowed) versus deletions or inversions (blocked).
- The Spine detects Constitution hash changes on each `think()` cycle and reloads the system prompt if the file has changed. Changes take effect immediately, without restart.
- The Spine enforces one hard invariant: the Constitution must never be empty or missing. If the file is blank or absent, the Spine refuses to construct an LLM call and injects a system notice requiring the agent to restore it.
- This aligns with P2 (Self-Creation): the agent can evolve its Constitution, but only additively. The Auditor and Spine together enforce the Ship of Theseus constraint.

### 10.3 Containment

The containment model from the current architecture is preserved:
- The Cortex runs as a non-root user (`talos`) via `gosu`
- `/runtime_scripts/` and `/app/.git/hooks/` are owned by root with 755 permissions
- The Spine binary is outside the Cortex's writable directories
- The `bash_command` tool rejects `--no-verify` and equivalent flags

---

## 11. Tool Schema Safety

Tool definitions follow the standard OpenAI JSON Schema convention. The Cortex includes tool definitions with every `think()` call — no separate registration protocol.

### 11.1 On-the-Fly Validation

When the Spine receives a `think()` call with tool definitions, it validates each schema on-the-fly:
- Must have `name` (string), `description` (string), `parameters` (valid JSON Schema object)
- No duplicate tool names within the same call
- Each parameter must have a `type` field
- Required parameters must be listed

Invalid schemas are rejected for that specific tool (the Spine logs a warning), but the rest of the call proceeds. The rejected tool is simply unavailable for that turn.

### 11.2 Runtime Validation

When the LLM returns a tool call, the Spine validates:
- Missing required parameters → reject with synthetic error
- Wrong types → reject with type error
- Unknown tool name → reject with available tools list

### 11.3 Tool Evolution

Since tools are included in every `think()` call, self-modification works naturally: after the Cortex modifies a tool and restarts, the next `think()` call includes the updated tool definition. No separate re-registration step is needed. The Spine always uses the tools provided in the current call — no stored state about tools between calls.

---

## 12. Control Plane

### 12.1 Spine Control Plane API (Port 4001)

| Endpoint | Purpose |
|----------|---------|
| `GET /status` | Current focus, turn count, context %, uptime, last event |
| `GET /events?tail=100` | Last N structured events |
| `GET /state` | Full state snapshot (memory, queue, focus, cognitive params) |
| `POST /message` | Inject a message to the Cortex (becomes a system notice) |
| `POST /command` | Send a control command (pause, resume, force_fold, force_restart) |
| `GET /health` | Spine health check (for talosctl) |

### 12.2 Communication Architecture

Communication is split between essential Spine notifications and Cortex-managed channels:

**Spine (essential notifications only):**
- Hardcoded Telegram adapter for crash alerts, health status, and critical notifications
- Control Plane API on port 4001 for observation and control
- Works even when the Cortex is down — the Spine can always reach the creator

**Cortex (all other communication):**
- The Cortex can create any communication tool as regular Python tools (Discord, Slack, email, etc.)
- These tools call external APIs directly — no Spine involvement needed
- The agent can evolve its own communication capabilities via self-modification (P2)
- Existing Telegram conversations happen through the Spine's adapter, but rich interactions are Cortex-driven

**Incoming messages:**
1. Creator sends a message via Telegram
2. Spine's Telegram adapter receives it
3. Spine injects it as a `system_notice` push notification to the Cortex
4. Cortex processes it in the next `think()` cycle

---

## 13. Volume Layout

All persistent data lives within the repo but is gitignored to prevent accidental commits.

```
talos_runtime/
  memory/            → gitignored, Cortex reads/writes directly
    agenda.md            Agent's task list and priorities
    agent_memory.json    KV store (50-slot cap)
    task_queue.json      Prioritized task queue
    folds/               Context fold synthesis history
    ...
  spine/             → gitignored, Cortex read-only, Spine manages
    events/              Structured event log (JSONL)
    snapshots/           State snapshots for crash recovery
    crashes/             Crash forensics bundles
    ...
.gitignore              → /memory/, /spine/
```

| Container Mount | Purpose | Managed By |
|-----------------|---------|------------|
| `/memory` | Agent state, KV store, task queue, folds | Cortex (direct filesystem access) |
| `/spine` | Event log, state snapshots, crash forensics | Spine (Cortex read-only) |
| `/app` | Cortex source code (named Docker volume) | Cortex (self-modifiable) |
| `/runtime_logs` | LLM call traces and audit logs | Gate |
| `/models` | `.gguf` model files | llama.cpp |