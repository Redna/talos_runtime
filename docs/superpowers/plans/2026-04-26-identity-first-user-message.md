# Identity-First System Prompt + Initial User Message Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap system prompt order (identity before constitution) and auto-inject a minimal HUD user message on the first `think()` call when no user message exists in the stream.

**Architecture:** Two focused changes to the Spine layer. `constitution.py` swaps concatenation order. `ipc_server.py` inspects the message stream before building the payload and injects a synthetic `role: user` HUD dump if the stream is purely system/assistant/tool.

**Tech Stack:** Python 3.13, pytest-asyncio, existing Spine IPC + Stream abstractions.

---

### Task 1: Swap system prompt order in `constitution.py`

**Files:**
- Modify: `talos/spine/constitution.py:17`
- Test: `talos/tests-spine/test_constitution.py:12-14`

- [ ] **Step 1: Write the failing test**

Update the assertion in `test_constitution.py` to expect identity before constitution:

```python
assert (
    result == "# Identity\nYou are Talos.\n\n# Principles\nAgency and continuity."
)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest talos/tests-spine/test_constitution.py::test_load_system_prompt_concatenates_both_files -v`
Expected: FAIL — actual output starts with constitution, not identity.

- [ ] **Step 3: Swap the concatenation in `constitution.py`**

Change line 17 from:
```python
return f"{constitution}\n\n{identity}"
```
to:
```python
return f"{identity}\n\n{constitution}"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest talos/tests-spine/test_constitution.py -v`
Expected: PASS on both tests.

- [ ] **Step 5: Commit**

```bash
git add talos/spine/constitution.py talos/tests-spine/test_constitution.py
git commit -m "refactor(spine): identity before constitution in system prompt"
```

---

### Task 2: Auto-inject HUD user message on first `think()`

**Files:**
- Modify: `talos/spine/ipc_server.py:78-150`
- Test: `talos/tests-spine/test_ipc_server.py:157-205` (add new test)

- [ ] **Step 1: Write the failing test**

Add this test to `talos/tests-spine/test_ipc_server.py` (after the existing tests):

```python
@pytest.mark.asyncio
async def test_ipc_think_injects_user_message_when_none_exists(tmp_path):
    mock_proxy = MagicMock()
    mock_proxy.call.return_value = {
        "assistant_message": "I'll help",
        "tool_calls": [
            {"id": "c1", "name": "bash_command", "arguments": {"command": "ls"}}
        ],
        "context_pct": 0.35,
        "tokens_used": 120,
        "finish_reason": "tool_calls",
    }
    server, cfg, stream, supervisor = _make_think_setup(tmp_path, gate_proxy=mock_proxy)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.socket_path)
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "think",
            "params": {
                "tools": [],
                "hud_data": {
                    "turn": 0,
                    "context_pct": 0.04,
                    "urgency": "nominal",
                    "memory_files": 7,
                    "focus": "none",
                }
            },
        }
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        resp = json.loads(data)
        assert resp["result"]["turn"] == 1

        # Verify a user message was injected into the stream
        user_msgs = [m for m in stream.messages if m.get("role") == "user"]
        assert len(user_msgs) == 1
        assert "[HUD]" in user_msgs[0]["content"]
        assert "turn=0" in user_msgs[0]["content"]

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
```

Run: `python -m pytest talos/tests-spine/test_ipc_server.py::test_ipc_think_injects_user_message_when_none_exists -v`
Expected: FAIL — user_msgs is empty.

- [ ] **Step 2: Implement the user message injection in `ipc_server.py`**

In the `"think"` branch of `_handle_request`, after parsing `params` and before calling `self.stream.build_payload()`, add a guard that injects a user message if the stream contains none:

```python
        if method == "think":
            if not self.gate_proxy:
                return self._error(req_id, -32000, "No gate proxy configured")
            hud = params.get("hud_data", {})
            hud.setdefault("turn", self.stream.turn)

            # Inject a synthetic user message on first turn if stream has no user input
            has_user = any(m.get("role") == "user" for m in self.stream.messages)
            if not has_user:
                hud_line = (
                    f"[HUD] turn={hud.get('turn', 0)}"
                    f" context_pct={hud.get('context_pct', 0.0):.2f}"
                    f" urgency={hud.get('urgency', 'nominal')}"
                    f" memory_files={hud.get('memory_files', 0)}"
                    f" focus={hud.get('focus', '')}"
                )
                self.stream.add_message({"role": "user", "content": hud_line})

            payload = self.stream.build_payload(
                params.get("tools", []),
                hud,
            )
```

- [ ] **Step 3: Run all IPC tests**

Run: `python -m pytest talos/tests-spine/test_ipc_server.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Run the full spine test suite**

Run: `python -m pytest talos/tests-spine/ -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add talos/spine/ipc_server.py talos/tests-spine/test_ipc_server.py
git commit -m "feat(spine): auto-inject HUD user message on first think when no user input exists"
```

---

### Task 3: Update parent repository submodule

- [ ] **Step 1: Stage and commit the talos submodule bump**

```bash
git add talos
git commit -m "chore: bump talos (identity-first prompt + synthetic user message)"
```

---

## Spec Coverage

| Spec Requirement | Task |
|---|---|
| Identity before constitution in system prompt | Task 1 |
| Synthetic HUD user message on first think | Task 2 |
| No user message → inject; user exists → skip | Task 2 (guard: `if not has_user`) |
| Stream message visible to xray | Existing gate trace writer (no changes needed) |

## Placeholder Scan

- No "TBD", "TODO", or "implement later" found.
- All code blocks contain exact, complete changes.
- All test assertions match the expected behavior.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-26-identity-first-user-message.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
