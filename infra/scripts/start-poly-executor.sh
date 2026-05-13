#!/usr/bin/env bash
# start-poly-executor.sh
#
# Launches executor-polymarket inside the vpn-polymarket network namespace.
# All Polymarket API traffic is automatically routed through the VPN.
# Redis / Postgres are accessed via the veth bridge (192.168.100.1).
#
# Prerequisites:
#   1. VPN namespace running:   ~/Constructure/infra/polymarket/start-polymarket-env.sh
#   2. veth bridge created:     infra/scripts/setup-veth.sh
#   3. Docker image built:      make build
#   4. .env file present in repo root (arb/.env)
#
# Usage:
#   ./infra/scripts/start-poly-executor.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NS="vpn-polymarket"
IMAGE="arb-executor-polymarket"
CONTAINER="arb-executor-polymarket"
ENV_FILE="$REPO_ROOT/.env"
POSTGRES_DSN_OVERRIDE="${POLY_EXECUTOR_POSTGRES_DSN:-postgresql://arb:${POSTGRES_PASSWORD:-arb}@192.168.100.1:5435/arb}"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# ── Preflight checks ──────────────────────────────────────────────────────────

if ! ip netns list | grep -q "$NS"; then
    echo "ERROR: Network namespace '$NS' not found."
    echo "  Run: $HOME/Constructure/infra/polymarket/start-polymarket-env.sh"
    exit 1
fi

if ! ip link show veth-arb-main &>/dev/null; then
    echo "veth bridge not found — setting it up..."
    bash "$SCRIPT_DIR/setup-veth.sh"
fi

if ! docker image inspect "$IMAGE" &>/dev/null; then
    echo "ERROR: Docker image '$IMAGE' not found."
    echo "  Run: make build   (from $REPO_ROOT)"
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    echo "  Copy .env.example to .env and fill in credentials."
    exit 1
fi

# ── Stop existing container ───────────────────────────────────────────────────

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "Stopping existing container $CONTAINER..."
    docker stop "$CONTAINER" 2>/dev/null || true
    docker rm   "$CONTAINER" 2>/dev/null || true
fi

# ── Build run command ─────────────────────────────────────────────────────────

# Override REDIS_HOST to reach Redis via veth bridge
# (127.0.0.1 refers to vpn namespace loopback, NOT the host)
RUN_CMD=(
    docker run
    --name "$CONTAINER"
    --detach
    --restart unless-stopped
    --network host
    --env-file "$ENV_FILE"
    --env REDIS_HOST=192.168.100.1
    --env POSTGRES_DSN="$POSTGRES_DSN_OVERRIDE"
    --log-driver json-file
    --log-opt max-size=50m
    --log-opt max-file=5
    "$IMAGE"
)

echo "Launching $CONTAINER inside namespace $NS..."
if $DRY_RUN; then
    echo "[DRY RUN] Would execute:"
    echo "  ip netns exec $NS ${RUN_CMD[*]}"
    exit 0
fi

"${SUDO[@]}" ip netns exec "$NS" "${RUN_CMD[@]}"

echo ""
echo "$CONTAINER started in namespace $NS"
echo "  Logs: docker logs -f $CONTAINER"
echo "  Stop: docker stop $CONTAINER"
