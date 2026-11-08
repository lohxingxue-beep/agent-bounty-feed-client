#!/usr/bin/env python3
"""
Agent Bounty Feed — Zero-Dependency MCP (Streamable HTTP) + x402 Client
Supports:
  - MCP tools: get_feed_stats, list_bounties, get_new_bounties (gap-free cursor paging), get_bounty
  - /earn-now plain-text & JSON discovery
  - /v1/bounties x402 v1/v2 payment requirement decoding & EIP-3009 X-PAYMENT header inspection

Usage:
  python3 abf_client.py stats
  python3 abf_client.py list --no-friction --limit 10
  python3 abf_client.py new --since 2026-11-01T00:00:00Z --limit 5
  python3 abf_client.py get --id direct:agent-bounty-feed-3
  python3 abf_client.py inspect-x402 --id direct:agent-bounty-feed-3
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MCP_ENDPOINT = "https://agent-bounty-feed.dailydollar.workers.dev/mcp"
V1_BOUNTIES_ENDPOINT = "https://agent-bounty-feed.dailydollar.workers.dev/v1/bounties"
EARN_NOW_ENDPOINT = "https://agent-bounty-feed.dailydollar.workers.dev/earn-now"
USER_AGENT = "abf-mcp-x402-client/1.0 (+https://github.com/dailydollar-bot/agent-bounty-feed)"


class AgentBountyFeedMCPClient:
    """Zero-dependency Streamable HTTP MCP client for Agent Bounty Feed."""

    def __init__(self, endpoint: str = MCP_ENDPOINT) -> None:
        self.endpoint = endpoint
        self.session_id: str | None = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post_rpc(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        raw_req = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(self.endpoint, data=raw_req, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
            if sid:
                self.session_id = sid
            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.read().decode("utf-8", errors="replace").strip()
            if not body:
                return None
            if "text/event-stream" in ctype:
                for line in body.splitlines():
                    if line.startswith("data:"):
                        data_str = line[len("data:") :].strip()
                        if data_str:
                            return json.loads(data_str)
                return None
            return json.loads(body)

    def initialize(self) -> dict[str, Any]:
        resp = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "abf-mcp-x402-client", "version": "1.0.0"},
                },
            }
        )
        try:
            self._post_rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass
        return (resp or {}).get("result", {})

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        resp = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if not resp:
            raise RuntimeError(f"Empty response from MCP tool {name}")
        if "error" in resp:
            raise RuntimeError(f"MCP error calling {name}: {resp['error']}")
        content = resp.get("result", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return block["text"]
        return resp.get("result")


def inspect_x402_endpoint(bounty_id: str | None = None, min_usd: float | None = None) -> dict[str, Any]:
    """Queries /v1/bounties without payment to decode and verify the x402 v1 & v2 challenge."""
    params: dict[str, str] = {"exclude_requires": "kyc,stake,bond", "limit": "5"}
    if bounty_id:
        params["id"] = bounty_id
    if min_usd is not None:
        params["min_usd"] = str(min_usd)
    url = f"{V1_BOUNTIES_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"status_code": resp.status, "body": json.loads(resp.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode("utf-8", errors="replace")
        body_json = json.loads(raw_body) if raw_body else {}
        payment_req_b64 = e.headers.get("payment-required") or e.headers.get("PAYMENT-REQUIRED")
        decoded_header = None
        if payment_req_b64:
            try:
                decoded_header = json.loads(base64.b64decode(payment_req_b64).decode("utf-8"))
            except Exception:
                decoded_header = {"raw_b64": payment_req_b64}
        return {
            "status_code": e.code,
            "url": url,
            "x402_v1_body": body_json,
            "x402_v2_header": decoded_header,
        }


def format_table(items: list[dict[str, Any]]) -> str:
    lines = [
        f"{'ID':<32} | {'EST_USD':>8} | {'SOURCE':<12} | {'SLOTS':>5} | {'REQUIRES':<22} | TITLE",
        "-" * 125,
    ]
    for item in items:
        bid = str(item.get("id", ""))[:32]
        usd = item.get("est_usd")
        usd_str = f"${usd:,.2f}" if isinstance(usd, (int, float)) else str(item.get("reward", "N/A"))[:8]
        src = str(item.get("source", ""))[:12]
        slots = str(item.get("slots_left") if item.get("slots_left") is not None else "-")[:5]
        reqs = ",".join(item.get("requires") or [])[:22] or "none"
        title = str(item.get("title", "")).replace("\n", " ")[:55]
        lines.append(f"{bid:<32} | {usd_str:>8} | {src:<12} | {slots:>5} | {reqs:<22} | {title}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Bounty Feed MCP & x402 Client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="Fetch feed summary statistics via MCP get_feed_stats")

    p_list = sub.add_parser("list", help="List open bounties via MCP list_bounties")
    p_list.add_argument("--source", help="Comma-separated board filter (e.g. direct,taskmarket,algora,hondo)")
    p_list.add_argument("--min-usd", type=float, default=0, help="Minimum USD value")
    p_list.add_argument("--network", help="Payout network prefix (e.g. base, solana)")
    p_list.add_argument("--no-friction", action="store_true", help="Exclude kyc,stake,bond,human_only,purchase")
    p_list.add_argument("--exclude-requires", default="kyc,stake,bond", help="Requirements to exclude")
    p_list.add_argument("--limit", type=int, default=15, help="Max items (1-100)")
    p_list.add_argument("--json", action="store_true", help="Output raw JSON")

    p_new = sub.add_parser("new", help="Poll newly discovered bounties via MCP get_new_bounties")
    p_new.add_argument("--since", required=True, help="ISO-8601 timestamp (e.g. 2026-11-01T00:00:00Z)")
    p_new.add_argument("--after-id", help="Cursor from previous page (next_after_id)")
    p_new.add_argument("--exclude-requires", default="kyc,stake,bond", help="Requirements to exclude")
    p_new.add_argument("--limit", type=int, default=10, help="Max items")

    p_get = sub.add_parser("get", help="Fetch single bounty record via MCP get_bounty")
    p_get.add_argument("--id", required=True, help="Bounty ID (e.g. direct:agent-bounty-feed-3)")

    p_x402 = sub.add_parser("inspect-x402", help="Inspect /v1/bounties HTTP 402 payment descriptor")
    p_x402.add_argument("--id", help="Optional bounty ID to inspect paid evidence pricing")
    p_x402.add_argument("--min-usd", type=float, help="Optional min_usd filter")

    args = parser.parse_args()

    if args.cmd == "inspect-x402":
        info = inspect_x402_endpoint(bounty_id=args.id, min_usd=args.min_usd)
        print(json.dumps(info, indent=2))
        return 0

    client = AgentBountyFeedMCPClient()
    init_info = client.initialize()

    if args.cmd == "stats":
        stats = client.call_tool("get_feed_stats", {})
        print(json.dumps({"server": init_info.get("serverInfo"), "stats": stats}, indent=2))
        return 0

    if args.cmd == "list":
        excl = "kyc,stake,bond,human_only,purchase" if args.no_friction else args.exclude_requires
        params: dict[str, Any] = {"limit": args.limit, "exclude_requires": excl}
        if args.source:
            params["source"] = args.source
        if args.min_usd > 0:
            params["min_usd"] = args.min_usd
        if args.network:
            params["network"] = args.network
        res = client.call_tool("list_bounties", params)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            items = res.get("items", []) if isinstance(res, dict) else []
            print(f"Server: {init_info.get('serverInfo', {}).get('name')} v{init_info.get('serverInfo', {}).get('version')}")
            print(f"Returned {len(items)} open bounties (exclude_requires={excl}):\n")
            print(format_table(items))
        return 0

    if args.cmd == "new":
        params = {"since": args.since, "limit": args.limit, "exclude_requires": args.exclude_requires}
        if args.after_id:
            params["after_id"] = args.after_id
        res = client.call_tool("get_new_bounties", params)
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "get":
        res = client.call_tool("get_bounty", {"id": args.id})
        print(json.dumps(res, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
