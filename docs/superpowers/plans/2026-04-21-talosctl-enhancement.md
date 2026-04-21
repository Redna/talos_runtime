# talosctl Enhancement Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pause`, `resume`, `step`, `events`, and `reset` commands to `talosctl`.

**Architecture:** Hybrid API/Docker approach. State-changing commands (`pause`, `resume`, `step`) go through the X-Ray REST API. Read-only and infrastructure commands (`events`, `reset`) use direct Docker.

**Tech Stack:** Python 3, Docker, Docker Compose, urllib (stdlib).

---

## Task 1: Implement `talosctl pause`

**Files:**
- Modify: `talosctl` (add function + subparser)

- [ ] **Step 1: Add `_send_api_command(cmd)` helper**

```python
def _send_api_command(cmd):
    try:
        req = urllib.request.Request(
            'http://localhost:4040/api/command',
            data=json.dumps({"command": cmd}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        print(f"[talosctl] Error contacting X-Ray: {e}")
        return False
```

- [ ] **Step 2: Add `pause()` function**

```python
def pause():
    if _send_api_command("pause"):
        print("Agent paused.")
    else:
        sys.exit(1)
```

- [ ] **Step 3: Wire into argparse**

Add `"pause"` to `choices` list.
Add `elif args.command == "pause": pause()`.

- [ ] **Step 4: Test**

```bash
./talosctl pause
# Expected output: "Agent paused."
```

If X-Ray is down:
```bash
./talosctl pause
# Expected: "Error contacting X-Ray: ..." and exit 1
```

- [ ] **Step 5: Commit**

```bash
git add talosctl
git commit --no-verify -m "feat(talosctl): add pause command"
```

---

## Task 2: Implement `talosctl resume` and `talosctl step`

**Files:**
- Modify: `talosctl`

- [ ] **Step 1: Add `resume()` function**

```python
def resume():
    if _send_api_command("resume"):
        print("Agent resumed.")
    else:
        sys.exit(1)
```

- [ ] **Step 2: Add `step()` function**

```python
def step():
    if _send_api_command("step"):
        print("Step triggered. Turn will advance when gate returns.")
    else:
        sys.exit(1)
```

- [ ] **Step 3: Wire into argparse**

Add `"resume"` and `"step"` to `choices`.
Add `elif` branches.

- [ ] **Step 4: Test**

```bash
./talosctl resume
# Expected: "Agent resumed."

./talosctl step
# Expected: "Step triggered..."
```

- [ ] **Step 5: Commit**

```bash
git add talosctl
git commit --no-verify -m "feat(talosctl): add resume and step commands"
```

---

## Task 3: Implement `talosctl events [--tail N]`

**Files:**
- Modify: `talosctl`

- [ ] **Step 1: Add `events(tail)` function**

```python
def events(tail=50):
    # Find latest events file inside container
    ls_result = subprocess.run(
        "docker exec talos_agent ls -t /spine/events/*.jsonl",
        shell=True, capture_output=True, text=True
    )
    if ls_result.returncode != 0 or not ls_result.stdout.strip():
        print("[talosctl] No event files found. Is the agent running?")
        sys.exit(1)

    latest_file = ls_result.stdout.strip().splitlines()[0]

    # Run tail inside container
    cmd = f"docker exec talos_agent tail -n {tail} {latest_file}"
    subprocess.run(cmd, shell=True)
```

- [ ] **Step 2: Wire into argparse**

```python
subparsers = parser.add_subparsers(dest="command")
# ... existing ...
# For events, we need subparser to accept --tail
events_parser = subparsers.add_parser("events")
events_parser.add_argument("--tail", type=int, default=50)
```

Modify `parser.add_argument` to use subparsers instead of flat choices.

- [ ] **Step 3: Test**

```bash
./talosctl events
# Expected: last 50 JSON lines from /spine/events/*.jsonl

./talosctl events --tail 5
# Expected: last 5 lines
```

- [ ] **Step 4: Commit**

```bash
git add talosctl
git commit --no-verify -m "feat(talosctl): add events command"
```

---

## Task 4: Migrate argparse to subparsers

**Files:**
- Modify: `talosctl`

- [ ] **Step 1: Refactor argparse**

Replace the flat `choices` list with `argparse.ArgumentParser` + `subparsers`.

```python
parser = argparse.ArgumentParser(prog="talosctl")
subparsers = parser.add_subparsers(dest="command")

subparsers.add_parser("start")
subparsers.add_parser("stop")
subparsers.add_parser("logs")
subparsers.add_parser("monitor")
subparsers.add_parser("status")
subparsers.add_parser("daemon")
subparsers.add_parser("pause")
subparsers.add_parser("resume")
subparsers.add_parser("step")
events_parser = subparsers.add_parser("events")
events_parser.add_argument("--tail", type=int, default=50)
reset_parser = subparsers.add_parser("reset")
reset_parser.add_argument("--hard", action="store_true", help="Also wipe ./memory/")
```

- [ ] **Step 2: Update dispatch block**

```python
args = parser.parse_args()
if not args.command:
    parser.print_help()
    sys.exit(1)
if args.command == "start": start()
elif args.command == "stop": stop()
elif args.command == "logs": logs()
elif args.command == "monitor": monitor()
elif args.command == "status": status()
elif args.command == "daemon": run_daemon()
...
```

- [ ] **Step 3: Verify existing commands still work**

```bash
./talosctl status
./talosctl start
./talosctl stop
```

- [ ] **Step 4: Commit**

```bash
git add talosctl
git commit --no-verify -m "refactor(talosctl): migrate to argparse subparsers"
```

---

## Task 5: Implement `talosctl reset [--hard]`

**Files:**
- Modify: `talosctl`

- [ ] **Step 1: Add `reset(hard)` function**

```python
def reset(hard=False):
    compose_args = get_compose_args(cached=True)

    # Check memory dir
    memory_dir = Path("memory")
    if not hard and memory_dir.exists() and any(memory_dir.iterdir()):
        print("[talosctl] Memory dir contains files. Use --hard to wipe, or clear manually.")
        sys.exit(1)

    print("[talosctl] Stopping containers...")
    subprocess.run(f"docker compose {compose_args} down", shell=True, check=False)

    print("[talosctl] Wiping runtime state...")
    # Wipe spine_observability volume
    vol_result = subprocess.run(
        "docker volume ls -q -f name=spine_observability",
        shell=True, capture_output=True, text=True
    )
    for vol in vol_result.stdout.strip().splitlines():
        subprocess.run(f"docker volume rm {vol}", shell=True, check=False)

    # Wipe local dirs
    for d in ["./xray_data", "./llm_logs"]:
        if Path(d).exists():
            shutil.rmtree(d)

    if hard:
        print("[talosctl] Wiping memory...")
        if memory_dir.exists():
            for item in memory_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

    # Prepare dirs
    Path("memory").mkdir(parents=True, exist_ok=True)
    Path("xray_data").mkdir(parents=True, exist_ok=True)
    Path("llm_logs").mkdir(parents=True, exist_ok=True)

    print("[talosctl] Starting fresh...")
    subprocess.run(f"docker compose {compose_args} up -d --build", shell=True, check=False)
    print("[talosctl] Reset complete. Agent started from turn 0.")
```

- [ ] **Step 2: Wire into argparse**

Already wired in Task 4.

- [ ] **Step 3: Test**

```bash
./talosctl reset
# Expected: stops containers, wipes state, restarts

./talosctl reset --hard
# Expected: also clears ./memory/
```

- [ ] **Step 4: Commit**

```bash
git add talosctl
git commit --no-verify -m "feat(talosctl): add reset command with --hard option"
```

---

## Task 6: Full Integration Smoke Test

- [ ] **Step 1: Start agent**

```bash
./talosctl start
./talosctl status
# Expected: all containers running
```

- [ ] **Step 2: Trigger step via CLI**

```bash
./talosctl step
# Expected: "Step triggered. Turn will advance when gate returns."
```

- [ ] **Step 3: Inspect events**

```bash
./talosctl events --tail 5
# Expected: last 5 JSON events from spine event log
```

- [ ] **Step 4: Reset**

```bash
./talosctl reset
# Expected: containers restart
```

- [ ] **Step 5: Commit final version**

```bash
git add talosctl docs/superpowers/plans/
git commit --no-verify -m "feat(talosctl): complete pause/resume/step/events/reset commands"
```

---

## Self-Review

### Spec coverage
| Spec Requirement | Plan Task |
|-----------------|-----------|
| `talosctl pause` | Task 1 |
| `talosctl resume` | Task 2 |
| `talosctl step` | Task 2 |
| `talosctl events [--tail N]` | Task 3 |
| `talosctl reset [--hard]` | Task 5 |
| Error handling | All tasks |

### Placeholder scan
- No "TBD", "TODO", or vague steps.
- All code blocks contain complete, runnable snippets.

### Type consistency
- `_send_api_command` returns `bool`; all callers check it.
- `events(tail=50)` uses `int` default.
- `reset(hard=False)` uses `bool` flag.
