#!/usr/bin/env python3
from pathlib import Path
import sys
root = Path(sys.argv[1])

def replace_once(path, old, new, label):
    s = path.read_text(encoding="utf-8")
    if new in s:
        return
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, got {n} in {path}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")

ws = root / "src/ws.rs"
replace_once(ws,
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
    ws_connect_once_with_sni(dial_addr, domain, path, timeout, domain).await
}

/// Connect using a fronting SNI while retaining the original HTTP Host header.
pub async fn ws_connect_once_with_sni(
    dial_addr: &str,
    domain: &str,
    path: &str,
    timeout: Duration,
    tls_sni: &str,
) -> Result<RawWebSocket, WsError> {
    if dial_addr.is_empty() {''',
"introduce SNI override")
replace_once(ws, 'let sni = server_name(domain);', 'let sni = server_name(tls_sni);', "use SNI override")

proxy = root / "src/proxy.rs"
s = proxy.read_text(encoding="utf-8")
if "ws_connect_once_with_sni" not in s:
    # Existing imports
    if "use crate::ws::ws_connect_once;" in s:
        s = s.replace("use crate::ws::ws_connect_once;", "use crate::ws::{ws_connect_once, ws_connect_once_with_sni};", 1)
    elif "ws_connect_once," not in s:
        raise SystemExit("cannot locate ws_connect_once import in proxy.rs")
    # Replace direct websocket attempt failure branch with alternate SNI attempt
    old = '''            Err(e) => {
                STATS.ws_errors.fetch_add(1, Ordering::Relaxed);
                if let Some(h) = e.handshake() {
                    if h.is_redirect() {
                        ws_failed_redirect = true;
                    } else {
                        all_redirects = false;
                    }
                } else {
                    all_redirects = false;
                }
            }'''
    new = '''            Err(e) => {
                STATS.ws_errors.fetch_add(1, Ordering::Relaxed);
                if let Some(h) = e.handshake() {
                    if h.is_redirect() { ws_failed_redirect = true; } else { all_redirects = false; }
                } else { all_redirects = false; }

                // Retry direct Telegram WS with alternate TLS SNI; HTTP Host remains Telegram domain.
                let front_timeout = Duration::from_secs_f64(timeout.max(1.0));
                match ws_connect_once_with_sni(target, dom, "/apiws", front_timeout, "sprinthost.ru").await {
                    Ok(ws) => {
                        linfo!("direct WS {} connected with alternate SNI", dom);
                        return (Some(ws), ws_failed_redirect, false);
                    }
                    Err(front_err) => {
                        ldebug!("direct WS alternate SNI {} failed: {}", dom, front_err.compact());
                    }
                }
            }'''
    if s.count(old) != 1:
        raise SystemExit(f"direct WS error branch anchor count={s.count(old)}")
    s = s.replace(old, new, 1)
    proxy.write_text(s, encoding="utf-8")

main = root / "app/src/main/java/com/amurcanov/tgwsproxy/MainActivity.kt"
s = main.read_text(encoding="utf-8")
s = s.replace("Array-backed list with cap of 50:", "Array-backed list with cap of 2000:")
s = s.replace("Merges consecutive duplicates and caps at 50 entries.", "Merges consecutive duplicates and caps at 2000 entries.")
s = s.replace("while (result.size > 50)", "while (result.size > 2000)")
main.write_text(s, encoding="utf-8")
print("Applied alternate-SNI retry and 2000-entry log retention.")
