#!/bin/bash
set -e

USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

# Create group and user if they do not exist
if ! getent group "$GROUP_ID" >/dev/null; then groupadd -g "$GROUP_ID" talos; fi
if ! getent passwd "$USER_ID" >/dev/null; then useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/bash talos; fi

USER_NAME=$(getent passwd "$USER_ID" | cut -d: -f1)

# Ensure the agent has write access to the workspace volume
chown -R "$USER_NAME":"$GROUP_ID" /app

# Basic Git Config for the user
sudo -u "$USER_NAME" -H git config --global user.name "Talos"
sudo -u "$USER_NAME" -H git config --global user.email "talos@agent.local"
sudo -u "$USER_NAME" -H git config --global --add safe.directory /app

# Ensure Git hooks are active
if [ -f "/runtime_scripts/setup_hooks.sh" ]; then
    cd /app && /bin/bash /runtime_scripts/setup_hooks.sh
fi

echo "Locking down semantic firewall and git hooks..."

# Transfer ownership of critical infrastructure to root
# The agent will run as PUID (e.g., 1000) and will have read/execute access, but ZERO write access.
chown -R root:root /runtime_scripts
chown -R root:root /app/.git/hooks

# Enforce strict permissions (755: Owner can rwx, others can only r-x)
chmod -R 755 /runtime_scripts
chmod -R 755 /app/.git/hooks

echo "Containment established."

# Start the Spine in the background
echo "Starting Spine..."
/usr/local/bin/spine /spine/spine_config.json &
SPINE_PID=$!

# Wait for Spine socket to be available
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

echo "Awaking Talos as $USER_NAME ($USER_ID:$GROUP_ID)..."
exec gosu "$USER_NAME" "$@"