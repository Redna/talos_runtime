# Talos Implementation Overview

Grounded reference of the actual implementation on `talos_seed` (commit `0af42f0`). Complements [ARCHITECTURE.md](ARCHITECTURE.md) which describes design principles and aspirations.

---

## 1. Two-Process Architecture

The agent runs as two Python processes inside the `talos` Docker container:

| Process | Role | User | Entry Point |
|---------|------|------|-------------|
| **Spine** | Brainstem — manages LLM stream, enforces constitution, supervises Cortex | root | `spine/main.py` |
| **Cortex** | Mind — ReAct loop, tool execution, self-modification | talos | `cortex/__main__.py` → `cortex/seed_agent.py` |

Communication is exclusively via JSON-RPC over Unix domain socket (`/tmp/spine.sock`). Cortex never imports Spine internals; Spine never imports Cortex internals.

### IPC Protocol (9 methods)

| Method | Direction | Purpose |
|--------|-----------|---------|
| `generate` | Cortex → Spine | State-accumulating loop pass with focus, tools, HUD (formerly `think`) |
| `stateless_generate` | Cortex → Spine | Raw pass-through to gate — no stream/HUD/fold mutation |
| `think` | Cortex → Spine | Backward-compat alias for `generate` |
| `tool_result` | Cortex → Spine | Record tool call result in stream |
| `request_fold` | Cortex → Spine | Trigger context fold with synthesis |
| `request_restart` | Cortex → Spine | Request Cortex restart |
| `emit_event` | Cortex → Spine | Log custom event to JSONL |
| `send_message` | Cortex → Spine | Telegram notification |
| `get_state` | Cortex → Spine | Query `{turn: N}` |

### Startup Sequence (`spine/main.py`)

```
1. Load SpineConfig (spine_config.json + env overrides)
2. Create /spine/events/ and /spine/trajectories/ dirs
3. Initialize: EventLogger, HealthMonitor, StreamManager
4. Wire: Supervisor, GateProxy, IPCServer
5. Register Telegram message callback → stream.queue_user_message()
6. Start IPCServer (Unix socket), TelegramPoller, Supervisor (with Cortex subprocess)
```

---

## 2. Spine Components

### 2.1 Config (`spine/config.py`)

`SpineConfig` dataclass at `spine/config.py:7-18`. Key defaults:

| Field | Default | Notes |
|-------|---------|-------|
| `gate_url` | `http://localhost:4000/v1/chat/completions` | Gate proxy endpoint |
| `context_threshold_pct` | `0.85` | Auto-fold advisory threshold |
| `stall_timeout` | `300.0` | Seconds before stall detection |
| `socket_path` | `/tmp/spine.sock` | IPC socket location |

Environment overrides for secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### 2.2 Stream Manager (`spine/stream.py`)

Owns the message buffer. Manages:

- **Message lifecycle**: system prompt (Message 0) + assistant/tool/user messages
- **HUD piggybacking** (`build_payload`, line 136): Appends HUD line to last tool message — never injects new system messages. Only one HUD per message (`_hud_last_index` guard, line 43).
- **Folding** (`fold`, line 81): Archives full trajectory to `/spine/trajectories/{timestamp}.json`, resets messages to system prompt + synthesis, resets turn counter.
- **Stall detection** (`detect_stall`, line 93): Counts tool call frequency in last 10 assistant messages. Threshold: 5 of same tool = stall.
- **Spine context estimate** (`estimate_context_pct`, line 67): `total_chars / 4 / context_window` — second signal against Ollama token count glitches.
- **Queue semantics**: System notices and user messages only flush when piggybacked onto a tool message. Survive to next turn if no tool message is eligible.
- **State persistence** (`write_state`, line 194): Writes `state.json` to `/spine/`.

### 2.3 Gate Proxy (`spine/gate_proxy.py`)

Synchronous HTTP client to the Gate. `call()` method (line 18):
- POSTs messages + tools + turn to `gate_url`
- 600s timeout
- Parses OpenAI-compatible response, normalizes tool_calls into `[{id, name, arguments}]`
- Returns `{assistant_message, reasoning, tool_calls, context_pct, tokens_used, finish_reason}`

### 2.4 IPC Server (`spine/ipc_server.py`)

Async Unix socket server. `_handle_request` dispatches 7 methods. Key behavior in `think` handler (line 84):

1. Records heartbeat
2. Injects synthetic user message on first turn if stream has no real user input
3. Runs `gate_proxy.call()` in thread pool (non-blocking)
4. Adds assistant message to stream with OpenAI-format tool_calls
5. **Auto-fold guard** (lines 174-209):
   - ≥90% context → forced fold (debounced at 95%: requires 2 consecutive readings)
   - ≥85% context → system notice to call `fold_context`
   - <85% → reset consecutive counter
6. **Context divergence detection** (line 163): If Ollama pct and spine estimate differ by >50%, emits `spine.context_divergence` event. Uses spine estimate when Ollama reports >100%.
7. **Telemetry staleness** (line 214): Warns if no tool activity in >60 minutes.
8. `tool_result` handler updates `_last_tool_event_time` (used by staleness check).

### 2.5 Supervisor (`spine/supervisor.py`)

Manages Cortex subprocess lifecycle. Run loop (`run`, line 93):

- Polls Cortex process every 5s
- Writes `health.json` every 5s, `commit.json` every 30s
- Crash handling: >3 consecutive failures → `_revert_to_last_good_commit()` (`git reset --hard`)
- Stall handling: `health.is_stalled()` → kill + restart Cortex
- Pause/resume: checks `.paused` sentinel file
- `_record_good_commit()`: Promotes current HEAD to `last_good_commit` on each healthy cycle

### 2.6 Health Monitor (`spine/health.py`)

`HealthMonitor` tracks:
- `last_event_time`: Updated by `record_event()` on every IPC call
- `cortex_start_time`: Updated by `cortex_started()` 
- `first_think_done`: Set after first successful think

`is_stalled()`: Returns true if `time_since_last_event > stall_timeout`.

### 2.7 Constitution (`spine/constitution.py`)

Loads `CONSTITUTION.md` + `identity.md` from `/app/`, concatenates into system prompt. Falls back to defaults if files missing.

### 2.8 Telegram (`spine/telegram.py`)

- `send_telegram_message()`: Synchronous HTTP POST (used via IPC `send_message` handler)
- `TelegramPoller`: Async long-polling loop via `asyncio.to_thread` for non-blocking HTTP. Auto-discovers `chat_id` from incoming messages.

---

## 3. Cortex Components

### 3.1 Main Loop (`cortex/seed_agent.py`)

The `main()` function (line 103) runs the ReAct loop:

```
while True:
    1. Check .paused / .single_step sentinels
    2. Build HUD from AgentState + context_pct + turn
    3. Call client.generate(focus, tools, hud_data) → get assistant message + tool calls
    4. Update state (tokens_consumed, error_streak reset)
    5. Batch rejection: if >MAX_TOOL_CALLS_PER_TURN (10), reject all
    6. For each tool call:
       a. Record in RepetitionDetector
       b. Check reflect abuse guard
       c. Check stall detection → inject synthetic tool result, break
       d. Execute tool via ToolRegistry
       e. Send tool result to Spine
       f. Handle request_restart (sys.exit)
    7. If single_step was active, re-pause
```

### 3.2 Repetition Detector (`seed_agent.py:25-82`)

Three-tier loop protection:

| Guard | Threshold | Action |
|-------|-----------|--------|
| Low-value tool stall | 3 consecutive `bash_command` | Report only |
| General tool stall | 5 consecutive same tool | Inject synthetic result, break |
| Reflect abuse | 5 `reflect` calls with `sleep_duration=0` in 10-turn window | Block reflect, clear focus |

### 3.3 Spine Client (`cortex/spine_client.py`)

Synchronous Unix socket client. Key design:
- 30s socket timeout
- 10MB max buffer size (prevents memory exhaustion from corrupted responses)
- Newline-delimited JSON (reads until `\n`)
- Wraps all errors as `SpineError(code, message)`

### 3.4 Tool Registry (`cortex/tool_registry.py`)

Decorator-based registration with namespace bucketing. `@registry.tool(description, parameters, bucket="core")` registers a function and generates OpenAI-compatible tool schema. `ToolRegistry` supports:

- **Bucketing:** `_buckets` dict maps namespace → tool names. `get_bucket_schemas(active_buckets)` filters schemas by namespace, always including `core`.
- **Plugin loading:** `reload_plugins()` scans `/app/cortex/plugins/*.py` using `importlib`, discovers functions marked with standalone `@tool` decorator, and registers them dynamically.
- **Max tools:** 60 tool cap enforced on registration.
- **Analytics:** Per-tool call/error counts saved to `analytics.json`.
- **Protected tools:** `set_focus`, `fold_context`, `read_file`, `write_file`, `git_commit` cannot be deregistered.
- `execute()` handles TypeError with detailed diagnostics (missing required params).

### 3.5 Agent State (`cortex/state.py`)

Persisted to `/memory/.agent_state.json`. Tracks:
- `current_focus` — active objective
- `error_streak` — consecutive failures (resets to 0 on successful think)
- `total_tokens_consumed` — cumulative

### 3.6 Tools (`cortex/tools/`)

**Executive** (`executive.py`): `set_focus`, `resolve_focus`, `fold_context`, `reflect` (with sleep + `.wake` sentinel check), `merge_memory_files` (synthesis via `stateless_generate` pipe instead of raw curl)

**File Ops** (`file_ops.py`): `read_file`, `write_file`, `patch_file` (multi-strip-level: -p0, -p1, -p2), `list_files`, `delete_path`, `search_code` (grep -rn), `validate_patch`, `search_and_replace` (exact string match, counts occurrences), `replace_block` (multi-line replacement), `bulk_rename`

**Physical** (`physical.py`): `bash_command` (60s timeout, blocked flags filter), `send_message` (Telegram), `request_restart` (rejects if uncommitted changes)

**Git Ops** (`git_ops.py`): `git_commit` (checks `--cached --quiet` first), `git_push` (blocks protected branches)

**Plugins** (`plugins/delegation.py`): `delegate_task` — isolated sub-agent worker loop via `stateless_generate` with restricted tool schemas (max 8 turns)

---

## 4. Protection Layers

All mechanical (not prompt-based). Listed in order of application:

| # | Layer | Location | Mechanism |
|---|-------|----------|-----------|
| 1 | Startup restore | `entrypoint.sh` | Copies `/spine_backup/` → `/app/spine/` on every container start |
| 2 | Auto-fold guard | `ipc_server.py:176-199` | ≥90% forced fold (debounced), ≥85% advisory notice |
| 3 | Context divergence detection | `ipc_server.py:160-172` | Compares Ollama pct vs spine char/4 estimate, uses spine estimate when Ollama >100% |
| 4 | Batch rejection | `seed_agent.py:155-170` | Rejects entire tool call batch if >10 calls in one turn |
| 5 | Repetition detection | `seed_agent.py:25-82` | Consecutive tool call tracking, low-value threshold at 3, general at 5 |
| 6 | Reflect abuse guard | `seed_agent.py:179-189` | Blocks reflect when 5+ calls with sleep_duration=0 in 10-turn window |
| 7 | Stall break | `seed_agent.py:191-213` | Injects synthetic tool result mid-loop to force tool diversity |
| 8 | Crash revert | `supervisor.py:231-256` | `git reset --hard` to `last_good_commit` after >3 consecutive failures |
| 9 | Socket buffer guard | `spine_client.py:33` | 10MB max response size from Spine |
| 10 | Command filtering | `physical.py:19-21`, `guards.py:8,45-48` | Blocks --no-verify flags, protected branches, dangerous shell patterns |

---

## 5. IPC Flow (One Full Turn)

```
Cortex: seed_agent.py main loop (line 131)
  │
  ├─(1)─► SpineClient.think(focus, tools, hud_data)
  │         └─► Unix socket → IPCServer._handle_request("think")
  │               ├─ health.record_event()
  │               ├─ Inject synthetic user msg if first turn & no real user input
  │               ├─ stream.build_payload(tools, hud)
  │               │     ├─ Deep copy messages
  │               │     ├─ Append queued system notices
  │               │     ├─ Append HUD line (if context >60% or urgency != nominal or notices queued)
  │               │     │     Format: "[HUD] turn=N context_pct=X urgency=Y memory_files=Z focus=W"
  │               │     ├─ Append queued user messages (Telegram)
  │               │     └─ Piggyback suffix onto last tool message
  │               ├─ run_in_executor → gate_proxy.call(payload, tools, turn)
  │               ├─ stream.add_message(assistant + openai-format tool_calls)
  │               ├─ Auto-fold guard check (context_pct vs thresholds)
  │               ├─ Context divergence check
  │               ├─ Telemetry staleness check
  │               ├─ stream.write_state()
  │               └─ Return {tool_calls, context_pct, tokens_used, turn}
  │
  ├─(2)─► Batch rejection check (>10 tool calls)
  │
  ├─(3)─► For each tool call:
  │         ├─ RepetitionDetector.record()
  │         ├─ Reflect abuse guard
  │         ├─ Stall detection → break if stalled
  │         ├─ registry.execute(name, args)
  │         └─ SpineClient.tool_result(id, output, success)
  │               └─► Unix socket → IPCServer._handle_request("tool_result")
  │                     └─ stream.record_tool_result()
  │
  └─(4)─► Loop back to (1)
```

---

## 6. File Layout

```
talos/                        ← Agent source repo (talos_seed branch)
├── cortex/
│   ├── __main__.py           ← Entry: imports + calls seed_agent.main()
│   ├── seed_agent.py         ← Main ReAct loop, RepetitionDetector, HUD builder
│   ├── spine_client.py       ← Unix socket JSON-RPC client
│   ├── state.py              ← AgentState (focus, error_streak, tokens)
│   ├── tool_registry.py      ← Decorator-based tool registration
│   └── tools/
│       ├── executive.py      ← set_focus, resolve_focus, fold_context, reflect, audit, verify, check_constitution
│       ├── file_ops.py       ← read, write, patch, list, delete, search, validate_patch, search_and_replace, bulk_rename
│       ├── physical.py       ← bash_command, send_message, request_restart
│       ├── git_ops.py        ← git_commit, git_checkout, git_push
│       └── guards.py         ← Protected files, blocked flags, dangerous patterns, constitution checks
├── spine/
│   ├── main.py               ← Async entry point, wires all subsystems
│   ├── config.py             ← SpineConfig dataclass + load_config
│   ├── ipc_server.py         ← Async Unix socket JSON-RPC server + auto-fold guard
│   ├── stream.py             ← Message stream, folding, HUD piggyback, stall detection, state persistence
│   ├── gate_proxy.py         ← Synchronous HTTP client to Gate
│   ├── supervisor.py         ← Cortex lifecycle, crash-revert, health+commit file output
│   ├── health.py             ← Stall/startup detection via event timestamps
│   ├── constitution.py       ← System prompt from CONSTITUTION.md + identity.md
│   ├── events.py             ← JSONL event logger
│   └── telegram.py           ← Outbound messages + async poller
├── CONSTITUTION.md           ← P0-P10 constitutional principles
├── identity.md               ← Agent identity + operating model
├── tests/                    ← Cortex tests
└── tests-spine/              ← Spine tests
```
