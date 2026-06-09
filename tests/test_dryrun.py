"""
Unit tests for the dry-run harness.

These tests cover the *components* of the dry-run — the mock
cortex driver, the scripted LLM gateway, and the metrics
collector.  The end-to-end scenarios (full Spine-Cortex-nono
run in docker) are exercised manually via ``talosctl dry-run``
because they need the docker host and a built image.

A few of the assertions intentionally call the live code paths
so a future refactor of, say, the OpenAI tool_call shape will
catch a regression here before it breaks the dry-run in
production.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Make the modules under test importable regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "talos" / "spine"))
sys.path.insert(0, str(_REPO_ROOT / "talos" / "cortex"))
sys.path.insert(0, str(_REPO_ROOT / "gate"))

import pytest


# ---------------------------------------------------------------------------
# Cortex driver
# ---------------------------------------------------------------------------

def test_driver_is_inactive_without_env(monkeypatch):
    """Driver must be a no-op when TALOS_DRYRUN_MODE is unset."""
    monkeypatch.delenv("TALOS_DRYRUN_MODE", raising=False)
    # Force a re-import so the module reads the current env.
    for mod in list(sys.modules):
        if mod == "dryrun_driver":
            del sys.modules[mod]
    import dryrun_driver
    assert dryrun_driver.is_active() is False
    plan = dryrun_driver.plan_next(1)
    # When inactive the driver still returns *some* plan (a bash
    # echo) so the calling code does not have to special-case the
    # driver.  That is the contract.
    assert plan.tool_name == "bash_command"


def test_driver_happy_mode(monkeypatch):
    """Happy mode issues bash echoes for every turn."""
    monkeypatch.setenv("TALOS_DRYRUN_MODE", "happy")
    for mod in list(sys.modules):
        if mod == "dryrun_driver":
            del sys.modules[mod]
    import dryrun_driver

    assert dryrun_driver.is_active() is True
    for turn in range(1, 6):
        plan = dryrun_driver.plan_next(turn)
        assert plan.tool_name == "bash_command"
        assert f"cycle {turn}" in plan.arguments["command"]
        assert plan.exit_after is False
        assert plan.sleep_after == 0


def test_driver_crash_mode(monkeypatch):
    """Crash mode plays N turns then issues request_restart and exits."""
    monkeypatch.setenv("TALOS_DRYRUN_MODE", "crash")
    monkeypatch.setenv("TALOS_DRYRUN_CRASH_AT_TURN", "3")
    for mod in list(sys.modules):
        if mod == "dryrun_driver":
            del sys.modules[mod]
    import dryrun_driver

    # Turns 1, 2: bash echoes
    p1 = dryrun_driver.plan_next(1)
    p2 = dryrun_driver.plan_next(2)
    assert p1.tool_name == "bash_command"
    assert p2.tool_name == "bash_command"
    assert not dryrun_driver.should_exit(p1)

    # Turn 3: request_restart
    p3 = dryrun_driver.plan_next(3)
    assert p3.tool_name == "request_restart"
    assert dryrun_driver.should_exit(p3) is True
    assert "simulated crash" in p3.arguments["reason"]


def test_driver_stall_mode(monkeypatch):
    """Stall mode plays N turns then issues reflect(sleep=10000)."""
    monkeypatch.setenv("TALOS_DRYRUN_MODE", "stall")
    monkeypatch.setenv("TALOS_DRYRUN_STALL_AT_TURN", "2")
    for mod in list(sys.modules):
        if mod == "dryrun_driver":
            del sys.modules[mod]
    import dryrun_driver

    p1 = dryrun_driver.plan_next(1)
    assert p1.tool_name == "bash_command"
    assert not dryrun_driver.should_block(p1)

    p2 = dryrun_driver.plan_next(2)
    assert p2.tool_name == "reflect"
    assert dryrun_driver.should_block(p2) is True
    assert p2.arguments["sleep_duration"] >= 1000


def test_driver_openai_shape(monkeypatch):
    """plan_to_openai_tool_call must produce the shape the Spine parses."""
    monkeypatch.setenv("TALOS_DRYRUN_MODE", "happy")
    for mod in list(sys.modules):
        if mod == "dryrun_driver":
            del sys.modules[mod]
    import dryrun_driver

    plan = dryrun_driver.plan_next(5)
    tc = dryrun_driver.plan_to_openai_tool_call(plan, 5)
    assert tc["id"] == "call_0005"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "bash_command"
    # The arguments must be a JSON string (the spine parses it).
    args = json.loads(tc["function"]["arguments"])
    assert "command" in args


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------

def test_metrics_record_crash_lazarus_stall():
    """The metrics module aggregates the events the spine emits."""
    from metrics import DryRunMetrics

    m = DryRunMetrics(scenario="crash")
    m.record_event({"type": "cortex.tool_call", "payload": {"tool": "bash_command"}})
    m.record_event({"type": "cortex.tool_result", "payload": {"duration_ms": 42, "success": True}})
    m.record_event({"type": "cortex.tool_call", "payload": {"tool": "bash_command"}})
    m.record_event({"type": "cortex.tool_result", "payload": {"duration_ms": 100, "success": True}})
    m.record_event({"type": "supervisor.cortex_exit", "payload": {"code": 0}})
    m.record_event({"type": "supervisor.cortex_stall", "payload": {"stall_timeout": 15}})
    m.record_event({"type": "supervisor.lazarus_triggered", "payload": {"reason": "crash_loop"}})
    m.record_event({"type": "spine.garbage_response", "payload": {"consecutive": 1}})

    m.finalize()

    assert m.total_cycles == 2
    assert m.crash_count == 1
    assert m.stall_count == 1
    assert m.lazarus_count == 1
    assert m.garbage_response_count == 1
    assert m.tool_call_counts["bash_command"] == 2
    assert 42 <= m.mean() <= 100

    table = m.summary_table()
    assert "crash count" in table
    assert "1" in table
    assert "lazarus count" in table


def test_metrics_collect_from_event_log(tmp_path):
    """The collector must read NDJSON files in date order."""
    from metrics import collect_from_event_log

    events_dir = tmp_path / "events"
    events_dir.mkdir()
    log_path = events_dir / "2026-06-08.jsonl"
    log_path.write_text(
        "\n".join([
            json.dumps({"type": "cortex.tool_call", "payload": {"tool": "bash_command"}}),
            json.dumps({"type": "cortex.tool_result", "payload": {"duration_ms": 10}}),
            json.dumps({"type": "supervisor.cortex_exit", "payload": {"code": 0}}),
            "",
        ])
    )
    m = collect_from_event_log(events_dir)
    assert m.total_cycles == 1
    assert m.crash_count == 1


def test_metrics_percentiles():
    """p50 / p99 / max / mean on an empty list must not raise."""
    from metrics import DryRunMetrics

    m = DryRunMetrics()
    assert m.mean() == 0.0
    assert m.max_ms() == 0.0
    assert m.p(50) == 0.0
    assert m.p(99) == 0.0

    # Small list — use a fixed sample.
    m.cycle_times_ms = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert m.mean() == 55.0
    assert m.max_ms() == 100.0
    # Nearest-rank percentile on a 10-item list picks index 4 (50)
    # for p50 and index 9 (100) for p99.  We do not use the
    # linear-interpolation method; nearest-rank is the cheapest
    # "good enough" choice for a small sample.
    assert m.p(50) == 50.0
    assert m.p(99) == 100.0


# ---------------------------------------------------------------------------
# Scripted LLM gateway — exercises the file format the scripts use, but
# does not start a real FastAPI server.
# ---------------------------------------------------------------------------

def test_happy_script_loads():
    """The happy script is intentionally empty (we use the default tool call)."""
    p = Path(_REPO_ROOT / "gate" / "dryrun_script_happy.json")
    data = json.loads(p.read_text())
    assert data["scenario"] == "happy"
    assert data["responses"] == []


def test_crash_script_has_six_turns():
    """The crash script plays 5 normal turns then a request_restart."""
    p = Path(_REPO_ROOT / "gate" / "dryrun_script_crash.json")
    data = json.loads(p.read_text())
    assert data["scenario"] == "crash"
    responses = data["responses"]
    assert len(responses) == 6
    # First 5 are bash_command.
    for i in range(5):
        tc = responses[i]["choices"][0]["message"]["tool_calls"][0]
        assert tc["function"]["name"] == "bash_command"
    # Turn 6 is request_restart.
    restart = responses[5]["choices"][0]["message"]["tool_calls"][0]
    assert restart["function"]["name"] == "request_restart"
    args = json.loads(restart["function"]["arguments"])
    assert "reason" in args


def test_stall_script_has_four_turns():
    """The stall script plays 3 normal turns then reflect(sleep=10000)."""
    p = Path(_REPO_ROOT / "gate" / "dryrun_script_stall.json")
    data = json.loads(p.read_text())
    assert data["scenario"] == "stall"
    responses = data["responses"]
    assert len(responses) == 4
    for i in range(3):
        tc = responses[i]["choices"][0]["message"]["tool_calls"][0]
        assert tc["function"]["name"] == "bash_command"
    reflect_call = responses[3]["choices"][0]["message"]["tool_calls"][0]
    assert reflect_call["function"]["name"] == "reflect"
    args = json.loads(reflect_call["function"]["arguments"])
    assert args["sleep_duration"] >= 1000


def test_gateway_healthz(monkeypatch):
    """The dryrun gate's healthz endpoint should return a healthy status."""
    # Set the env vars *before* importing the app so the module-level
    # constants pick them up.
    monkeypatch.setenv("DRYRUN_SCENARIO", "happy")
    monkeypatch.setenv("DRYRUN_SCRIPT", str(_REPO_ROOT / "gate" / "dryrun_script_happy.json"))
    from fastapi.testclient import TestClient
    import importlib
    if "dryrun_app" in sys.modules:
        del sys.modules["dryrun_app"]
    import dryrun_app
    client = TestClient(dryrun_app.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["scenario"] == "happy"


def test_gateway_serves_scripted_response(monkeypatch):
    """A request after startup should return the first scripted response."""
    monkeypatch.setenv("DRYRUN_SCENARIO", "crash")
    monkeypatch.setenv("DRYRUN_SCRIPT", str(_REPO_ROOT / "gate" / "dryrun_script_crash.json"))
    from fastapi.testclient import TestClient
    if "dryrun_app" in sys.modules:
        del sys.modules["dryrun_app"]
    import dryrun_app
    client = TestClient(dryrun_app.app)
    # Trigger the startup handler to load the script.
    client.get("/healthz")
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # First scripted response is a bash_command tool call.
    tc = body["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "bash_command"
    args = json.loads(tc["function"]["arguments"])
    assert "echo" in args["command"]


def test_gateway_falls_through_after_script(monkeypatch):
    """After the script is exhausted the gateway returns a default bash echo."""
    monkeypatch.setenv("DRYRUN_SCENARIO", "crash")
    monkeypatch.setenv("DRYRUN_SCRIPT", str(_REPO_ROOT / "gate" / "dryrun_script_crash.json"))
    from fastapi.testclient import TestClient
    if "dryrun_app" in sys.modules:
        del sys.modules["dryrun_app"]
    import dryrun_app
    client = TestClient(dryrun_app.app)
    client.get("/healthz")
    # Drain all 6 scripted entries.
    for i in range(6):
        client.post("/v1/chat/completions", json={"messages": []})
    # 7th request should be the default.
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": []},
    )
    body = resp.json()
    tc = body["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "bash_command"
    args = json.loads(tc["function"]["arguments"])
    assert "dry-run cycle" in args["command"]


# ---------------------------------------------------------------------------
# talosctl dry-run subcommand — the argparse glue
# ---------------------------------------------------------------------------

def test_talosctl_dry_run_help():
    """The dry-run subcommand must surface a --help that lists all flags."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "talosctl", "dry-run", "--help"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0
    out = result.stdout
    for flag in ("--scenario", "--cycles", "--crash-after", "--stall-after",
                 "--stall-timeout", "--timeout", "--keep"):
        assert flag in out, f"missing {flag} in talosctl dry-run --help"


def test_talosctl_dry_run_rejects_bad_scenario():
    """An unknown scenario must be rejected by argparse."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "talosctl", "dry-run", "--scenario", "bogus"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
