#!/usr/bin/env bash
# setup-veth.sh
#
# Creates a veth pair that bridges the main network namespace and the
# vpn-polymarket namespace so that executor-polymarket (running in VPN ns)
# can reach Redis / Postgres / Qdrant running on the host.
#
# Bridge addressing:
#   main ns  → veth-main  → 192.168.100.1/30
#   vpn  ns  → veth-vpn   → 192.168.100.2/30
#
# Set REDIS_HOST=192.168.100.1 in executor-polymarket's environment.

set -euo pipefail

NS="vpn-polymarket"
VETH_MAIN="veth-arb-main"
VETH_VPN="veth-arb-vpn"
MAIN_IP="192.168.100.1/30"
VPN_IP="192.168.100.2/30"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

# Verify namespace exists
if ! ip netns list | grep -q "^${NS}"; then
    echo "ERROR: Network namespace '$NS' does not exist"
    echo "  Run: ~/Constructure/infra/polymarket/start-polymarket-env.sh first"
    exit 1
fi

if ip link show "$VETH_MAIN" &>/dev/null; then
    echo "veth pair already exists — skipping"
    exit 0
fi

echo "Creating veth pair $VETH_MAIN <-> $VETH_VPN"

# Create the pair in the main namespace
"${SUDO[@]}" ip link add "$VETH_MAIN" type veth peer name "$VETH_VPN"

# Move vpn end into the namespace
"${SUDO[@]}" ip link set "$VETH_VPN" netns "$NS"

# Configure main side
"${SUDO[@]}" ip addr add "$MAIN_IP" dev "$VETH_MAIN"
"${SUDO[@]}" ip link set "$VETH_MAIN" up

# Configure vpn side
"${SUDO[@]}" ip netns exec "$NS" ip addr add "$VPN_IP" dev "$VETH_VPN"
"${SUDO[@]}" ip netns exec "$NS" ip link set "$VETH_VPN" up

echo "veth bridge ready"
echo "  main namespace: $VETH_MAIN @ $MAIN_IP"
echo "  vpn  namespace: $VETH_VPN  @ $VPN_IP"
echo ""
echo "Set REDIS_HOST=192.168.100.1 for executor-polymarket"
