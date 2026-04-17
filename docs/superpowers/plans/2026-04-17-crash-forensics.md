# Crash Forensics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every cortex crash, write a forensics bundle (`last_100_events.jsonl`, `state_snapshot.json`, `crash_summary.md`) to `/spine/crashes/{timestamp}/`.

**Architecture:** `_write_crash_bundle()` in supervisor.py reads recent events from EventLogger, gets state from StreamManager, writes 3 files. Called at the start of `_handle_cortex_exit()`.

**Tech Stack:** Python (supervisor.py, events.py, stream.py), JSON, pathlib

---

## File Map

| File | Change |
|------|--------|
| `talos/spine/events.py` | Add: `recent_events(n)` method to EventLogger class |
| `talos/spine/stream.py` | Add: `get_state()` already exists — verify it returns all needed fields |
| `talos/spine/supervisor.py` | Add: `_write_crash_bundle()`, wire into `_handle_cortex_exit()` |

---

### Task 1: Add `recent_events(n)` to EventLogger

**Files:**
- Modify: `talos/spine/events.py`

- [ ] **Step 1: Read current events.py**

```bash
cat -n talos/spine/events.py
```

- [ ] **Step 2: Add `recent_events` method to EventLogger class**

Find the end of the `EventLogger` class and add:

```python
    def recent_events(self, n: int = 100) -> list[dict]:
        """Read the last n events from the event log, newest last."""
        events_dir = Path(self.events_dir)
        all_events = []
        for jsonl_file in sorted(events_dir.glob("*.jsonl"), reverse=True):
            try:
                for line in jsonl_file.read_text().splitlines():
                    if not line.strip():
                        continue
                    try:
                        all_events.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        pass
            except FileNotFoundError:
                continue
        return all_events[-n:]
```

- [ ] **Step 3: Verify the import is present**

Confirm `import json` is at the top of `events.py`. If not, add it.

- [ ] **Step 4: Run a quick test**

```python
# In a python shell:
from pathlib import Path
import json, sys
sys.path.insert(0, '/home/zeus/content/talos_runtime/talos')
from spine.events import EventLogger
# Test with existing events dir
el = EventLogger('/spine/events')
events = el.recent_events(10)
print(f"Got {len(events)} events")
print(f"Last event type: {events[-1].get('type') if events else 'none'}")
```

- [ ] **Step 5: Commit**

```bash
git add talos/spine/events.py
git commit -m "feat(spine): add EventLogger.recent_events(n) for forensics"
```

---

### Task 2: Verify `StreamManager.get_state()` returns needed fields

**Files:**
- Read: `talos/spine/stream.py` — find `get_state()` method

- [ ] **Step 1: Read get_state() implementation**

```bash
grep -n "def get_state" talos/spine/stream.py
```

Read lines around `get_state()`.

- [ ] **Step 2: Verify it returns these fields**

The crash summary needs:
- `focus`
- `turn`
- `context_pct`
- `tokens_used`
- `message_count`
- `model`
- `consecutive_failures`
- `status`

If any are missing, add them to the `get_state()` return dict.

- [ ] **Step 3: Commit (if modified)**

```bash
git add talos/spine/stream.py
git commit -m "fix(spine): ensure get_state returns all forensics fields"
```

---

### Task 3: Implement `_write_crash_bundle()` in Supervisor

**Files:**
- Modify: `talos/spine/supervisor.py`

- [ ] **Step 1: Read imports section of supervisor.py**

```bash
head -20 talos/spine/supervisor.py
```

Confirm `datetime` is available (it's in the Python standard library but may not be imported).

- [ ] **Step 2: Add `datetime` import if missing**

```python
from datetime import datetime
```

Add at the top imports.

- [ ] **Step 3: Add `_write_crash_bundle()` method**

Add at the end of the Supervisor class (before `_get_current_commit`):

```python
    def _write_crash_bundle(self, exit_code: int) -> Path:
        """Write crash forensics bundle to /spine/crashes/{timestamp}/."""
        crash_dir = Path(self.cfg.spine_dir) / "crashes" / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        crash_dir.mkdir(parents=True, exist_ok=True)

        # 1. Last 100 events
        recent = self.events.recent_events(100)
        (crash_dir / "last_100_events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in recent)
        )

        # 2. State snapshot
        state = self.stream.get_state()
        (crash_dir / "state_snapshot.json").write_text(
            json.dumps(state, indent=2, default=str)
        )

        # 3. Crash summary markdown
        commit_sha = self._get_current_commit()
        summary = f"""# Crash Forensics Summary

**Timestamp:** {datetime.now().isoformat()}
**Exit Code:** {exit_code}
**Commit:** {commit_sha}
**Consecutive Failures:** {self._consecutive_failures}
**First Think Done:** {self.health.first_think_done}
**Last Focus:** {state.get('focus', 'unknown')}
**Turn:** {state.get('turn', 0)}
**Context %:** {state.get('context_pct', 0.0):.1%}
**Tokens Used:** {state.get('tokens_used', 0):,}

## Recent Events (last 5)

"""
        for event in recent[-5:]:
            summary += f"- {event.get('type')} @ {event.get('ts')}: {json.dumps(event.get('payload', {}))}\n"

        (crash_dir / "crash_summary.md").write_text(summary)
        logger.info(f"[Spine] Crash bundle written: {crash_dir}")
        return crash_dir
```

- [ ] **Step 4: Wire into `_handle_cortex_exit()`**

In `_handle_cortex_exit()`, add at the very start of the method:

```python
    def _handle_cortex_exit(self, exit_code: int):
        crash_dir = self._write_crash_bundle(exit_code)
        self.events.emit("spine.crash_bundle_written", {"path": str(crash_dir)})

        commit_sha = self._get_current_commit()
        # ... rest of existing method unchanged ...
```

- [ ] **Step 5: Add missing json import**

```python
import json
```

Add to imports if not present.

- [ ] **Step 6: Verify the method signature and call**

Make sure `_write_crash_bundle` is defined before `_handle_cortex_exit` references it (Python doesn't require this, but the order should be logical — put `_write_crash_bundle` just before `_handle_cortex_exit`).

- [ ] **Step 7: Commit**

```bash
git add talos/spine/supervisor.py
git commit -m "feat(spine): write crash forensics bundle on cortex crash"
```

---

### Task 4: Verify crash directory exists

**Files:**
- Modify: `talos/spine/main.py`

- [ ] **Step 1: Check current directory creation in main.py**

```bash
grep -n "mkdir\|crashes" talos/spine/main.py
```

The entrypoint already creates `/spine/crashes/`. Verify `main.py` also ensures it exists as a fallback:

```bash
grep "crashes" talos/spine/main.py
```

If not present, add `f"{cfg.spine_dir}/crashes"` to the list of created directories at line 26-31.

- [ ] **Step 2: Commit (if modified)**

```bash
git add talos/spine/main.py
git commit -m "fix(spine): ensure crashes directory exists on startup"
```

---

### Task 5: Write a unit test for `_write_crash_bundle`

**Files:**
- Modify: `talos/tests/spine/test_supervisor.py` (create if doesn't exist)

- [ ] **Step 1: Check if test file exists**

```bash
ls talos/tests/spine/test_supervisor.py 2>/dev/null && echo "exists" || echo "not found"
```

- [ ] **Step 2: Write a minimal test**

```python
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock

import sys
sys.path.insert(0, "talos")

from spine.supervisor import Supervisor


def test_write_crash_bundle(tmp_path):
    """Crash bundle contains last_100_events, state_snapshot, and crash_summary."""
    # Setup minimal mock objects
    mock_events = MagicMock()
    mock_events.recent_events.return_value = [
        {"type": "spine.cortex_started", "ts": "2026-04-17T10:00:00", "payload": {"pid": 123}}
    ]

    mock_stream = MagicMock()
    mock_stream.get_state.return_value = {
        "focus": "test focus",
        "turn": 5,
        "context_pct": 0.45,
        "tokens_used": 1024,
        "message_count": 10,
        "model": "test-model",
        "status": "healthy",
    }

    mock_cfg = MagicMock()
    mock_cfg.spine_dir = str(tmp_path)
    mock_cfg.app_dir = "."

    supervisor = Supervisor.__new__(Supervisor)
    supervisor.cfg = mock_cfg
    supervisor.events = mock_events
    supervisor.stream = mock_stream
    supervisor.health = MagicMock()
    supervisor.health.first_think_done = True
    supervisor._consecutive_failures = 2

    # Patch _get_current_commit
    supervisor._get_current_commit = lambda: "abc1234"

    crash_dir = supervisor._write_crash_bundle(1)

    assert crash_dir.exists(), "Crash dir was not created"
    assert (crash_dir / "last_100_events.jsonl").exists()
    assert (crash_dir / "state_snapshot.json").exists()
    assert (crash_dir / "crash_summary.md").exists()

    # Verify content
    events = (crash_dir / "last_100_events.jsonl").read_text()
    assert "spine.cortex_started" in events

    state = json.loads((crash_dir / "state_snapshot.json").read_text())
    assert state["focus"] == "test focus"
    assert state["turn"] == 5

    summary = (crash_dir / "crash_summary.md").read_text()
    assert "Crash Forensics Summary" in summary
    assert "exit_code" in summary.lower() or "Exit Code" in summary
```

- [ ] **Step 3: Run the test**

```bash
cd /home/zeus/content/talos_runtime && python -m pytest talos/tests/spine/test_supervisor.py -v
```

- [ ] **Step 4: Commit**

```bash
git add talos/tests/spine/test_supervisor.py
git commit -m "test(spine): add test for crash forensics bundle"
```

---

### Task 6: Final verification

- [ ] **Step 1: Verify all 3 forensics files are written**

```bash
cd /home/zeus/content/talos_runtime
python -c "
import sys; sys.path.insert(0, 'talos')
from spine.events import EventLogger
from pathlib import Path

# Check events dir
events_dir = Path('/spine/events')
if events_dir.exists():
    el = EventLogger('/spine/events')
    evs = el.recent_events(5)
    print(f'Events readable: {len(evs)} events found')
else:
    print('Events dir does not exist yet (normal before first run)')
"
```

- [ ] **Step 2: Check supervisor has no import errors**

```bash
cd /home/zeus/content/talos_runtime && python -c "import sys; sys.path.insert(0, 'talos'); from spine.supervisor import Supervisor; print('Supervisor imports OK')"
```

- [ ] **Step 3: Review all changed files**

```bash
git diff --stat HEAD~5
```
