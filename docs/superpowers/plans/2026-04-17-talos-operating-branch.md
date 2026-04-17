# Talos Operating Branch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the agent always works on `feat/talos`, auto-pushes after every commit, and reverts uncommitted changes on startup.

**Architecture:** Entrypoint handles branch switching and cleanup. Post-commit hook handles auto-push. Git tool restrictions prevent writing outside `feat/talos`.

**Tech Stack:** Bash (entrypoint), shell script (post-commit hook), Python (git_tools.py)

---

## File Map

| File | Change |
|------|--------|
| `entrypoint.sh` | Modify: add feat/talos checkout, pull, dirty-tree revert |
| `scripts/post-commit` | Create: auto-push after commit |
| `scripts/setup_hooks.sh` | Modify: install post-commit hook too |
| `talos/cortex/tools/git_operations.py` | Add: enforce feat/talos branch restrictions |

---

### Task 1: Update entrypoint.sh for feat/talos

**Files:**
- Modify: `entrypoint.sh:12-30`

- [ ] **Step 1: Read current entrypoint.sh section 1-30**

Read `entrypoint.sh` lines 1-30 to see current git/setup logic.

- [ ] **Step 2: Replace git worktree section with feat/talos logic**

Find and replace this block in entrypoint.sh (around lines 12-19):

```bash
ORIGINAL_GIT_POINTER=""
if [ -f /app/.git ] && grep -q "gitdir:" /app/.git && [ -d /runtime_git/objects ]; then
    ORIGINAL_GIT_POINTER=$(cat /app/.git)
    echo "[Entrypoint] Setting up git worktree for submodule..."
    cp -a /runtime_git /tmp/runtime_git
    sed -i "s|worktree = .*|worktree = /app|" /tmp/runtime_git/config
    echo "gitdir: /tmp/runtime_git" > /app/.git
fi
```

Replace with:

```bash
GIT_REMOTE=${GIT_REMOTE:-origin}
GIT_BRANCH=${GIT_BRANCH:-feat/talos}

if [ -f /app/.git ] && grep -q "gitdir:" /app/.git && [ -d /runtime_git/objects ]; then
    echo "[Entrypoint] Setting up git worktree for submodule..."
    cp -a /runtime_git /tmp/runtime_git
    sed -i "s|worktree = .*|worktree = /app|" /tmp/runtime_git/config
    echo "gitdir: /tmp/runtime_git" > /app/.git
fi

echo "[Entrypoint] Ensuring agent on $GIT_BRANCH..."
cd /app

if git rev-parse --verify "$GIT_BRANCH" > /dev/null 2>&1; then
    git checkout "$GIT_BRANCH"
    git pull --rebase "$GIT_REMOTE" "$GIT_BRANCH"
else
    git checkout -b "$GIT_BRANCH"
    git push -u "$GIT_REMOTE" "$GIT_BRANCH"
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "[Entrypoint] WARNING: Uncommitted changes found. Reverting..."
    git checkout -- .
fi

echo "[Entrypoint] Agent is on $(git branch --show-current) ($(git rev-parse --short HEAD))"
```

- [ ] **Step 3: Verify the change looks correct**

```bash
grep -n "feat/talos\|GIT_BRANCH\|GIT_REMOTE" entrypoint.sh
```

Expected: Shows the new variables and branch commands.

- [ ] **Step 4: Commit**

```bash
git add entrypoint.sh
git commit -m "feat: ensure agent operates on feat/talos branch

- Switch to feat/talos on every startup
- Pull remote progress with rebase
- Revert any uncommitted changes immediately
- Auto-create branch if it doesn't exist yet"
```

---

### Task 2: Create post-commit hook

**Files:**
- Create: `scripts/post-commit`

- [ ] **Step 1: Create the post-commit hook**

```bash
cat > scripts/post-commit << 'EOF'
#!/bin/bash
GIT_REMOTE=${GIT_REMOTE:-origin}
GIT_BRANCH=${GIT_BRANCH:-feat/talos}
echo "[Post-commit] Pushing to $GIT_REMOTE/$GIT_BRANCH..."
git push "$GIT_REMOTE" "$GIT_BRANCH" 2>&1 || {
    echo "[Post-commit] WARNING: Push failed. Agent will be notified."
    exit 0
}
EOF
chmod +x scripts/post-commit
```

- [ ] **Step 2: Verify it was created**

```bash
cat scripts/post-commit
```

- [ ] **Step 3: Commit**

```bash
git add scripts/post-commit
git commit -m "feat: add post-commit hook for auto-push to feat/talos"
```

---

### Task 3: Update setup_hooks.sh to install post-commit

**Files:**
- Modify: `scripts/setup_hooks.sh` (already partially updated — verify the full file is correct)

- [ ] **Step 1: Read current setup_hooks.sh**

Read the full `scripts/setup_hooks.sh` to verify it now includes both pre-commit and post-commit installation.

- [ ] **Step 2: Verify post-commit installation is present**

Look for this section in setup_hooks.sh:
```bash
chmod +x "$PRE_COMMIT_FILE"
chmod +x "$POST_COMMIT_FILE"
echo "Git hooks installed successfully."
```

If missing, add it.

- [ ] **Step 3: Commit**

```bash
git add scripts/setup_hooks.sh
git commit -m "feat: setup_hooks installs both pre-commit and post-commit hooks"
```

---

### Task 4: Enforce feat/talos in git_operations tool

**Files:**
- Modify: `talos/cortex/tools/git_operations.py`

The agent must never push to or checkout `main`. Add a guard.

- [ ] **Step 1: Read git_operations.py**

Read the full `talos/cortex/tools/git_operations.py`.

- [ ] **Step 2: Add branch restriction decorator or check**

Add a helper at the top of the file:

```python
PROTECTED_BRANCHES = {"main", "master", "origin/main", "origin/master"}

def _check_branch_allowed(branch: str) -> str:
    """Verify the branch is allowed. Returns error message if not."""
    if branch in PROTECTED_BRANCHES:
        return f"[ERROR] Cannot operate on protected branch '{branch}'. Use feat/talos."
    if branch.startswith("origin/"):
        base = branch.replace("origin/", "")
        if base not in PROTECTED_BRANCHES and base != "feat/talos":
            return f"[ERROR] Cannot push to origin/{base}. Use feat/talos."
    return ""
```

Then in `git_push()` (or wherever push is called), add:

```python
result = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    capture_output=True, text=True
)
current = result.stdout.strip()
err = _check_branch_allowed(current)
if err:
    return err
```

And in `git_checkout_branch()` or equivalent:

```python
err = _check_branch_allowed(branch_name)
if err:
    return err
```

- [ ] **Step 3: Test the restriction**

Verify `main`, `master`, `origin/main` are all blocked.

- [ ] **Step 4: Commit**

```bash
git add talos/cortex/tools/git_operations.py
git commit -m "feat: block git operations on main/master branches"
```

---

### Task 6: Update agent identity with operating branch rules

**Files:**
- Modify: `talos/identity.md`

- [ ] **Step 1: Read current identity.md**

```bash
cat -n talos/identity.md
```

- [ ] **Step 2: Add operating model section to identity**

Append to the end of `talos/identity.md`:

```markdown
## Operating Model

You work exclusively on the `feat/talos` branch. All your commits live there. You never modify `main` or any other branch. Your work is automatically pushed after every commit — treat your branch as a live backup.

Before starting work after a restart, you will be placed on `feat/talos`. If uncommitted changes exist from a previous session, they are reverted — start fresh from the last committed state.
```

- [ ] **Step 3: Commit**

```bash
git add talos/identity.md
git commit -m "docs: add operating branch rules to agent identity"
```

---

### Task 7: Verify full integration

- [ ] **Step 1: Check all files changed**

```bash
git diff --stat HEAD~5
```

Expected changes:
- `entrypoint.sh` — feat/talos setup
- `scripts/post-commit` — new file
- `scripts/setup_hooks.sh` — post-commit hook installation
- `talos/cortex/tools/git_operations.py` — branch restrictions
- `talos/identity.md` — operating model section

- [ ] **Step 2: Verify identity includes feat/talos mention**

```bash
grep -n "feat/talos" talos/identity.md
```

Expected: Shows the new section.
