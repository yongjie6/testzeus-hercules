#!/usr/bin/env node
/**
 * Protocol smoke test: spawns server.js over stdio, performs the MCP
 * handshake, and lists tools. Does NOT need WeChat DevTools running --
 * the automator connection is lazy, so this only proves the MCP layer
 * is wired correctly.
 *
 *   node smoke.mjs
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const child = spawn("node", [path.join(here, "server.js")], {
  stdio: ["pipe", "pipe", "inherit"],
});

const send = (msg) => child.stdin.write(JSON.stringify(msg) + "\n");
let buffer = "";
let failed = false;

child.stdout.on("data", (chunk) => {
  buffer += chunk.toString();
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;
    const msg = JSON.parse(line);

    if (msg.id === 1) {
      console.log("initialize ok:", msg.result.serverInfo);
      send({ jsonrpc: "2.0", method: "notifications/initialized" });
      send({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
    } else if (msg.id === 2) {
      const tools = msg.result.tools;
      console.log(`tools/list ok: ${tools.length} tools`);
      for (const t of tools) {
        const required = t.inputSchema.required || [];
        console.log(`  - ${t.name}(${required.join(", ")})`);
        if (!t.description || !t.inputSchema) {
          console.error(`    !! ${t.name} is missing description or schema`);
          failed = true;
        }
      }
      // Calling a tool without DevTools running must fail cleanly, not hang.
      send({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: { name: "mp_current_page", arguments: {} },
      });
    } else if (msg.id === 3) {
      const text = msg.result.content[0].text;
      // DevTools may or may not be running. Either a real page or an
      // actionable connection error is a pass; anything else is not.
      const connected = !msg.result.isError && /path/.test(text);
      const cleanError = msg.result.isError && /DevTools/.test(text);
      console.log(
        connected
          ? `tools/call ok: DevTools reachable, ${text}`
          : cleanError
            ? "tools/call ok: DevTools absent, actionable error returned"
            : `tools/call UNEXPECTED: ${text}`
      );
      if (!connected && !cleanError) failed = true;
      child.kill();
      process.exit(failed ? 1 : 0);
    }
  }
});

send({
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion: "2024-11-05",
    capabilities: {},
    clientInfo: { name: "smoke", version: "1" },
  },
});

setTimeout(() => {
  console.error("timed out");
  child.kill();
  process.exit(1);
}, 60000);
