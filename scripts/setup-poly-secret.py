#!/usr/bin/env python3
"""Derive Polymarket API credentials from wallet private key and patch K8s secret.

Usage:
    python3 scripts/setup-poly-secret.py 0xYOUR_PRIVATE_KEY
    python3 scripts/setup-poly-secret.py           # prompts for key (hidden input)

This script:
1. Derives API key/secret/passphrase from the private key via Polymarket CLOB
2. Patches the arb-secrets K8s secret with all 4 values
3. Restarts executor-polymarket to pick up the new credentials
"""

import base64
import json
import subprocess
import sys
import getpass


def main():
    # Get private key
    if len(sys.argv) > 1:
        pk = sys.argv[1]
    else:
        pk = getpass.getpass("Polymarket wallet private key (0x...): ")

    pk = pk.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    # Derive API credentials
    print("Deriving API credentials from private key...")
    from py_clob_client.client import ClobClient

    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=pk,
    )
    try:
        creds = client.derive_api_key()
    except Exception:
        print("  derive failed, creating new API key...")
        creds = client.create_api_key()

    # Normalize — derive returns dict, create returns ApiCreds object
    if isinstance(creds, dict):
        api_key = creds["apiKey"]
        secret = creds["secret"]
        passphrase = creds["passphrase"]
    else:
        api_key = creds.api_key
        secret = creds.api_secret
        passphrase = creds.api_passphrase

    print(f"  API Key:      {api_key[:12]}...")
    print(f"  Secret:       {secret[:12]}...")
    print(f"  Passphrase:   {passphrase[:12]}...")

    # Build kubectl patch
    def b64(s):
        return base64.b64encode(s.encode()).decode()

    patch = json.dumps({
        "data": {
            "POLY_PRIVATE_KEY": b64(pk),
            "POLY_API_KEY": b64(api_key),
            "POLY_API_SECRET": b64(secret),
            "POLY_API_PASSPHRASE": b64(passphrase),
        }
    })

    print("\nPatching arb-secrets...")
    result = subprocess.run(
        ["kubectl", "patch", "secret", "arb-secrets", "-n", "constructure-arb", "-p", patch],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        sys.exit(1)
    print(f"  {result.stdout.strip()}")

    # Restart executor-polymarket
    print("Restarting executor-polymarket...")
    result = subprocess.run(
        ["kubectl", "rollout", "restart", "deployment/executor-polymarket", "-n", "constructure-arb"],
        capture_output=True, text=True,
    )
    print(f"  {result.stdout.strip()}")
    print("\nDone. Run 'kubectl logs -n constructure-arb -l app=executor-polymarket --tail=10' to verify.")


if __name__ == "__main__":
    main()
