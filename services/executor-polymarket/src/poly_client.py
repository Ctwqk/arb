"""Polymarket CLOB API client with EIP-712 order signing.

Relies on py-clob-client (Polymarket's official Python library) for
order construction and signing, and aiohttp for async HTTP calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "https://clob.polymarket.com"


class PolymarketClient:
    """Async wrapper around Polymarket CLOB REST API.

    Signing is delegated to py_clob_client which handles EIP-712
    TypedData construction and eth_account signing internally.
    """

    def __init__(
        self,
        private_key: str,          # 0x-prefixed hex private key
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        chain_id: int = 137,       # Polygon mainnet
        host: str = _DEFAULT_HOST,
    ) -> None:
        self._host = host
        self._chain_id = chain_id
        self._session: Optional[aiohttp.ClientSession] = None

        # Build py-clob-client for signing
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        self._clob = ClobClient(
            host=host,
            chain_id=chain_id,
            key=private_key,
            creds=ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            ),
            signature_type=0,  # EOA
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    # ── Order lifecycle ───────────────────────────────────────────────────────

    def place_order_sync(
        self,
        token_id: str,   # YES or NO token id
        side: str,       # "BUY" or "SELL"
        price: float,    # [0.01, 0.99]
        size: float,     # USDC amount (e.g. 50.0 = $50 at that price)
        order_type: str = "GTC",
    ) -> dict:
        """Place a signed limit order via py-clob-client (synchronous).

        Returns the API response dict containing order_id.
        """
        from py_clob_client.clob_types import OrderArgs, OrderType, Side as ClSide

        side_enum = ClSide.BUY if side.upper() == "BUY" else ClSide.SELL
        order_type_enum = OrderType.GTC

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side_enum,
            expiration=0,  # no expiration
        )
        signed_order = self._clob.create_order(order_args)
        resp = self._clob.post_order(signed_order, order_type_enum)
        logger.info(
            "Polymarket order placed: token=%s side=%s price=%.3f size=%.2f | resp=%s",
            token_id[:16],
            side,
            price,
            size,
            resp,
        )
        return resp

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict:
        """Async wrapper — runs synchronous signing in executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.place_order_sync(token_id, side, price, size),
        )

    async def get_order(self, order_id: str) -> dict:
        session = await self._get_session()
        async with session.get(f"{self._host}/order/{order_id}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def cancel_order(self, order_id: str) -> dict:
        session = await self._get_session()
        async with session.delete(
            f"{self._host}/order/{order_id}"
        ) as resp:
            resp.raise_for_status()
            result = await resp.json()
            logger.info("Polymarket order cancelled: %s", order_id)
            return result

    async def get_orderbook(self, token_id: str) -> dict:
        session = await self._get_session()
        async with session.get(
            f"{self._host}/book", params={"token_id": token_id}
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_balance(self) -> float:
        """Return USDC balance for the configured wallet."""
        session = await self._get_session()
        async with session.get(f"{self._host}/balance") as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get("balance", 0))
            return 0.0

    async def wait_for_fill(
        self,
        order_id: str,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            order = await self.get_order(order_id)
            status = order.get("status", "").lower()
            if status in ("matched", "filled", "cancelled"):
                return order
            await asyncio.sleep(poll_interval)
        try:
            await self.cancel_order(order_id)
        except Exception as exc:
            logger.warning("Failed to cancel timed-out Polymarket order %s: %s", order_id, exc)
        return await self.get_order(order_id)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
