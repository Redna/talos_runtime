import os
import sys
import subprocess
import json
import urllib.request
import urllib.error
from pathlib import Path

API_AUDIT_URL = os.environ.get("API_AUDIT_URL", "http://gate:4000/v1/audit")
LOG_DIR = Path("/runtime_logs")
ROOT_DIR = Path("/app")


def get_staged_diff() -> str:
    result = subprocess.run(
        ["git", "diff", "--staged"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_last_trajectory() -> list:
    if not LOG_DIR.exists():
        return []
    try:
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

    messages = load_last_trajectory()
    if not messages:
        print("[Auditor] Warning: No prior trace log found. Cache will be cold.")

    constitution = (
        (ROOT_DIR / "CONSTITUTION.md").read_text(encoding="utf-8")
        if (ROOT_DIR / "CONSTITUTION.md").exists()
        else ""
    )

    payload = json.dumps(
        {
            "git_diff": diff,
            "constitution": constitution,
            "messages": messages,
        }
    ).encode("utf-8")

    try:
        print("[Sentinel] Performing hot-cache self-audit...")
        req = urllib.request.Request(
            API_AUDIT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=200) as resp:
            audit_report = json.loads(resp.read().decode("utf-8"))

        if audit_report.get("rejected"):
            print(f"\n[Sentinel] REJECTED: {audit_report.get('reason')}")
            sys.exit(1)

        print(f"[Sentinel] APPROVED: {audit_report.get('reason')}")
        sys.exit(0)

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[Sentinel] Auditor HTTP error {e.code}: {body}")
        sys.exit(1)
    except Exception as e:
        print(f"[Sentinel] Auditor Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_audit()
