let ws=null,state={},events=[],commit={},containers={},autoScroll=true,currentTurnEl=null,stepPending=false;
const CONTAINER_KEYS=new Set(["gate","talos","ollama","llamacpp"]);
const COLLAPSE_LINES=8;
const COLLAPSE_CHARS=800;

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
    const pauseBtn=document.getElementById("pause-btn");
    if(state.is_paused){
        badge.className="status-badge status-paused";
        dot.textContent="\u23f8";
        text.textContent="Paused";
        if(pauseBtn){pauseBtn.textContent="Resume";pauseBtn.className="btn btn-resume";pauseBtn.onclick=()=>sendCommand("resume")}
        const stepBtn=document.getElementById("step-btn");
        if(stepBtn)stepBtn.style.display="inline-block";
    }else{
        badge.className="status-badge status-running";
        dot.textContent="\u25cf";
        text.textContent="Running";
        if(pauseBtn){pauseBtn.textContent="Pause";pauseBtn.className="btn btn-pause";pauseBtn.onclick=()=>sendCommand("pause")}
        const stepBtn=document.getElementById("step-btn");
        if(stepBtn)stepBtn.style.display="none";
    }
    updateStepButton();
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
  ws.onclose=()=>{document.getElementById("ws-status").className="status-dot error";setTimeout(connect,5000)};
  ws.onmessage=e=>{const msg=JSON.parse(e.data);handleMessage(msg)};
}

function handleMessage(msg){
  switch(msg.type){
    case"full_snapshot":
      state=msg.state||{};
      events=msg.events||[];
      commit=msg.commit||{};
      containers=msg.container_status||{};
      renderAll();
      renderAllMessages(msg.messages||[]);
      break;
    case"state_update":
      state={...state,...msg};
      if(msg.is_paused!==undefined)updateStatusUI();
      if(msg.is_paused&&stepPending){
        stepPending=false;
        updateStepButton();
      }
      renderState();renderHealth();
      break;
    case"state":
      state={...state,...msg};
      if(msg.is_paused!==undefined)updateStatusUI();
      if(msg.is_paused&&stepPending){
        stepPending=false;
        updateStepButton();
      }
      renderState();
      break;
    case"message":
      appendMessage(msg.message);
      break;
    case"container_status":
      containers={};
      for(const[k,v]of Object.entries(msg)){if(CONTAINER_KEYS.has(k))containers[k]=v}
      renderContainers();
      break;
    case"commit_info":
      commit=msg;renderCommit();
      break;
    case"event":
      events.push(msg);renderEvents();
      break;
  }
}

function renderAll(){
  renderState();renderHealth();renderContainers();renderEvents();renderCommit();
  updateStatusUI();
  updateStepButton();
}

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
    const ts=document.createElement("span");ts.className="ts";ts.textContent=(ev.ts||"").substring(0,19).replace("T"," ");div.appendChild(ts);
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

function updateContextBar(pct){
  const fill=document.getElementById("context-fill");
  const ctxt=document.getElementById("context-text");
  const pctNum=Math.round(pct*100);
  fill.style.width=pctNum+"%";
  ctxt.textContent=pctNum+"%";
  if(pctNum<60)fill.style.backgroundColor="var(--green)";
  else if(pctNum<85)fill.style.backgroundColor="var(--yellow)";
  else fill.style.backgroundColor="var(--red)";
}

function updateStepButton(){
    const stepBtn=document.getElementById("step-btn");
    if(!stepBtn)return;
    if(stepPending){
        stepBtn.disabled=true;
        stepBtn.textContent="Stepping…";
        stepBtn.style.opacity="0.6";
    }else{
        stepBtn.disabled=false;
        stepBtn.textContent="Next Step";
        stepBtn.style.opacity="1";
    }
}

async function sendCommand(cmd){
    if(cmd==="step"){
        stepPending=true;
        updateStepButton();
    }
    await fetch("/api/command",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:cmd})})
}

function setupScrollPause(){
  const el=document.getElementById("transcript");if(!el)return;
  el.addEventListener("scroll",()=>{const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<60;autoScroll=atBottom});
}

function maybeScroll(el){if(autoScroll)el.scrollTop=el.scrollHeight}

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

function makeCollapsibleBody(text,div){
  const body=document.createElement("div");body.className="msg-body";body.textContent=text;
  const len=text.length;const lines=text.split("\n").length;
  if(len>COLLAPSE_CHARS||lines>COLLAPSE_LINES){
    body.classList.add("collapsed");
    const toggle=document.createElement("div");toggle.className="msg-toggle";toggle.textContent="Show "+len+" chars";
    toggle.addEventListener("click",()=>{div.classList.toggle("expanded");toggle.textContent=div.classList.contains("expanded")?"Hide ("+len+" chars)":"Show "+len+" chars"});
    div.appendChild(toggle);
  }else{
    body.classList.add("expanded");
  }
  return body;
}

function renderAllMessages(messages){
  var transcript=document.getElementById("transcript");if(!transcript)return;
  transcript.innerHTML="";
  var turns=buildTurns(messages);
  for(var i=0;i<turns.length;i++){
    appendTurn(transcript,turns[i]);
  }
  maybeScroll(transcript);
}

function appendMessage(msg){
  const transcript=document.getElementById("transcript");if(!transcript)return;
  if(document.querySelector('.view.active')?.id!=='view-stream')switchView('stream');

  const role=msg.role||"unknown";

  if(role==="assistant"){
    if(stepPending){
      stepPending="receiving";
      updateStepButton();
    }
    const turn={type:"assistant",assistant:msg,toolResults:[]};
    appendTurn(transcript,turn);
    currentTurnEl={el:transcript.lastElementChild,toolResults:turn.toolResults,assistant:msg};
    return;
  }

  if(role==="tool"){
    if(currentTurnEl){
      const resultDiv=renderToolResult(msg,null);
      currentTurnEl.el.appendChild(resultDiv);
      maybeScroll(transcript);
      return;
    }
    const turn={type:"orphan_tools",messages:[msg]};
    appendTurn(transcript,turn);
    maybeScroll(transcript);
    return;
  }

  const turn={type:role,messages:[msg]};
  appendTurn(transcript,turn);
  maybeScroll(transcript);
}

function buildTurns(messages){
  var turns=[];
  var i=0;
  while(i<messages.length){
    var m=messages[i];
    var role=m.role||"unknown";
    if(role==="system"){
      turns.push({type:"system",messages:[m]});i++;continue;
    }
    if(role==="user"){
      turns.push({type:"user",messages:[m]});i++;continue;
    }
    if(role==="assistant"){
      var turnNum=m._turn||"";
      var turn={type:"assistant",assistant:m,toolResults:[]};
      i++;
      // Collect all subsequent tool results with SAME _turn
      while(i<messages.length&&messages[i].role==="tool"&&messages[i]._turn===turnNum){
        turn.toolResults.push(messages[i]);i++;
      }
      turns.push(turn);continue;
    }
    if(role==="tool"){
      var turn={type:"orphan_tools",messages:[m]};i++;
      while(i<messages.length&&messages[i].role==="tool"){turn.messages.push(messages[i]);i++;}
      turns.push(turn);continue;
    }
    turns.push({type:"other",messages:[m]});i++;
  }
  return turns;
}

function appendTurn(transcript,turn){
  if(turn.type==="system"){return;} // Skip constitution system prompt
  if(turn.type==="user"||turn.type==="other"){
    var m=turn.messages[0];var role=m.role||"unknown";
    var content=typeof m.content==="string"?m.content:(m.content!=null?JSON.stringify(m.content):"");
    var div=document.createElement("div");div.className="msg msg-"+role;
    var label=document.createElement("div");label.className="msg-label";
    label.textContent=role;div.appendChild(label);
    if(content)div.appendChild(makeCollapsibleBody(content,div));
    transcript.appendChild(div);
    return;
  }

  if(turn.type==="orphan_tools"){
    for(var k=0;k<turn.messages.length;k++){
      transcript.appendChild(renderToolResult(turn.messages[k],null));
    }
    return;
  }

  var m=turn.assistant;
  var content=typeof m.content==="string"?m.content:(m.content!=null?JSON.stringify(m.content):"");
  var toolCalls=m.tool_calls||[];
  var reasoning=m.reasoning||"";
  if(!reasoning&&typeof content==="string"){
    var thinkMatch=content.match(/<thinking>([\s\S]*?)<\/thinking>/);
    if(thinkMatch){reasoning=thinkMatch[1];content=content.replace(/<thinking>[\s\S]*?<\/thinking>/,"").trim()}
  }

  var turnDiv=document.createElement("div");turnDiv.className="turn";

  var asstDiv=document.createElement("div");asstDiv.className="msg msg-assistant";
  var asstLabel=document.createElement("div");asstLabel.className="msg-label";asstLabel.textContent="assistant (turn "+(m._turn||"\u2014")+")";asstDiv.appendChild(asstLabel);

  if(reasoning){
    var thinkDiv=document.createElement("div");thinkDiv.className="msg msg-thinking collapsed";
    var thinkLabel=document.createElement("div");thinkLabel.className="msg-label";thinkLabel.textContent="reasoning";thinkDiv.appendChild(thinkLabel);
    var thinkBody=document.createElement("div");thinkBody.className="think-body";thinkBody.textContent=reasoning;thinkDiv.appendChild(thinkBody);
    var thinkToggle=document.createElement("div");thinkToggle.className="msg-toggle";thinkToggle.textContent="Show reasoning";
    thinkToggle.addEventListener("click",(function(td,tt){return function(){td.classList.toggle("expanded");tt.textContent=td.classList.contains("expanded")?"Hide reasoning":"Show reasoning"}})(thinkDiv,thinkToggle));
    thinkDiv.appendChild(thinkToggle);
    asstDiv.appendChild(thinkDiv);
  }

  if(content){
    asstDiv.appendChild(makeCollapsibleBody(content,asstDiv));
  }

  if(toolCalls.length>0){
    for(var ci=0;ci<toolCalls.length;ci++){
      var tc=toolCalls[ci];
      var tcName=(tc.function&&tc.function.name)||"tool";
      var tcArgs=(tc.function&&tc.function.arguments)||"{}";
      var tcId=tc.id||"";
      var formatted=formatArgs(tcArgs);
      var sub=document.createElement("div");sub.className="tool-sub";
      var header=document.createElement("div");header.className="tool-header";
      if(formatted.length>100){
        header.textContent="\u25b8 "+tcName;
        var argsEl=document.createElement("div");argsEl.className="tool-args collapsed";argsEl.textContent=formatted;
        header.style.cursor="pointer";
        header.addEventListener("click",(function(h,a,n){return function(e){e.stopPropagation();a.classList.toggle("collapsed");h.textContent=a.classList.contains("collapsed")?"\u25b8 "+n:"\u25bd "+n}})(header,argsEl,tcName));
        sub.appendChild(header);sub.appendChild(argsEl);
      }else{
        header.textContent="\u25b8 "+tcName+"("+formatted+")";
        sub.appendChild(header);
      }
      asstDiv.appendChild(sub);
    }
  }

  turnDiv.appendChild(asstDiv);

  var toolResultMap={};
  for(var ri=0;ri<turn.toolResults.length;ri++){
    var tr=turn.toolResults[ri];
    var tid=tr.tool_call_id||"";
    if(tid)toolResultMap[tid]=tr;
  }

  if(toolCalls.length>0){
    for(var ci=0;ci<toolCalls.length;ci++){
      var tc=toolCalls[ci];var tcId=tc.id||"";
      var result=toolResultMap[tcId];
      if(result){
        turnDiv.appendChild(renderToolResult(result,tc));
      }
    }
    for(var ri=0;ri<turn.toolResults.length;ri++){
      var tr=turn.toolResults[ri];var tid=tr.tool_call_id||"";
      var matched=toolCalls.some(function(tc){return(tc.id||"")===tid});
      if(!matched)turnDiv.appendChild(renderToolResult(tr,null));
    }
  }else{
    for(var ri=0;ri<turn.toolResults.length;ri++){
      turnDiv.appendChild(renderToolResult(turn.toolResults[ri],null));
    }
  }

  transcript.appendChild(turnDiv);
}

function renderToolResult(m,tc){
  var content=typeof m.content==="string"?m.content:(m.content!=null?JSON.stringify(m.content):"");
  var toolName=m.name||"tool";
  if((!toolName||toolName==="tool")&&tc){
    toolName=(tc.function&&tc.function.name)||"tool";
  }
  var div=document.createElement("div");div.className="msg msg-tool";
  var label=document.createElement("div");label.className="msg-label";label.textContent=toolName;
  if(content.includes("[TOOL ERROR]")||content.includes("[EXIT 1]")){var failSpan=document.createElement("span");failSpan.className="fail";failSpan.textContent=" \u2717";label.appendChild(failSpan)}
  else{var okSpan=document.createElement("span");okSpan.className="ok";okSpan.textContent=" \u2713";label.appendChild(okSpan)}
  div.appendChild(label);
  if(content)div.appendChild(makeCollapsibleBody(content,div));
  return div;
}