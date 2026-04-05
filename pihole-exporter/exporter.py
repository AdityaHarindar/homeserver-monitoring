#!/usr/bin/env python3
"""
Pi-hole v6 Prometheus exporter.

Pi-hole v6 switched from a static API token to session-based auth:
  POST /api/auth {"password": "..."} -> {"session": {"sid": "...", "validity": 1800}}
  Then pass `sid` header on every subsequent request.

This exporter handles session management and exposes metrics on /metrics.
"""

import os
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib import request, error
from urllib.parse import urlencode
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PIHOLE_HOST = os.environ.get("PIHOLE_HOST", "localhost")
PIHOLE_PORT = os.environ.get("PIHOLE_PORT", "80")
PIHOLE_PASSWORD = os.environ.get("PIHOLE_PASSWORD", "")
EXPORTER_PORT = int(os.environ.get("PORT", "9617"))
BASE_URL = f"http://{PIHOLE_HOST}:{PIHOLE_PORT}"

# Session state (shared, protected by a lock)
_lock = threading.Lock()
_sid = None
_sid_expires = 0  # unix timestamp


def _api(path, method="GET", body=None, headers=None):
    headers = headers or {}
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} on {path}: {e.read().decode()[:200]}")


def _authenticate():
    global _sid, _sid_expires
    if not PIHOLE_PASSWORD:
        log.warning("PIHOLE_PASSWORD not set — Pi-hole API metrics will be unavailable")
        return None
    log.info("Authenticating with Pi-hole at %s", BASE_URL)
    resp = _api("/api/auth", method="POST", body={"password": PIHOLE_PASSWORD})
    session = resp.get("session", {})
    if not session.get("valid"):
        raise RuntimeError(f"Pi-hole auth rejected: {session.get('message')}")
    _sid = session["sid"]
    validity = session.get("validity", 1800)
    _sid_expires = time.time() + validity - 30  # 30s safety margin
    log.info("Authenticated, session valid for %ds", validity)
    return _sid


def _get_sid():
    global _sid, _sid_expires
    with _lock:
        if _sid is None or time.time() >= _sid_expires:
            _authenticate()
        return _sid


def _fetch(path):
    sid = _get_sid()
    if sid is None:
        return None
    try:
        return _api(path, headers={"sid": sid})
    except RuntimeError as e:
        # Session may have expired mid-flight — re-auth once and retry
        if "401" in str(e) or "403" in str(e):
            log.warning("Session expired mid-request, re-authenticating")
            with _lock:
                _authenticate()
            return _api(path, headers={"sid": _get_sid()})
        raise


def collect_metrics():
    metrics = {}
    try:
        summary = _fetch("/api/stats/summary")
        blocking = _fetch("/api/dns/blocking")
    except Exception as e:
        log.error("Failed to fetch Pi-hole stats: %s", e)
        metrics["pihole_up"] = 0
        return metrics

    metrics["pihole_up"] = 1

    q = summary.get("queries", {})
    metrics["pihole_dns_queries_total"] = q.get("total", 0)
    metrics["pihole_ads_blocked_today"] = q.get("blocked", 0)
    metrics["pihole_ads_percentage_today"] = q.get("percent_blocked", 0.0)
    metrics["pihole_queries_forwarded"] = q.get("forwarded", 0)
    metrics["pihole_queries_cached"] = q.get("cached", 0)
    metrics["pihole_unique_domains"] = q.get("unique_domains", 0)
    metrics["pihole_query_frequency"] = q.get("frequency", 0.0)

    clients = summary.get("clients", {})
    metrics["pihole_unique_clients"] = clients.get("active", 0)
    metrics["pihole_clients_ever_seen"] = clients.get("total", 0)

    gravity = summary.get("gravity", {})
    metrics["pihole_domains_being_blocked"] = gravity.get("domains_being_blocked", 0)

    b = blocking.get("blocking", "unknown") if blocking else "unknown"
    metrics["pihole_status"] = 1 if b == "enabled" else 0

    return metrics


HELP = {
    "pihole_up":                    ("gauge", "1 if Pi-hole is reachable and auth succeeded"),
    "pihole_dns_queries_total":     ("gauge", "Total DNS queries today"),
    "pihole_ads_blocked_today":     ("gauge", "DNS queries blocked today"),
    "pihole_ads_percentage_today":  ("gauge", "Percentage of queries blocked today"),
    "pihole_queries_forwarded":     ("gauge", "DNS queries forwarded to upstream today"),
    "pihole_queries_cached":        ("gauge", "DNS queries answered from cache today"),
    "pihole_unique_domains":        ("gauge", "Unique domains seen today"),
    "pihole_query_frequency":       ("gauge", "DNS queries per minute (recent)"),
    "pihole_unique_clients":        ("gauge", "Unique active clients today"),
    "pihole_clients_ever_seen":     ("gauge", "Total clients ever seen"),
    "pihole_domains_being_blocked": ("gauge", "Total domains on the blocklist"),
    "pihole_status":                ("gauge", "1 if Pi-hole blocking is enabled, 0 if disabled"),
}


def render_metrics(metrics):
    lines = []
    for name, value in metrics.items():
        if name in HELP:
            mtype, help_text = HELP[name]
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request access logs

    def do_GET(self):
        if self.path == "/metrics":
            metrics = collect_metrics()
            body = render_metrics(metrics).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    log.info("Pi-hole exporter starting on :%d", EXPORTER_PORT)
    log.info("Targeting Pi-hole at %s", BASE_URL)
    # Authenticate eagerly on startup so first scrape is fast
    try:
        _authenticate()
    except Exception as e:
        log.warning("Initial auth failed (will retry on first scrape): %s", e)
    server = HTTPServer(("0.0.0.0", EXPORTER_PORT), Handler)
    log.info("Listening")
    server.serve_forever()
