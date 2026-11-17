#!/usr/bin/env python3
"""Apply the TLS 1.2 compatibility/timeout diagnostic patch to the unpacked source."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_tls_diagnostic.py path/to/src/ws.rs")

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")

old_tls = """    let mut cfg = ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();"""
new_tls = """    // Diagnostic compatibility profile: TCP connects, but TLS stalls on both
    // Wi-Fi and mobile. Force TLS 1.2 to rule out TLS 1.3 interoperability issues.
    let mut cfg = ClientConfig::builder_with_protocol_versions(&[&rustls::version::TLS12])
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();"""

old_timeout = """pub fn ws_handshake_timeout(total: Duration) -> Duration {
    if total <= Duration::ZERO {
        Duration::from_secs(3)
    } else if total > Duration::from_secs(3) {
        Duration::from_secs(3)
    } else {
        total
    }
}"""
new_timeout = """pub fn ws_handshake_timeout(total: Duration) -> Duration {
    // Allow slow mobile handshakes; keep the timeout bounded at 8 seconds.
    if total <= Duration::ZERO {
        Duration::from_secs(8)
    } else if total > Duration::from_secs(8) {
        Duration::from_secs(8)
    } else {
        total
    }
}"""

if new_tls not in source:
    if old_tls not in source:
        raise SystemExit("TLS config anchor not found; refusing to patch unknown source")
    source = source.replace(old_tls, new_tls, 1)

if new_timeout not in source:
    if old_timeout not in source:
        raise SystemExit("TLS timeout anchor not found; refusing to patch unknown source")
    source = source.replace(old_timeout, new_timeout, 1)

path.write_text(source, encoding="utf-8")
print("Applied TLS 1.2-only profile and 8-second handshake timeout.")
