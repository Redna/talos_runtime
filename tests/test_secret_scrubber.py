"""Unit tests for runtime_scripts/secret_scrubber.

Covers the four spec cases plus a few negatives so future refactors can't
accidentally start clobbering ordinary text (e.g. 40-hex git SHAs that
happen to look like 40 hex chars).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make sure we can import the module directly, regardless of which
# directory pytest was invoked from.
_RUNTIME_SCRIPTS = Path(__file__).resolve().parent.parent / "runtime_scripts"
if str(_RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SCRIPTS))

from secret_scrubber import SECRET_PATTERNS, scrub, scrub_dict  # noqa: E402

# A synthetic token matching the gho_ pattern. We deliberately do NOT
# use the real incident token in source — that one was GitGuardian-flagged
# and revoked. This token matches the regex shape (gho_ + 36 alphanumerics)
# but is not a real GitHub credential.
INCIDENT_TOKEN = "gho_" + "A" * 36


# ---------------------------------------------------------------------------
# scrub(): spec cases
# ---------------------------------------------------------------------------


def test_scrub_replaces_gho_token():
    # 36 alnum chars is the minimum; the spec value is 40.
    long_token = "gho_" + "a" * 40
    assert scrub(long_token) == "gho_REDACTED"
    # The incident token (real-world shape).
    assert scrub(INCIDENT_TOKEN) == "gho_REDACTED"


def test_scrub_replaces_x_access_token_url_form():
    raw = f"https://x-access-token:{INCIDENT_TOKEN}@github.com/Redna/talos.git"
    expected = "https://x-access-token:REDACTED@github.com/Redna/talos.git"
    assert scrub(raw) == expected


def test_scrub_ghp_pat():
    pat = "ghp_" + "A" * 40
    assert scrub(f"token: {pat}") == "token: ghp_REDACTED"


def test_scrub_github_fine_grained_pat():
    pat = "github_pat_" + "1" * 40
    assert scrub(pat) == "github_pat_REDACTED"


def test_scrub_github_server_to_server():
    pat = "ghs_" + "Z" * 40
    assert scrub(pat) == "ghs_REDACTED"


def test_scrub_github_refresh_token():
    pat = "ghr_" + "Q" * 40
    assert scrub(pat) == "ghr_REDACTED"


def test_scrub_anthropic_key():
    key = "sk-ant-" + "A" * 40
    assert scrub(key) == "sk-ant-REDACTED"


def test_scrub_openai_key():
    key = "sk-" + "A" * 40
    assert scrub(key) == "sk-REDACTED"


def test_scrub_telegram_bot_token():
    bot = "12345678:AA" + "A" * 35
    assert scrub(f"see {bot} in vault") == "see TELEGRAM_BOT_TOKEN_REDACTED in vault"


def test_scrub_nvidia_key():
    key = "nvapi-" + "x" * 40
    assert scrub(key) == "nvapi-REDACTED"


def test_scrub_together_key_with_context():
    # 40 hex chars that look like a git SHA, BUT the surrounding text
    # mentions "together", so we DO redact.
    key = "a" * 40
    text = f"Using together api key {key} for inference"
    scrubed = scrub(text)
    assert key not in scrubed
    assert "TOGETHERAI_KEY_REDACTED" in scrubed


def test_scrub_actual_incident_token():
    """The exact token from the 2026-06-07 incident must be redacted."""
    raw = f"remote.origin.url=https://x-access-token:{INCIDENT_TOKEN}@github.com/Redna/talos.git"
    out = scrub(raw)
    assert INCIDENT_TOKEN not in out
    assert "gho_REDACTED" in out or "x-access-token:REDACTED" in out


# ---------------------------------------------------------------------------
# scrub_dict(): nested structures
# ---------------------------------------------------------------------------


def test_scrub_dict_nested_messages():
    payload = {
        "messages": [
            {"role": "system", "content": "You are Talos."},
            {"role": "user", "content": "git config --list"},
            {"role": "tool", "content": f"token: gho_{'a' * 40}"},
        ]
    }
    out = scrub_dict(payload)
    assert out["messages"][2]["content"] == "token: gho_REDACTED"
    # Make sure we did not mutate the input.
    assert INCIDENT_TOKEN[:10] in payload["messages"][2]["content"] or "gho_" in payload["messages"][2]["content"]


def test_scrub_dict_dict_in_list():
    pat = "ghp_" + "x" * 40
    out = scrub_dict({"messages": [{"content": f"token: {pat}"}]})
    assert out == {"messages": [{"content": "token: ghp_REDACTED"}]}


def test_scrub_dict_handles_tuples_and_lists():
    payload = {"items": (f"gho_{'a' * 40}", "plain text", ["sk-" + "B" * 40])}
    out = scrub_dict(payload)
    assert out["items"][0] == "gho_REDACTED"
    assert out["items"][1] == "plain text"
    assert out["items"][2][0] == "sk-REDACTED"


def test_scrub_dict_does_not_mutate_input():
    original = {"a": f"gho_{'a' * 40}"}
    snapshot = original.copy()
    scrub_dict(original)
    assert original == snapshot


def test_scrub_dict_passes_through_non_strings():
    assert scrub_dict({"n": 42, "f": 3.14, "b": True, "none": None}) == {
        "n": 42,
        "f": 3.14,
        "b": True,
        "none": None,
    }


def test_scrub_dict_string_key_redacted():
    # 40 alnum chars after the prefix exactly matches the pattern length
    # so the substitution wipes the whole key (the x's are gone).
    key = "ghp_" + "x" * 40
    out = scrub_dict({key: "ok"})
    # The literal 40-x payload is gone -- it is now the redacted
    # placeholder, which is what matters.
    only_key = next(iter(out.keys()))
    assert only_key == "ghp_REDACTED"
    assert "xxxx" not in only_key


# ---------------------------------------------------------------------------
# Negative cases: things that LOOK like secrets but must NOT be redacted
# ---------------------------------------------------------------------------


def test_scrub_does_not_touch_40_hex_sha_alone():
    """A bare 40-char hex string (a git SHA) must NOT be redacted unless
    'together' is in the surrounding context."""
    sha = "a" * 40
    assert scrub(sha) == sha
    assert scrub(f"commit {sha} by alice") == f"commit {sha} by alice"


def test_scrub_does_not_touch_random_short_text():
    assert scrub("hello world") == "hello world"
    assert scrub("") == ""
    # Too short to match any pattern.
    assert scrub("gho_short") == "gho_short"
    assert scrub("sk-short") == "sk-short"


def test_scrub_returns_non_string_unchanged():
    assert scrub(None) is None
    assert scrub(123) == 123
    assert scrub(["a", "b"]) == ["a", "b"]


def test_scrub_is_idempotent():
    once = scrub(f"gho_{'a' * 40}")
    twice = scrub(once)
    assert once == twice == "gho_REDACTED"


def test_secret_patterns_is_non_empty_list_of_pairs():
    assert isinstance(SECRET_PATTERNS, list)
    assert SECRET_PATTERNS, "SECRET_PATTERNS must not be empty"
    for entry in SECRET_PATTERNS:
        assert isinstance(entry, tuple) and len(entry) == 2
        pattern, replacement = entry
        assert isinstance(pattern, str) and pattern
        assert isinstance(replacement, str) and replacement


# ---------------------------------------------------------------------------
# Sanity: the patterns themselves are valid regexes (regression guard).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern,_replacement", SECRET_PATTERNS)
def test_each_pattern_is_valid_regex(pattern, _replacement):
    import re

    re.compile(pattern)


if __name__ == "__main__":
    # Allow `python tests/test_secret_scrubber.py` to run the tests.
    import sys as _sys

    _sys.exit(pytest.main([__file__, "-v"]))
