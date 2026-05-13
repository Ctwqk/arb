#!/usr/bin/env bash
# start.sh — Full system startup
#
# 1. Ensures vpn-polymarket namespace is running
# 2. Sets up veth bridge for namespace connectivity
# 3. Starts infrastructure + app services via docker-compose
# 4. Launches executor-polymarket inside VPN namespace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VPN_SCRIPT="$HOME/Constructure/infra/polymarket/start-polymarket-env.sh"

echo "═══════════════════════════════════════════"
echo "  Arbitrage System — Full Startup"
echo "═══════════════════════════════════════════"

# ── Step 1: VPN namespace ─────────────────────────────────────────────────────
echo ""
echo "[1/4] Starting VPN namespace..."
if [[ -f "$VPN_SCRIPT" ]]; then
    bash "$VPN_SCRIPT"
else
    echo "  WARNING: $VPN_SCRIPT not found — skipping VPN setup"
fi

# ── Step 2: veth bridge ────────────────────────────────────────────────────────
echo ""
echo "[2/4] Setting up veth bridge..."
bash "$SCRIPT_DIR/setup-veth.sh"

# ── Step 3: docker-compose services ───────────────────────────────────────────
echo ""
echo "[3/4] Starting infrastructure and app services..."
cd "$REPO_ROOT"
docker compose up -d --build

echo "  Waiting for services to be healthy..."
sleep 5

# ── Step 4: executor-polymarket in VPN namespace ───────────────────────────────
echo ""
echo "[4/4] Launching executor-polymarket in VPN namespace..."
bash "$SCRIPT_DIR/start-poly-executor.sh"

echo ""
echo "═══════════════════════════════════════════"
echo "  All systems running"
echo ""
echo "  Logs:"
echo "    docker compose logs -f collector"
echo "    docker compose logs -f strategy"
echo "    docker logs -f arb-executor-polymarket"
echo ""
echo "  Stop:"
echo "    docker compose down"
echo "    docker stop arb-executor-polymarket"
echo "═══════════════════════════════════════════"
