#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

NS="${POLYMARKET_NS:-vpn-polymarket}"
CONFIG="${SINGBOX_CONFIG:-$REPO_ROOT/singbox.json}"

if ! command -v ip >/dev/null 2>&1; then
    echo "missing required command: ip" >&2
    exit 1
fi

if ! command -v sing-box >/dev/null 2>&1; then
    echo "missing required command: sing-box" >&2
    exit 1
fi

if [[ -f "$CONFIG" ]]; then
    :
else
    echo "missing sing-box config: $CONFIG" >&2
    exit 1
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

# 1 如果 namespace 不存在就创建
if ! ip netns list | grep -Eq "^${NS}( |$)"; then
    echo "Creating namespace..."
    "${SUDO[@]}" ip netns add "$NS"
    "${SUDO[@]}" ip netns exec "$NS" ip link set lo up
fi

# 2 如果 sing-box 已经在跑就不再启动
if pgrep -f "sing-box run -c $CONFIG" > /dev/null; then
    echo "sing-box already running"
else
    echo "Starting sing-box..."
    "${SUDO[@]}" ip netns exec "$NS" sing-box run -c "$CONFIG" &
fi

echo "Environment ready."
