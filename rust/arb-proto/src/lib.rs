use serde::{Deserialize, Serialize};

pub const PAIRS_SET: &str = "pairs:all";
pub const POSITIONS_SET: &str = "positions:all";
pub const STREAM_MATCHED_MARKETS: &str = "stream:matched_markets";

pub fn orderbook_key(prefix: &str, exchange: &str, market_id: &str) -> String {
    format!("{prefix}orderbook:{exchange}:{market_id}")
}

pub fn market_key(prefix: &str, exchange: &str, market_id: &str) -> String {
    format!("{prefix}market:{exchange}:{market_id}")
}

pub fn price_key(prefix: &str, exchange: &str, market_id: &str) -> String {
    format!("{prefix}price:{exchange}:{market_id}")
}

pub fn markets_set_key(prefix: &str, exchange: &str) -> String {
    format!("{prefix}markets:{exchange}")
}

pub fn pair_key(pair_id: &str) -> String {
    format!("pair:{pair_id}")
}

pub fn position_key(exchange: &str, market_id: &str) -> String {
    format!("position:{exchange}:{market_id}")
}

pub fn stream_market_updates(prefix: &str) -> String {
    format!("{prefix}stream:market_updates")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderbookPayload {
    pub market_id: String,
    pub exchange: String,
    pub bids: Vec<[f64; 2]>,
    pub asks: Vec<[f64; 2]>,
    pub ts: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketMetaPayload {
    pub event: String,
    pub exchange: String,
    pub market_id: String,
    pub title: String,
    pub description: String,
    pub category: String,
    pub end_date: String,
    pub status: String,
    pub ts: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub market_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event_ticker: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub market_slug: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub question_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outcome_count: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub yes_sub_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub no_sub_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rules_primary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rules_secondary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub yes_token_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub no_token_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub condition_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MatchedPairPayload {
    pub pair_id: String,
    pub kalshi_market_id: String,
    pub polymarket_market_id: String,
    pub similarity_score: f64,
    #[serde(default)]
    pub created_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Yes,
    No,
}

impl Side {
    pub fn as_str(&self) -> &'static str {
        match self {
            Side::Yes => "yes",
            Side::No => "no",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeSignal {
    pub signal_id: String,
    pub pair_id: String,
    pub kalshi_market_id: String,
    pub polymarket_market_id: String,
    pub kalshi_side: Side,
    pub poly_side: Side,
    pub size: f64,
    pub kalshi_price: f64,
    pub poly_price: f64,
    pub expected_edge: f64,
    pub ts: f64,
}

impl TradeSignal {
    pub fn new(
        pair_id: String,
        kalshi_market_id: String,
        polymarket_market_id: String,
        kalshi_side: Side,
        poly_side: Side,
        size: f64,
        kalshi_price: f64,
        poly_price: f64,
        expected_edge: f64,
        ts: f64,
    ) -> Self {
        Self {
            signal_id: uuid::Uuid::new_v4().to_string(),
            pair_id,
            kalshi_market_id,
            polymarket_market_id,
            kalshi_side,
            poly_side,
            size,
            kalshi_price,
            poly_price,
            expected_edge,
            ts,
        }
    }
}
