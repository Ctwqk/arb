"""Redis key schemas for the arbitrage system.

Naming convention:
  orderbook:{exchange}:{market_id}   -> JSON Orderbook (Hash)
  market:{exchange}:{market_id}      -> JSON MarketMeta (Hash)
  price:{exchange}:{market_id}       -> float string (String)
  markets:{exchange}                 -> set of all known market_ids
  pair:{pair_id}                     -> JSON MatchedPair (String)
  pairs:all                          -> set of all pair_ids
  position:{exchange}:{market_id}    -> signed float string (String)
  positions:all                      -> set of "exchange:market_id" keys

Redis Streams:
  stream:market_updates   collector -> strategy/resolver
  stream:trade_signals    strategy  -> executors
  stream:order_updates    executors -> logging/monitoring

Consumer groups:
  cg:strategy             reads stream:market_updates
  cg:resolver             reads stream:market_updates
  cg:executor-kalshi      reads stream:trade_signals
  cg:executor-polymarket  reads stream:trade_signals
"""


def orderbook_key(exchange: str, market_id: str) -> str:
    return f"orderbook:{exchange}:{market_id}"


def market_key(exchange: str, market_id: str) -> str:
    return f"market:{exchange}:{market_id}"


def price_key(exchange: str, market_id: str) -> str:
    return f"price:{exchange}:{market_id}"


def markets_set_key(exchange: str) -> str:
    return f"markets:{exchange}"


def pair_key(pair_id: str) -> str:
    return f"pair:{pair_id}"


def position_key(exchange: str, market_id: str) -> str:
    return f"position:{exchange}:{market_id}"


# Constants
PAIRS_SET = "pairs:all"
POSITIONS_SET = "positions:all"

STREAM_MARKET_UPDATES = "stream:market_updates"
STREAM_TRADE_SIGNALS = "stream:trade_signals"
STREAM_ORDER_UPDATES = "stream:order_updates"

CG_STRATEGY = "cg:strategy"
CG_RESOLVER = "cg:resolver"
CG_EXECUTOR_KALSHI = "cg:executor-kalshi"
CG_EXECUTOR_POLY = "cg:executor-polymarket"
