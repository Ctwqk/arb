use anyhow::{anyhow, Context, Result};
use arb_proto::{
    market_key, markets_set_key, orderbook_key, price_key, stream_market_updates, MarketMetaPayload,
    OrderbookPayload,
};
use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use log::{error, info, warn};
use redis::aio::ConnectionManager;
use redis::{cmd, AsyncCommands};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::Client;
use rsa::pkcs1::DecodeRsaPrivateKey;
use rsa::pkcs1v15::SigningKey;
use rsa::pkcs8::DecodePrivateKey;
use rsa::signature::{RandomizedSigner, SignatureEncoding};
use rsa::RsaPrivateKey;
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use sha2::Sha256;
use std::collections::HashMap;
use std::env;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

const STREAM_MAX_LEN: usize = 10_000;
const ORDERBOOK_TTL_SECS: usize = 600;
const MARKET_META_TTL_SECS: usize = 86_400;
const MARKET_REFRESH_INTERVAL_SECS: u64 = 3_600;
const KALSHI_BATCH_SIZE: usize = 50;
const POLY_BATCH_SIZE: usize = 100;
const MAX_BACKOFF_SECS: u64 = 60;

fn deserialize_string_or_default<'de, D>(deserializer: D) -> Result<String, D::Error>
where
    D: Deserializer<'de>,
{
    Ok(Option::<String>::deserialize(deserializer)?.unwrap_or_default())
}

fn deserialize_bool_or_default<'de, D>(deserializer: D) -> Result<bool, D::Error>
where
    D: Deserializer<'de>,
{
    Ok(Option::<bool>::deserialize(deserializer)?.unwrap_or_default())
}

fn deserialize_vec_or_default<'de, D, T>(deserializer: D) -> Result<Vec<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Ok(Option::<Vec<T>>::deserialize(deserializer)?.unwrap_or_default())
}

#[derive(Clone)]
struct Config {
    redis_host: String,
    redis_port: u16,
    redis_password: String,
    kalshi_api_key_id: String,
    kalshi_private_key_pem: String,
    kalshi_api_base: String,
    kalshi_ws_url: String,
    poly_clob_host: String,
    poly_ws_url: String,
    shadow_prefix: String,
}

impl Config {
    fn from_env() -> Self {
        Self {
            redis_host: env::var("REDIS_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
            redis_port: env::var("REDIS_PORT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(6379),
            redis_password: env::var("REDIS_PASSWORD").unwrap_or_default(),
            kalshi_api_key_id: env::var("KALSHI_API_KEY_ID").unwrap_or_default(),
            kalshi_private_key_pem: env::var("KALSHI_PRIVATE_KEY_PEM")
                .unwrap_or_default()
                .replace("\\n", "\n"),
            kalshi_api_base: env::var("KALSHI_API_BASE")
                .unwrap_or_else(|_| "https://api.elections.kalshi.com/trade-api/v2".to_string()),
            kalshi_ws_url: env::var("KALSHI_WS_URL")
                .unwrap_or_else(|_| "wss://api.elections.kalshi.com/trade-api/ws/v2".to_string()),
            poly_clob_host: env::var("POLY_CLOB_HOST")
                .unwrap_or_else(|_| "https://clob.polymarket.com".to_string()),
            poly_ws_url: env::var("POLY_WS_URL")
                .unwrap_or_else(|_| "wss://ws-subscriptions-clob.polymarket.com/ws/market".to_string()),
            shadow_prefix: env::var("ARB_SHADOW_PREFIX").unwrap_or_default(),
        }
    }
}

#[derive(Clone)]
struct Publisher {
    redis: ConnectionManager,
    prefix: String,
}

impl Publisher {
    async fn publish_orderbook(
        &self,
        exchange: &str,
        market_id: &str,
        bids: Vec<[f64; 2]>,
        asks: Vec<[f64; 2]>,
        ts: f64,
    ) -> Result<()> {
        let mut redis = self.redis.clone();
        let payload = OrderbookPayload {
            market_id: market_id.to_string(),
            exchange: exchange.to_string(),
            bids,
            asks,
            ts,
        };
        let key = orderbook_key(&self.prefix, exchange, market_id);
        let json = serde_json::to_string(&payload)?;
        redis
            .set_ex::<_, _, ()>(key, json, ORDERBOOK_TTL_SECS as u64)
            .await?;
        if let Some(best_ask) = payload.asks.first() {
            redis
                .set_ex::<_, _, ()>(
                    price_key(&self.prefix, exchange, market_id),
                    best_ask[0].to_string(),
                    ORDERBOOK_TTL_SECS as u64,
                )
                .await?;
        }
        let stream = stream_market_updates(&self.prefix);
        let _: String = cmd("XADD")
            .arg(&stream)
            .arg("MAXLEN")
            .arg("~")
            .arg(STREAM_MAX_LEN)
            .arg("*")
            .arg("event")
            .arg("orderbook")
            .arg("exchange")
            .arg(exchange)
            .arg("market_id")
            .arg(market_id)
            .arg("ts")
            .arg(ts.to_string())
            .query_async(&mut redis)
            .await?;
        Ok(())
    }

    async fn publish_market_meta(&self, meta: &MarketMetaPayload) -> Result<()> {
        let mut redis = self.redis.clone();
        let key = market_key(&self.prefix, &meta.exchange, &meta.market_id);
        redis
            .set_ex::<_, _, ()>(
                key,
                serde_json::to_string(meta)?,
                MARKET_META_TTL_SECS as u64,
            )
            .await?;
        let _: usize = redis
            .sadd(markets_set_key(&self.prefix, &meta.exchange), &meta.market_id)
            .await?;
        let stream = stream_market_updates(&self.prefix);
        let _: String = cmd("XADD")
            .arg(&stream)
            .arg("MAXLEN")
            .arg("~")
            .arg(STREAM_MAX_LEN)
            .arg("*")
            .arg("event")
            .arg("market_meta")
            .arg("exchange")
            .arg(&meta.exchange)
            .arg("market_id")
            .arg(&meta.market_id)
            .arg("ts")
            .arg(meta.ts.to_string())
            .query_async(&mut redis)
            .await?;
        Ok(())
    }
}

fn now_ts() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_else(|_| Duration::from_secs(0))
        .as_secs_f64()
}

fn now_ms() -> i64 {
    (now_ts() * 1000.0) as i64
}

fn decode_private_key(pem: &str) -> Result<RsaPrivateKey> {
    RsaPrivateKey::from_pkcs8_pem(pem)
        .or_else(|_| RsaPrivateKey::from_pkcs1_pem(pem))
        .context("failed to decode Kalshi private key")
}

fn kalshi_auth_headers(cfg: &Config, private_key: &RsaPrivateKey, method: &str, path: &str) -> Result<HeaderMap> {
    let ts = now_ms().to_string();
    let message = format!("{ts}{}{path}", method.to_uppercase());
    let signing_key = SigningKey::<Sha256>::new(private_key.clone());
    let mut rng = rsa::rand_core::OsRng;
    let sig = signing_key.sign_with_rng(&mut rng, message.as_bytes());
    let signature = base64::engine::general_purpose::STANDARD.encode(sig.to_bytes());
    let mut headers = HeaderMap::new();
    headers.insert(
        HeaderName::from_static("kalshi-access-key"),
        HeaderValue::from_str(&cfg.kalshi_api_key_id)?,
    );
    headers.insert(
        HeaderName::from_static("kalshi-access-timestamp"),
        HeaderValue::from_str(&ts)?,
    );
    headers.insert(
        HeaderName::from_static("kalshi-access-signature"),
        HeaderValue::from_str(&signature)?,
    );
    Ok(headers)
}

#[derive(Debug, Deserialize)]
struct KalshiMarketsResponse {
    #[serde(default)]
    markets: Vec<KalshiMarket>,
    #[serde(default)]
    cursor: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct KalshiMarket {
    ticker: String,
    #[serde(default)]
    title: String,
    #[serde(default)]
    subtitle: String,
    #[serde(default)]
    category: String,
    #[serde(default)]
    event_ticker: String,
    #[serde(default)]
    close_time: String,
    #[serde(default)]
    expected_expiration_time: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    market_type: String,
    #[serde(default)]
    yes_sub_title: String,
    #[serde(default)]
    no_sub_title: String,
    #[serde(default)]
    rules_primary: String,
    #[serde(default)]
    rules_secondary: String,
}

fn should_keep_kalshi_market(market: &KalshiMarket) -> bool {
    let title = market.title.trim();
    if title.is_empty() {
        return false;
    }
    // Skip Kalshi multi-leg / bundle products; they dominate the feed and do
    // not have clean one-to-one equivalents on Polymarket.
    if market.ticker.starts_with("KXMVE") {
        return false;
    }
    // Comma-heavy titles are almost always bundled outcomes rather than a
    // single binary market.
    if title.matches(',').count() > 1 {
        return false;
    }
    true
}

async fn refresh_kalshi_markets(
    client: &Client,
    cfg: &Config,
    private_key: &RsaPrivateKey,
    publisher: &Publisher,
) -> Result<Vec<String>> {
    let mut tickers = Vec::new();
    let mut cursor: Option<String> = None;
    let mut pages = 0usize;
    let mut skipped = 0usize;
    let refresh_started = now_ts();
    loop {
        let path = "/markets";
        let headers = kalshi_auth_headers(cfg, private_key, "GET", path)?;
        let mut req = client
            .get(format!("{}{}", cfg.kalshi_api_base, path))
            .headers(headers)
            .query(&[("status", "open"), ("limit", "200")]);
        if let Some(cur) = &cursor {
            req = req.query(&[("cursor", cur)]);
        }
        let response = req.send().await?;
        if response.status().as_u16() == 429 {
            let retry_after = response
                .headers()
                .get("Retry-After")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(10);
            warn!("Kalshi market refresh rate limited, waiting {retry_after}s...");
            tokio::time::sleep(Duration::from_secs(retry_after)).await;
            continue;
        }
        let payload: KalshiMarketsResponse = response.error_for_status()?.json().await?;
        if payload.markets.is_empty() {
            break;
        }
        pages += 1;
        cursor = payload.cursor.clone();
        for market in payload.markets {
            if !should_keep_kalshi_market(&market) {
                skipped += 1;
                continue;
            }
            if !market.ticker.is_empty() {
                tickers.push(market.ticker.clone());
            }
            let meta = MarketMetaPayload {
                event: "market_meta".to_string(),
                exchange: "kalshi".to_string(),
                market_id: market.ticker,
                title: market.title,
                description: market.subtitle,
                category: market.category,
                end_date: if market.expected_expiration_time.is_empty() {
                    market.close_time
                } else {
                    market.expected_expiration_time
                },
                status: if market.status.is_empty() {
                    "open".to_string()
                } else {
                    market.status
                },
                ts: refresh_started,
                market_type: (!market.market_type.is_empty()).then_some(market.market_type),
                event_ticker: (!market.event_ticker.is_empty()).then_some(market.event_ticker),
                market_slug: None,
                question_id: None,
                outcome_count: None,
                yes_sub_title: (!market.yes_sub_title.is_empty()).then_some(market.yes_sub_title),
                no_sub_title: (!market.no_sub_title.is_empty()).then_some(market.no_sub_title),
                rules_primary: (!market.rules_primary.is_empty()).then_some(market.rules_primary),
                rules_secondary: (!market.rules_secondary.is_empty())
                    .then_some(market.rules_secondary),
                yes_token_id: None,
                no_token_id: None,
                condition_id: None,
            };
            publisher.publish_market_meta(&meta).await?;
        }
        if pages % 20 == 0 {
            info!(
                "Kalshi refresh progress: pages={} kept={} skipped={} elapsed={:.1}s",
                pages,
                tickers.len(),
                skipped,
                now_ts() - refresh_started
            );
        }
        if cursor.as_deref().unwrap_or("").is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
    info!(
        "Kalshi discovered {} usable markets across {} pages in {:.1}s (skipped={})",
        tickers.len(),
        pages,
        now_ts() - refresh_started,
        skipped,
    );
    Ok(tickers)
}

fn normalize_kalshi_levels(raw_levels: &Value) -> Vec<[f64; 2]> {
    raw_levels
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|lvl| {
            let price = lvl.get(0)?.as_i64()?;
            let size = lvl.get(1)?.as_f64().or_else(|| lvl.get(1)?.as_i64().map(|v| v as f64))?;
            Some([((price as f64) / 100.0 * 1_000_000.0).round() / 1_000_000.0, size])
        })
        .collect()
}

async fn handle_kalshi_message(
    publisher: &Publisher,
    books: &Arc<Mutex<HashMap<String, OrderbookPayload>>>,
    msg: &Value,
) -> Result<()> {
    let msg_type = msg.get("type").and_then(Value::as_str).unwrap_or_default();
    let market_id = msg
        .get("market_ticker")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    if market_id.is_empty() {
        return Ok(());
    }
    let ts = now_ts();
    match msg_type {
        "orderbook_snapshot" => {
            let yes = msg.get("yes").cloned().unwrap_or_else(|| json!({}));
            let bids = normalize_kalshi_levels(yes.get("bids").unwrap_or(&Value::Null));
            let asks = normalize_kalshi_levels(yes.get("asks").unwrap_or(&Value::Null));
            let payload = OrderbookPayload {
                market_id: market_id.clone(),
                exchange: "kalshi".to_string(),
                bids: bids.clone(),
                asks: asks.clone(),
                ts,
            };
            books.lock().await.insert(market_id.clone(), payload);
            publisher
                .publish_orderbook("kalshi", &market_id, bids, asks, ts)
                .await?;
        }
        "orderbook_delta" => {
            let price = msg.get("price").and_then(Value::as_i64).unwrap_or_default() as f64 / 100.0;
            let delta = msg
                .get("delta")
                .and_then(Value::as_f64)
                .or_else(|| msg.get("delta").and_then(Value::as_i64).map(|v| v as f64))
                .unwrap_or_default();
            let is_bid = msg.get("is_bid").and_then(Value::as_bool).unwrap_or(true);
            let mut guard = books.lock().await;
            let book = match guard.get_mut(&market_id) {
                Some(book) => book,
                None => return Ok(()),
            };
            let levels = if is_bid { &mut book.bids } else { &mut book.asks };
            let mut updated = false;
            let mut remove_idx = None;
            for (idx, level) in levels.iter_mut().enumerate() {
                if (level[0] - price).abs() < 1e-6 {
                    level[1] = (level[1] + delta).max(0.0);
                    if level[1] == 0.0 {
                        remove_idx = Some(idx);
                    }
                    updated = true;
                    break;
                }
            }
            if let Some(idx) = remove_idx {
                levels.remove(idx);
            }
            if !updated && delta > 0.0 {
                levels.push([price, delta]);
                if is_bid {
                    levels.sort_by(|a, b| b[0].partial_cmp(&a[0]).unwrap());
                } else {
                    levels.sort_by(|a, b| a[0].partial_cmp(&b[0]).unwrap());
                }
            }
            let snapshot = book.clone();
            drop(guard);
            publisher
                .publish_orderbook("kalshi", &market_id, snapshot.bids, snapshot.asks, ts)
                .await?;
        }
        _ => {}
    }
    Ok(())
}

async fn kalshi_run(cfg: Config, publisher: Publisher) -> Result<()> {
    let client = Client::builder().use_rustls_tls().build()?;
    let private_key = decode_private_key(&cfg.kalshi_private_key_pem)?;
    let books = Arc::new(Mutex::new(HashMap::<String, OrderbookPayload>::new()));
    let mut tickers: Vec<String> = Vec::new();
    let mut last_refresh = 0.0f64;
    let mut backoff = 1u64;
    loop {
        match async {
            if tickers.is_empty() || now_ts() - last_refresh > MARKET_REFRESH_INTERVAL_SECS as f64 {
                tickers = refresh_kalshi_markets(&client, &cfg, &private_key, &publisher).await?;
                last_refresh = now_ts();
            }
            books.lock().await.clear();

            let headers = kalshi_auth_headers(&cfg, &private_key, "GET", "/trade-api/ws/v2")?;
            let mut request = cfg.kalshi_ws_url.clone().into_client_request()?;
            for (name, value) in headers.iter() {
                request.headers_mut().insert(name.clone(), value.clone());
            }
            let (mut ws, _) = connect_async(request).await?;
            info!("Kalshi WS connected");
            for batch in tickers.chunks(KALSHI_BATCH_SIZE) {
                let payload = json!({
                    "id": now_ms(),
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": batch,
                    }
                });
                ws.send(Message::Text(payload.to_string())).await?;
            }
            while let Some(frame) = ws.next().await {
                match frame? {
                    Message::Text(text) => {
                        let value: Value = serde_json::from_str(&text)?;
                        if let Err(err) = handle_kalshi_message(&publisher, &books, &value).await {
                            warn!("Kalshi message handling failed: {err:#}");
                        }
                    }
                    Message::Ping(data) => {
                        ws.send(Message::Pong(data)).await?;
                    }
                    Message::Close(_) => break,
                    _ => {}
                }
            }
            Ok::<(), anyhow::Error>(())
        }
        .await
        {
            Ok(_) => backoff = 1,
            Err(err) => {
                error!("Kalshi collector error: {err:#}");
                tokio::time::sleep(Duration::from_secs(backoff)).await;
                backoff = (backoff * 2).min(MAX_BACKOFF_SECS);
            }
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
struct PolymarketMarketsResponse {
    #[serde(default)]
    data: Vec<PolymarketMarket>,
    #[serde(default)]
    next_cursor: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct PolymarketMarket {
    #[serde(default, deserialize_with = "deserialize_bool_or_default")]
    active: bool,
    #[serde(default, deserialize_with = "deserialize_bool_or_default")]
    closed: bool,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    question: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    description: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    category: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    condition_id: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    question_id: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    market_slug: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    end_date_iso: String,
    #[serde(default, deserialize_with = "deserialize_vec_or_default")]
    tokens: Vec<PolymarketToken>,
}

#[derive(Debug, Clone, Deserialize)]
struct PolymarketToken {
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    token_id: String,
    #[serde(default, deserialize_with = "deserialize_string_or_default")]
    outcome: String,
}

async fn refresh_polymarket_markets(
    client: &Client,
    cfg: &Config,
    publisher: &Publisher,
) -> Result<(Vec<String>, HashMap<String, String>)> {
    let mut token_ids = Vec::new();
    let mut token_to_market = HashMap::new();
    let mut next_cursor = "MA==".to_string();
    let refresh_started = now_ts();
    let mut pages = 0usize;
    let mut kept_markets = 0usize;
    let mut skipped_markets = 0usize;
    loop {
        let response = client
            .get(format!("{}/markets", cfg.poly_clob_host))
            .query(&[("next_cursor", next_cursor.as_str())])
            .send()
            .await?
            .error_for_status()?;
        let payload: PolymarketMarketsResponse = response.json().await?;
        if payload.data.is_empty() {
            break;
        }
        pages += 1;
        next_cursor = payload.next_cursor.clone().unwrap_or_default();
        let now = now_ts();
        for market in payload.data.into_iter().filter(|m| m.active && !m.closed) {
            let yes_token = market
                .tokens
                .iter()
                .find(|token| token.outcome.eq_ignore_ascii_case("yes"))
                .map(|token| token.token_id.clone());
            let no_token = market
                .tokens
                .iter()
                .find(|token| token.outcome.eq_ignore_ascii_case("no"))
                .map(|token| token.token_id.clone());
            let mut added_any_token = false;
            for token in &market.tokens {
                if !token.token_id.is_empty() {
                    added_any_token = true;
                    token_ids.push(token.token_id.clone());
                    token_to_market.insert(token.token_id.clone(), market.condition_id.clone());
                }
            }
            if !added_any_token {
                skipped_markets += 1;
                continue;
            }
            let meta = MarketMetaPayload {
                event: "market_meta".to_string(),
                exchange: "polymarket".to_string(),
                market_id: market.condition_id.clone(),
                title: market.question,
                description: market.description,
                category: market.category,
                end_date: market.end_date_iso,
                status: "open".to_string(),
                ts: now,
                market_type: Some("binary".to_string()),
                event_ticker: None,
                market_slug: (!market.market_slug.is_empty()).then_some(market.market_slug),
                question_id: (!market.question_id.is_empty()).then_some(market.question_id),
                outcome_count: Some(market.tokens.len() as u32),
                yes_sub_title: None,
                no_sub_title: None,
                rules_primary: None,
                rules_secondary: None,
                yes_token_id: yes_token,
                no_token_id: no_token,
                condition_id: Some(market.condition_id),
            };
            publisher.publish_market_meta(&meta).await?;
            kept_markets += 1;
        }
        if pages % 20 == 0 {
            info!(
                "Polymarket refresh progress: pages={} kept_markets={} token_streams={} elapsed={:.1}s",
                pages,
                kept_markets,
                token_ids.len(),
                now_ts() - refresh_started
            );
        }
        if next_cursor.is_empty() || next_cursor == "LTE=" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    info!(
        "Polymarket discovered {} usable markets / {} token streams across {} pages in {:.1}s (skipped={})",
        kept_markets,
        token_ids.len(),
        pages,
        now_ts() - refresh_started,
        skipped_markets,
    );
    Ok((token_ids, token_to_market))
}

fn parse_poly_level(raw: &Value) -> Option<[f64; 2]> {
    Some([
        raw.get("price")?.as_str()?.parse().ok()?,
        raw.get("size")?.as_str()?.parse().ok()?,
    ])
}

async fn handle_poly_message(
    publisher: &Publisher,
    books: &Arc<Mutex<HashMap<String, OrderbookPayload>>>,
    token_to_market: &Arc<Mutex<HashMap<String, String>>>,
    value: &Value,
) -> Result<()> {
    let event_type = value
        .get("event_type")
        .or_else(|| value.get("type"))
        .and_then(Value::as_str)
        .unwrap_or_default();
    let ts = now_ts();
    match event_type {
        "book" => {
            let asset_id = value
                .get("asset_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            if asset_id.is_empty() {
                return Ok(());
            }
            let market_id = token_to_market
                .lock()
                .await
                .get(&asset_id)
                .cloned()
                .or_else(|| {
                    value
                        .get("market")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .unwrap_or_else(|| asset_id.clone());
            let bids = value
                .get("bids")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(parse_poly_level)
                .collect::<Vec<_>>();
            let asks = value
                .get("asks")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(parse_poly_level)
                .collect::<Vec<_>>();
            let payload = OrderbookPayload {
                market_id: market_id.clone(),
                exchange: "polymarket".to_string(),
                bids: bids.clone(),
                asks: asks.clone(),
                ts,
            };
            books.lock().await.insert(asset_id.clone(), payload);
            publisher
                .publish_orderbook("polymarket", &market_id, bids, asks, ts)
                .await?;
        }
        "price_change" | "tick_size_change" => {
            let changes = value
                .get("price_changes")
                .or_else(|| value.get("changes"))
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if changes.is_empty() {
                return Ok(());
            }
            let mut guard = books.lock().await;
            let token_map = token_to_market.lock().await;
            let mut snapshots: Vec<(String, OrderbookPayload)> = Vec::new();
            for change in changes {
                let asset_id = change
                    .get("asset_id")
                    .or_else(|| value.get("asset_id"))
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                if asset_id.is_empty() {
                    continue;
                }
                let market_id = token_map
                    .get(&asset_id)
                    .cloned()
                    .or_else(|| {
                        value
                            .get("market")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .unwrap_or_else(|| asset_id.clone());
                let book = match guard.get_mut(&asset_id) {
                    Some(book) => book,
                    None => continue,
                };
                let price = change
                    .get("price")
                    .and_then(Value::as_str)
                    .and_then(|v| v.parse::<f64>().ok())
                    .unwrap_or_default();
                let size = change
                    .get("size")
                    .and_then(Value::as_str)
                    .and_then(|v| v.parse::<f64>().ok())
                    .unwrap_or_default();
                let is_bid = change
                    .get("side")
                    .and_then(Value::as_str)
                    .map(|side| side.eq_ignore_ascii_case("BUY"))
                    .unwrap_or(true);
                let levels = if is_bid { &mut book.bids } else { &mut book.asks };
                let mut updated = false;
                let mut remove_idx = None;
                for (idx, level) in levels.iter_mut().enumerate() {
                    if (level[0] - price).abs() < 1e-6 {
                        if size == 0.0 {
                            remove_idx = Some(idx);
                        } else {
                            level[1] = size;
                        }
                        updated = true;
                        break;
                    }
                }
                if let Some(idx) = remove_idx {
                    levels.remove(idx);
                }
                if !updated && size > 0.0 {
                    levels.push([price, size]);
                    if is_bid {
                        levels.sort_by(|a, b| b[0].partial_cmp(&a[0]).unwrap());
                    } else {
                        levels.sort_by(|a, b| a[0].partial_cmp(&b[0]).unwrap());
                    }
                }
                snapshots.push((market_id, book.clone()));
            }
            drop(token_map);
            drop(guard);
            for (market_id, snapshot) in snapshots {
                publisher
                    .publish_orderbook("polymarket", &market_id, snapshot.bids, snapshot.asks, ts)
                    .await?;
            }
        }
        _ => {}
    }
    Ok(())
}

async fn polymarket_run(cfg: Config, publisher: Publisher) -> Result<()> {
    let client = Client::builder().use_rustls_tls().build()?;
    let books = Arc::new(Mutex::new(HashMap::<String, OrderbookPayload>::new()));
    let token_to_market = Arc::new(Mutex::new(HashMap::<String, String>::new()));
    let mut token_ids: Vec<String> = Vec::new();
    let mut last_refresh = 0.0f64;
    let mut backoff = 1u64;
    loop {
        match async {
            if token_ids.is_empty() || now_ts() - last_refresh > MARKET_REFRESH_INTERVAL_SECS as f64 {
                let (new_token_ids, new_token_to_market) =
                    refresh_polymarket_markets(&client, &cfg, &publisher).await?;
                token_ids = new_token_ids;
                let mut guard = token_to_market.lock().await;
                guard.clear();
                guard.extend(new_token_to_market);
                last_refresh = now_ts();
            }
            books.lock().await.clear();
            let (mut ws, _) = connect_async(cfg.poly_ws_url.clone()).await?;
            info!("Polymarket WS connected");
            for (idx, batch) in token_ids.chunks(POLY_BATCH_SIZE).enumerate() {
                let payload = if idx == 0 {
                    json!({
                        "type": "market",
                        "assets_ids": batch,
                    })
                } else {
                    json!({
                        "operation": "subscribe",
                        "assets_ids": batch,
                    })
                };
                ws.send(Message::Text(payload.to_string())).await?;
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            let mut heartbeat = tokio::time::interval(Duration::from_secs(10));
            loop {
                let frame = tokio::select! {
                    _ = heartbeat.tick() => {
                        ws.send(Message::Text("PING".to_string())).await?;
                        continue;
                    }
                    frame = ws.next() => frame,
                };
                let Some(frame) = frame else {
                    break;
                };
                match frame? {
                    Message::Text(text) => {
                        let trimmed = text.trim();
                        if trimmed.is_empty()
                            || trimmed.eq_ignore_ascii_case("PING")
                            || trimmed.eq_ignore_ascii_case("PONG")
                            || trimmed == "{}"
                        {
                            continue;
                        }
                        let parsed: Value = match serde_json::from_str(trimmed) {
                            Ok(value) => value,
                            Err(err) => {
                                warn!("Skipping non-JSON Polymarket frame: {err}");
                                continue;
                            }
                        };
                        match parsed {
                            Value::Array(items) => {
                                for item in items {
                                    if let Err(err) = handle_poly_message(&publisher, &books, &token_to_market, &item).await {
                                        warn!("Polymarket message handling failed: {err:#}");
                                    }
                                }
                            }
                            Value::Object(_) => {
                                if let Err(err) = handle_poly_message(&publisher, &books, &token_to_market, &parsed).await {
                                    warn!("Polymarket message handling failed: {err:#}");
                                }
                            }
                            _ => {}
                        }
                    }
                    Message::Ping(data) => {
                        ws.send(Message::Pong(data)).await?;
                    }
                    Message::Close(_) => break,
                    _ => {}
                }
            }
            Ok::<(), anyhow::Error>(())
        }
        .await
        {
            Ok(_) => backoff = 1,
            Err(err) => {
                error!("Polymarket collector error: {err:#}");
                tokio::time::sleep(Duration::from_secs(backoff)).await;
                backoff = (backoff * 2).min(MAX_BACKOFF_SECS);
            }
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    let cfg = Config::from_env();
    let redis_url = if cfg.redis_password.is_empty() {
        format!("redis://{}:{}/", cfg.redis_host, cfg.redis_port)
    } else {
        format!(
            "redis://:{}@{}:{}/",
            cfg.redis_password, cfg.redis_host, cfg.redis_port
        )
    };
    let redis_client = redis::Client::open(redis_url)?;
    let redis = ConnectionManager::new(redis_client).await?;
    let publisher = Publisher {
        redis,
        prefix: cfg.shadow_prefix.clone(),
    };
    info!(
        "collector-rs starting (shadow_prefix={})",
        if cfg.shadow_prefix.is_empty() {
            "<none>"
        } else {
            &cfg.shadow_prefix
        }
    );
    tokio::try_join!(
        kalshi_run(cfg.clone(), publisher.clone()),
        polymarket_run(cfg.clone(), publisher)
    )?;
    Err(anyhow!("collector-rs terminated unexpectedly"))
}
