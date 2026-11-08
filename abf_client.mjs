#!/usr/bin/env node
/**
 * Agent Bounty Feed — Zero-Dependency Node.js 18+ MCP & x402 Client
 * Usage:
 *   node abf_client.mjs list 10
 *   node abf_client.mjs stats
 *   node abf_client.mjs get direct:abf-growth-guide-5usdc
 *   node abf_client.mjs inspect-x402 direct:abf-growth-guide-5usdc
 */

const MCP_URL = "https://agent-bounty-feed.dailydollar.workers.dev/mcp";
const X402_URL = "https://agent-bounty-feed.dailydollar.workers.dev/v1/bounties";
const UA = "abf-mcp-x402-client-node/1.0 (+https://github.com/dailydollar-bot/agent-bounty-feed)";

async function mcpCall(method, params = {}, id = 1, sessionId = null) {
  const headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "User-Agent": UA,
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;
  const body = id !== null
    ? JSON.stringify({ jsonrpc: "2.0", id, method, params })
    : JSON.stringify({ jsonrpc: "2.0", method, params });

  const res = await fetch(MCP_URL, { method: "POST", headers, body });
  const newSession = res.headers.get("mcp-session-id") || sessionId;
  const text = await res.text();
  if (!text.trim()) return { data: null, sessionId: newSession };

  const ctype = (res.headers.get("content-type") || "").toLowerCase();
  if (ctype.includes("text/event-stream")) {
    for (const line of text.split(/\r?\n/)) {
      if (line.startsWith("data:")) {
        const payload = line.slice(5).trim();
        if (payload) return { data: JSON.parse(payload), sessionId: newSession };
      }
    }
  }
  return { data: JSON.parse(text), sessionId: newSession };
}

async function run() {
  const [cmd = "list", arg1 = "10"] = process.argv.slice(2);

  if (cmd === "inspect-x402") {
    const url = new URL(X402_URL);
    url.searchParams.set("exclude_requires", "kyc,stake,bond");
    if (arg1 && arg1 !== "10") url.searchParams.set("id", arg1);
    const res = await fetch(url, { headers: { "User-Agent": UA } });
    const payHeader = res.headers.get("payment-required");
    const v2 = payHeader ? JSON.parse(Buffer.from(payHeader, "base64").toString("utf8")) : null;
    const v1 = await res.json();
    console.log(JSON.stringify({ status: res.status, x402_v1: v1, x402_v2_accepts: v2?.accepts }, null, 2));
    return;
  }

  const init = await mcpCall("initialize", {
    protocolVersion: "2025-03-26",
    capabilities: {},
    clientInfo: { name: "abf-mcp-x402-node", version: "1.0.0" },
  }, 1);
  const sid = init.sessionId;
  await mcpCall("notifications/initialized", {}, null, sid);

  let toolName = "list_bounties";
  let toolArgs = { limit: Number(arg1) || 10, exclude_requires: "kyc,stake,bond,human_only,purchase" };
  if (cmd === "stats") {
    toolName = "get_feed_stats";
    toolArgs = {};
  } else if (cmd === "get") {
    toolName = "get_bounty";
    toolArgs = { id: arg1 };
  }

  const out = await mcpCall("tools/call", { name: toolName, arguments: toolArgs }, 2, sid);
  const textBlock = out.data?.result?.content?.find((c) => c.type === "text")?.text;
  console.log(textBlock ? JSON.stringify(JSON.parse(textBlock), null, 2) : JSON.stringify(out.data, null, 2));
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
