#!/usr/bin/env node
/**
 * WeChat Mini Program MCP server for TestZeus Hercules.
 *
 * Thin, stateless client: it CONNECTS to an already-running WeChat DevTools
 * instance rather than launching one. Hercules destroys the MCP session after
 * every scenario (MCPHelper.destroy), so this process is spawned and killed
 * once per scenario -- launching DevTools here would restart the IDE on every
 * single test case.
 *
 * Transport is stdio, so stdout belongs to the MCP protocol.
 * All diagnostics go to stderr. Never console.log here.
 */

import fs from "node:fs";
import path from "node:path";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import automator from "miniprogram-automator";

const WS_ENDPOINT = process.env.WXMP_WS_ENDPOINT || "ws://localhost:9420";
const PROOF_DIR = process.env.WXMP_PROOF_DIR || process.cwd();
const CONNECT_TIMEOUT = Number(process.env.WXMP_CONNECT_TIMEOUT || 30000);

const log = (...args) => console.error("[wxmp]", ...args);

/* ------------------------------------------------------------------ *
 * Login profiles
 *
 * Tokens must never travel through the LLM. The model passes an alias
 * ("vip_user"); the real credential is resolved here, in-process.
 * ------------------------------------------------------------------ */

function loadLoginProfiles() {
  const raw = process.env.WXMP_LOGIN_PROFILES;
  if (!raw) return {};
  try {
    // Accept either an inline JSON object or a path to a JSON file.
    if (raw.trim().startsWith("{")) return JSON.parse(raw);
    return JSON.parse(fs.readFileSync(raw, "utf-8"));
  } catch (err) {
    log("failed to load WXMP_LOGIN_PROFILES:", err.message);
    return {};
  }
}

const LOGIN_PROFILES = loadLoginProfiles();

/* ------------------------------------------------------------------ *
 * Connection (lazy singleton)
 * ------------------------------------------------------------------ */

let miniProgram = null;

async function getMiniProgram() {
  if (miniProgram) return miniProgram;
  try {
    miniProgram = await automator.connect({
      wsEndpoint: WS_ENDPOINT,
      timeout: CONNECT_TIMEOUT,
    });
    log("connected to", WS_ENDPOINT);
    return miniProgram;
  } catch (err) {
    throw new Error(
      `Cannot reach WeChat DevTools at ${WS_ENDPOINT}: ${err.message}. ` +
        "Open WeChat DevTools with the project loaded, then enable " +
        "Settings -> Security -> Service Port (CLI/HTTP). This server only " +
        "connects to a running instance; it never launches one."
    );
  }
}

async function requireElement(selector) {
  const mp = await getMiniProgram();
  const page = await mp.currentPage();
  const el = await page.$(selector);
  if (!el) {
    throw new Error(
      `No element matched selector "${selector}" on page ${page.path}. ` +
        "Selectors are WXML/WXSS selectors, not CSS selectors for a browser DOM."
    );
  }
  return el;
}

/* ------------------------------------------------------------------ *
 * Tool schemas
 *
 * Descriptions are read by the LLM, so they carry the usage contract.
 * Hercules exposes each of these as its own StructuredTool with this
 * exact inputSchema (see mcp_help.py::_attach_tools_to_agent).
 * ------------------------------------------------------------------ */

const TOOLS = [
  {
    name: "mp_navigate",
    description:
      "Navigate the mini program to a page route. Use reLaunch to reset the " +
      "page stack (preferred at the start of a test), navigateTo to push a " +
      "page, switchTab for tabBar pages, navigateBack to pop. Route must be " +
      "an absolute path such as /pages/index/index.",
    inputSchema: {
      type: "object",
      properties: {
        method: {
          type: "string",
          enum: ["reLaunch", "navigateTo", "redirectTo", "switchTab", "navigateBack"],
          description: "Navigation method",
        },
        url: {
          type: "string",
          description:
            "Absolute route with optional query, e.g. /pages/detail/detail?id=1. Omit for navigateBack.",
        },
      },
      required: ["method"],
    },
  },
  {
    name: "mp_current_page",
    description:
      "Return the current page route and query parameters. Call this to confirm " +
      "where you are before interacting with elements.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "mp_tap",
    description:
      "Tap an element identified by a WXML selector. This is the mini program " +
      "equivalent of a click.",
    inputSchema: {
      type: "object",
      properties: {
        selector: {
          type: "string",
          description: "WXML selector, e.g. '.submit-btn' or '#login'",
        },
      },
      required: ["selector"],
    },
  },
  {
    name: "mp_input",
    description:
      "Type text into an input or textarea element. Only works on input-like " +
      "components; use mp_tap for buttons.",
    inputSchema: {
      type: "object",
      properties: {
        selector: { type: "string", description: "WXML selector of the input" },
        text: { type: "string", description: "Text to enter" },
      },
      required: ["selector", "text"],
    },
  },
  {
    name: "mp_get_text",
    description:
      "Read the visible text of an element. Use this to assert on-screen copy.",
    inputSchema: {
      type: "object",
      properties: {
        selector: { type: "string", description: "WXML selector" },
      },
      required: ["selector"],
    },
  },
  {
    name: "mp_element_exists",
    description:
      "Check whether an element exists on the current page. Returns true or " +
      "false instead of failing, so it is safe for negative assertions.",
    inputSchema: {
      type: "object",
      properties: {
        selector: { type: "string", description: "WXML selector" },
      },
      required: ["selector"],
    },
  },
  {
    name: "mp_page_data",
    description:
      "Dump the current page's data object as JSON. Useful for asserting on " +
      "state that is not rendered as text.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "mp_call_method",
    description:
      "Call a method defined on the current Page instance, with optional " +
      "positional arguments.",
    inputSchema: {
      type: "object",
      properties: {
        method: { type: "string", description: "Page method name" },
        args: {
          type: "array",
          items: {},
          description: "Positional arguments, defaults to none",
        },
      },
      required: ["method"],
    },
  },
  {
    name: "mp_login_as",
    description:
      "Log in as a preconfigured test user by alias, e.g. 'vip_user'. This " +
      "writes that user's credentials into mini program storage and reloads " +
      "the entry page. Prefer this over driving the real login UI: WeChat " +
      "auth dialogs are native and cannot be tapped by automation. Never pass " +
      "a raw token here -- only the alias.",
    inputSchema: {
      type: "object",
      properties: {
        profile: {
          type: "string",
          description: "Alias of a configured login profile",
        },
      },
      required: ["profile"],
    },
  },
  {
    name: "mp_set_storage",
    description:
      "Write a key into mini program local storage (wx.setStorageSync). Do not " +
      "use this for credentials -- use mp_login_as so secrets stay out of the " +
      "conversation.",
    inputSchema: {
      type: "object",
      properties: {
        key: { type: "string", description: "Storage key" },
        value: { type: "string", description: "Value to store" },
      },
      required: ["key", "value"],
    },
  },
  {
    name: "mp_get_storage",
    description: "Read a key from mini program local storage (wx.getStorageSync).",
    inputSchema: {
      type: "object",
      properties: { key: { type: "string", description: "Storage key" } },
      required: ["key"],
    },
  },
  {
    name: "mp_clear_storage",
    description:
      "Clear all mini program local storage (wx.clearStorageSync). Use to reset " +
      "login state to a logged-out baseline.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "mp_mock_wx",
    description:
      "Replace a wx API's return value for this session, e.g. mock 'login' or " +
      "'getUserProfile'. Required to get past native WeChat permission dialogs, " +
      "which automation cannot tap.",
    inputSchema: {
      type: "object",
      properties: {
        method: {
          type: "string",
          description: "wx API name without the wx. prefix, e.g. login",
        },
        result: {
          type: "object",
          description: "JSON object the mocked API should return",
        },
      },
      required: ["method", "result"],
    },
  },
  {
    name: "mp_restore_wx",
    description: "Undo a previous mp_mock_wx and restore the real wx API.",
    inputSchema: {
      type: "object",
      properties: { method: { type: "string", description: "wx API name" } },
      required: ["method"],
    },
  },
  {
    name: "mp_wait",
    description:
      "Wait for a selector to appear, or for a fixed number of milliseconds. " +
      "Prefer waiting on a selector over a fixed delay.",
    inputSchema: {
      type: "object",
      properties: {
        selector: { type: "string", description: "WXML selector to wait for" },
        ms: { type: "number", description: "Milliseconds to wait instead" },
      },
    },
  },
  {
    name: "mp_screenshot",
    description:
      "Capture a screenshot of the simulator as execution evidence. Call this " +
      "after meaningful steps and before assertions.",
    inputSchema: {
      type: "object",
      properties: {
        name: {
          type: "string",
          description: "Short slug used as the file name, e.g. after_login",
        },
      },
      required: ["name"],
    },
  },
  {
    name: "mp_webview_url",
    description:
      "Return the URL loaded by the <web-view> component on the current page, " +
      "or 'none' if the page has no web-view. Automation cannot reach inside a " +
      "web-view -- the embedded H5 is a separate browser context. Use this to " +
      "read the live URL (it usually carries signed, short-lived query params) " +
      "and then hand that URL to the browser agent to test the H5 page on its " +
      "own. Do not try to tap or read elements inside the web-view.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "mp_evaluate",
    description:
      "Escape hatch: evaluate a JavaScript expression inside the mini program " +
      "logic layer and return the result. Has access to wx and getApp(). Use " +
      "only when no dedicated tool fits.",
    inputSchema: {
      type: "object",
      properties: {
        expression: {
          type: "string",
          description: "JS expression, e.g. getApp().globalData.userId",
        },
      },
      required: ["expression"],
    },
  },
];

/* ------------------------------------------------------------------ *
 * Handlers
 * ------------------------------------------------------------------ */

const handlers = {
  async mp_navigate({ method, url }) {
    const mp = await getMiniProgram();
    if (method === "navigateBack") {
      await mp.navigateBack();
      return "Navigated back.";
    }
    if (!url) throw new Error(`${method} requires a url.`);
    await mp[method](url);
    return `${method} -> ${url}`;
  },

  async mp_current_page() {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    return JSON.stringify({ path: page.path, query: page.query });
  },

  async mp_tap({ selector }) {
    const el = await requireElement(selector);
    await el.tap();
    return `Tapped ${selector}`;
  },

  async mp_input({ selector, text }) {
    const el = await requireElement(selector);
    await el.input(text);
    return `Entered text into ${selector}`;
  },

  async mp_get_text({ selector }) {
    const el = await requireElement(selector);
    return await el.text();
  },

  async mp_element_exists({ selector }) {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const el = await page.$(selector);
    return el ? "true" : "false";
  },

  async mp_page_data() {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    return JSON.stringify(await page.data());
  },

  async mp_call_method({ method, args = [] }) {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const result = await page.callMethod(method, ...args);
    return JSON.stringify(result ?? null);
  },

  async mp_login_as({ profile }) {
    const conf = LOGIN_PROFILES[profile];
    if (!conf) {
      const known = Object.keys(LOGIN_PROFILES);
      throw new Error(
        `Unknown login profile "${profile}". Configured profiles: ` +
          (known.length ? known.join(", ") : "(none -- set WXMP_LOGIN_PROFILES)")
      );
    }
    const mp = await getMiniProgram();
    await mp.callWxMethod("clearStorageSync");
    for (const [key, value] of Object.entries(conf.storage || {})) {
      await mp.callWxMethod("setStorageSync", key, value);
    }
    const entry = conf.relaunch || "/pages/index/index";
    await mp.reLaunch(entry);
    // Deliberately does not echo the credential values back.
    return `Logged in as "${profile}" (${
      Object.keys(conf.storage || {}).length
    } storage keys set), relaunched at ${entry}`;
  },

  async mp_set_storage({ key, value }) {
    const mp = await getMiniProgram();
    await mp.callWxMethod("setStorageSync", key, value);
    return `Set storage ${key}`;
  },

  async mp_get_storage({ key }) {
    const mp = await getMiniProgram();
    const value = await mp.callWxMethod("getStorageSync", key);
    return JSON.stringify(value ?? null);
  },

  async mp_clear_storage() {
    const mp = await getMiniProgram();
    await mp.callWxMethod("clearStorageSync");
    return "Storage cleared.";
  },

  async mp_mock_wx({ method, result }) {
    const mp = await getMiniProgram();
    await mp.mockWxMethod(method, result);
    return `Mocked wx.${method}`;
  },

  async mp_restore_wx({ method }) {
    const mp = await getMiniProgram();
    await mp.restoreWxMethod(method);
    return `Restored wx.${method}`;
  },

  async mp_wait({ selector, ms }) {
    const mp = await getMiniProgram();
    if (selector) {
      const page = await mp.currentPage();
      await page.waitFor(selector);
      return `Waited for ${selector}`;
    }
    await mp.waitFor(ms ?? 500);
    return `Waited ${ms ?? 500}ms`;
  },

  async mp_screenshot({ name }) {
    const mp = await getMiniProgram();
    const safe = String(name).replace(/[^a-zA-Z0-9_-]/g, "_");
    fs.mkdirSync(PROOF_DIR, { recursive: true });
    const file = path.join(PROOF_DIR, `wxmp_${safe}.png`);
    await mp.screenshot({ path: file });
    return `Screenshot saved: ${file}`;
  },

  async mp_webview_url() {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const el = await page.$("web-view");
    if (!el) return "none";
    // The element itself is queryable as ordinary WXML; only its *content*
    // is out of reach. src is what bridges to the browser agent.
    const src = await el.attribute("src");
    return src || "none";
  },

  async mp_evaluate({ expression }) {
    const mp = await getMiniProgram();
    const result = await mp.evaluate(
      function (code) {
        // eslint-disable-next-line no-eval
        return eval(code);
      },
      expression
    );
    return JSON.stringify(result ?? null);
  },
};

/* ------------------------------------------------------------------ *
 * Wiring
 * ------------------------------------------------------------------ */

const server = new Server(
  { name: "wxmp", version: "0.1.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args = {} } = request.params;
  const handler = handlers[name];
  if (!handler) {
    return {
      isError: true,
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
    };
  }
  try {
    const text = await handler(args);
    return { content: [{ type: "text", text: String(text) }] };
  } catch (err) {
    log(`${name} failed:`, err.message);
    return {
      isError: true,
      content: [{ type: "text", text: `${name} failed: ${err.message}` }],
    };
  }
});

async function shutdown() {
  if (miniProgram) {
    try {
      await miniProgram.disconnect();
    } catch {
      /* DevTools may already be gone; nothing useful to do here. */
    }
  }
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

await server.connect(new StdioServerTransport());
log(`ready (endpoint=${WS_ENDPOINT}, profiles=${Object.keys(LOGIN_PROFILES).length})`);
