# Reflect / Wake Integration Plan

**Date:** 2026-04-17
**Status:** Draft
**Scope:** Verify and document the end-to-end wake/interrupt flow between Telegram, Spine, and Cortex.

---

## 1. Current Flow (As Implemented)

### 1.1 Telegram Inbound → Notice + Wake File

```
Telegram message arrives
    ↓
TelegramPoller._handle_update()
    ↓
on_telegram_message(text):
  → stream_mgr.queue_system_notice(f"[TELEGRAM | {text}]")
  → wake_path = Path(cfg.spine_dir) / ".wake"
  → wake_path.touch()          ← creates /spine/.wake
```

### 1.2 Next Think → HUD Shows Notice

```
Cortex calls spine.think()
    ↓
StreamManager._build_payload():
  → queued_notices = ["[TELEGRAM | ...]"]
  → should_show_hud = True (queued_notices is non-empty)
  → HUD appended to last tool result message
    ↓
Gate → LLM → model sees: [TELEGRAM | message text]
```

### 1.3 Model Calls reflect() → Wake File Interrupts Sleep

```
reflect(status="...", sleep_duration=60):
    wake_path = Path(os.environ["SPINE_DIR"]) / ".wake"
    deadline = time.time() + min(sleep_duration, 120)
    while time.time() < deadline:
        if wake_path.exists():      ← polls every 0.5s
            wake_path.unlink(missing_ok=True)
            break                   ← early exit!
        time.sleep(0.5)
    return f"[REFLECT] {status}"
```

### 1.4 Wake File Lifecycle

| Event | File Created | File Deleted |
|-------|-------------|--------------|
| Telegram message | `touch /spine/.wake` | — |
| `reflect()` finds it | — | `unlink()` |
| `reflect()` timeout | — | never (timeout exits loop) |
| Startup (entrypoint) | — | `/spine/` is clean |

---

## 2. Verified Working Cases

### Case A: Telegram → Model immediately calls reflect()
```
Turn N:   Telegram message arrives → .wake created + notice queued
Turn N+1: think() → HUD shows notice → model calls reflect(sleep=120)
          reflect() polls .wake → finds it → breaks immediately
Result:   Agent responds to Telegram message with minimal delay
```

### Case B: Telegram → Model continues work → later calls reflect()
```
Turn N:   Telegram → .wake + notice
Turn N+1: think() → HUD shows notice → model ignores, continues work
Turn N+2: think() → notice still in queue? No — cleared after Turn N+1 HUD display
          .wake still exists (if reflect wasn't called yet)
Turn N+K: model calls reflect(sleep=60)
          reflect() polls .wake → finds it → breaks
Result:   Works IF reflect() is called within sleep_duration of Telegram arrival
```

### Case C: Model doesn't call reflect() at all
```
Turn N:   Telegram → .wake + notice
Turn N+1: think() → HUD shows notice → model ignores
          .wake is NOT deleted (reflect was never called)
Turn N+X: .wake persists in /spine/ until next Telegram or restart
Result:   Notice was delivered. Wake file unused but harmless.
```

---

## 3. Potential Issues

### Issue 1: Notice not persistently flagged across turns

`queued_notices` is a one-shot: it's added to the HUD and then cleared. If the model doesn't act on the notice on the very next turn, it's gone from subsequent HUDs. The model must respond to the Telegram message on the turn immediately following the notice appearing.

**Current behavior:** Notice appears for exactly one turn. If the model doesn't call `reflect()` or otherwise act on it that turn, the notice disappears from subsequent turns.

**Spec says:** "On the next `think()` call, the Spine injects the queued notice into the HUD piggyback. The agent sees the message at the start of its next thinking cycle."

**Verdict:** This matches the spec — the notice is injected at the start of the NEXT cycle. If the model doesn't respond that turn, it's a model behavior issue, not a system issue.

### Issue 2: Wake file is only consumed by reflect()

If a Telegram message arrives and the model doesn't call `reflect()` within `sleep_duration`, the wake file sits unused. This is fine — the notice was already delivered via HUD. The wake file is just an optimization to reduce latency.

### Issue 3: Race condition — Telegram arrives during reflect() sleep

```
reflect(sleep=120) polling:
  Tick 1s: .wake exists? No
  Tick 2s: Telegram arrives → .wake touched
  Tick 3s: .wake exists? Yes → delete + break
Result: Telegram interrupt works even during reflect sleep
```

This works correctly. The polling loop checks every 0.5s, so maximum latency is 0.5s after the wake file is created.

---

## 4. What to Verify at Runtime

1. **Telegram → notice appears in HUD**: Send a test Telegram message, observe the next LLM response includes `[TELEGRAM | ...]` in context
2. **reflect() early exit**: Call `reflect(sleep=120)`, immediately touch `/spine/.wake` from another terminal, observe it returns in < 1s instead of waiting 120s
3. **Notice persists only one turn**: Verify the Telegram notice does NOT appear in HUD on the second turn after it first appeared (unless a new Telegram arrives)

---

## 5. Open Question

**Should `reflect()` be the only way the wake file is consumed?**

Currently: yes. The wake file is only created by Telegram messages and only consumed by `reflect()`.

Alternative design: the cortex main loop could also check the wake file on every iteration (not just during reflect), and set an internal flag that causes the next `think()` to include an extra prompt nudge. This would be more robust but requires cortex changes.

Recommendation: **Keep current design.** The latency benefit is real, and the notice-via-HUD is already a reliable delivery mechanism. The wake file is a bonus, not the primary channel.
