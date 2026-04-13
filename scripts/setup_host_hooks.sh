#!/bin/bash
# Install pre-push hook for the talos_runtime host repository.
# Scans outgoing commits for secrets before pushing to remote.
# Requires: trufflehog (brew install trufflehog or https://github.com/trufflesecurity/trufflehog/releases)

set -e

if [ ! -f /.dockerenv ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel)"
else
    echo "Error: This script should only run on the host, not inside Docker."
    exit 1
fi

if ! command -v trufflehog &>/dev/null; then
    echo "ERROR: trufflehog not found on PATH. Install it first:"
    echo "  brew install trufflehog"
    echo "  or download from https://github.com/trufflesecurity/trufflehog/releases"
    exit 1
fi

HOOK_FILE="$REPO_ROOT/.git/hooks/pre-push"

cat > "$HOOK_FILE" << 'HOOK'
#!/bin/bash
# Pre-push hook: scan outgoing commits for secrets
REPO_ROOT="$(git rev-parse --show-toplevel)"

while read local_ref local_oid remote_ref remote_oid; do
    if [ "$remote_oid" = "0000000000000000000000000000000000000000" ]; then
        RANGE="$local_oid"
    else
        RANGE="$remote_oid..$local_oid"
    fi

    echo "[Pre-push] Scanning outgoing commits for secrets..."
    trufflehog git "$REPO_ROOT" --only-verified --fail --no-update HEAD 2>/dev/null
    RC=$?
    if [ $RC -eq 1 ]; then
        echo "[Pre-push] SECRET DETECTED in outgoing commits! Push aborted."
        exit 1
    fi
done
HOOK

chmod +x "$HOOK_FILE"
echo "Pre-push hook installed successfully at $HOOK_FILE"