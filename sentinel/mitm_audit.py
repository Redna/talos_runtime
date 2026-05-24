import os
import re
import json
import subprocess
import requests
import logging
from mitmproxy import http

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sentinel")

# Configuration
GATE_URL = os.getenv("GATE_URL", "http://gate:4000")
PII_PATTERNS = [
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    r"[0-9]{3}-[0-9]{3}-[0-9]{4}"
]
PROTECTED_PATHS = ["spine/", "scripts/post-commit", ".gitmodules"]
AGENT_APP_DIR = "/app"

def get_git_diff(old_rev, new_rev) -> str:
    try:
        # Ensure safe.directory is set at runtime just in case
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", AGENT_APP_DIR])
        
        if not re.match(r"^[0-9a-f]{40}$", old_rev) or not re.match(r"^[0-9a-f]{40}$", new_rev):
            return ""
        
        if old_rev == "0000000000000000000000000000000000000000":
            cmd = ["git", "show", new_rev]
        else:
            cmd = ["git", "diff", old_rev, new_rev]
            
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=AGENT_APP_DIR, timeout=30)
        if result.returncode != 0:
            logger.error(f"Git diff failed (code {result.returncode}): {result.stderr}")
            return ""
        return result.stdout.strip()
    except Exception as e:
        logger.error(f"Error getting git diff: {e}")
        return ""

def run_constitutional_audit(diff: str) -> dict:
    if not diff: return {"rejected": False}
    constitution_path = os.path.join(AGENT_APP_DIR, "CONSTITUTION.md")
    constitution = ""
    if os.path.exists(constitution_path):
        with open(constitution_path, "r") as f: constitution = f.read()

    file_chunks = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    for chunk in file_chunks:
        if not chunk.strip(): continue
        header_match = re.search(r"^diff --git a/(.*) b/.*$", chunk, re.MULTILINE)
        filename = header_match.group(1) if header_match else "unknown file"
        for protected in PROTECTED_PATHS:
            if filename.startswith(protected):
                return {"rejected": True, "reason": f"Immutable component violation: {filename}"}
        
        # Check PII/Secrets in chunk
        for pattern in PII_PATTERNS:
            if re.search(pattern, chunk):
                return {"rejected": True, "reason": f"PII detected in {filename}"}
        if "ghp_" in chunk or "sk-" in chunk:
            return {"rejected": True, "reason": f"Secret detected in {filename}"}

        res = requests.post(f"{GATE_URL}/v1/audit", json={"git_diff": chunk, "constitution": constitution, "messages": []}, timeout=60).json()
        if res.get("rejected"):
            res["reason"] = f"File: {filename} - {res.get('reason')}"
            return res
    return {"rejected": False}

def request(flow: http.HTTPFlow) -> None:
    logger.info(f"REQ: {flow.request.method} {flow.request.pretty_host}{flow.request.path}")
    
    if flow.request.method == "CONNECT":
        logger.info(f"HTTPS Tunnel established to {flow.request.pretty_host}")
        return

    if flow.request.method == "POST":
        logger.info(f"Intercepted POST to {flow.request.pretty_host}{flow.request.path}")
    
    # Aggressive Git Interception
    is_git_push = "git-receive-pack" in flow.request.path or (flow.request.method == "POST" and "service=git-receive-pack" in flow.request.path)
    
    if is_git_push:
        logger.info(f"Git push detected to {flow.request.pretty_host}")
        body = flow.request.get_text() or ""
        match = re.search(r"([0-9a-f]{40}) ([0-9a-f]{40})", body)
        if match:
            old_rev, new_rev = match.groups()
            logger.info(f"Auditing push: {old_rev[:7]} -> {new_rev[:7]}")
            diff = get_git_diff(old_rev, new_rev)
            if diff:
                audit_res = run_constitutional_audit(diff)
                if audit_res.get("rejected"):
                    reason = audit_res.get("reason", "Violation detected")
                    logger.warning(f"BLOCKING PUSH: {reason}")
                    flow.response = http.Response.make(403, f"SENTINEL REJECTED: {reason}".encode(), {"Content-Type": "text/plain"})
                    return
            else:
                logger.warning("Empty diff, skipping audit.")
        else:
            # Maybe PII/Secrets in raw body (git protocol metadata)
            pass

    # General POST Audit
    if flow.request.method == "POST" and not is_git_push:
        content = flow.request.get_text() or ""
        for pattern in PII_PATTERNS:
            if re.search(pattern, content):
                flow.response = http.Response.make(403, b"SENTINEL REJECTED: PII Detected", {"Content-Type": "text/plain"})
                return
        if "ghp_" in content or "sk-" in content:
             flow.response = http.Response.make(403, b"SENTINEL REJECTED: Secret Detected", {"Content-Type": "text/plain"})
             return
