#!/usr/bin/env python3
"""talosctl — command-line interface for the Talos autonomous agent.

Subcommands:
    pause      Pause the agent (idempotent)
    resume     Resume the agent from paused state (idempotent)
    step       Trigger a single step when paused
    events     Tail recent spine events
    reset      Restart docker compose and optionally wipe state

Examples:
    talosctl pause
    talosctl step
    talosctl events --tail 20
    talosctl reset --hard
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import httpx

XRAY_URL = "http://localhost:4040"
TALOS_CONTAINER = "talos_agent"


def _post_command(command: str) -> dict:
    try:
        resp = httpx.post(
            f"{XRAY_URL}/api/command",
            json={"command": command},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        print(f"Error: X-Ray returned {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print(f"Error: X-Ray unreachable at {XRAY_URL}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_pause(_args: argparse.Namespace) -> None:
    data = _post_command("pause")
    print(data.get("message", "Agent paused."))


def cmd_resume(_args: argparse.Namespace) -> None:
    data = _post_command("resume")
    print(data.get("message", "Agent resumed."))


def cmd_step(_args: argparse.Namespace) -> None:
    data = _post_command("step")
    print(data.get("message", "Step triggered."))


def cmd_events(args: argparse.Namespace) -> None:
    tail = args.tail
    # Find latest event file
    ls = subprocess.run(
        ["docker", "exec", TALOS_CONTAINER, "ls", "-t", "/spine/events/"],
        capture_output=True,
        text=True,
    )
    if ls.returncode != 0:
        print(f"Warning: could not list events: {ls.stderr.strip()}")
        # Fallback: try with raw ls
        ls = subprocess.run(
            [
                "docker",
                "exec",
                TALOS_CONTAINER,
                "sh",
                "-c",
                "ls -t /spine/events/*.jsonl 2>/dev/null || true",
            ],
            capture_output=True,
            text=True,
        )
    files = [f for f in ls.stdout.strip().split("\n") if f.endswith(".jsonl")]
    if not files:
        print("No event files found in /spine/events/.")
        sys.exit(0)
    latest = "/spine/events/" + files[0]
    cmd = ["docker", "exec", TALOS_CONTAINER, "tail", "-n", str(tail), latest]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip()}")
        sys.exit(1)
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            print(json.dumps(obj, indent=2))
        except json.JSONDecodeError:
            print(line)


def cmd_reset(args: argparse.Namespace) -> None:
    compose_path = Path(__file__).parent / "docker-compose.yml"
    if not compose_path.exists():
        # Maybe we are inside the repo root but not next to docker-compose.yml
        compose_path = Path.cwd() / "docker-compose.yml"
    if not compose_path.exists():
        print("Error: docker-compose.yml not found.")
        sys.exit(1)

    # Check memory preservation
    memory_dir = Path("memory")
    if memory_dir.exists() and any(memory_dir.iterdir()):
        if not args.hard and not args.preserve:
            print(
                "Memory dir contains files. Use --hard to wipe, or --preserve to keep."
            )
            sys.exit(1)

    print("Stopping containers...")
    subprocess.run(["docker", "compose", "-f", str(compose_path), "down"], check=True)

    print("Wiping observability volumes...")
    subprocess.run(
        ["docker", "volume", "rm", "-f", "talos_runtime_spine_observability"],
        capture_output=True,
    )

    print("Wiping app volume...")
    subprocess.run(
        ["docker", "volume", "rm", "-f", "talos_runtime_talos_app"],
        capture_output=True,
    )

    print("Cleaning local xray_data / llm_logs...")
    for d in ("xray_data", "llm_logs"):
        p = Path(d)
        if p.exists():
            for child in p.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    import shutil

                    shutil.rmtree(child)

    if args.hard and memory_dir.exists():
        print("Wiping memory...")
        for child in memory_dir.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                import shutil

                shutil.rmtree(child)

    print("Rebuilding and starting...")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d", "--build"],
        check=True,
    )
    print("Reset complete. Agent started from turn 0.")


def main() -> NoReturn:
    parser = argparse.ArgumentParser(
        prog="talosctl",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pause", help="Pause the agent")
    sub.add_parser("resume", help="Resume the agent")
    sub.add_parser("step", help="Trigger a single step when paused")

    events_parser = sub.add_parser("events", help="Tail recent spine events")
    events_parser.add_argument(
        "--tail", type=int, default=50, help="Number of lines (default: 50)"
    )

    reset_parser = sub.add_parser(
        "reset", help="Restart docker compose and optionally wipe state"
    )
    reset_parser.add_argument("--hard", action="store_true", help="Also wipe ./memory/")
    reset_parser.add_argument(
        "--preserve", action="store_true", help="Preserve ./memory/ even if non-empty"
    )

    args = parser.parse_args()

    handlers = {
        "pause": cmd_pause,
        "resume": cmd_resume,
        "step": cmd_step,
        "events": cmd_events,
        "reset": cmd_reset,
    }

    handlers[args.command](args)
    sys.exit(0)


if __name__ == "__main__":
    main()
