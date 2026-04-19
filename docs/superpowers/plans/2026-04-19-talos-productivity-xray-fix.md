# Talos Productivity & X-Ray Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the groundhog-day fold loop, stabilize the X-ray dashboard, and add spend tracking to memory so the agent is productive across context folds.

**Architecture:** Three independent changes: (1) enrich the fold mechanism in Spine with a context backpack and last-N preservation, (2) fix X-ray trajectory rendering and WS stability, (3) add spend to HUD data. Each task is self-contained and testable.

**Tech Stack:** Python 3.13, asyncio, httpx, FastAPI, vanilla JS

---

### Task 1: Add `spend` field to HUDData and format it in the HUD

**Files:**
- Modify: `talos/spine/ipc_types.py:15-18` (HUDData dataclass)
- Modify: `talos/spine/stream.py:381-412` (_format_hud method)

- [ ] **Step 1: Write the failing test**

In `talos/tests/spine/test_stream.py`, add a test that verifies `_format_hud` includes spend when provided:

```python
def test_format_hud_with_spend(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.turn = 5
    sm.tokens_used = 1000
    sm.context_pct = 0.2
    hud = sm._format_hud(
        HUDData(memory_keys=2, last_keys=["k1", "k2"], urgency="nominal"),
        0.2, 5, 1000, [],
    )
    assert "Spend:" not in hud
    hud2 = sm._format_hud(
        HUDData(memory_keys=2, last_keys=["k1", "k2"], urgency="nominal", spend=3.50),
        0.2, 5, 1000, [],
    )
    assert "Spend: $3.50" in hud2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talos && uv run pytest tests/spine/test_stream.py::test_format_hud_with_spend -v`
Expected: FAIL — `HUDData` doesn't accept `spend` yet

- [ ] **Step 3: Add `spend` field to `HUDData`**

In `talos/spine/ipc_types.py`, change the `HUDData` dataclass:

```python
@dataclass
class HUDData:
    memory_keys: int
    last_keys: list[str]
    urgency: str
    spend: float = 0.0
```

- [ ] **Step 4: Update `_format_hud` to include spend when nonzero**

In `talos/spine/stream.py`, in the `_format_hud` method, add spend after the tokens line inside the `hud_parts` list:

Find the section:
```python
        hud_parts = [
            "[HUD",
            f"Context: {int(context_pct * 100)}%",
            f"Turn: {turn}",
            f"Tokens: {tokens_used}",
            f"Memory: {hud_data.memory_keys} keys",
        ]
```

Change to:
```python
        hud_parts = [
            "[HUD",
            f"Context: {int(context_pct * 100)}%",
            f"Turn: {turn}",
            f"Tokens: {tokens_used}",
            f"Memory: {hud_data.memory_keys} keys",
        ]
        if hud_data.spend > 0:
            hud_parts.append(f"Spend: ${hud_data.spend:.2f}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd talos && uv run pytest tests/spine/test_stream.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd talos
git add spine/ipc_types.py spine/stream.py tests/spine/test_stream.py
git commit -m "feat: add spend field to HUDData and display in HUD"
```

---

### Task 2: Populate `spend` in HUDData from Gate response

**Files:**
- Modify: `talos/spine/stream.py:94-107` (think method, where ThinkResponse is processed)
- Modify: `talos/spine/ipc_server.py` (where `_parse_think` builds ThinkRequest)

- [ ] **Step 1: Write the failing test**

In `talos/tests/spine/test_stream.py`, add a test verifying that `_format_hud` uses spend from `ThinkRequest.hud_data.spend`:

```python
def test_hud_includes_spend_from_request(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.messages = [
        Message(role="user", content="init"),
        Message(role="assistant", content="thinking", tool_calls=[{"id": "c1", "type": "function", "function": {"name": "reflect", "arguments": "{}"}}]),
        Message(role="tool", content="reflected", tool_call_id="c1"),
    ]
    sm.turn = 5
    req = ThinkRequest(
        focus="stay on task",
        tools=[],
        hud_data=HUDData(memory_keys=2, last_keys=["k1", "k2"], urgency="nominal", spend=7.25),
    )
    payload = sm._build_payload(req)
    tool_msgs = [m for m in payload if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "Spend: $7.25" in tool_msgs[0].content
```

- [ ] **Step 2: Run test to verify it passes**

This should already pass since Task 1 added spend formatting and `_build_payload` calls `_format_hud` with `hud_data`. Run: `cd talos && uv run pytest tests/spine/test_stream.py::test_hud_includes_spend_from_request -v`
Expected: PASS (spend flows through automatically)

- [ ] **Step 3: Wire spend from Gate response into stream state**

In `talos/spine/stream.py`, find the `think` method where `ThinkResponse` is processed (around line 94). Add a line to store spend from the response:

After the line that sets `self.context_pct` from the response, add:
```python
        if resp.context_pct is not None:
            self.context_pct = resp.context_pct
```

Wait — spend comes from the Gate, not the ThinkResponse. Spend needs to be tracked in StreamManager and set from the IPC server. Let me check the flow.

The spend data comes from `gate/app.py` response headers. The Spine IPC server receives the think response and returns it to Cortex. The `HUDData` is constructed in `ipc_server.py` from the cortex's think request parameters. **We need Cortex to pass spend in its think request** or **Spine to track spend itself**.

The simplest approach: Spine tracks cumulative spend from Gate responses and populates `HUDData.spend` before building the payload.

In `talos/spine/stream.py`, add a `spend` attribute to `StreamManager.__init__`:

```python
        self.spend: float = 0.0
```

In `talos/spine/ipc_server.py`, find where the think response is processed and add spend tracking. The Gate returns usage info in the response. Find the `_parse_think` method and the `_think_response_to_dict` method — the spend already flows through `xray_client` but needs to reach `StreamManager`.

Actually, the simplest path: the `xray_client` already tracks spend from Gate. But `StreamManager` doesn't have access to it. Instead, we add `spend` tracking to `StreamManager` directly from the `_send_to_gate` response.

In `talos/spine/stream.py`, find the `_send_to_gate` method and add spend extraction from the response. After `resp.get("usage", {})`, extract `total_tokens` and calculate spend based on model.

Simpler: add to `_format_hud`'s caller in `_build_payload`. The `HUDData` already has `spend` as a field (from Task 1). We need the IPC server to populate it.

In `talos/spine/ipc_server.py`, find where `ThinkRequest` is constructed (in `_parse_think`) and where `HUDData` is built. The Cortex sends the think request — we need to add spend there, OR we handle it in Spine.

**Cleanest approach**: Store spend in `StreamManager` when we get the Gate response, then populate `HUDData.spend` in `_build_payload`.

In `talos/spine/stream.py`, in the `think` method, after `_send_to_gate` returns the response dict, extract spend:

Find the section after `_send_to_gate`:
```python
        resp = await self._send_to_gate(api_req)

        assistant_content = ""
        tool_calls = []
        raw_calls = []
```

Add after variable initialization:
```python
        usage = resp.get("usage", {})
        if usage:
            total_tokens = usage.get("total_tokens", 0)
            if total_tokens > 0:
                self.spend = total_tokens * self._get_cost_per_token()
```

But we don't have `_get_cost_per_token` yet. **Simpler:** Let Spine track cumulative spend from Gate response headers (which already include spend tracking). The Gate response may or may not include spend info.

**Simplest approach:** Skip the Gate response parsing. Instead, **have the Cortex seed_agent pass the current spend to Spine in each think request**. The Cortex already receives spend from previous think calls via the HUD. We add a `spend` field to the think RPC params.

Actually simplest of all: the X-ray client already tracks spend and broadcasts it. We just need to add `spend` to the `state_update` events. But that's X-ray, not Spine.

**Final simplest approach:** Add `spend` tracking to `StreamManager`. Track it from the Gate response. The Gate response includes `usage.total_tokens` (or equivalent). We compute spend from that.

In `talos/spine/stream.py`:
1. Add `self.spend: float = 0.0` to `__init__`
2. In `think()`, after getting the response, add: `self.spend += resp.get("usage", {}).get("total_tokens", 0) * cost_per_1k / 1000` (with a simple cost model)
3. In `get_state()`, add `"spend": round(self.spend, 2)`
4. In `_build_payload()`, set `req.hud_data.spend = self.spend` before formatting

For cost calculation, add a simple method:
```python
    def _get_cost_per_token(self) -> float:
        """Approximate cost per token for the configured model."""
        costs = {
            "gemma4:31b-cloud": 0.000002,
        }
        return costs.get(self.cfg.gate_model, 0.0)
```

Let me simplify this even further. Just track token usage and compute spend.

- [ ] **Step 4: Add spend tracking to StreamManager**

In `talos/spine/stream.py`, in `__init__`, add after `self._pending_notices`:
```python
        self.spend: float = 0.0
```

In the `think` method, after `_send_to_gate`, find where `context_pct` is set from the response and add:
```python
        resp = await self._send_to_gate(api_req)

        assistant_content = ""
        tool_calls = []
        raw_calls = []
        if resp.get("choices"):
            choice = resp["choices"][0]
            assistant_content = choice["message"].get("content", "")
```

After the response parsing block, add spend tracking:
```python
        usage = resp.get("usage", {})
        if usage:
            total_tokens = usage.get("total_tokens", 0)
            self.spend += total_tokens * 0.000002
```

In `_build_payload`, before calling `_format_hud`, set spend on the hud_data:
```python
        hud_data = req.hud_data
        hud_data.spend = self.spend
```

Wait — `HUDData` is a dataclass, modifying it in-place is fine since it's only used once.

In `get_state`, add:
```python
            "spend": round(self.spend, 2),
```

- [ ] **Step 5: Run all stream tests**

Run: `cd talos && uv run pytest tests/spine/test_stream.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd talos
git add spine/stream.py spine/ipc_types.py tests/spine/test_stream.py
git commit -m "feat: track spend in StreamManager and display in HUD"
```

---

### Task 3: Enrich fold with context backpack

**Files:**
- Modify: `talos/spine/stream.py:415-444` (_enforce_fold method)
- Modify: `talos/spine/stream.py:346-367` (_request_fold_synthesis method)
- Add test to: `talos/tests/spine/test_stream.py`

- [ ] **Step 1: Write the failing test**

In `talos/tests/spine/test_stream.py`, add a test that verifies `_enforce_fold` includes a backpack section:

```python
def test_enforce_fold_includes_backpack(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.messages = [
        Message(role="system", content="system"),
        Message(role="user", content="init"),
        Message(role="assistant", content="thinking"),
        Message(role="tool", content="file contents here", tool_call_id="c1"),
        Message(role="assistant", content="more thinking"),
        Message(role="tool", content="more file contents", tool_call_id="c2"),
    ]
    sm.turn = 85
    sm.context_pct = 0.90
    sm._focus = "Fix the memory synthesis module"
    tools = [ToolDef(name="reflect", description="Reflect", parameters={"type": "object", "properties": {}})]
    req = ThinkRequest(focus="Fix the memory synthesis module", tools=tools, hud_data=HUDData(memory_keys=5, last_keys=["k1", "k2"], urgency="nominal"))
    folded_msgs, folded_tools = sm._enforce_fold(sm._build_payload(req), req.tools)
    assert len(folded_tools) == 1
    assert folded_tools[0].name == "fold_context"
    user_msgs = [m for m in folded_msgs if m.role == "user"]
    backpack_msg = None
    for m in user_msgs:
        if "[CONTEXT BACKPACK]" in m.content:
            backpack_msg = m
            break
    assert backpack_msg is not None
    assert "Focus: Fix the memory synthesis module" in backpack_msg.content
    assert "Memory keys (5):" in backpack_msg.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talos && uv run pytest tests/spine/test_stream.py::test_enforce_fold_includes_backpack -v`
Expected: FAIL — `_enforce_fold` doesn't inject a backpack message yet

- [ ] **Step 3: Implement the context backpack in `_enforce_fold`**

In `talos/spine/stream.py`, modify `_enforce_fold` to build and inject a context backpack message:

Replace the current `_enforce_fold` method:

```python
    def _build_backpack(self, req: ThinkRequest) -> str:
        lines = ["[CONTEXT BACKPACK]", f"Focus: {getattr(self, '_focus', 'no focus')}"]
        lines.append(f"Turn: {self.turn}")
        lines.append(f"Tokens used: {self.tokens_used}")
        lines.append(f"Memory keys ({req.hud_data.memory_keys}): {', '.join(req.hud_data.last_keys)}")
        last_tools = [m for m in self.messages if m.role == "tool"][-3:]
        if last_tools:
            lines.append("Recent tool outputs:")
            for t in last_tools:
                content_preview = t.content[:300] if t.content else "(empty)"
                lines.append(f"  {content_preview}")
        lines.append("[/CONTEXT BACKPACK]")
        return "\n".join(lines)

    def _enforce_fold(
        self, messages: list[Message], tools: list[ToolDef]
    ) -> tuple[list[Message], list[ToolDef]]:
        if len(messages) < 2:
            return messages, tools

        folded = [messages[0]]
        if len(messages) > 1:
            folded.append(messages[1])

        backpack = self._build_backpack(ThinkRequest(
            focus=getattr(self, "_focus", "no focus"),
            tools=tools,
            hud_data=HUDData(memory_keys=0, last_keys=[], urgency="nominal"),
        ))

        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "assistant":
                folded.append(messages[i])
                break

        folded.insert(-1, Message(role="user", content=backpack))

        fold_tool = ToolDef(
            name="fold_context",
            description="Compress the conversation context into a summary",
            parameters={
                "type": "object",
                "properties": {
                    "synthesis": {
                        "type": "string",
                        "description": "A concise summary using the DELTA pattern: State Delta, Negative Knowledge, Handoff",
                    },
                },
                "required": ["synthesis"],
            },
        )
        return folded, [fold_tool]
```

Note: `folded.insert(-1, ...)` puts the backpack message just before the last assistant message, which is the most natural placement for context.

Also need to update the test for `test_enforce_fold` — the existing test checks fold behavior. Verify it still works.

Wait — `_enforce_fold` currently receives `messages` and `tools` but needs a `ThinkRequest` to build the backpack. Let me check the current signature and call site.

The call site is in `think()`:
```python
            fold_messages, fold_tools = self._enforce_fold(messages, req.tools)
```

We have access to `req` in the `think` method but not in `_enforce_fold`. The simplest change: pass `req` or just `req.hud_data` to `_enforce_fold`.

Change `_enforce_fold` signature to accept `req: ThinkRequest`:

```python
    def _enforce_fold(
        self, messages: list[Message], tools: list[ToolDef], req: ThinkRequest
    ) -> tuple[list[Message], list[ToolDef]]:
```

And update the call site:
```python
            fold_messages, fold_tools = self._enforce_fold(messages, req.tools, req)
```

Then `_build_backpack` can use `req.hud_data` directly.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd talos && uv run pytest tests/spine/test_stream.py::test_enforce_fold_includes_backpack -v`
Expected: PASS

- [ ] **Step 5: Also enrich `_request_fold_synthesis` to include backpack**

In `talos/spine/stream.py`, modify `_request_fold_synthesis` to include the backpack in its prompt:

Find the method:
```python
    async def _request_fold_synthesis(self, req: ThinkRequest) -> str:
        fold_prompt = (
            "Your context window is nearly full. You MUST produce a DELTA-pattern synthesis of everything above.\n"
            "Format: State Delta (what changed), Negative Knowledge (what didn't work), Handoff (next steps).\n"
            "Write the synthesis as plain text. Be thorough — this replaces your entire conversation history."
        )
```

Replace with:
```python
    async def _request_fold_synthesis(self, req: ThinkRequest) -> str:
        backpack = self._build_backpack(req)
        fold_prompt = (
            "Your context window is nearly full. You MUST produce a DELTA-pattern synthesis.\n"
            f"{backpack}\n\n"
            "Format: State Delta (what changed), Negative Knowledge (what didn't work), Handoff (next steps).\n"
            "Write the synthesis as plain text. Be thorough — this replaces your entire conversation history."
        )
```

- [ ] **Step 6: Update existing tests and run full suite**

The existing `test_enforce_fold` may need updating since we changed the signature. Check it passes:

Run: `cd talos && uv run pytest tests/spine/test_stream.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd talos
git add spine/stream.py tests/spine/test_stream.py
git commit -m "feat: add context backpack to fold synthesis"
```

---

### Task 4: Preserve last 2 tool results during fold

**Files:**
- Modify: `talos/spine/stream.py:452-462` (apply_fold method)
- Add test to: `talos/tests/spine/test_stream.py`

- [ ] **Step 1: Write the failing test**

```python
def test_apply_fold_preserves_last_tool_results(tmp_path):
    cfg = make_config(tmp_path)
    sm = StreamManager(cfg)
    sm.messages = [
        Message(role="system", content="constitution"),
        Message(role="user", content="genesis"),
        Message(role="assistant", content="thinking", tool_calls=[{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]),
        Message(role="tool", content="file contents here", tool_call_id="c1"),
        Message(role="assistant", content="more analysis", tool_calls=[{"id": "c2", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]),
        Message(role="tool", content="command output", tool_call_id="c2"),
        Message(role="assistant", content="final thoughts"),
    ]
    sm.apply_fold("DELTA synthesis: completed memory synthesis")
    assert len(sm.messages) == 5
    assert sm.messages[0].role == "system"
    assert sm.messages[1].role == "user"
    tool_msgs = [m for m in sm.messages if m.role == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0].content == "command output"
    assert tool_msgs[1].content == "DELTA synthesis: completed memory synthesis" or sm.messages[-1].content == "DELTA synthesis: completed memory synthesis"
    assert sm.context_pct == 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talos && uv run pytest tests/spine/test_stream.py::test_apply_fold_preserves_last_tool_results -v`
Expected: FAIL — `apply_fold` currently collapses to 3 messages, not 5

- [ ] **Step 3: Modify `apply_fold` to preserve last 2 tool results**

In `talos/spine/stream.py`, replace the `apply_fold` method:

```python
    def apply_fold(self, synthesis: str):
        if len(self.messages) < 2:
            return
        preserved = [self.messages[0], self.messages[1]]
        tool_messages = [m for m in self.messages if m.role == "tool"]
        last_two_tools = tool_messages[-2:] if len(tool_messages) >= 2 else tool_messages
        if last_two_tools:
            preserved.extend(last_two_tools)
        preserved.append(Message(role="assistant", content=synthesis))
        self.messages = preserved
        self._init_message = None
        self.turn += 1
        self.context_pct = 0.1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd talos && uv run pytest tests/spine/test_stream.py::test_apply_fold_preserves_last_tool_results -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd talos && uv run pytest tests/spine/test_stream.py -v`
Expected: All PASS (may need to update existing `test_apply_fold` if it checks message count)

- [ ] **Step 6: Commit**

```bash
cd talos
git add spine/stream.py tests/spine/test_stream.py
git commit -m "feat: preserve last 2 tool results across context fold"
```

---

### Task 5: X-Ray trajectory debounce — only re-render on message count change

**Files:**
- Modify: `xray/static/app.js:61-68` (handleMessage function)
- Modify: `xray/static/app.js:244-249` (renderTrajectory function)

- [ ] **Step 1: Update `handleMessage` to not call `renderTrajectory` on every `state_update`**

In `xray/static/app.js`, the `handleMessage` function processes `state_update` events and calls `renderAll()` which doesn't call `renderTrajectory`. Verify this is already the case — `renderAll` only calls `renderState`, `renderHealth`, `renderContainers`, `renderEvents`, `renderCommit`. 

Actually, looking at the code, `state_update` only calls `renderState()` and `renderHealth()`, not `renderTrajectory`. The trajectory is only rendered on `trajectory` and `full_snapshot` messages. The flicker issue is likely from the `trajectory` event being sent too frequently.

The fix: add a debounce to trajectory rendering. Only re-render if the `totalCount` or `showingCount` has changed, OR if think just started/ended.

The `lastTrajectoryKey` check is already in place. Let me strengthen it to also include the last message's tool calls or content hash:

In `xray/static/app.js`, update the key calculation in `renderTrajectory`:

```javascript
function renderTrajectory(messages,model,totalCount,showingCount){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  const newCount=messages?messages.length:0;
  const lastContent=messages&&messages.length?messages[messages.length-1].content?.slice(-50)||"":"";
  const lastCalls=messages&&messages.length&&messages[messages.length-1].tool_calls?messages[messages.length-1].tool_calls.length:0;
  const key=totalCount+"|"+showingCount+"|"+newCount+"|"+lastContent+"|"+lastCalls;
  if(key===lastTrajectoryKey)return;
  lastTrajectoryKey=key;
  currentAssistantEl=null;transcript.innerHTML="";
```

This prevents re-rendering when only a token was appended to an already-rendered assistant message (since content changes but key stays the same for the last 50 chars + tool call count).

Wait — streaming tokens change the content on every token. That's intentional for live updates. The real flicker is from full trajectory re-renders happening on every `think_start`/`think_end` event.

Let me check: does the Gate broadcast a full `trajectory` message on every think cycle? Yes — in `gate/app.py`, the trajectory is broadcast after every LLM call.

The fix: only re-render if the key actually changed. Current implementation already does this. The remaining issue is: when a `full_snapshot` is sent on WS reconnect, it causes a full re-render. And WS reconnects happen frequently.

Let me also add: don't re-render trajectory during an active think (streaming). Only render when think is not active.

```javascript
function renderTrajectory(messages,model,totalCount,showingCount){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  const newCount=messages?messages.length:0;
  const key=totalCount+"|"+showingCount+"|"+newCount;
  if(key===lastTrajectoryKey)return;
  lastTrajectoryKey=key;
  currentAssistantEl=null;transcript.innerHTML="";
```

And in `handleMessage`, skip trajectory re-render during streaming:

Find the `case"trajectory"` line and add a check:
```javascript
    case"trajectory":if(!thinkActive){renderTrajectory(msg.messages,msg.model,msg.total_count,msg.showing_count)}break;
```

This means: during live streaming, don't re-render the full trajectory. Only update it when streaming is done (think_end) or on full_snapshot.

And on `think_end`, trigger a trajectory render:
```javascript
    case"think_end":endThink(msg.tokens_in,msg.tokens_out,msg.context_pct);renderState();break;
```

Wait, we need to actually trigger the re-render after think ends. The WS will send a trajectory event when the full response is available. Let me check if `think_end` is followed by a `trajectory` event.

Looking at the Gate code, trajectory is broadcast after each completion (both streaming and non-streaming). The `think_end` event is also sent. So the sequence is: `think_start` → `stream_token`/`tool_call` events → `think_end` → `trajectory` broadcast.

So the fix is: skip trajectory rendering during `thinkActive`, and the `trajectory` event that arrives after `think_end` will trigger the render.

- [ ] **Step 2: Apply changes to `app.js`**

Update `renderTrajectory` to use simplified key and add `thinkActive` check:

```javascript
function renderTrajectory(messages,model,totalCount,showingCount){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  const newCount=messages?messages.length:0;
  const key=totalCount+"|"+showingCount+"|"+newCount;
  if(key===lastTrajectoryKey)return;
  lastTrajectoryKey=key;
  currentAssistantEl=null;transcript.innerHTML="";
```

Update `handleMessage` for `trajectory` case to skip during active think:

```javascript
    case"trajectory":if(!thinkActive)renderTrajectory(msg.messages,msg.model,msg.total_count,msg.showing_count);break;
```

- [ ] **Step 3: Commit**

```bash
cd ..
git add xray/static/app.js
git commit -m "fix: debounce X-ray trajectory rendering during active think"
```

---

### Task 6: X-Ray state diffing — only push changed state

**Files:**
- Modify: `xray/xray_client.py:115-148` (_poll_spine_state method)

- [ ] **Step 1: Add state diffing to `_poll_spine_state`**

In `xray/xray_client.py`, modify `_poll_spine_state` to only emit a `state_update` event if the state has actually changed:

Find the method:
```python
    async def _poll_spine_state(self):
        backoff = 1.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.spine_url}/state")
                    if resp.status_code == 200:
                        self._state = resp.json()
                        self.is_paused = self._state.get("is_paused", False)
                    try:
                        health_resp = await client.get(f"{self.spine_url}/health")
                        if health_resp.status_code == 200:
                            health_data = health_resp.json()
                            self._state["spine_status"] = health_data.get(
                                "status", "unknown"
                            )
                            if "consecutive_failures" in health_data:
                                self._state["consecutive_failures"] = health_data[
                                    "consecutive_failures"
                                ]
                    except Exception:
                        self._state["spine_status"] = "offline"
                    self.on_event(
                        {
                            "type": "state_update",
                            "is_paused": self.is_paused,
                            "call_pending": self.call_pending,
                            **self._state,
                        }
                    )
                    backoff = 1.0
            except Exception:
                backoff = min(backoff * 2, 30.0)
            await asyncio.sleep(3)
```

Add a `_last_state_event` attribute to track the previous state dict, and only emit if it changed:

Add `self._last_state_event: dict = {}` to `XRayClient.__init__`.

Replace the `self.on_event(...)` call with diffing logic:

```python
                    new_event = {
                        "type": "state_update",
                        "is_paused": self.is_paused,
                        "call_pending": self.call_pending,
                        **self._state,
                    }
                    if new_event != self._last_state_event:
                        self._last_state_event = new_event
                        self.on_event(new_event)
                    backoff = 1.0
```

- [ ] **Step 2: Commit**

```bash
cd ..
git add xray/xray_client.py
git commit -m "fix: only push X-ray state updates when state actually changes"
```

---

### Task 7: Add SSE timeout matching to xray_client

**Files:**
- Modify: `xray/xray_client.py:61-92` (_subscribe_gate_stream method)

- [ ] **Step 1: Increase SSE timeout to match Gate**

In `xray/xray_client.py`, the `_subscribe_gate_stream` method uses `httpx.AsyncClient()` without specifying a timeout for the SSE connection. The Gate sends keepalive pings every 15 seconds with a 1800-second total timeout.

Change the SSE client creation to use an appropriate read timeout:

Find:
```python
                    "GET", f"{self.gate_url}/v1/xray/stream"
```

Change the surrounding httpx client to have a longer timeout:
```python
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "GET", f"{self.gate_url}/v1/xray/stream"
                    ) as resp:
```

And for the state stream:
```python
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "GET", f"{self.gate_url}/v1/xray/state"
                    ) as resp:
```

Both methods need the timeout increase from default (5s) to 60s since SSE is long-lived.

Also find the `_subscribe_gate_state` method and apply the same timeout.

- [ ] **Step 2: Commit**

```bash
cd ..
git add xray/xray_client.py
git commit -m "fix: increase SSE timeout in xray_client to match Gate keepalive"
```

---

### Task 8: Full integration test and deploy

**Files:**
- All modified files across tasks

- [ ] **Step 1: Run all Spine tests**

Run: `cd talos && uv run pytest tests/spine/ -v`
Expected: All PASS

- [ ] **Step 2: Build and deploy**

```bash
cd /home/zeus/content/talos_runtime
docker compose build talos xray
docker compose up -d talos xray
```

- [ ] **Step 3: Verify Spine state includes `spend`**

```bash
docker exec talos_agent python3 -c "
import json, urllib.request
r = urllib.request.urlopen('http://localhost:4001/state')
print(json.dumps(json.loads(r.read()), indent=2))
"
```

Expected: `spend` field appears in the state output.

- [ ] **Step 4: Verify fold produces backpack**

Trigger a fold by sending a large context through the agent, or check the logs for `[CONTEXT BACKPACK]` in fold synthesis requests.

- [ ] **Step 5: Verify X-ray is stable**

Open the X-ray dashboard and verify:
- State updates appear without flickering
- Trajectory renders correctly after think cycles
- WS connection stays connected (no rapid disconnects)
- Spend displays in the UI

- [ ] **Step 6: Push all changes**

```bash
cd /home/zeus/content/talos_runtime/talos
git push origin feat/talos
cd /home/zeus/content/talos_runtime
git add talos xray
git commit -m "feat: rich fold, spend tracking, x-ray stability fixes"
git push origin feat/spine-cortex
```