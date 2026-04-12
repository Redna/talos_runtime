# 6. Build the Spine (Go sidecar)
FROM golang:1.22-alpine AS spine-builder
WORKDIR /build
COPY talos_runtime/spine/go.mod talos_runtime/spine/go.sum ./
RUN go mod download
COPY talos_runtime/spine/*.go ./
RUN CGO_ENABLED=0 go build -o spine .

# Use a lightweight Python base image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TALOS_DRIVE_ROOT=/drive \
    TALOS_REPO_DIR=/app \
    # Store venv OUTSIDE of /app so it isn't overwritten by the docker-compose bind mount
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Enable BuildKit mount caching for apt and uv
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    git curl gosu sudo wget gnupg && \
    # Install GitHub CLI
    mkdir -p -m 755 /etc/apt/keyrings && \
    wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install gh -y

# Install uv for fast package management
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh

# 1. Cache dependencies (Layer is cached unless pyproject.toml/uv.lock changes)
COPY talos/pyproject.toml talos/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --no-progress

# 2. Copy the actual code
COPY talos/ .

# 3. Final sync to install the local project (fast as deps are cached)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-progress

# 4. Add runtime scripts (Hardened)
COPY talos_runtime/scripts/ /runtime_scripts/
RUN chown -R root:root /runtime_scripts && chmod -R 555 /runtime_scripts && \
    chmod +x /runtime_scripts/setup_hooks.sh

# 5. Add the entrypoint script
COPY talos_runtime/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 7. Copy the Spine binary from the Go builder
COPY --from=spine-builder /build/spine /usr/local/bin/spine
RUN chmod +x /usr/local/bin/spine

# 8. Copy Spine configuration
COPY talos_runtime/spine/spine_config.json /spine/spine_config.json

# The entrypoint launches the seed agent directly
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# Use absolute path to the persistent venv python
CMD ["/venv/bin/python", "seed_agent.py"]