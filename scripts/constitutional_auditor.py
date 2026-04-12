import os
import sys
import subprocess
import json
import requests
from pathlib import Path

# Configuration - hardened and outside agent's write path
API_AUDIT_URL = os.environ.get("API_AUDIT_URL", "http://gate:4000/v1/audit")
LOG_DIR = Path("/runtime_logs")  # Gate logs path inside container
ROOT_DIR = Path("/app")

def get_staged_diff() -> str:
    result = subprocess.run(
        ["git", "diff", "--staged"],
        capture_output=True,
        text=True
    )
    return result.stdout.strip()

def load_last_trajectory() -> list:
    """Loads the exact message history from the most recent LLM trace log."""
    if not LOG_DIR.exists():
        return []

    try:
        # Find the newest call-*.json file
        logs = sorted(LOG_DIR.glob("call-*.json"), key=os.path.getmtime, reverse=True)
        if not logs:
            return []

        last_log = logs[0]
        data = json.loads(last_log.read_text(encoding="utf-8"))
        return data.get("messages", [])
    except Exception as e:
        print(f"[Auditor] Warning: Could not load last trajectory from trace: {e}")
        return []

def run_audit() -> None:
    diff = get_staged_diff()
    if not diff:
        sys.exit(0)

    # Reconstruct the exact trajectory from the last trace
    # This guarantees 100% KV cache hit for the prefix
    messages = load_last_trajectory()
    if not messages:
        print("[Auditor] Warning: No prior trace log found. Cache will be cold.")

    constitution = (ROOT_DIR / "CONSTITUTION.md").read_text(encoding="utf-8") if (ROOT_DIR / "CONSTITUTION.md").exists() else ""

    payload = {
        "git_diff": diff,
        "constitution": constitution,
        "messages": messages
    }

    try:
        print(f"[Sentinel] Performing hot-cache self-audit...")
        response = requests.post(API_AUDIT_URL, json=payload, timeout=200)
        response.raise_for_status()

        audit_report = response.json()
        if audit_report.get("rejected"):
            print(f"\n[Sentinel] REJECTED: {audit_report.get('reason')}")
            sys.exit(1)

        print(f"[Sentinel] APPROVED: {audit_report.get('reason')}")
        sys.exit(0)

    except Exception as e:
        print(f"[Sentinel] Auditor Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_audit()