"""Shared data models for the arbitrage system."""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Exchange(str, Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class Side(str, Enum):
    YES = "yes"
    NO = "no"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PriceLevel(BaseModel):
    price: float  # normalized [0.0, 1.0]
    size: float   # contracts


class Orderbook(BaseModel):
    market_id: str
    exchange: Exchange
    bids: List[PriceLevel]  # sorted descending by price
    asks: List[PriceLevel]  # sorted ascending by price
    ts: float = Field(default_factory=time.time)

    def best_ask(self) -> Optional[PriceLevel]:
        return self.asks[0] if self.asks else None

    def best_bid(self) -> Optional[PriceLevel]:
        return self.bids[0] if self.bids else None

    def available_size_at_or_below(self, price: float) -> float:
        """Sum of ask sizes at or below the given price."""
        return sum(l.size for l in self.asks if l.price <= price)


class MarketMeta(BaseModel):
    market_id: str
    exchange: Exchange
    title: str
    description: str = ""
    category: str = ""
    end_date: Optional[str] = None
    status: str = "open"
    market_type: Optional[str] = None
    # Kalshi-specific
    event_ticker: Optional[str] = None
    yes_sub_title: Optional[str] = None
    no_sub_title: Optional[str] = None
    rules_primary: Optional[str] = None
    rules_secondary: Optional[str] = None
    # Polymarket-specific
    market_slug: Optional[str] = None
    question_id: Optional[str] = None
    outcome_count: Optional[int] = None
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    condition_id: Optional[str] = None


class MatchedPair(BaseModel):
    pair_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kalshi_market_id: str
    polymarket_market_id: str
    similarity_score: float
    created_at: float = Field(default_factory=time.time)


class TradeSignal(BaseModel):
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    pair_id: str
    kalshi_market_id: str
    polymarket_market_id: str
    # What to buy on each exchange (always BUY — we buy either YES or NO)
    kalshi_side: Side
    poly_side: Side
    size: float           # contracts
    kalshi_price: float   # limit price
    poly_price: float     # limit price
    expected_edge: float  # gross profit fraction before fees
    ts: float = Field(default_factory=time.time)


class OrderUpdate(BaseModel):
    update_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str
    executor: str          # "kalshi" or "polymarket"
    exchange_order_id: str
    status: OrderStatus
    filled_size: float = 0.0
    avg_price: float = 0.0
    ts: float = Field(default_factory=time.time)
