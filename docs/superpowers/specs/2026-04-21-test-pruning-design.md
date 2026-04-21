# Test Pruning for Agent-Friendly Evolution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate spine tests (immovable infrastructure) from cortex tests (agent-evolved), prune cortex tests to behavior-only, exclude spine tests from the Docker container.

**Architecture:** Spine tests move to `tests-spine/` at the talos package root (in git, not in container). Cortex tests stay in `tests/` but are pruned to ~30 behavior-only tests. No pytest markers needed — the container only contains cortex tests, so `pytest` naturally runs only what the agent can modify.

**Tech Stack:** Python 3.12, pytest, Docker COPY exclusions via .dockerignore

---

## File Map

### Moved
```
tests/spine/test_config.py       → tests-spine/test_config.py
tests/spine/test_constitution.py → tests-spine/test_constitution.py
tests/spine/test_events.py       → tests-spine/test_events.py
tests/spine/test_gate_proxy.py   → tests-spine/test_gate_proxy.py
tests/spine/test_health.py       → tests-spine/test_health.py
tests/spine/test_ipc_server.py   → tests-spine/test_ipc_server.py
tests/spine/test_ipc_types.py    → tests-spine/test_ipc_types.py
tests/spine/test_stream.py       → tests-spine/test_stream.py
tests/spine/test_supervisor.py   → tests-spine/test_supervisor.py
```

### Deleted
```
tests/spine/__init__.py          (moved with the rest)
tests/test_integration.py        (spine+cortex integration, not cortex-alone)
tests/cortex/test_spine_client.py (trivial creation tests)
```

### Pruned (behavior-only)
```
tests/cortex/test_seed_agent.py  (8 → 3 tests)
tests/cortex/test_state.py       (4 → 2 tests)
tests/cortex/test_tool_registry.py (7 → 3 tests)
tests/tools/test_executive.py    (10 → 5 tests)
tests/tools/test_file_ops.py     (9 → 4 tests)
tests/tools/test_git_ops.py      (10 → 4 tests)
tests/tools/test_guards.py       (17 → 3 tests)
tests/tools/test_physical.py     (18 → 6 tests)
```

### Modified
```
.dockerignore                     (exclude tests-spine/)
Dockerfile                        (no changes needed — COPY talos/ copies everything except .dockerignore exclusions)
conftest.py or pyproject.toml     (no markers needed — just update testpaths)
```

---

## Task 1: Move spine tests to tests-spine/

- [ ] Create `tests-spine/` directory at talos package root
- [ ] Move all 9 spine test files + __init__.py
- [ ] Delete `tests/spine/` directory
- [ ] Run `PYTHONPATH=. python -m pytest tests-spine/ tests/ -v` to verify all 150 still pass
- [ ] Commit

## Task 2: Delete integration and trivial tests

- [ ] Delete `tests/test_integration.py` (spine+cortex integration)
- [ ] Delete `tests/cortex/test_spine_client.py` (trivial)
- [ ] Run tests to verify remaining pass
- [ ] Commit

## Task 3: Prune cortex tests to behavior-only

### test_seed_agent.py (8 → 3)
Keep: `test_at_threshold`, `test_reset`, `test_build_hud`
Remove: `test_below_threshold`, `test_low_value_tool`, `test_alternating_no_false_positive`, `test_stall_report`, `test_max_tool_calls_per_turn`

### test_state.py (4 → 2)
Keep: `test_set_focus_persists`, `test_resolve_focus_clears`
Remove: `test_error_streak_persists_across_save_load`, `test_default_values`

### test_tool_registry.py (7 → 3)
Keep: `test_register_and_execute`, `test_type_error_reports_missing_args`, `test_unknown_tool_error`
Remove: `test_schema_generation`, `test_has_tool`, `test_tool_names`, `test_execute_exception_returns_error`

### test_executive.py (10 → 5)
Keep: `test_set_focus_execution`, `test_resolve_focus_execution`, `test_fold_context_execution`, `test_fold_context_calls_request_fold`, `test_reflect_execution`
Remove: `test_set_focus_registers`, `test_resolve_focus_registers`, `test_fold_context_registers`, `test_reflect_registers`, `test_reflect_with_sleep`

### test_file_ops.py (9 → 4)
Keep: `test_write_file_creates`, `test_write_file_rejects_spine_path`, `test_read_file_happy`, `test_read_file_not_found`
Remove: `test_read_file_registers`, `test_write_file_registers`, `test_patch_file_registers`, `test_write_file_creates_dirs`, `test_read_file_line_range`, `test_patch_file_rejects_spine_path`

### test_git_ops.py (10 → 4)
Keep: `test_git_commit_execution`, `test_git_checkout_rejects_main`, `test_git_push_rejects_main`, `test_git_push_allows_feature`
Remove: `test_git_commit_registers`, `test_git_checkout_registers`, `test_git_push_registers`, `test_git_checkout_rejects_master`, `test_git_checkout_rejects_origin_main`, `test_git_push_rejects_origin_master`

### test_guards.py (17 → 3)
Consolidate into:
- `test_spine_write_detects_writes` (redirect, append, tee, cp, mv, python, sed, dd, install — one parametrized test)
- `test_spine_write_allows_reads` (cat, grep, non-spine — one parametrized test)
- `test_blocked_flags_and_protected_branches` (verify --no-verify, --no-gpg-sign, main, master)

### test_physical.py (18 → 6)
Keep: `test_bash_command_echo`, `test_bash_command_nonzero_exit`, `test_bash_command_rejects_spine_write_redirect`, `test_bash_command_rejects_spine_python_write`, `test_send_message_execution`, `test_request_restart_dirty_repo`
Remove: All `*_registers` tests, `test_bash_command_empty_output`, `test_bash_command_rejects_no_verify`, `test_bash_command_rejects_no_gpg_sign`, `test_bash_command_rejects_spine_write_append`, `test_bash_command_rejects_spine_tee`, `test_bash_command_rejects_spine_cp`, `test_bash_command_rejects_spine_mv`, `test_bash_command_allows_reading_spine`, `test_bash_command_rejects_spine_sed_i`, `test_request_restart_clean_repo`

- [ ] Prune each file
- [ ] Run tests to verify
- [ ] Commit

## Task 4: Add .dockerignore to exclude tests-spine/

- [ ] Create `.dockerignore` with `tests-spine/`
- [ ] Verify Dockerfile COPY still works (it copies `talos/` subdir of parent repo)
- [ ] Commit

## Task 5: Verify final state

- [ ] `pytest tests/` should find ~30 cortex tests
- [ ] `pytest tests-spine/ tests/` should still find all ~120+ tests including spine
- [ ] Review the final test count and report