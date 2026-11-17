#!/usr/bin/env python3
from pathlib import Path
import sys
root = Path(sys.argv[1])

def once(path, old, new, label):
    s = path.read_text(encoding="utf-8")
    if new in s:
        return s
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    s = s.replace(old, new, 1)
    path.write_text(s, encoding="utf-8")
    return s

ws = root / "src/ws.rs"
s = ws.read_text(encoding="utf-8")
if "pub async fn ws_connect_once_with_sni(" not in s:
    # The diagnostic patch splits the original function into a wrapper and a phase-timeout implementation.
    old_call = """dial_addr, domain, path, timeout, tls_timeout, timeout, timeout,
    ).await"""
    new_call = """dial_addr, domain, path, timeout, tls_timeout, timeout, timeout, domain,
    ).await"""
    once(ws, old_call, new_call, "pass default SNI")
    s = ws.read_text(encoding="utf-8")
    old_sig = """    http_response_timeout: Duration,
) -> Result<RawWebSocket, WsError> {"""
    new_sig = """    http_response_timeout: Duration,
    tls_sni: &str,
) -> Result<RawWebSocket, WsError> {"""
    once(ws, old_sig, new_sig, "phase function SNI parameter")
    s = ws.read_text(encoding="utf-8")
    s = s.replace("let sni = server_name(domain);", "let sni = server_name(tls_sni);", 1)
    ws.write_text(s, encoding="utf-8")
    # Add a public wrapper for the alternate SNI attempt.
    marker = "pub async fn ws_connect_once_with_phase_timeouts("
    helper = """/// Connect with an alternate TLS SNI while preserving the Telegram HTTP Host header.
pub async fn ws_connect_once_with_sni(
    dial_addr: &str,
    domain: &str,
    path: &str,
    timeout: Duration,
    tls_sni: &str,
) -> Result<RawWebSocket, WsError> {
    let tls_timeout = ws_handshake_timeout(timeout);
    ws_connect_once_with_phase_timeouts(
        dial_addr, domain, path, timeout, tls_timeout, timeout, timeout, tls_sni,
    ).await
}

"""
    s = ws.read_text(encoding="utf-8")
    if s.count(marker) != 1:
        raise SystemExit("phase function insertion point not found")
    ws.write_text(s.replace(marker, helper + marker, 1), encoding="utf-8")

proxy = root / "src/proxy.rs"
s = proxy.read_text(encoding="utf-8")
if "ws_connect_once_with_sni" not in s:
    old = """            Err(e) => {
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
            }"""
    new = """            Err(e) => {
                STATS.ws_errors.fetch_add(1, Ordering::Relaxed);
                if let Some(h) = e.handshake() {
                    if h.is_redirect() { ws_failed_redirect = true; } else { all_redirects = false; }
                } else { all_redirects = false; }

                // Retry direct Telegram WS using the desktop project's alternate SNI.
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
            }"""
    if s.count(old) != 1:
        raise SystemExit(f"direct WS error branch anchor count={s.count(old)}")
    proxy.write_text(s.replace(old, new, 1), encoding="utf-8")

main = root / "app/src/main/java/com/amurcanov/tgwsproxy/MainActivity.kt"
s = main.read_text(encoding="utf-8")
s = s.replace("Array-backed list with cap of 50:", "Array-backed list with cap of 2000:")
s = s.replace("Merges consecutive duplicates and caps at 50 entries.", "Merges consecutive duplicates and caps at 2000 entries.")
s = s.replace("while (result.size > 50)", "while (result.size > 2000)")
main.write_text(s, encoding="utf-8")
print("Applied alternate-SNI retry and 2000-entry log retention.")
