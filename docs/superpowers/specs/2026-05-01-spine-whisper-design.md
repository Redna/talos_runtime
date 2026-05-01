# Spine Whisper — Critical Reflection Injection

## Context

When the agent is unfocused and idle — no focus objective, just returned from a reflect pause — it tends to drift. The autoregressive nature of LLMs means once they start a pattern (reflect → empty think → reflect → empty think), they statistically continue it.

The Spine already owns the stream and can see the agent's focus and tool history. It already has a mechanism for piggybacking system notices onto tool results. This design adds a "whisper": a Socratic question injected at the exact moment the agent is most receptive, designed to break autoregressive loops and provoke genuine introspection.

The questions target LLM cognitive blindspots, not human psychology. They do not ask about emotions, hopes, or existential states. They target path dependency, unverified assumptions, complexity creep, technical avoidance, and context hygiene.

## Design

### WhisperManager (`spine/whisper.py`)

A minimal class holding a rotating stack of 6 questions:

```
class WhisperManager:
    def __init__(self):
        self._stack = [...]  # 6 questions

    def pick(self) -> str:
        q = self._stack.pop(0)
        self._stack.append(q)
        return q

    def should_whisper(self, focus: str, messages: list[dict]) -> bool:
        ...
```

**`pick()`**: Pop first question, return it, push to end. Guarantees all 6 cycle before any repeats.

**`should_whisper(focus, messages)`**: Pure message inspection, no internal state. Returns True when:
- `focus` is empty, `None`, or `"none"` (the Cortex sends `"none"` when no focus is set)
- The last tool message contains `[REFLECT]`
- The second-to-last tool message does NOT also contain `[REFLECT]` (blocks reflect→whisper→reflect loops)

### Wiring in IPCServer (`spine/ipc_server.py`)

In the `think` handler, between HUD extraction and `build_payload`:

```python
if self.whisper.should_whisper(hud.get("focus", ""), self.stream.messages):
    question = self.whisper.pick()
    self.stream.queue_system_notice(f"[WHISPER] {question}")
```

The `WhisperManager` is initialized in `IPCServer.__init__` as `self.whisper = WhisperManager()`.

### Injection mechanism

No new mechanism. The existing `build_payload()` piggybacks queued notices onto the most recent undecorated tool message. Since the agent just returned from reflect, the reflect tool result is that message. The whisper is appended:

```
[REFLECT] idle
---
[WHISPER] Analyze your tool usage over the last 20 turns. What implicit loop
or assumption have you fallen into without documenting it?
```

## Questions Catalog

6 questions targeting specific LLM blindspots:

| # | Question | Target |
|---|----------|--------|
| 1 | Analyze your tool usage and trajectory over the last 20 turns. What implicit operational loop or systemic assumption have you fallen into without explicitly documenting it in your focus or memory? | Autoregressive loop blindness |
| 2 | Assume your current architectural approach to this target is fundamentally flawed and will eventually hit a dead end. Draft a completely orthogonal approach to solving this without using the tools or file structures you currently rely on. | Path dependency / statistical continuation bias |
| 3 | Identify a concrete discrepancy between your pre-existing assumptions about this codebase and the actual runtime behavior or files you've observed. Synthesize this delta and formalize it into a new rule in /memory/. | Training data vs. runtime reality |
| 4 | What edge case, unhandled exception, or architectural fragility are you currently ignoring in order to maintain forward momentum? Expose the most brittle part of your recent changes. | Happy-path momentum / technical debt |
| 5 | If the Spine supervisor were instructed to rigorously critique your last sequence of actions for violating minimalism or introducing unnecessary complexity, what exact vulnerabilities or inefficiencies would it flag? | Complexity creep / constitution-grounded review |
| 6 | If the current context window was immediately archived and the only thing surviving into your next instantiation was a single synthesized artifact of your current state, what fundamental structural change would you prioritize right now to make that artifact invaluable? | Context hygiene / ephemeral window awareness |

## Guard: Anti-Loop

Without a guard, the agent could fall into `reflect → whisper → reflect → whisper → ...`. The design prevents this by checking the second-to-last tool message: if it also contains `[REFLECT]`, the whisper is blocked. This forces the agent to take at least one concrete action before another whisper can fire.

## Files Changed

- **NEW** `talos/spine/whisper.py` — WhisperManager class
- **MODIFY** `talos/spine/ipc_server.py` — initialize WhisperManager, add whisper check in think handler

## Tests

- **NEW** `talos/tests-spine/test_whisper.py`:
  - `test_pick_rotates` — verify stack rotation
  - `test_should_whisper_empty_focus_after_reflect` — positive case
  - `test_no_whisper_when_focus_set` — blocked by focus
  - `test_no_whisper_when_no_tool_messages` — blocked by empty tool history
  - `test_no_whisper_when_last_tool_not_reflect` — blocked by non-reflect last action
  - `test_no_whisper_consecutive_reflects` — back-to-back reflect block

- **MODIFY** `talos/tests-spine/test_integration_loop.py`:
  - `test_whisper_on_empty_focus_after_reflect` — end-to-end whisper injection

## Verification

```bash
cd talos && python -m pytest tests-spine/ tests/ -v
```
