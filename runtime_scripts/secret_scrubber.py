"""
Secret scrubber for runtime log payloads.

The nono proxy keeps the real GitHub OAuth token out of the Cortex sandbox,
but tool output that the agent (or the Gate) runs can still echo the token
back in a tool result -- for example, `git config --list --show-origin` or
`git remote -v` will print the real `gho_...` value embedded in
`remote.origin.url`. Because the Gate and xray sit *outside* the Cortex
sandbox, they capture the full request/response (including echoed tool
output) and persist it to `llm_logs/` and `xray_data/messages/*.jsonl`.

This module provides a tiny, dependency-free scrubber that replaces any
known-shape secret with a stable placeholder. It is applied at *write time*
to the dict the logger is about to persist, so the on-disk artifacts never
contain the raw token.

Design notes:
    - Pure stdlib (`re` only); no ML, no I/O.
    - Patterns are anchored to vendor prefixes (`gho_`, `ghp_`, `sk-`, ...)
      to keep false positives low. We deliberately do NOT match a bare 40
      hex string by itself; that pattern is only fired in a Together AI
      context.
    - `scrub_dict` recurses through dicts / lists / tuples and only touches
      string leaves, so it can be wrapped around any already-serialised
      payload (e.g. a message list) without breaking structure.
    - Idempotent: a string that has already been redacted (e.g.
      `gho_REDACTED`) is not re-matched, because every replacement is
      fixed-length and shorter than the minimum pattern length.
"""

from __future__ import annotations

import re
from typing import Any

# (pattern, replacement) tuples. Order matters: longer / more specific
# prefixes should be matched first (e.g. `sk-ant-` before `sk-`).
SECRET_PATTERNS: list[tuple[str, str]] = [
    # GitHub OAuth tokens (gho_) -- the actual leak from the 2026-06-07
    # incident used the prefix gho_.
    (r"gho_[A-Za-z0-9]{36,}", "gho_REDACTED"),
    # GitHub classic PATs.
    (r"ghp_[A-Za-z0-9]{36,}", "ghp_REDACTED"),
    # GitHub fine-grained PATs (look like `github_pat_XXXX_YYYY`).
    (r"github_pat_[A-Za-z0-9_]{40,}", "github_pat_REDACTED"),
    # GitHub server-to-server tokens.
    (r"ghs_[A-Za-z0-9]{36,}", "ghs_REDACTED"),
    # GitHub refresh tokens.
    (r"ghr_[A-Za-z0-9]{36,}", "ghr_REDACTED"),
    # Anthropic API keys (must come before the generic `sk-`).
    (r"sk-ant-[A-Za-z0-9_-]{20,}", "sk-ant-REDACTED"),
    # OpenAI-style secret keys.
    (r"sk-[A-Za-z0-9]{20,}", "sk-REDACTED"),
    # Telegram bot tokens: numeric_id:AA[alnum], where AA is the literal
    # token alphabet prefix and the right-hand side is at least 30 chars.
    (r"\b\d{8,12}:AA[A-Za-z0-9_-]{30,}\b", "TELEGRAM_BOT_TOKEN_REDACTED"),
    # GitHub x-access-token URL form: `https://x-access-token:<TOKEN>@host`.
    # Match the literal prefix so we do not eat the rest of the URL.
    (r"x-access-token:[^@\s]+@", "x-access-token:REDACTED@"),
    # NVIDIA API keys (start with nvapi-).
    (r"nvapi-[A-Za-z0-9_-]{20,}", "nvapi-REDACTED"),
    # Together AI keys. 40 hex chars, but only when the surrounding context
    # (within the same string) mentions Together, to avoid clobbering git
    # SHAs and the like.
    (r"\b[a-f0-9]{40}\b(?=[\s\S]{0,200}?(?:together|togetherai))", "TOGETHERAI_KEY_REDACTED"),
    (r"(?:together|togetherai)[\s\S]{0,200}?\b[a-f0-9]{40}\b", "TOGETHERAI_KEY_REDACTED"),
]

# Pre-compile for speed; scrubbing is called on every tool result and we
# want it to be a no-op cost on the hot path.
_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), repl) for p, repl in SECRET_PATTERNS
]


def scrub(text: str) -> str:
    """Return ``text`` with any matched secret replaced by a placeholder.

    Non-string inputs are returned unchanged; callers that need recursive
    scrubbing of containers should use :func:`scrub_dict`.
    """
    if not isinstance(text, str) or not text:
        return text
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text


def scrub_dict(value: Any) -> Any:
    """Recursively scrub all string values inside a nested structure.

    Dicts, lists, and tuples are walked in place; their non-string leaves
    are returned unchanged. Strings are passed through :func:`scrub`.
    Strings nested inside other containers are scrubbed, and dict keys
    that are themselves strings are also scrubbed (so a key like
    ``"Authorization": "Bearer ghp_..."`` is redacted on both sides).
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(k) if isinstance(k, str) else k: scrub_dict(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_dict(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub_dict(v) for v in value)
    return value


__all__ = ["SECRET_PATTERNS", "scrub", "scrub_dict"]
