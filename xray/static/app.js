let ws=null,state={},events=[],commit={},containers={},thinkActive=false,autoScroll=true,prevMsgCount=0,currentAssistantEl=null,isPaused=false,callPending=false;
const CONTAINER_KEYS=new Set(["gate","talos","ollama","llamacpp"]);
const COLLAPSE_LINES=5;

function switchView(name){
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  const view=document.getElementById('view-'+name);
  if(view)view.classList.add('active');
  const tab=document.querySelector('.tab[data-view="'+name+'"]');
  if(tab)tab.classList.add('active');
}

function updateStatusUI(){
    const badge=document.getElementById("status-badge");
    const dot=document.getElementById("status-dot");
    const text=document.getElementById("status-text");
    const pending=document.getElementById("pending-indicator");
    const pendingText=document.getElementById("pending-text");
    const pauseBtn=document.getElementById("pause-btn");
    if(isPaused){
        badge.className="status-badge status-paused";
        dot.textContent="⏸";
        text.textContent="Paused";
        if(pauseBtn){pauseBtn.textContent="Resume";pauseBtn.className="btn btn-resume";pauseBtn.onclick=()=>sendCommand("resume")}
        if(callPending){pending.classList.remove("hidden");pendingText.textContent="Waiting on LLM..."}else{pending.classList.remove("hidden");pendingText.textContent="No active call"}
    }else{
        badge.className="status-badge status-running";
        dot.textContent="●";
        text.textContent="Running";
        if(pauseBtn){pauseBtn.textContent="Pause";pauseBtn.className="btn btn-pause";pauseBtn.onclick=()=>sendCommand("pause")}
        pending.classList.add("hidden");
    }
}

document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.tab').forEach(tab=>{
    tab.addEventListener('click',()=>switchView(tab.dataset.view));
  });
  setupScrollPause();
  updateStatusUI();
  connect();
});

function connect(){
  const proto=location.protocol==="https:"?"wss:":"ws:";
  ws=new WebSocket(proto+"//"+location.host+"/ws");
  ws.onopen=()=>{document.getElementById("ws-status").className="status-dot connected"};
  ws.onclose=()=>{document.getElementById("ws-status").className="status-dot error";setTimeout(connect,3000)};
  ws.onmessage=e=>{const msg=JSON.parse(e.data);handleMessage(msg)};
}

function handleMessage(msg){
  switch(msg.type){
    case"full_snapshot":state=msg.state||{};events=msg.events||[];commit=msg.commit||{};containers=msg.container_status||{};renderAll();break;
    case"state_update":state={...state,...msg};if(msg.is_paused!==undefined||msg.call_pending!==undefined){isPaused=msg.is_paused||false;callPending=msg.call_pending||false;updateStatusUI()}renderState();renderHealth();break;
    case"state":state={...state,...msg};if(msg.is_paused!==undefined||msg.call_pending!==undefined){isPaused=msg.is_paused||false;callPending=msg.call_pending||false;updateStatusUI()}renderState();break;
    case"trajectory":renderTrajectory(msg.messages,msg.model,msg.total_count,msg.showing_count);break;
    case"stream_token":appendLiveToken(msg.content,msg.model);break;
    case"tool_call":appendLiveToolCall(msg.name,msg.arguments);break;
    case"tool_result":break;
    case"error":appendError(msg.message,msg.model);break;
    case"think_start":startThink(msg.model);break;
    case"think_end":endThink(msg.tokens_in,msg.tokens_out,msg.context_pct);break;
    case"event":events.push(msg);renderEvents();break;
    case"container_status":containers={};for(const[k,v]of Object.entries(msg)){if(CONTAINER_KEYS.has(k))containers[k]=v}renderContainers();break;
    case"commit_info":commit=msg;renderCommit();break;
  }
}

function startThink(model){
  thinkActive=true;
  document.getElementById("stream-status").className="status-dot active";
  document.getElementById("stream-model").textContent=model||"\u2014";
  document.getElementById("stream-turn").textContent="Turn "+(state.turn||"\u2014");
  if(document.querySelector('.view.active')?.id!=='view-stream') switchView('stream');
}

function endThink(tokensIn,tokensOut,contextPct){
  thinkActive=false;
  document.getElementById("stream-status").className="status-dot";
  if(tokensIn)document.getElementById("tokens-in").textContent="In: "+tokensIn;
  if(tokensOut)document.getElementById("tokens-out").textContent="Out: "+tokensOut;
  if(contextPct!==undefined)updateContextBar(contextPct);
}

function updateContextBar(pct){
  const fill=document.getElementById("context-fill");
  const text=document.getElementById("context-text");
  const pctNum=Math.round(pct*100);
  fill.style.width=pctNum+"%";
  text.textContent=pctNum+"%";
  if(pctNum<60)fill.style.backgroundColor="var(--green)";
  else if(pctNum<85)fill.style.backgroundColor="var(--yellow)";
  else fill.style.backgroundColor="var(--red)";
}

function renderAll(){renderState();renderHealth();renderContainers();renderEvents();renderCommit()}

function renderState(){
  if(state.context_pct!==undefined)updateContextBar(state.context_pct);
  if(state.tokens_used!==undefined)document.getElementById("tokens-total").textContent="Total: "+state.tokens_used;
  if(state.turn!==undefined)document.getElementById("turn-count").textContent="Turn: "+state.turn;
  if(state.model)document.getElementById("model-info").textContent=state.model;
  if(state.spend!==undefined)document.getElementById("spend").textContent="Spend: $"+state.spend.toFixed(2);
}

function renderHealth(){
  const el=document.getElementById("spine-status");
  const spineStatus=state.spine_status||state.status||"unknown";
  el.textContent="Spine: "+spineStatus;
  if(spineStatus==="healthy")el.style.color="var(--green)";
  else if(spineStatus==="stalled")el.style.color="var(--red)";
  else el.style.color="var(--yellow)";
  document.getElementById("lazarus").textContent="Failures: "+(state.consecutive_failures!=null?state.consecutive_failures:"\u2014");
}

function renderContainers(){
  const el=document.getElementById("container-dots");
  el.innerHTML="";
  for(const[name,status]of Object.entries(containers)){
    if(!CONTAINER_KEYS.has(name))continue;
    const d=document.createElement("div");d.className="container-dot";
    const dot=document.createElement("span");dot.className="dot";
    dot.style.backgroundColor=status==="healthy"?"var(--green)":status==="offline"?"var(--dim)":"var(--red)";
    d.appendChild(dot);d.appendChild(document.createTextNode(name));el.appendChild(d);
  }
}

function dedupEvents(evts){const seen=new Set();return evts.filter(e=>{const key=e.type+"|"+e.ts;return!seen.has(key)&&(seen.add(key),true)})}

function renderEvents(){
  const el=document.getElementById("event-list");el.innerHTML="";
  const recent=dedupEvents(events.slice(-50));
  for(const ev of recent){
    const div=document.createElement("div");let cls="event-item";
    const type=ev.type||ev.event_type||"";
    if(type.includes("restart"))cls+=" restart";
    else if(type.includes("crash"))cls+=" crash";
    else if(type.includes("override"))cls+=" override";
    else if(type.includes("started"))cls+=" started";
    div.className=cls;
    const ts=document.createElement("span");ts.className="ts";ts.textContent=(ev.ts||"").substring(11,19);div.appendChild(ts);
    let summary=type.replace(/^(spine\.|cortex\.)/,"");
    if(ev.reason)summary+=" : "+ev.reason;
    if(ev.exit_code)summary+=" (exit "+ev.exit_code+")";
    if(ev.tool)summary+=" \u25b8 "+ev.tool;
    if(ev.success===false)summary+=" \u2717";else if(ev.success===true)summary+=" \u2713";
    div.appendChild(document.createTextNode(summary));el.appendChild(div);
  }
  el.scrollTop=el.scrollHeight;
}

function renderCommit(){
  const el=document.getElementById("commit-info");
  if(!commit.candidate){el.textContent="No commit info";return}
  let text="Candidate: "+commit.candidate.substring(0,8);
  if(commit.candidate_msg)text+=" \u2014 "+commit.candidate_msg;
  if(commit.stable)text+=" | Stable: "+commit.stable.substring(0,8);
  if(commit.ahead)text+=" | "+commit.ahead+" ahead";
  el.textContent=text;
}

async function sendCommand(cmd){await fetch("/api/command",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:cmd})})}

function setupScrollPause(){
  const el=document.getElementById("transcript");if(!el)return;
  el.addEventListener("scroll",()=>{const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<60;autoScroll=atBottom});
}

function maybeScroll(el){if(autoScroll)el.scrollTop=el.scrollHeight}

function createAssistantBubble(){
  const transcript=document.getElementById("transcript");
  const div=document.createElement("div");div.className="msg msg-assistant expanded";
  const label=document.createElement("div");label.className="msg-label";label.textContent="assistant";div.appendChild(label);
  const body=document.createElement("div");body.className="msg-body";div.appendChild(body);
  transcript.appendChild(div);currentAssistantEl=div;return div;
}

function appendLiveToken(content,model){
  if(!currentAssistantEl)createAssistantBubble();
  const body=currentAssistantEl.querySelector(".msg-body");
  body.textContent+=content;maybeScroll(document.getElementById("transcript"));
}

function parseArgKeys(args){
  if(!args)return"";
  if(typeof args==="string"){try{const p=JSON.parse(args);if(Array.isArray(p))return p.map(String).join(", ");if(typeof p==="object")return Object.keys(p).join(", ");return String(p)}catch{return args}}
  if(typeof args==="object"){if(Array.isArray(args))return args.map(String).join(", ");return Object.keys(args).join(", ")}
  return String(args);
}

function formatArgs(argsStr){
  if(!argsStr)return"";
  let parsed=argsStr;
  if(typeof argsStr==="string"){try{parsed=JSON.parse(argsStr)}catch{return argsStr}}
  if(typeof parsed!=="object")return String(parsed);
  if(Array.isArray(parsed))return parsed.map(i=>String(i)).join("\n");
  const lines=[];
  for(const[k,v]of Object.entries(parsed)){
    const val=typeof v==="string"?v:JSON.stringify(v,null,2);
    lines.push(k+": "+val);
  }
  return lines.join("\n");
}

function appendLiveToolCall(name,args){
  if(!currentAssistantEl)createAssistantBubble();
  const sub=document.createElement("div");sub.className="tool-sub";
  const header=document.createElement("div");header.className="tool-header";header.textContent="\u25b8 "+name;
  const argsText=formatArgs(args);
  if(argsText.length>80){
    const argsEl=document.createElement("div");argsEl.className="tool-args collapsed";argsEl.textContent=argsText;
    header.style.cursor="pointer";
    header.addEventListener("click",()=>{
      argsEl.classList.toggle("collapsed");
      header.textContent=argsEl.classList.contains("collapsed")?"\u25b8 "+name:"\u25bd "+name;
    });
    sub.appendChild(header);sub.appendChild(argsEl);
  }else{
    header.textContent+="("+argsText+")";
    sub.appendChild(header);
  }
  currentAssistantEl.appendChild(sub);maybeScroll(document.getElementById("transcript"));
}

function appendError(message,model){
  const transcript=document.getElementById("transcript");
  const div=document.createElement("div");div.className="msg msg-error";
  const label=document.createElement("div");label.className="msg-label";label.textContent="error";div.appendChild(label);
  const body=document.createElement("div");body.className="msg-body";body.textContent=message||"Unknown error";div.appendChild(body);
  transcript.appendChild(div);currentAssistantEl=null;maybeScroll(transcript);
}

function renderTrajectory(messages,model,totalCount,showingCount){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  const newCount=messages?messages.length:0;
  currentAssistantEl=null;transcript.innerHTML="";
  if(totalCount&&totalCount>showingCount){
    const notice=document.createElement("div");notice.className="fold-notice";
    notice.textContent="\u2014 showing last "+showingCount+" of "+totalCount+" messages \u2014";
    transcript.appendChild(notice);
  }
  prevMsgCount=showingCount||newCount;
  const lastToolCallIdMap={};
  for(let i=0;i<messages.length;i++){
    const m=messages[i];const role=m.role||"unknown";
    if(role==="assistant"&&m.tool_calls){for(const tc of m.tool_calls){if(tc.id)lastToolCallIdMap[tc.id]=tc}}
    const div=document.createElement("div");
    const content=typeof m.content==="string"?m.content:JSON.stringify(m.content);
    const lines=content?content.split("\n"):[];
    const needsCollapse=lines.length>COLLAPSE_LINES||content.length>500;
    div.className="msg msg-"+role+(needsCollapse?"":" expanded");
    const label=document.createElement("div");label.className="msg-label";
    if(role==="tool"){
      let toolName="tool";const tcId=m.tool_call_id;
      if(tcId&&lastToolCallIdMap[tcId]){toolName=lastToolCallIdMap[tcId].function?.name||lastToolCallIdMap[tcId].name||"tool"}
      else{for(const[id,tc]of Object.entries(lastToolCallIdMap)){if(tc.function)toolName=tc.function.name||"tool"}}
      label.textContent=toolName;
      if(content&&typeof content==="string"&&content.includes("Error")){const failSpan=document.createElement("span");failSpan.className="fail";failSpan.textContent=" \u2717";label.appendChild(failSpan)}
      else{const okSpan=document.createElement("span");okSpan.className="ok";okSpan.textContent=" \u2713";label.appendChild(okSpan)}
    }else{label.textContent=role}
    div.appendChild(label);
    const body=document.createElement("div");body.className="msg-body"+(needsCollapse?" collapsed":"");body.textContent=content;div.appendChild(body);
    if(m.tool_calls&&role==="assistant"){
      for(const tc of m.tool_calls){
        const sub=document.createElement("div");sub.className="tool-sub";
        const tcName=tc.function?.name||tc.name||"tool";
        const tcArgs=tc.function?.arguments||"{}";
        const formatted=formatArgs(tcArgs);
        const header=document.createElement("div");header.className="tool-header";header.textContent="\u25b8 "+tcName;
        if(formatted.length>80){
          const argsEl=document.createElement("div");argsEl.className="tool-args collapsed";argsEl.textContent=formatted;
          header.style.cursor="pointer";
          header.addEventListener("click",()=>{
            argsEl.classList.toggle("collapsed");
            header.textContent=argsEl.classList.contains("collapsed")?"\u25b8 "+tcName:"\u25bd "+tcName;
          });
          sub.appendChild(header);sub.appendChild(argsEl);
        }else{
          header.textContent+="("+formatted+")";
          sub.appendChild(header);
        }
        div.appendChild(sub);
      }
    }
    if(needsCollapse){
      const toggle=document.createElement("div");toggle.className="msg-toggle";toggle.textContent="Show more";
      toggle.addEventListener("click",()=>{div.classList.toggle("expanded");toggle.textContent=div.classList.contains("expanded")?"Show less":"Show more"});
      div.appendChild(toggle);
    }
    transcript.appendChild(div);
    if(role==="assistant")currentAssistantEl=div;
  }
  maybeScroll(transcript);
}