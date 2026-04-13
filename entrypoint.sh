#!/bin/bash
set -e

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

if ! getent group "$GROUP_ID" >/dev/null; then groupadd -g "$GROUP_ID" talos; fi
if ! getent passwd "$USER_ID" >/dev/null; then useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/bash talos; fi

USER_NAME=$(getent passwd "$USER_ID" | cut -d: -f1)

sudo -u "$USER_NAME" -H git config --global user.name "Talos"
sudo -u "$USER_NAME" -H git config --global user.email "talos@agent.local"
sudo -u "$USER_NAME" -H git config --global --add safe.directory /app
sudo -u "$USER_NAME" -H git config --global --add safe.directory /runtime_git
git config --global --add safe.directory /app
git config --global --add safe.directory /runtime_git

if [ -f /app/.git ] && grep -q "gitdir:" /app/.git; then
    echo "[Entrypoint] Setting up git worktree for submodule..."
    cp -a /runtime_git /tmp/runtime_git
    sed -i "s|worktree = .*|worktree = /app|" /tmp/runtime_git/config
    echo "gitdir: /tmp/runtime_git" > /app/.git
fi

if [ -f "/runtime_scripts/setup_hooks.sh" ]; then
    cd /app && /bin/bash /runtime_scripts/setup_hooks.sh
fi

echo "Locking down semantic firewall and git hooks..."

chown -R root:root /runtime_scripts
GIT_HOOKS_DIR="/app/.git/hooks"
if [ -f /app/.git ]; then
    GIT_HOOKS_DIR="/tmp/runtime_git/hooks"
fi
chown -R root:root "$GIT_HOOKS_DIR"
chmod -R 755 /runtime_scripts
chmod -R 755 "$GIT_HOOKS_DIR"

echo "Containment established."

echo "Starting Spine..."
python -m spine /spine/spine_config.json &
SPINE_PID=$!

echo "Waiting for Spine socket..."
for i in $(seq 1 30); do
  if [ -S /tmp/spine.sock ]; then
    echo "Spine socket ready."
    break
  fi
  sleep 1
done

if [ ! -S /tmp/spine.sock ]; then
  echo "ERROR: Spine socket not available after 30 seconds"
  exit 1
fi

if git -C /app rev-parse HEAD > /dev/null 2>&1; then
  git -C /app rev-parse HEAD > /spine/last_candidate_commit
  echo "[Entrypoint] Recorded candidate commit"
else
  echo "[Entrypoint] WARNING: /app is not a git repository, skipping candidate commit"
fi

echo "Awaking Talos as $USER_NAME ($USER_ID:$GROUP_ID)..."
exec gosu "$USER_NAME" "$@"