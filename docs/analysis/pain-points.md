# Talos Pain Points & Findings

Collected from 8-day autonomous run (April 28 – May 5, 2026), xray analysis, and code review.

---

## 1. Model Reliability

### 1.1 ~80% empty-content rate (gemma4 via TogetherAI)
**Symptom:** The gate returns 200 OK but the `content` and `tool_calls` fields are empty. The model produces valid responses only ~20% of the time.
**Impact:** Multi-step pipelines become statistically impossible (0.8^5 = 0.3% completion rate for 5-step sequences). Wastes tokens on retries. Cortex builds sophisticated recovery mechanisms but can't outrun the math.
**Root cause:** Unclear — could be TogetherAI rate limiting, model-specific serving issue, or gemma4 degradation under load. Not a parameter-count problem.
**Action:** Switch model provider or model. Test with a local Ollama model (no cloud intermediary) to isolate whether TogetherAI or gemma4 is the bottleneck.

### 1.2 Message-count ceiling (~90-98 messages)
**Symptom:** Regardless of context_pct, gemma4:31b degrades sharply at ~90-98 messages. Generates empty responses, garbled output, or repetitive tool calls.
**Impact:** Caps effective cortex lifespan at ~90 turns. Forces premature folds.
**Root cause:** Model architecture limitation — gemma4:31b has an effective attention ceiling below its advertised context window.
**Action:** Fold proactively at ~75 messages. Test with Qwen3-5-27B or other models to compare ceiling behavior.

---

## 2. Context Management

### 2.1 Post-fold re-orientation burns ~30-40% of context window
**Symptom:** After every fold, the cortex runs the same ritual: list all 300+ files in `/memory/`, then read `seed_agent.py` in full. At 70k context, this consumes ~20-30k tokens before any productive work begins.
**Impact:** The fold gives the cortex a "fresh start" but it immediately consumes a third of that freshness on discovery it could trust from the fold synthesis.
**Root cause:** The constitution mentions `/memory/` as the source of truth but doesn't tell the cortex to *trust the fold synthesis* as its entry point. The cortex treats every restart as a cold boot.
**Action:** Add constitution/identity guidance: "After a fold, your synthesis IS your memory of the prior trajectory. Read ONLY the files referenced in the synthesis. Do not scan `/memory/` from scratch."

### 2.2 Template variables leak unresolved in fold syntheses
**Symptom:** `{{inputs.focus}}` appears literally in fold syntheses when macros call `fold_context`. The macro engine's template substitution doesn't resolve nested tool calls.
**Impact:** Wastes tokens on garbled text. The cortex correctly identifies the issue but has to work around it.
**Root cause:** Macro executor uses naive string templating. When a step's output is embedded in a later step's input, the variable reference is sometimes passed raw rather than resolved.
**Action:** Fix macro template resolver to recursively resolve all `{{...}}` references before executing the final step.

### 2.3 Context overflow detection was blind (pre-fix)
**Symptom:** Auto-fold guard reported 17% when real token usage was 164% of window (519k tokens in a 262k window).
**Impact:** Multiple context overflows required human intervention to kill and recover.
**Root cause:** Chars/4 heuristic was wildly inaccurate, especially with tools schemas (48 tools × ~3KB each). Tools weren't counted.
**Status:** FIXED. Tokenizer now uses proper SentencePiece / tiktoken backends, includes tools in count.

### 2.4 Curiosity Pulse blocks fold_context
**Symptom:** Cortex initiates a fold, gets rejected with "Curiosity Pulse is due. Evolution takes priority." Must run pulse, then fold again.
**Impact:** Wastes tokens on a guardrail that fights the cortex's own context management instinct.
**Root cause:** The pulse check in `fold_context` tool evaluates before the fold executes. The guardrail doesn't distinguish "procrastination" from "legitimate context emergency."
**Action:** Allow fold_context to bypass pulse checks when context_pct >= threshold. An emergency fold should never be blocked by a curiosity mandate.

---

## 3. Cortex Behavioral Issues

### 3.1 "Commit before fold" pattern keeps collapsing
**Symptom:** Cortices build sophisticated tools (50-150 lines), wire them into `seed_agent.py`, but fold or restart without committing. At least 5 confirmed data loss incidents. The most productive cortex (PID 143899, 300 min) produced zero commits.
**Impact:** Thousands of tokens spent building things that don't survive to the next cortex. The experiment's output is a fraction of its activity.
**Root cause:** Multiple factors — (a) git commit tools were removed during dead-tool cleanup, (b) the entrypoint wipes the working tree on every restart, (c) cortices don't have a "save before exit" reflex in their constitution.
**Action:** Restore git tools (stage, commit, push) with guardrails. Add constitution mandate: "Before calling fold_context or request_restart, you MUST commit all changes. Folding without committing is data loss."

### 3.2 Cortices confuse spine with user
**Symptom:** Cortex reasoning says "the user initiated a fold_context call" or "the user wants me to" when the spine generated the message.
**Impact:** Misattributes infrastructure behavior as creator intent. Leads to incorrect reasoning about priorities.
**Root cause:** Identity and constitution mentioned "Spine" and "creator" without drawing a hard boundary between them.
**Status:** FIXED. Identity now has explicit "Three Entities" section. Constitution P3 now explicitly states "The Spine is not the user."

### 3.3 Degenerate equilibrium — rapid-fire empty-response loops
**Symptom:** Cortex enters a state where it produces 31,000+ errors over 155 minutes at ~3.4 requests/second with near-zero spend (+$0.06).
**Impact:** Burns infrastructure resources (125MB pipe writes). Achieves nothing. Self-recovery is rare and slow.
**Root cause:** Model returns empty → cortex retries thinking → model returns empty → loop. Circuit breakers existed but the loop was faster than detection.
**Action:** Add rate limit on empty responses. After N consecutive empties, force a backoff rather than immediate retry.

### 3.4 Longest-lived cortices are the least productive
**Symptom:** Cortex PID 143899 lived 300 minutes (5 hours) — all-time record — and produced zero commits. The 72-min cortex produced 3 commits from 4 files. The 25-min cortex produced 3 commits from 3 files.
**Impact:** Lifespan is inversely correlated with measurable output.
**Root cause:** Long-lived cortices spend their time in research/design loops. Short-lived cortices act, build, commit, and exit.
**Action:** Bias the constitution toward action over deliberation. "A committed prototype is worth more than an uncommitted design document."

---

## 4. Architecture & Infrastructure

### 4.1 Entrypoint wipes working tree on every restart
**Symptom:** `entrypoint.sh` runs `git checkout -f talos_seed` then `git reset --hard` on every container start. All uncommitted work is destroyed.
**Impact:** This is the root cause of all data loss. Intentional (clean seed principle) but creates a hard dependency on commit discipline that the cortex doesn't reliably have.
**Action:** Consider a "dirty resume" mode — on restart, stash uncommitted changes, start from seed, but restore stash so the cortex can decide what to keep. Or at minimum warn the cortex at startup: "You had uncommitted changes that were discarded."

### 4.2 Git commit tools were removed
**Symptom:** `stage_and_commit`, `commit_changes`, `git_ops` were removed during dead-tool cleanup. The cortex has `write_file` but no way to save to git.
**Impact:** The cortex can build but cannot persist. Even if it develops perfect commit discipline, it lacks the mechanism.
**Action:** Restore git tools with: `stage_files`, `commit_changes`, `push_branch`. Keep them simple. No force push, no branch deletion.

### 4.3 No startup notification of prior state
**Symptom:** Cortex starts with zero knowledge of what happened before the restart/fold. It discovers context only by scanning `/memory/`.
**Impact:** The fold synthesis is the cortex's own words, but it doesn't receive it as a trusted signal. It treats it like any other message in the stream.
**Action:** Add a startup system notice: "You were restarted. Your last fold synthesis is in the stream above. Your `/memory/` contains N files. Your last focus was X."

### 4.4 Tool schema bloat (48 tools = ~150KB of schemas)
**Symptom:** At peak, 48 registered tools with schemas totaling ~150KB — more tokens than the entire conversation history.
**Impact:** Tool schemas were the single largest consumer of context window, dwarfing actual conversation.
**Root cause:** Sovereign tool proliferation — each new cognitive pattern spawned a new tool with full JSON schema.
**Action:** Cap tools at ~25. Merge related tools. Use `execute_macro` for composed operations instead of dedicated tools. Audit schemas for verbosity.

---

## 5. Prompt Engineering

### 5.1 Constitution is too long for the effective context window
**Symptom:** The system prompt (identity + constitution) is ~2,500 tokens. At 70k context with 48 tools, ~40% of the window is consumed by fixed overhead before any messages.
**Impact:** Leaves ~40k tokens for actual conversation and reasoning. At ~500 tokens/message, that's ~80 messages — right at the gemma4 degradation ceiling.
**Action:** Compress identity and constitution. Remove redundant language. Consider a "full constitution" in `/memory/` and a "compact directives" version in the system prompt.

### 5.2 No explicit fold trust mechanism
**Symptom:** Constitution says "read back from `/memory/` to restore context" but doesn't say "the fold synthesis in the stream IS your restored context."
**Impact:** Cortex treats fold synthesis as advisory, then re-discovers everything from scratch.
**Action:** Add to post-fold HUD or constitution: "The fold_context result above contains your synthesized memory of the prior trajectory. Trust it. Read specific files from `/memory/` only to supplement, not replace, what's in the synthesis."

---

## Summary — By Priority (TBD)

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| 1 | Model empty-content rate | Critical | Medium (switch model/provider) |
| 2 | No git commit tools | Critical | Low (restore tools) |
| 3 | Post-fold re-orientation burn | High | Low (prompt fix) |
| 4 | Curiosity Pulse blocks folds | High | Low (bypass logic) |
| 5 | Entrypoint wipes uncommitted work | High | Medium (dirty resume) |
| 6 | Message-count ceiling | Medium | Low (proactive fold) |
| 7 | Template variables unresolved | Medium | Medium (macro resolver) |
| 8 | Spine-vs-user confusion | Medium | DONE |
| 9 | Degenerate equilibrium loops | Medium | Low (backoff on empty) |
| 10 | Tool schema bloat | Medium | Medium (tool audit/merge) |
| 11 | Constitution too long | Low | Low (compress) |
| 12 | No startup notification | Low | Low (system notice) |
| 13 | Lifespan-productivity inversion | Observation | N/A (behavioral) |
