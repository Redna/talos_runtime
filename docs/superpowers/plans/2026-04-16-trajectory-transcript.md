# Trajectory Transcript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live chat transcript to the X-ray dashboard showing the full conversation context the agent sees and thinks.

**Architecture:** Gate intercepts the `messages` array from each completions request and broadcasts it as a `trajectory` event. The frontend renders it as role-colored chat bubbles with collapse/expand, and appends live response tokens/tool_calls to the current assistant bubble.

**Tech Stack:** Python (FastAPI, gate), Vanilla JS/CSS/HTML (xray frontend), WebSocket (xray→browser)

**Spec:** `docs/superpowers/specs/2026-04-16-trajectory-transcript-design.md`

---

### Task 1: Gate broadcasts trajectory event

**Files:**
- Modify: `gate/app.py:207-220` (the `chat_completions` handler, after extracting `body` and before the budget check)

- [ ] **Step 1: Add trajectory broadcast in chat_completions handler**

In `gate/app.py`, inside the `chat_completions` function, after line 211 (`is_streaming = body.get...`) and before line 222 (`if backend_key != "local"...`), insert the trajectory broadcast:

```python
    # Broadcast trajectory: full conversation context the agent sees
    raw_messages = body.get("messages", [])
    trajectory_messages = []
    for m in raw_messages:
        msg = {"role": m.get("role", "")}
        content = m.get("content", "")
        if m.get("role") == "tool" and isinstance(content, str) and len(content) > 2000:
            msg["content"] = content[:2000] + f"[...truncated, {len(content)} chars total]"
        else:
            msg["content"] = content
        if "tool_calls" in m:
            msg["tool_calls"] = m["tool_calls"]
        if "tool_call_id" in m:
            msg["tool_call_id"] = m["tool_call_id"]
        trajectory_messages.append(msg)

    _xray_broadcast({
        "type": "trajectory",
        "messages": trajectory_messages,
        "model": model,
        "ts": time.time(),
    })
```

- [ ] **Step 2: Rebuild and restart gate**

Run: `cd /teamspace/studios/this_studio/talos_runtime && docker compose build gate && docker compose up -d gate`

- [ ] **Step 3: Verify trajectory events appear in SSE stream**

Run: `timeout 20 curl -sN http://localhost:4000/v1/xray/stream 2>&1 | grep trajectory | head -1`

Expected: A line containing `"type":"trajectory"` with a `messages` array.

- [ ] **Step 4: Commit**

```bash
cd /teamspace/studios/this_studio/talos_runtime && git add gate/app.py && git commit -m "feat(gate): broadcast trajectory event with conversation context"
```

---

### Task 2: HTML — Replace stream panel with transcript container

**Files:**
- Modify: `xray/static/index.html:31-39` (the `#stream-panel` section)

- [ ] **Step 1: Replace stream-panel with transcript panel**

In `xray/static/index.html`, replace the `#stream-panel` section (lines 31-39):

```html
        <section id="stream-panel">
            <div id="stream-header">
                <span id="stream-status" class="status-dot"></span>
                <span id="stream-turn">Turn &mdash;</span>
                <span id="stream-model">&mdash;</span>
                <span id="stream-time">&mdash;</span>
            </div>
            <div id="stream-content"></div>
        </section>
```

With:

```html
        <section id="stream-panel">
            <div id="stream-header">
                <span id="stream-status" class="status-dot"></span>
                <span id="stream-turn">Turn &mdash;</span>
                <span id="stream-model">&mdash;</span>
                <span id="stream-time">&mdash;</span>
            </div>
            <div id="transcript"></div>
        </section>
```

Keep `#stream-panel` and `#stream-header` unchanged — only replace `#stream-content` with `#transcript`.

- [ ] **Step 2: Commit**

```bash
cd /teamspace/studios/this_studio/talos_runtime && git add xray/static/index.html && git commit -m "feat(xray): replace stream-content with transcript container"
```

---

### Task 3: CSS — Add chat bubble styles

**Files:**
- Modify: `xray/static/style.css` (append new styles at the end)

- [ ] **Step 1: Add transcript and bubble styles to style.css**

Append the following CSS to the end of `xray/static/style.css` (after the existing `}` on the last line):

```css
#transcript{flex:1;padding:16px;overflow-y:auto;line-height:1.6}
#transcript .msg{margin-bottom:8px;padding:8px 12px;border-radius:6px;position:relative}
#transcript .msg-label{font-size:10px;font-weight:bold;text-transform:uppercase;margin-bottom:4px;letter-spacing:.5px}
#transcript .msg-body{white-space:pre-wrap;word-break:break-word}
#transcript .msg-body.collapsed{max-height:80px;overflow:hidden;position:relative}
#transcript .msg-body.collapsed::after{content:"";position:absolute;bottom:0;left:0;right:0;height:24px;background:linear-gradient(transparent,var(--card))}
#transcript .msg.expanded .msg-body.collapsed{max-height:none;overflow:visible}
#transcript .msg.expanded .msg-body.collapsed::after{display:none}
#transcript .msg-toggle{font-size:10px;color:var(--blue);cursor:pointer;margin-top:4px;user-select:none}
#transcript .msg-system{background:#1c2128;color:var(--dim)}
#transcript .msg-system .msg-label{color:var(--dim)}
#transcript .msg-user{background:#162744;color:var(--text);border-left:3px solid var(--blue)}
#transcript .msg-user .msg-label{color:var(--blue)}
#transcript .msg-assistant{background:var(--card);color:var(--text);border-left:3px solid var(--green)}
#transcript .msg-assistant .msg-label{color:var(--green)}
#transcript .msg-tool{background:#1a1a2e;color:var(--dim);border-left:3px solid var(--yellow)}
#transcript .msg-tool .msg-label{color:var(--yellow)}
#transcript .msg-tool .msg-label .fail{color:var(--red)}
#transcript .msg-tool .msg-label .ok{color:var(--green)}
#transcript .tool-sub{margin:4px 0 4px 12px;padding:4px 8px;background:#2a2a1a;border-radius:4px;font-size:12px;color:var(--yellow)}
#transcript .fold-notice{text-align:center;padding:8px;color:var(--dim);font-style:italic;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin:8px 0}
```

- [ ] **Step 2: Commit**

```bash
cd /teamspace/studios/this_studio/talos_runtime && git add xray/static/style.css && git commit -m "feat(xray): add chat bubble styles for transcript panel"
```

---

### Task 4: JS — Trajectory rendering with chat bubbles and collapse/expand

**Files:**
- Modify: `xray/static/app.js` (full rewrite of message rendering)

This is the largest task. The app.js needs:
1. A `trajectory` handler that renders all messages as chat bubbles
2. `stream_token` and `tool_call` handlers that append to the current assistant bubble
3. Collapse/expand toggle per bubble
4. Auto-scroll with pause on scroll-up
5. Context fold detection

- [ ] **Step 1: Replace app.js with new implementation**

Write the complete new `xray/static/app.js`:

```javascript
let ws=null,state={},events=[],commit={},containers={},thinkActive=false,autoScroll=true,prevMsgCount=0,currentAssistantEl=null;
const CONTAINER_KEYS=new Set(["gate","talos","ollama","llamacpp"]);
const COLLAPSE_LINES=5;
const TRUNCATE_TOOL=2000;

function connect(){const proto=location.protocol==="https:"?"wss:":"ws:";ws=new WebSocket(proto+"//"+location.host+"/ws");ws.onopen=()=>{document.getElementById("ws-status").className="status-dot connected"};ws.onclose=()=>{document.getElementById("ws-status").className="status-dot error";setTimeout(connect,3000)};ws.onmessage=e=>{const msg=JSON.parse(e.data);handleMessage(msg)}}

function handleMessage(msg){switch(msg.type){case"full_snapshot":state=msg.state||{};events=msg.events||[];commit=msg.commit||{};containers=msg.container_status||{};renderAll();break;case"state_update":state={...state,...msg};renderState();renderHealth();break;case"state":state={...state,...msg};renderState();break;case"trajectory":renderTrajectory(msg.messages,msg.model);break;case"stream_token":appendLiveToken(msg.content,msg.model);break;case"tool_call":appendLiveToolCall(msg.name,msg.arguments);break;case"think_start":startThink(msg.model);break;case"think_end":endThink(msg.tokens_in,msg.tokens_out,msg.context_pct);break;case"event":events.push(msg);renderEvents();break;case"container_status":containers={};for(const[k,v]of Object.entries(msg)){if(CONTAINER_KEYS.has(k))containers[k]=v}renderContainers();break;case"commit_info":commit=msg;renderCommit();break}}

function startThink(model){thinkActive=true;document.getElementById("stream-status").className="status-dot active";document.getElementById("stream-model").textContent=model||"\u2014";document.getElementById("stream-turn").textContent="Turn "+(state.turn||"\u2014")}

function endThink(tokensIn,tokensOut,contextPct){thinkActive=false;document.getElementById("stream-status").className="status-dot";if(tokensIn)document.getElementById("tokens-in").textContent="In: "+tokensIn;if(tokensOut)document.getElementById("tokens-out").textContent="Out: "+tokensOut;if(contextPct!==undefined)updateContextBar(contextPct)}

function updateContextBar(pct){const fill=document.getElementById("context-fill");const text=document.getElementById("context-text");const pctNum=Math.round(pct*100);fill.style.width=pctNum+"%";text.textContent=pctNum+"%";if(pctNum<60)fill.style.backgroundColor="var(--green)";else if(pctNum<85)fill.style.backgroundColor="var(--yellow)";else fill.style.backgroundColor="var(--red)"}

function parseArgKeys(args){if(!args)return"";if(typeof args==="string"){try{const p=JSON.parse(args);if(Array.isArray(p))return p.map(String).join(", ");if(typeof p==="object")return Object.keys(p).join(", ");return String(p)}catch{return args}}if(typeof args==="object"){if(Array.isArray(args))return args.map(String).join(", ");return Object.keys(args).join(", ")}return String(args)}

function renderTrajectory(messages,model){const el=document.getElementById("transcript");const prevLen=prevMsgCount;prevMsgCount=messages.length;if(prevLen>0&&messages.length<prevLen/2){const notice=document.createElement("div");notice.className="fold-notice";notice.textContent="\u2014 context folded \u2014";el.appendChild(notice);maybeScroll(el);return}
el.innerHTML="";currentAssistantEl=null;let lastToolCallIdMap={};for(const m of messages){if(m.role==="assistant"&&m.tool_calls){for(const tc of m.tool_calls||[]){if(tc.id)lastToolCallIdMap[tc.id]=tc.function?.name||tc.name||"tool"}}}
for(const m of messages){const div=document.createElement("div");const content=(m.content||"").toString();const lines=content.split("\n").length;const needsCollapse=lines>COLLAPSE_LINES||content.length>500;let roleClass="msg-"+m.role;let label=m.role.toUpperCase();let extraLabel="";if(m.role==="system")roleClass="msg-system";else if(m.role==="user")roleClass="msg-user";else if(m.role==="assistant")roleClass="msg-assistant";else if(m.role==="tool"){roleClass="msg-tool";const toolName=lastToolCallIdMap[m.tool_call_id]||"tool";label="TOOL: "+toolName;extraLabel=document.createElement("span");extraLabel.className="ok";extraLabel.textContent=" \u2713"}
div.className="msg "+roleClass+(needsCollapse?"":" expanded");const labelEl=document.createElement("div");labelEl.className="msg-label";labelEl.textContent=label;if(extraLabel)labelEl.appendChild(extraLabel);div.appendChild(labelEl);const body=document.createElement("div");body.className="msg-body"+(needsCollapse?" collapsed":"");if(m.role==="assistant"&&m.tool_calls&&m.tool_calls.length>0){const textPart=content.trim();if(textPart)body.textContent=textPart;for(const tc of m.tool_calls||[]){const fn=tc.function||{};const sub=document.createElement("div");sub.className="tool-sub";sub.textContent="\u25b8 "+(fn.name||tc.name||"?")+"("+parseArgKeys(fn.arguments||tc.arguments)+")";body.appendChild(sub)}}else{body.textContent=content||"(empty)"}
div.appendChild(body);if(needsCollapse){const toggle=document.createElement("div");toggle.className="msg-toggle";toggle.textContent="Show more";toggle.onclick=()=>{div.classList.toggle("expanded");toggle.textContent=div.classList.contains("expanded")?"Show less":"Show more"};div.appendChild(toggle)}
el.appendChild(div);if(m.role==="assistant")currentAssistantEl=div}
maybeScroll(el)}

function appendLiveToken(content,model){if(!thinkActive)startThink(model);let el=document.getElementById("transcript");if(!currentAssistantEl){currentAssistantEl=createAssistantBubble();el.appendChild(currentAssistantEl)}let body=currentAssistantEl.querySelector(".msg-body");if(!body){body=document.createElement("div");body.className="msg-body";currentAssistantEl.appendChild(body)}if(!body.textContent&&currentAssistantEl.querySelector(".msg-label")){}body.textContent+=content;maybeScroll(el)}

function appendLiveToolCall(name,args){let el=document.getElementById("transcript");if(!currentAssistantEl){currentAssistantEl=createAssistantBubble();el.appendChild(currentAssistantEl)}let body=currentAssistantEl.querySelector(".msg-body");if(!body){body=document.createElement("div");body.className="msg-body";currentAssistantEl.appendChild(body)}const sub=document.createElement("div");sub.className="tool-sub";sub.textContent="\u25b8 "+name+"("+parseArgKeys(args)+")";body.appendChild(sub);maybeScroll(el)}

function createAssistantBubble(){const div=document.createElement("div");div.className="msg msg-assistant expanded";const label=document.createElement("div");label.className="msg-label";label.textContent="ASSISTANT";div.appendChild(label);const body=document.createElement("div");body.className="msg-body";div.appendChild(body);return div}

function maybeScroll(el){if(autoScroll)el.scrollTop=el.scrollHeight}

function setupScrollPause(){const el=document.getElementById("transcript");if(!el)return;el.addEventListener("scroll",()=>{const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<50;autoScroll=atBottom})}

function renderAll(){renderState();renderHealth();renderContainers();renderEvents();renderCommit();setupScrollPause()}

function renderState(){if(state.context_pct!==undefined)updateContextBar(state.context_pct);if(state.tokens_used!==undefined)document.getElementById("tokens-total").textContent="Total: "+state.tokens_used;if(state.turn!==undefined)document.getElementById("turn-count").textContent="Turn: "+state.turn;if(state.model)document.getElementById("model-info").textContent=state.model;if(state.spend!==undefined)document.getElementById("spend").textContent="Spend: $"+state.spend.toFixed(2)}

function renderHealth(){const el=document.getElementById("spine-status");const spineStatus=state.spine_status||state.status||"unknown";el.textContent="Spine: "+spineStatus;if(spineStatus==="healthy")el.style.color="var(--green)";else if(spineStatus==="stalled")el.style.color="var(--red)";else el.style.color="var(--yellow)";document.getElementById("lazarus").textContent="Failures: "+(state.consecutive_failures!=null?state.consecutive_failures:"\u2014")}

function renderContainers(){const el=document.getElementById("container-dots");el.innerHTML="";for(const[name,status]of Object.entries(containers)){if(!CONTAINER_KEYS.has(name))continue;const d=document.createElement("div");d.className="container-dot";const dot=document.createElement("span");dot.className="dot";dot.style.backgroundColor=status==="healthy"?"var(--green)":status==="offline"?"var(--dim)":"var(--red)";d.appendChild(dot);d.appendChild(document.createTextNode(name));el.appendChild(d)}}

function dedupEvents(evts){const seen=new Set();return evts.filter(e=>{const key=e.type+"|"+e.ts;return!seen.has(key)&&(seen.add(key),true)})}

function renderEvents(){const el=document.getElementById("event-list");el.innerHTML="";const recent=dedupEvents(events.slice(-50));for(const ev of recent){const div=document.createElement("div");let cls="event-item";const type=ev.type||ev.event_type||"";if(type.includes("restart"))cls+=" restart";else if(type.includes("crash"))cls+=" crash";else if(type.includes("override"))cls+=" override";else if(type.includes("started"))cls+=" started";div.className=cls;const ts=document.createElement("span");ts.className="ts";ts.textContent=(ev.ts||"").substring(11,19);div.appendChild(ts);let summary=type.replace(/^(spine\.|cortex\.)/,"");if(ev.reason)summary+=" : "+ev.reason;if(ev.exit_code)summary+=" (exit "+ev.exit_code+")";if(ev.tool)summary+=" \u25b8 "+ev.tool;if(ev.success===false)summary+=" \u2717";else if(ev.success===true)summary+=" \u2713";div.appendChild(document.createTextNode(summary));el.appendChild(div)}el.scrollTop=el.scrollHeight}

function renderCommit(){const el=document.getElementById("commit-info");if(!commit.candidate){el.textContent="No commit info";return}let text="Candidate: "+commit.candidate.substring(0,8);if(commit.candidate_msg)text+=" \u2014 "+commit.candidate_msg;if(commit.stable)text+=" | Stable: "+commit.stable.substring(0,8);if(commit.ahead)text+=" | "+commit.ahead+" ahead";el.textContent=text}

async function sendCommand(cmd){await fetch("/api/command",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:cmd})})}

connect();setupScrollPause();
```

- [ ] **Step 2: Commit**

```bash
cd /teamspace/studios/this_studio/talos_runtime && git add xray/static/app.js && git commit -m "feat(xray): render trajectory as chat transcript with collapse/expand"
```

---

### Task 5: Rebuild, deploy, and verify end-to-end

**Files:** None (verification only)

- [ ] **Step 1: Rebuild xray and gate containers**

Run: `cd /teamspace/studios/this_studio/talos_runtime && docker compose build --no-cache gate xray && docker compose up -d gate xray`

- [ ] **Step 2: Restart talos agent for fresh trajectory events**

Run: `cd /teamspace/studios/this_studio/talos_runtime && docker compose restart talos`

- [ ] **Step 3: Wait for cortex to start thinking, then verify trajectory events in WebSocket**

Run this Python script to capture WebSocket events for 30 seconds and check for trajectory:

```python
import asyncio, websockets, json, time

async def test():
    async with websockets.connect('ws://localhost:4040/ws') as ws:
        start = time.time()
        found = False
        while time.time() - start < 30:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                if msg.get("type") == "trajectory":
                    msgs = msg.get("messages", [])
                    roles = [m.get("role") for m in msgs]
                    print(f"TRAJECTORY: {len(msgs)} messages, roles: {roles}")
                    found = True
                    break
            except asyncio.TimeoutError:
                continue
        if not found:
            print("NO trajectory event received in 30s")

asyncio.run(test())
```

Expected output: `TRAJECTORY: N messages, roles: ['system', 'user', 'assistant', 'tool', ...]`

- [ ] **Step 4: Verify dashboard renders chat bubbles at http://localhost:4040**

Open browser to `http://localhost:4040`. Expected:
- Left panel shows chat bubbles with role labels (SYSTEM, FOCUS, ASSISTANT, TOOL)
- Tool messages show tool name label
- Long messages have "Show more" toggle
- New think cycles append live assistant content
- Context bar, health, events, and commit panels still work

- [ ] **Step 5: Final commit (if any adjustments were made)**

```bash
cd /teamspace/studios/this_studio/talos_runtime && git add -A && git commit -m "feat: trajectory transcript live in X-ray dashboard"
```