#!/usr/bin/env python3
"""Install phase-specific Cloudflare connect diagnostics in the Android source tree."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_tls_diagnostic.py path/to/project/root")

root = Path(sys.argv[1])
config = root / "src/config.rs"
ws = root / "src/ws.rs"
cf = root / "src/cfproxy.rs"
proxy = root / "src/proxy.rs"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source anchor in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    config,
    'pub const CFPROXY_DIAL_PHASE_TIMEOUT: Duration = Duration::from_secs(4);',
    '''pub const CFPROXY_ATTEMPT_TIMEOUT: Duration = Duration::from_secs(12);
pub const CFPROXY_TCP_TIMEOUT: Duration = Duration::from_secs(4);
pub const CFPROXY_TLS_TIMEOUT: Duration = Duration::from_secs(8);
pub const CFPROXY_HTTP_WRITE_TIMEOUT: Duration = Duration::from_secs(3);
pub const CFPROXY_HTTP_RESPONSE_TIMEOUT: Duration = Duration::from_secs(5);''',
    "Cloudflare timeout constants",
)

replace_once(ws, 'use std::time::Duration;', 'use std::time::{Duration, Instant};', "ws timing import")

replace_once(
    ws,
    '''    let mut cfg = ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();
    cfg.resumption = rustls::client::Resumption::in_memory_sessions(100);''',
    '''    let mut cfg = ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();
    // The proxy uses an HTTP/1.1 Upgrade request after TLS.
    // Keep TLS 1.2 and 1.3 enabled; advertise only the matching ALPN protocol.
    cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
    cfg.resumption = rustls::client::Resumption::in_memory_sessions(100);''',
    "TLS ALPN profile",
)

replace_once(
    ws,
    '''    TimeoutAt(&'static str),
    /// Hostname attempt followed by one or more explicit-IP attempts.''',
    '''    TimeoutAt(&'static str),
    /// A timed-out phase with its measured elapsed time in milliseconds.
    TimeoutAtElapsed(&'static str, u128),
    /// Hostname attempt followed by one or more explicit-IP attempts.''',
    "elapsed timeout error variant",
)
replace_once(
    ws,
    '''            WsError::TimeoutAt(phase) => format!("timeout({})", phase),
            WsError::Fallback''',
    '''            WsError::TimeoutAt(phase) => format!("timeout({})", phase),
            WsError::TimeoutAtElapsed(phase, ms) => format!("timeout({}:{}ms)", phase, ms),
            WsError::Fallback''',
    "elapsed timeout formatting",
)

replace_once(
    ws,
    '''pub async fn ws_connect_once(
    dial_addr: &str,
    domain: &str,
    path: &str,
    timeout: Duration,
) -> Result<RawWebSocket, WsError> {
    if dial_addr.is_empty() {''',
    '''pub async fn ws_connect_once(
    dial_addr: &str,
    domain: &str,
    path: &str,
    timeout: Duration,
) -> Result<RawWebSocket, WsError> {
    let tls_timeout = ws_handshake_timeout(timeout);
    ws_connect_once_with_phase_timeouts(
        dial_addr, domain, path, timeout, tls_timeout, timeout, timeout,
    ).await
}

pub async fn ws_connect_once_with_phase_timeouts(
    dial_addr: &str,
    domain: &str,
    path: &str,
    tcp_timeout: Duration,
    tls_timeout: Duration,
    http_write_timeout: Duration,
    http_response_timeout: Duration,
) -> Result<RawWebSocket, WsError> {
    if dial_addr.is_empty() {''',
    "phase timeout wrapper",
)

replace_once(
    ws,
    '''    let raw_conn = match tokio::time::timeout(timeout, TcpStream::connect(&target_addr)).await {
        Ok(Ok(c)) => c,
        Ok(Err(e)) => return Err(WsError::Io(e)),
        Err(_) => return Err(WsError::TimeoutAt("tcp")),
    };''',
    '''    let tcp_started = Instant::now();
    let raw_conn = match tokio::time::timeout(tcp_timeout, TcpStream::connect(&target_addr)).await {
        Ok(Ok(c)) => c,
        Ok(Err(e)) => return Err(WsError::Io(e)),
        Err(_) => return Err(WsError::TimeoutAtElapsed("tcp", tcp_started.elapsed().as_millis())),
    };''',
    "TCP phase timeout",
)
replace_once(
    ws,
    '''    let handshake_timeout = ws_handshake_timeout(timeout);
    let tls_conn =
        match tokio::time::timeout(handshake_timeout, connector.connect(sni, raw_conn)).await {''',
    '''    let tls_started = Instant::now();
    let tls_conn =
        match tokio::time::timeout(tls_timeout, connector.connect(sni, raw_conn)).await {''',
    "TLS phase timeout",
)
replace_once(
    ws,
    '''                return Err(WsError::TimeoutAt("tls"));''',
    '''                return Err(WsError::TimeoutAtElapsed("tls", tls_started.elapsed().as_millis()));''',
    "TLS elapsed report",
)
replace_once(
    ws,
    '''    match tokio::time::timeout(timeout, write_half.write_all(req.as_bytes())).await {
        Ok(Ok(())) => {}
        Ok(Err(e)) => return Err(WsError::Io(e)),
        Err(_) => return Err(WsError::TimeoutAt("http-write")),
    }''',
    '''    let http_write_started = Instant::now();
    match tokio::time::timeout(http_write_timeout, write_half.write_all(req.as_bytes())).await {
        Ok(Ok(())) => {}
        Ok(Err(e)) => return Err(WsError::Io(e)),
        Err(_) => return Err(WsError::TimeoutAtElapsed("http-write", http_write_started.elapsed().as_millis())),
    }''',
    "HTTP write phase timeout",
)
replace_once(
    ws,
    '''    let read_result = tokio::time::timeout(timeout, async {''',
    '''    let http_response_started = Instant::now();
    let read_result = tokio::time::timeout(http_response_timeout, async {''',
    "HTTP response phase timeout",
)
replace_once(
    ws,
    '''        Err(_) => return Err(WsError::TimeoutAt("http-response")),''',
    '''        Err(_) => return Err(WsError::TimeoutAtElapsed("http-response", http_response_started.elapsed().as_millis())),''',
    "HTTP response elapsed report",
)

replace_once(
    cf,
    'use crate::ws::{is_http_status_error, ws_connect_once, RawWebSocket, WsError};',
    'use crate::ws::{is_http_status_error, ws_connect_once_with_phase_timeouts, RawWebSocket, WsError};',
    "Cloudflare ws import",
)

old_cf_timeout_helper = '''fn new_timed_attempt_timeout(base: Duration, phase: Duration) -> Duration {
    let mut eff = base;
    if eff <= Duration::ZERO {
        eff = Duration::from_secs(5);
    }
    if eff > phase {
        eff = phase;
    }
    eff
}

'''
source = cf.read_text(encoding="utf-8")
if old_cf_timeout_helper in source:
    cf.write_text(source.replace(old_cf_timeout_helper, "", 1), encoding="utf-8")

replace_once(
    cf,
    '''pub async fn cf_connect_domain(
    domain: &str,
    path: &str,
    timeout: f64,
) -> (Option<RawWebSocket>, String, Option<WsError>) {
    let path = if path.is_empty() { "/apiws" } else { path };

    let attempt_timeout = crate::ws::ws_connect_timeout(timeout);
    let phase_timeout = attempt_timeout.min(CFPROXY_DIAL_PHASE_TIMEOUT);

    match ws_connect_once(domain, domain, path, phase_timeout).await {''',
    '''async fn cf_ws_attempt(
    dial_addr: &str,
    domain: &str,
    path: &str,
    total_timeout: Duration,
) -> Result<RawWebSocket, WsError> {
    let tcp_timeout = total_timeout.min(CFPROXY_TCP_TIMEOUT);
    let tls_timeout = total_timeout.min(CFPROXY_TLS_TIMEOUT);
    let http_write_timeout = total_timeout.min(CFPROXY_HTTP_WRITE_TIMEOUT);
    let http_response_timeout = total_timeout.min(CFPROXY_HTTP_RESPONSE_TIMEOUT);
    let started = Instant::now();
    match tokio::time::timeout(
        total_timeout,
        ws_connect_once_with_phase_timeouts(
            dial_addr, domain, path,
            tcp_timeout, tls_timeout, http_write_timeout, http_response_timeout,
        ),
    ).await {
        Ok(result) => result,
        Err(_) => Err(WsError::TimeoutAtElapsed("total", started.elapsed().as_millis())),
    }
}

pub async fn cf_connect_domain(
    domain: &str,
    path: &str,
    timeout: f64,
) -> (Option<RawWebSocket>, String, Option<WsError>) {
    let path = if path.is_empty() { "/apiws" } else { path };

    let attempt_timeout = crate::ws::ws_connect_timeout(timeout).min(CFPROXY_ATTEMPT_TIMEOUT);

    match cf_ws_attempt(domain, domain, path, attempt_timeout).await {''',
    "Cloudflare phase-aware attempt helper",
)
replace_once(
    cf,
    '''                let result = ws_connect_once(&ip, &domain, &path, phase_timeout).await;''',
    '''                let result = cf_ws_attempt(&ip, &domain, &path, attempt_timeout).await;''',
    "Cloudflare IP phase-aware attempt",
)

replace_once(
    proxy,
    'let (ws, resolved_ip, err) = cf_connect_domain(&domain, "/apiws", 5.0).await;',
    'let (ws, resolved_ip, err) = cf_connect_domain(&domain, "/apiws", 12.0).await;',
    "Cloudflare attempt budget caller",
)


# Experimental ECH path: fetch HTTPS RR over DoH, then use rustls client-side ECH.
# AWS-LC is required because rustls' ring provider does not implement HPKE.
cargo = root / "Cargo.toml"
cargo_text = cargo.read_text(encoding="utf-8")
cargo_text = cargo_text.replace(
    'tokio-rustls = { version = "0.26", default-features = false, features = ["ring"] }',
    'tokio-rustls = { version = "0.26", default-features = false, features = ["aws_lc_rs"] }',
)
cargo_text = cargo_text.replace(
    'rustls = { version = "0.23", default-features = false, features = ["ring", "std"] }',
    'rustls = { version = "0.23", default-features = false, features = ["aws_lc_rs", "std", "tls12"] }',
)
cargo.write_text(cargo_text, encoding="utf-8")

source = ws.read_text(encoding="utf-8")
source = source.replace(
    'use rustls::{ClientConfig, DigitallySignedStruct, SignatureScheme};',
    'use rustls::{ClientConfig, DigitallySignedStruct, SignatureScheme};\nuse rustls::client::{EchConfig, EchMode};',
)
source = source.replace(
    'use rustls_pki_types::{CertificateDer, ServerName, UnixTime};',
    'use rustls_pki_types::{CertificateDer, EchConfigListBytes, ServerName, UnixTime};',
)
old_tls = '''static TLS_CONFIG: Lazy<Arc<ClientConfig>> = Lazy::new(|| {
    let mut cfg = ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();
    cfg.resumption = rustls::client::Resumption::in_memory_sessions(100);
    Arc::new(cfg)
});'''
new_tls = '''static TLS_CONFIG: Lazy<Arc<ClientConfig>> = Lazy::new(|| {
    let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
    let mut cfg = ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .expect("AWS-LC TLS versions")
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();
    cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
    cfg.resumption = rustls::client::Resumption::in_memory_sessions(100);
    Arc::new(cfg)
});

static ECH_TLS_CACHE: Lazy<tokio::sync::Mutex<HashMap<String, Arc<ClientConfig>>>> =
    Lazy::new(|| tokio::sync::Mutex::new(HashMap::new()));

async fn ech_tls_config(domain: &str) -> Option<Arc<ClientConfig>> {
    if let Some(cached) = ECH_TLS_CACHE.lock().await.get(domain).cloned() {
        return Some(cached);
    }

    // HTTPS RR (type 65) carries the ECHConfigList. Use DoH so the lookup itself
    // does not disclose the target hostname to the mobile operator's DNS.
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .ok()?;
    let response = client
        .get("https://cloudflare-dns.com/dns-query")
        .query(&[("name", domain), ("type", "HTTPS")])
        .header("accept", "application/dns-json")
        .send()
        .await
        .ok()?;
    let json: serde_json::Value = response.json().await.ok()?;
    let answers = json.get("Answer")?.as_array()?;

    for answer in answers {
        if answer.get("type").and_then(|v| v.as_u64()) != Some(65) {
            continue;
        }
        let data = answer.get("data").and_then(|v| v.as_str())?;
        for token in data.split_ascii_whitespace() {
            let Some(value) = token.strip_prefix("ech=") else { continue };
            let encoded = value.trim_matches('"').trim_matches('\\'');
            let bytes = base64::engine::general_purpose::STANDARD.decode(encoded).ok()?;
            let ech_list = EchConfigListBytes::from(bytes);
            let ech = EchConfig::new(
                ech_list,
                rustls::crypto::aws_lc_rs::hpke::ALL_SUPPORTED_SUITES,
            ).ok()?;

            let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
            let mut cfg = ClientConfig::builder_with_provider(provider)
                .with_ech(EchMode::from(ech))
                .ok()?
                .dangerous()
                .with_custom_certificate_verifier(Arc::new(NoVerify))
                .with_no_client_auth();
            cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
            cfg.resumption = rustls::client::Resumption::in_memory_sessions(100);
            let cfg = Arc::new(cfg);
            ECH_TLS_CACHE.lock().await.insert(domain.to_string(), cfg.clone());
            return Some(cfg);
        }
    }
    None
}

async fn tls_connector_for_domain(domain: &str) -> TlsConnector {
    match ech_tls_config(domain).await {
        Some(cfg) => {
            ldebug!("ECH enabled for {}", domain);
            TlsConnector::from(cfg)
        }
        None => {
            ldebug!("ECH unavailable for {}, using regular TLS", domain);
            TlsConnector::from(TLS_CONFIG.clone())
        }
    }
}'''
if new_tls not in source:
    if old_tls not in source:
        raise SystemExit("TLS_CONFIG anchor not found")
    source = source.replace(old_tls, new_tls, 1)
source = source.replace(
    'let connector = TlsConnector::from(TLS_CONFIG.clone());',
    'let connector = tls_connector_for_domain(domain).await;',
)
ws.write_text(source, encoding="utf-8")
print("Applied experimental ECH path: HTTPS RR lookup via Cloudflare DoH, rustls ECH with AWS-LC HPKE, regular TLS fallback.")

print("Applied phase-specific CF timeouts (TCP 4s / TLS 8s / HTTP write 3s / HTTP response 5s; total 12s), elapsed-phase errors, and TLS 1.2/1.3 + ALPN http/1.1 profile.")
