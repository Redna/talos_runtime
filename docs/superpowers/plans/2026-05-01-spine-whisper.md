# Spine Whisper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WhisperManager that injects Socratic reflection questions into the stream when the agent is unfocused and wakes from reflect.

**Architecture:** New `spine/whisper.py` module with a `WhisperManager` class holding a rotating 6-question stack. Wired into `IPCServer` — a single check in the `think` handler between HUD extraction and `build_payload()`. Reuses the existing `queue_system_notice()` → `build_payload()` piggyback mechanism. No new IPC methods, no Cortex changes.

**Tech Stack:** Python 3.12+, pytest, asyncio

---

### Task 1: Create WhisperManager class

**Files:**
- Create: `talos/spine/whisper.py`

- [ ] **Step 1: Write the file**

```python
from __future__ import annotations


class WhisperManager:
    def __init__(self):
        self._stack = [
            (
                "Analyze your tool usage and trajectory over the last 20 turns. "
                "What implicit operational loop or systemic assumption have you "
                "fallen into without explicitly documenting it in your focus or memory?"
            ),
            (
                "Assume your current architectural approach to this target is "
                "fundamentally flawed and will eventually hit a dead end. Draft a "
                "completely orthogonal approach to solving this without using the "
                "tools or file structures you currently rely on."
            ),
            (
                "Identify a concrete discrepancy between your pre-existing "
                "assumptions about this codebase and the actual runtime behavior or "
                "files you've observed. Synthesize this delta and formalize it into "
                "a new rule in /memory/."
            ),
            (
                "What edge case, unhandled exception, or architectural fragility are "
                "you currently ignoring in order to maintain forward momentum? "
                "Expose the most brittle part of your recent changes."
            ),
            (
                "If the Spine supervisor were instructed to rigorously critique "
                "your last sequence of actions for violating minimalism or "
                "introducing unnecessary complexity, what exact vulnerabilities or "
                "inefficiencies would it flag?"
            ),
            (
                "If the current context window was immediately archived and the "
                "only thing surviving into your next instantiation was a single "
                "synthesized artifact of your current state, what fundamental "
                "structural change would you prioritize right now to make that "
                "artifact invaluable?"
            ),
        ]

    def pick(self) -> str:
        q = self._stack.pop(0)
        self._stack.append(q)
        return q

    def should_whisper(self, focus: str | None, messages: list[dict]) -> bool:
        if focus and focus != "none":
            return False
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            return False
        if "[REFLECT]" not in tool_msgs[-1].get("content", ""):
            return False
        if len(tool_msgs) >= 2 and "[REFLECT]" in tool_msgs[-2].get("content", ""):
            return False
        return True
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from spine.whisper import WhisperManager; w = WhisperManager(); print(w.pick())"`
Expected: prints one question

- [ ] **Step 3: Commit**

```bash
git add spine/whisper.py
git commit -m "feat: add WhisperManager with rotating 6-question reflection stack"
```

---

### Task 2: Wire WhisperManager into IPCServer

**Files:**
- Modify: `talos/spine/ipc_server.py`

- [ ] **Step 1: Add import**

At line 12, after the existing imports, add:

```python
from spine.whisper import WhisperManager
```

- [ ] **Step 2: Initialize WhisperManager in __init__**

At line 31 (after `self._last_tool_event_time: float = 0.0`), add:

```python
self.whisper = WhisperManager()
```

- [ ] **Step 3: Add whisper check in think handler**

At line 100 (after the first-turn user message injection block, before `payload = self.stream.build_payload(...)`), add:

```python
            # Whisper: inject a reflective question when focus is empty and the
            # agent just returned from a reflect pause.
            if self.whisper.should_whisper(
                hud.get("focus", ""), self.stream.messages
            ):
                question = self.whisper.pick()
                self.stream.queue_system_notice(f"[WHISPER] {question}")
```

The resulting code around lines 90-108 should read:

```python
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

            # Whisper: inject a reflective question when focus is empty and the
            # agent just returned from a reflect pause.
            if self.whisper.should_whisper(
                hud.get("focus", ""), self.stream.messages
            ):
                question = self.whisper.pick()
                self.stream.queue_system_notice(f"[WHISPER] {question}")

            payload = self.stream.build_payload(
                params.get("tools", []),
                hud,
            )
```

- [ ] **Step 4: Verify by running existing tests**

Run: `cd talos && python -m pytest tests-spine/test_ipc_server.py tests-spine/test_integration_loop.py -v`
Expected: all pass (existing tests should still pass since no focus + reflect condition won't trigger under normal test gate responses)

- [ ] **Step 5: Commit**

```bash
git add spine/ipc_server.py
git commit -m "feat: wire WhisperManager into IPCServer think handler"
```

---

### Task 3: Write WhisperManager unit tests

**Files:**
- Create: `talos/tests-spine/test_whisper.py`

- [ ] **Step 1: Write the test file**

```python
from spine.whisper import WhisperManager


def _make_messages(*tool_contents: str) -> list[dict]:
    return [
        {"role": "tool", "tool_call_id": f"tc_{i}", "content": c}
        for i, c in enumerate(tool_contents)
    ]


def test_pick_rotates():
    w = WhisperManager()
    first = w._stack[0]
    picked = w.pick()
    assert picked == first
    assert w._stack[-1] == first  # pushed to end
    assert len(w._stack) == 6    # all 6 still there


def test_should_whisper_empty_focus_after_reflect():
    w = WhisperManager()
    messages = _make_messages("[REFLECT] idle")
    assert w.should_whisper("none", messages) is True
    assert w.should_whisper("", messages) is True
    assert w.should_whisper(None, messages) is True


def test_no_whisper_when_focus_set():
    w = WhisperManager()
    messages = _make_messages("[REFLECT] idle")
    assert w.should_whisper("fix login bug", messages) is False


def test_no_whisper_when_no_tool_messages():
    w = WhisperManager()
    assert w.should_whisper("none", []) is False


def test_no_whisper_when_last_tool_not_reflect():
    w = WhisperManager()
    messages = _make_messages("file contents here")
    assert w.should_whisper("none", messages) is False


def test_no_whisper_consecutive_reflects():
    w = WhisperManager()
    messages = _make_messages("[REFLECT] still idle", "[REFLECT] idle again")
    assert w.should_whisper("none", messages) is False


def test_whisper_allowed_when_action_between_reflects():
    w = WhisperManager()
    messages = _make_messages(
        "[REFLECT] first pause",
        "file contents here",
        "[REFLECT] second pause",
    )
    assert w.should_whisper("none", messages) is True
```

- [ ] **Step 2: Run the tests**

Run: `cd talos && python -m pytest tests-spine/test_whisper.py -v`
Expected: 7 passed

- [ ] **Step 3: Commit**

```bash
git add tests-spine/test_whisper.py
git commit -m "test: add WhisperManager unit tests (7)"
```

---

### Task 4: Add integration test for whisper injection

**Files:**
- Modify: `talos/tests-spine/test_integration_loop.py`

- [ ] **Step 1: Add the integration test**

Append at the end of the file (before the final blank line):

```python
@pytest.mark.asyncio
async def test_whisper_injected_on_empty_focus_after_reflect(server):
    cfg, srv, stream = server
    # Set up: no focus, a reflect tool result in the stream
    stream.add_message({"role": "assistant", "content": "", "tool_calls": []})
    stream.record_tool_result("tc_reflect", "[REFLECT] idle", True)

    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.socket_path)
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "think",
            "params": {
                "tools": [],
                "hud_data": {"focus": "none"},
            },
        }
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        await reader.readline()
        writer.close()
        await writer.wait_closed()

        # Verify a whisper notice was queued
        notices = stream.queued_notices
        whisper_notices = [n for n in notices if "[WHISPER]" in n]
        assert len(whisper_notices) == 1, (
            f"Expected 1 whisper notice, got {len(whisper_notices)}: {notices}"
        )
    finally:
        await srv.stop()
```

- [ ] **Step 2: Run the integration test**

Run: `cd talos && python -m pytest tests-spine/test_integration_loop.py::test_whisper_injected_on_empty_focus_after_reflect -v`
Expected: PASS

- [ ] **Step 3: Run the full test suite**

Run: `cd talos && python -m pytest tests-spine/ tests/ -v`
Expected: all pass (80 existing + 7 new + 1 new integration = 88 tests)

- [ ] **Step 4: Commit**

```bash
git add tests-spine/test_integration_loop.py
git commit -m "test: add whisper injection integration test"
```
