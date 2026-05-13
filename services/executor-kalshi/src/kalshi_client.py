"""Kalshi REST API client for order execution."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Optional

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

# Kalshi REST base (v2)
_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    """Async Kalshi REST API client with RSA auth."""

    def __init__(self, api_key_id: str, private_key_pem: str, api_base: str = _API_BASE) -> None:
        self._api_key_id = api_key_id
        self._api_base = api_base
        self._pk = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=self._api_base,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        msg = ts + method.upper() + path + body
        sig = self._pk.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    async def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        session = await self._get_session()
        body_str = json.dumps(body) if body else ""
        headers = self._auth_headers(method, path, body_str)
        url = path
        async with session.request(
            method, url, headers=headers, data=body_str or None
        ) as resp:
            text = await resp.text()
            if not resp.ok:
                logger.error("Kalshi API error %d %s: %s", resp.status, path, text)
                resp.raise_for_status()
            return json.loads(text) if text else {}

    # ── Order management ──────────────────────────────────────────────────────

    async def place_order(
        self,
        ticker: str,
        side: str,       # "yes" or "no"
        action: str,     # "buy" or "sell"
        count: int,
        price_cents: int,  # 1–99
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Place a limit order. Returns the full order object."""
        body = {
            "ticker": ticker,
            "action": action,
            "type": "limit",
            "side": side,
            "count": count,
            "yes_price" if side == "yes" else "no_price": price_cents,
            "client_order_id": client_order_id or str(uuid.uuid4()),
            "time_in_force": "gtc",
        }
        result = await self._request("POST", "/portfolio/orders", body)
        order = result.get("order", result)
        logger.info(
            "Kalshi order placed: id=%s ticker=%s side=%s count=%d price=%dc",
            order.get("id"),
            ticker,
            side,
            count,
            price_cents,
        )
        return order

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/portfolio/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> dict:
        result = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        logger.info("Kalshi order cancelled: %s", order_id)
        return result

    async def get_balance(self) -> float:
        """Return available USD balance in dollars."""
        data = await self._request("GET", "/portfolio/balance")
        # Returns cents
        return data.get("balance", 0) / 100.0

    async def get_positions(self) -> list:
        data = await self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    async def wait_for_fill(
        self,
        order_id: str,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> dict:
        """Poll until order is filled/cancelled or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            order = await self.get_order(order_id)
            status = order.get("status", "")
            if status in ("filled", "cancelled", "closed"):
                return order
            await asyncio.sleep(poll_interval)
        # Timed out — cancel and return latest state
        try:
            await self.cancel_order(order_id)
        except Exception as exc:
            logger.warning("Failed to cancel timed-out order %s: %s", order_id, exc)
        return await self.get_order(order_id)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
