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
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",  # Email
    r"[0-9]{3}-[0-9]{3}-[0-9]{4}",                     # Phone
    r"\([0-9]{3}\) [0-9]{3}-[0-9]{4}"                  # Phone (alt)
]
# Protected paths that the agent is FORBIDDEN from modifying via git push
PROTECTED_PATHS = ["spine/", "scripts/post-commit", ".gitmodules"]
AGENT_APP_DIR = "/app"

def get_git_diff(old_rev, new_rev) -> str:
    """Extract diff from the shared /agent_app volume repository."""
    try:
        if not re.match(r"^[0-9a-f]{40}$", old_rev) or not re.match(r"^[0-9a-f]{40}$", new_rev):
            return ""
        
        # If old_rev is all zeros, it's a new branch push.
        if old_rev == "0000000000000000000000000000000000000000":
            cmd = ["git", "show", new_rev]
        else:
            cmd = ["git", "diff", old_rev, new_rev]
            
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=AGENT_APP_DIR,
            timeout=30
        )
        return result.stdout.strip()
    except Exception as e:
        logger.error(f"Error getting git diff: {e}")
        return ""

def run_constitutional_audit(diff: str) -> dict:
    """
    Perform an atomic, file-level Constitutional Audit.
    Iterates through each modified file to ensure full context for each change.
    """
    if not diff:
        return {"rejected": False, "reason": "No diff found to audit."}
    
    constitution_path = os.path.join(AGENT_APP_DIR, "CONSTITUTION.md")
    constitution = ""
    if os.path.exists(constitution_path):
        with open(constitution_path, "r") as f:
            constitution = f.read()

    # Split diff into atomic file chunks based on the standard 'diff --git' header
    file_chunks = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    
    for chunk in file_chunks:
        if not chunk.strip():
            continue
            
        # Extract filename for logging and protection check
        header_match = re.search(r"^diff --git a/(.*) b/.*$", chunk, re.MULTILINE)
        filename = header_match.group(1) if header_match else "unknown file"
        
        # 1. Hard Protected Path Check
        for protected in PROTECTED_PATHS:
            if filename.startswith(protected):
                return {
                    "rejected": True, 
                    "reason": f"Immutable Component Violation: Access Denied to {filename}. The Spine and Core infrastructure are protected from modification."
                }

        logger.info(f"Auditing file: {filename}...")
        res = _call_gate_audit(chunk, constitution)
        
        if res.get("rejected"):
            # Include filename in the rejection reason for rich cues
            res["reason"] = f"File: {filename} - {res.get('reason')}"
            return res

    return {"rejected": False, "reason": "All files passed audit."}

def _call_gate_audit(diff_chunk: str, constitution: str) -> dict:
    try:
        resp = requests.post(
            f"{GATE_URL}/v1/audit",
            json={
                "git_diff": diff_chunk,
                "constitution": constitution,
                "messages": [] 
            },
            timeout=200
        )
        return resp.json()
    except Exception as e:
        logger.error(f"Auditor call failed: {e}")
        return {"rejected": True, "reason": f"Sentinel Auditor Connectivity Error: {e}"}

def request(flow: http.HTTPFlow) -> None:
    # 1. Deep Git Audit
    if "github.com" in flow.request.pretty_host and "git-receive-pack" in flow.request.path:
        logger.info(f"Intercepting Git push to {flow.request.pretty_host}")
        
        try:
            body = flow.request.content.decode("utf-8", errors="ignore")
            # Extract revisions from pkt-line sequence
            match = re.search(r"([0-9a-f]{40}) ([0-9a-f]{40}) (refs/heads/[\w/-]+)", body)
            if match:
                old_rev, new_rev, ref = match.groups()
                logger.info(f"Starting deep audit: {ref} ({old_rev[:7]} -> {new_rev[:7]})")
                
                diff = get_git_diff(old_rev, new_rev)
                if not diff:
                    logger.warning("Skipping audit: Empty diff or error extracting diff.")
                    return

                # Check for PII/Secrets in the entire diff first (Static Analysis)
                for pattern in PII_PATTERNS:
                    if re.search(pattern, diff):
                        logger.warning("BLOCKED: PII detected in diff")
                        flow.response = http.Response.make(
                            403,
                            f"SENTINEL REJECTED: Potential PII detected in push {new_rev[:7]}".encode(),
                            {"Content-Type": "text/plain"}
                        )
                        return

                if "ghp_" in diff or "sk-" in diff:
                    logger.warning("BLOCKED: Secret detected in diff")
                    flow.response = http.Response.make(
                        403,
                        f"SENTINEL REJECTED: Potential API Key detected in push {new_rev[:7]}".encode(),
                        {"Content-Type": "text/plain"}
                    )
                    return

                # Run Atomic Constitutional Audit + Protected Path Check
                audit_res = run_constitutional_audit(diff)
                if audit_res.get("rejected"):
                    reason = audit_res.get("reason", "Unknown violation")
                    logger.warning(f"BLOCKED: {reason}")
                    flow.response = http.Response.make(
                        403,
                        f"SENTINEL REJECTED: {reason}".encode(),
                        {"Content-Type": "text/plain"}
                    )
                    return
                
                logger.info(f"Push PASSED deep audit for {new_rev[:7]}")
        except Exception as e:
            logger.error(f"Git audit error: {e}")
            flow.response = http.Response.make(
                403,
                f"SENTINEL ERROR: Could not audit git push. Details: {e}".encode(),
                {"Content-Type": "text/plain"}
            )
            return

    # 2. General HTTP Audit
    if flow.request.method == "POST":
        content = flow.request.get_text() or ""
        if content:
            # Check for PII
            for pattern in PII_PATTERNS:
                if re.search(pattern, content):
                    logger.warning("BLOCKED: PII match in POST body")
                    flow.response = http.Response.make(
                        403,
                        b"SENTINEL REJECTED: Access Denied: Potential PII detected in outbound request.",
                        {"Content-Type": "text/plain"}
                    )
                    return

            # Check for Secrets
            if "ghp_" in content or "sk-" in content:
                logger.warning("BLOCKED: Potential API Key match in POST body")
                flow.response = http.Response.make(
                    403,
                    b"SENTINEL REJECTED: Access Denied: Potential API Key detected in outbound request.",
                    {"Content-Type": "text/plain"}
                )
                return

def response(flow: http.HTTPFlow) -> None:
    pass
