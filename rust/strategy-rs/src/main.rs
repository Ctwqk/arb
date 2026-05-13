use anyhow::Result;
use arb_proto::{
    orderbook_key, pair_key, position_key, stream_market_updates, MatchedPairPayload, OrderbookPayload,
    Side, TradeSignal, PAIRS_SET, POSITIONS_SET, STREAM_MATCHED_MARKETS,
};
use log::{error, info, warn};
use redis::aio::ConnectionManager;
use redis::streams::{StreamReadOptions, StreamReadReply};
use redis::{cmd, AsyncCommands, Value};
use std::collections::{HashMap, HashSet};
use std::env;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::RwLock;

const DEFAULT_SIGNAL_COOLDOWN_SECS: u64 = 30;

#[derive(Clone)]
struct Config {
    redis_host: String,
    redis_port: u16,
    redis_password: String,
    min_edge: f64,
    fee_estimate: f64,
    max_trade_size: f64,
    max_position_per_market: f64,
    min_liquidity: f64,
    max_portfolio_notional: f64,
    market_data_prefix: String,
    pair_data_prefix: String,
    matched_market_stream: String,
    trade_signal_stream: String,
    cooldown_prefix: String,
    validated_prefix: String,
    require_validated_pairs: bool,
    consumer_group: String,
    pair_consumer_group: String,
    consumer_name: String,
    block_ms: usize,
    batch: usize,
}

impl Config {
    fn from_env() -> Self {
        let market_data_prefix = env::var("ARB_MARKET_DATA_PREFIX").unwrap_or_default();
        let pair_data_prefix = env::var("ARB_PAIR_DATA_PREFIX").unwrap_or_default();
        let matched_market_stream = env::var("ARB_MATCHED_MARKETS_STREAM")
            .unwrap_or_else(|_| format!("{pair_data_prefix}{STREAM_MATCHED_MARKETS}"));
        let validated_prefix =
            env::var("ARB_VALIDATED_PREFIX").unwrap_or_else(|_| pair_data_prefix.clone());
        Self {
            redis_host: env::var("REDIS_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
            redis_port: env::var("REDIS_PORT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(6379),
            redis_password: env::var("REDIS_PASSWORD").unwrap_or_default(),
            min_edge: env::var("MIN_EDGE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.03),
            fee_estimate: env::var("FEE_ESTIMATE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.02),
            max_trade_size: env::var("MAX_TRADE_SIZE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(100.0),
            max_position_per_market: env::var("MAX_POSITION_PER_MARKET")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(500.0),
            min_liquidity: env::var("MIN_LIQUIDITY")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(10.0),
            max_portfolio_notional: env::var("MAX_PORTFOLIO_NOTIONAL")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(10_000.0),
            pair_data_prefix,
            matched_market_stream,
            trade_signal_stream: env::var("ARB_TRADE_SIGNAL_STREAM")
                .unwrap_or_else(|_| "stream:trade_signals".to_string()),
            cooldown_prefix: env::var("ARB_COOLDOWN_PREFIX")
                .unwrap_or_else(|_| "cooldown:".to_string()),
            validated_prefix,
            require_validated_pairs: env::var("STRATEGY_REQUIRE_VALIDATED_PAIRS")
                .ok()
                .map(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on"))
                .unwrap_or(false),
            consumer_group: env::var("STRATEGY_CONSUMER_GROUP")
                .unwrap_or_else(|_| "cg:strategy".to_string()),
            pair_consumer_group: env::var("STRATEGY_PAIR_CONSUMER_GROUP")
                .unwrap_or_else(|_| "cg:strategy-pairs".to_string()),
            consumer_name: env::var("STRATEGY_CONSUMER_NAME")
                .unwrap_or_else(|_| "strategy-rs-0".to_string()),
            block_ms: env::var("STRATEGY_BLOCK_MS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(100),
            batch: env::var("STRATEGY_BATCH")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(50),
            market_data_prefix,
        }
    }
}

fn prefixed_pair_key(prefix: &str, pair_id: &str) -> String {
    format!("{prefix}{}", pair_key(pair_id))
}

fn prefixed_pairs_set(prefix: &str) -> String {
    format!("{prefix}{PAIRS_SET}")
}

fn validated_pair_key(prefix: &str, pair_id: &str) -> String {
    format!("{prefix}pair_validated:{pair_id}")
}

fn now_ts() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_else(|_| Duration::from_secs(0))
        .as_secs_f64()
}

#[derive(Debug, Clone)]
struct ArbOpportunity {
    kalshi_side: Side,
    poly_side: Side,
    kalshi_price: f64,
    poly_price: f64,
    gross_edge: f64,
    available_size: f64,
}

fn best_level(levels: &[[f64; 2]]) -> Option<(f64, f64)> {
    levels.first().map(|level| (level[0], level[1]))
}

fn detect_opportunity(
    kalshi_ob: &OrderbookPayload,
    poly_ob: &OrderbookPayload,
    config: &Config,
) -> Option<ArbOpportunity> {
    let (k_yes_price, k_yes_size) = best_level(&kalshi_ob.asks)?;
    let (p_yes_price, p_yes_size) = best_level(&poly_ob.asks)?;

    let k_best_bid = best_level(&kalshi_ob.bids);
    let p_best_bid = best_level(&poly_ob.bids);

    let mut best_opp: Option<ArbOpportunity> = None;

    if let Some((poly_yes_bid, poly_yes_bid_size)) = p_best_bid {
        let p_no_price = (1.0 - poly_yes_bid).round_to(6);
        let cost = k_yes_price + p_no_price;
        let edge = 1.0 - cost;
        let size = k_yes_size.min(poly_yes_bid_size).min(config.max_trade_size);
        if edge > config.min_edge + config.fee_estimate && size >= config.min_liquidity {
            best_opp = Some(ArbOpportunity {
                kalshi_side: Side::Yes,
                poly_side: Side::No,
                kalshi_price: k_yes_price,
                poly_price: p_no_price,
                gross_edge: edge,
                available_size: size,
            });
        }
    }

    if let Some((kalshi_yes_bid, kalshi_yes_bid_size)) = k_best_bid {
        let k_no_price = (1.0 - kalshi_yes_bid).round_to(6);
        let cost = k_no_price + p_yes_price;
        let edge = 1.0 - cost;
        let size = kalshi_yes_bid_size.min(p_yes_size).min(config.max_trade_size);
        if edge > config.min_edge + config.fee_estimate
            && size >= config.min_liquidity
            && best_opp
                .as_ref()
                .map(|current| edge > current.gross_edge)
                .unwrap_or(true)
        {
            best_opp = Some(ArbOpportunity {
                kalshi_side: Side::No,
                poly_side: Side::Yes,
                kalshi_price: k_no_price,
                poly_price: p_yes_price,
                gross_edge: edge,
                available_size: size,
            });
        }
    }

    best_opp
}

trait RoundTo {
    fn round_to(self, precision: i32) -> f64;
}

impl RoundTo for f64 {
    fn round_to(self, precision: i32) -> f64 {
        let factor = 10f64.powi(precision);
        (self * factor).round() / factor
    }
}

#[derive(Clone)]
struct RiskManager {
    redis: ConnectionManager,
    config: Config,
}

impl RiskManager {
    async fn check(
        &self,
        pair_id: &str,
        kalshi_market_id: &str,
        polymarket_market_id: &str,
        size: f64,
        kalshi_price: f64,
        poly_price: f64,
    ) -> Result<bool> {
        let mut redis = self.redis.clone();
        let cooldown_key = format!("{}{}", self.config.cooldown_prefix, pair_id);
        let cooldown_exists: bool = redis.exists(&cooldown_key).await?;
        if cooldown_exists {
            return Ok(false);
        }

        let k_pos: f64 = redis
            .get::<_, Option<String>>(position_key("kalshi", kalshi_market_id))
            .await?
            .and_then(|value| value.parse().ok())
            .unwrap_or(0.0);
        let p_pos: f64 = redis
            .get::<_, Option<String>>(position_key("polymarket", polymarket_market_id))
            .await?
            .and_then(|value| value.parse().ok())
            .unwrap_or(0.0);

        if k_pos.abs() + size > self.config.max_position_per_market {
            return Ok(false);
        }
        if p_pos.abs() + size > self.config.max_position_per_market {
            return Ok(false);
        }

        let position_keys: Vec<String> = redis.smembers(POSITIONS_SET).await.unwrap_or_default();
        let notional = if position_keys.is_empty() {
            0.0
        } else {
            let values: Vec<Option<String>> = redis.get(position_keys).await.unwrap_or_default();
            values
                .into_iter()
                .flatten()
                .filter_map(|value| value.parse::<f64>().ok())
                .map(|value| value.abs())
                .sum::<f64>()
        };
        let trade_notional = size * (kalshi_price + poly_price);
        Ok(notional + trade_notional <= self.config.max_portfolio_notional)
    }

    async fn record_signal(&self, pair_id: &str) -> Result<()> {
        let mut redis = self.redis.clone();
        let cooldown_key = format!("{}{}", self.config.cooldown_prefix, pair_id);
        redis
            .set_ex::<_, _, ()>(cooldown_key, "1", DEFAULT_SIGNAL_COOLDOWN_SECS)
            .await?;
        Ok(())
    }
}

fn stream_field_to_string(value: &Value) -> String {
    match value {
        Value::BulkString(bytes) => String::from_utf8_lossy(bytes).to_string(),
        Value::SimpleString(value) => value.clone(),
        Value::Int(value) => value.to_string(),
        _ => String::new(),
    }
}

async fn ensure_consumer_group(redis: &mut ConnectionManager, stream: &str, group: &str, id: &str) -> Result<()> {
    let result: redis::RedisResult<String> = cmd("XGROUP")
        .arg("CREATE")
        .arg(stream)
        .arg(group)
        .arg(id)
        .arg("MKSTREAM")
        .query_async(redis)
        .await;
    match result {
        Ok(_) => Ok(()),
        Err(err) if err.to_string().contains("BUSYGROUP") => Ok(()),
        Err(err) => Err(err.into()),
    }
}

async fn load_all_pairs(
    redis: &mut ConnectionManager,
    pair_data_prefix: &str,
) -> Result<HashMap<String, HashSet<String>>> {
    let pair_ids: Vec<String> = redis
        .smembers(prefixed_pairs_set(pair_data_prefix))
        .await
        .unwrap_or_default();
    let mut index: HashMap<String, HashSet<String>> = HashMap::new();
    for pair_id in pair_ids {
        if let Some(raw) = redis
            .get::<_, Option<String>>(prefixed_pair_key(pair_data_prefix, &pair_id))
            .await?
        {
            let pair: MatchedPairPayload = serde_json::from_str(&raw)?;
            index
                .entry(format!("kalshi:{}", pair.kalshi_market_id))
                .or_default()
                .insert(pair.pair_id.clone());
            index
                .entry(format!("polymarket:{}", pair.polymarket_market_id))
                .or_default()
                .insert(pair.pair_id);
        }
    }
    Ok(index)
}

async fn evaluate_pair(
    redis: &mut ConnectionManager,
    risk: &RiskManager,
    config: &Config,
    pair_id: &str,
) -> Result<Option<TradeSignal>> {
    if config.require_validated_pairs {
        let is_validated: bool = redis
            .exists(validated_pair_key(&config.validated_prefix, pair_id))
            .await?;
        if !is_validated {
            return Ok(None);
        }
    }
    let raw_pair = match redis
        .get::<_, Option<String>>(prefixed_pair_key(&config.pair_data_prefix, pair_id))
        .await?
    {
        Some(value) => value,
        None => return Ok(None),
    };
    let pair: MatchedPairPayload = serde_json::from_str(&raw_pair)?;
    let k_raw = match redis
        .get::<_, Option<String>>(orderbook_key(&config.market_data_prefix, "kalshi", &pair.kalshi_market_id))
        .await?
    {
        Some(value) => value,
        None => return Ok(None),
    };
    let p_raw = match redis
        .get::<_, Option<String>>(orderbook_key(
            &config.market_data_prefix,
            "polymarket",
            &pair.polymarket_market_id,
        ))
        .await?
    {
        Some(value) => value,
        None => return Ok(None),
    };
    let kalshi_ob: OrderbookPayload = serde_json::from_str(&k_raw)?;
    let poly_ob: OrderbookPayload = serde_json::from_str(&p_raw)?;
    let Some(opp) = detect_opportunity(&kalshi_ob, &poly_ob, config) else {
        return Ok(None);
    };
    if !risk
        .check(
            &pair.pair_id,
            &pair.kalshi_market_id,
            &pair.polymarket_market_id,
            opp.available_size,
            opp.kalshi_price,
            opp.poly_price,
        )
        .await?
    {
        return Ok(None);
    }
    risk.record_signal(&pair.pair_id).await?;
    Ok(Some(TradeSignal::new(
        pair.pair_id,
        pair.kalshi_market_id,
        pair.polymarket_market_id,
        opp.kalshi_side,
        opp.poly_side,
        opp.available_size,
        opp.kalshi_price,
        opp.poly_price,
        opp.gross_edge - config.fee_estimate,
        now_ts(),
    )))
}

async fn emit_trade_signal(redis: &mut ConnectionManager, stream: &str, signal: &TradeSignal) -> Result<()> {
    let _: String = cmd("XADD")
        .arg(stream)
        .arg("MAXLEN")
        .arg("~")
        .arg(1000)
        .arg("*")
        .arg("signal_id")
        .arg(&signal.signal_id)
        .arg("pair_id")
        .arg(&signal.pair_id)
        .arg("kalshi_market_id")
        .arg(&signal.kalshi_market_id)
        .arg("polymarket_market_id")
        .arg(&signal.polymarket_market_id)
        .arg("kalshi_side")
        .arg(signal.kalshi_side.as_str())
        .arg("poly_side")
        .arg(signal.poly_side.as_str())
        .arg("size")
        .arg(signal.size.to_string())
        .arg("kalshi_price")
        .arg(signal.kalshi_price.to_string())
        .arg("poly_price")
        .arg(signal.poly_price.to_string())
        .arg("expected_edge")
        .arg(signal.expected_edge.to_string())
        .arg("ts")
        .arg(signal.ts.to_string())
        .query_async(redis)
        .await?;
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    let config = Config::from_env();
    let redis_url = if config.redis_password.is_empty() {
        format!("redis://{}:{}/", config.redis_host, config.redis_port)
    } else {
        format!(
            "redis://:{}@{}:{}/",
            config.redis_password, config.redis_host, config.redis_port
        )
    };
    let redis_client = redis::Client::open(redis_url)?;
    let mut redis = ConnectionManager::new(redis_client).await?;

    let market_stream = stream_market_updates(&config.market_data_prefix);
    ensure_consumer_group(&mut redis, &market_stream, &config.consumer_group, "$").await?;
    ensure_consumer_group(
        &mut redis,
        &config.matched_market_stream,
        &config.pair_consumer_group,
        "$",
    )
    .await?;

    let pair_index = Arc::new(RwLock::new(
        load_all_pairs(&mut redis, &config.pair_data_prefix).await?,
    ));
    info!(
        "strategy-rs starting (market_data_prefix={} pair_data_prefix={} matched_market_stream={} trade_signal_stream={} require_validated_pairs={})",
        if config.market_data_prefix.is_empty() {
            "<none>"
        } else {
            &config.market_data_prefix
        },
        if config.pair_data_prefix.is_empty() {
            "<none>"
        } else {
            &config.pair_data_prefix
        },
        config.matched_market_stream,
        config.trade_signal_stream
        ,
        config.require_validated_pairs
    );

    let risk = RiskManager {
        redis: redis.clone(),
        config: config.clone(),
    };

    let market_index_task = {
        let pair_index = pair_index.clone();
        let config = config.clone();
        let risk = risk.clone();
        let mut redis = redis.clone();
        tokio::spawn(async move {
            loop {
                let opts = StreamReadOptions::default()
                    .group(&config.consumer_group, &config.consumer_name)
                    .count(config.batch)
                    .block(config.block_ms);
                let reply = redis
                    .xread_options::<_, _, StreamReadReply>(&[&market_stream], &[">"], &opts)
                    .await;
                let Ok(reply) = reply else {
                    warn!("strategy-rs market stream read failed");
                    tokio::time::sleep(Duration::from_secs(1)).await;
                    continue;
                };
                for key in reply.keys {
                    for entry in key.ids {
                        let exchange = entry
                            .map
                            .get("exchange")
                            .map(stream_field_to_string)
                            .unwrap_or_default();
                        let market_id = entry
                            .map
                            .get("market_id")
                            .map(stream_field_to_string)
                            .unwrap_or_default();
                        let lookup = format!("{exchange}:{market_id}");
                        let pair_ids = {
                            let guard = pair_index.read().await;
                            guard.get(&lookup).cloned().unwrap_or_default()
                        };
                        for pair_id in pair_ids {
                            match evaluate_pair(&mut redis, &risk, &config, &pair_id).await {
                                Ok(Some(signal)) => {
                                    if let Err(err) =
                                        emit_trade_signal(&mut redis, &config.trade_signal_stream, &signal).await
                                    {
                                        warn!("failed to emit trade signal: {err:#}");
                                    }
                                }
                                Ok(None) => {}
                                Err(err) => warn!("pair evaluation failed: {err:#}"),
                            }
                        }
                        let _: redis::RedisResult<i32> =
                            redis.xack(&market_stream, &config.consumer_group, &[entry.id.as_str()]).await;
                    }
                }
            }
        })
    };

    let pair_task = {
        let pair_index = pair_index.clone();
        let config = config.clone();
        let mut redis = redis.clone();
        tokio::spawn(async move {
            loop {
                let opts = StreamReadOptions::default()
                    .group(&config.pair_consumer_group, &config.consumer_name)
                    .count(10)
                    .block(1000);
                let reply = redis
                    .xread_options::<_, _, StreamReadReply>(
                        &[&config.matched_market_stream],
                        &[">"],
                        &opts,
                    )
                    .await;
                let Ok(reply) = reply else {
                    warn!("strategy-rs matched-pairs stream read failed");
                    tokio::time::sleep(Duration::from_secs(1)).await;
                    continue;
                };
                for key in reply.keys {
                    for entry in key.ids {
                        let pair_id = entry
                            .map
                            .get("pair_id")
                            .map(stream_field_to_string)
                            .unwrap_or_default();
                        let kalshi_market_id = entry
                            .map
                            .get("kalshi_market_id")
                            .map(stream_field_to_string)
                            .unwrap_or_default();
                        let polymarket_market_id = entry
                            .map
                            .get("polymarket_market_id")
                            .map(stream_field_to_string)
                            .unwrap_or_default();
                        if !pair_id.is_empty() && !kalshi_market_id.is_empty() && !polymarket_market_id.is_empty() {
                            let mut guard = pair_index.write().await;
                            guard
                                .entry(format!("kalshi:{kalshi_market_id}"))
                                .or_default()
                                .insert(pair_id.clone());
                            guard
                                .entry(format!("polymarket:{polymarket_market_id}"))
                                .or_default()
                                .insert(pair_id.clone());
                        }
                        let _: redis::RedisResult<i32> = redis
                            .xack(
                                &config.matched_market_stream,
                                &config.pair_consumer_group,
                                &[entry.id.as_str()],
                            )
                            .await;
                    }
                }
            }
        })
    };

    let (market_join, pair_join) = tokio::join!(market_index_task, pair_task);
    if let Err(err) = market_join {
        error!("market task failed: {err}");
    }
    if let Err(err) = pair_join {
        error!("pair task failed: {err}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn orderbook(exchange: &str, bids: Vec<[f64; 2]>, asks: Vec<[f64; 2]>) -> OrderbookPayload {
        OrderbookPayload {
            market_id: "mkt".to_string(),
            exchange: exchange.to_string(),
            bids,
            asks,
            ts: 0.0,
        }
    }

    fn config() -> Config {
        Config {
            redis_host: String::new(),
            redis_port: 6379,
            redis_password: String::new(),
            min_edge: 0.03,
            fee_estimate: 0.02,
            max_trade_size: 100.0,
            max_position_per_market: 500.0,
            min_liquidity: 10.0,
            max_portfolio_notional: 10_000.0,
            market_data_prefix: String::new(),
            pair_data_prefix: String::new(),
            matched_market_stream: STREAM_MATCHED_MARKETS.to_string(),
            trade_signal_stream: "shadow:stream:trade_signals".to_string(),
            cooldown_prefix: "shadow:cooldown:".to_string(),
            consumer_group: "cg:test".to_string(),
            pair_consumer_group: "cg:test-pairs".to_string(),
            consumer_name: "strategy-rs-test".to_string(),
            block_ms: 100,
            batch: 50,
        }
    }

    #[test]
    fn detects_yes_no_opportunity() {
        let kalshi = orderbook("kalshi", vec![[0.52, 50.0]], vec![[0.55, 50.0]]);
        let poly = orderbook("polymarket", vec![[0.62, 80.0]], vec![[0.66, 80.0]]);
        let opp = detect_opportunity(&kalshi, &poly, &config()).expect("opportunity");
        assert_eq!(opp.kalshi_side, Side::Yes);
        assert_eq!(opp.poly_side, Side::No);
    }
}
