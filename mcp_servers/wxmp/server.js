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
import { applyDevToolsCompat } from "./compat.mjs";
import { parseWxml, formatSnapshot, findByText } from "./wxml.mjs";

const WS_ENDPOINT = process.env.WXMP_WS_ENDPOINT || "ws://localhost:9420";
const PROOF_DIR = process.env.WXMP_PROOF_DIR || process.cwd();
const CONNECT_TIMEOUT = Number(process.env.WXMP_CONNECT_TIMEOUT || 30000);
const FIXTURE_DEFAULTS = process.env.WXMP_FIXTURE_DEFAULTS || "";

/**
 * Mirrors fe_app/packages/e2e/tests/utils/test-router.ts. Tests name routes
 * instead of spelling out paths, per that suite's conventions; tab routes need
 * switchTab rather than navigateTo, which is why the two lists are separate.
 */
const ROUTES = {
  login: "/features/auth/pages/login/index",
  home: "/pages/index/index",
  chat: "/features/chat/pages/index",
  mine: "/features/mine/pages/index",
  luxxTest: "/features/luxx_test/pages/index",
  unregister: "/features/account/pages/unregister/index",
  webview: "/features/webview/pages/index",
};
const TAB_ROUTES = new Set(["home", "chat", "mine"]);

const TOKENS_KEY = "esm_tokens";
const USER_KEY = "esm_user";

const log = (...args) => console.error("[wxmp]", ...args);

applyDevToolsCompat((msg) => log(msg));

/* ------------------------------------------------------------------ *
 * Login profiles
 *
 * Tokens must never travel through the LLM. The model passes an alias
 * ("vip_user"); the real credential is resolved here, in-process.
 * ------------------------------------------------------------------ */

function loadFixtureDefaults() {
  if (!FIXTURE_DEFAULTS) return {};
  try {
    const raw = FIXTURE_DEFAULTS.trim().startsWith("{")
      ? FIXTURE_DEFAULTS
      : fs.readFileSync(FIXTURE_DEFAULTS, "utf-8");
    const parsed = JSON.parse(raw);
    // Accepts the e2e fixture shape ({defaults: {...}}) or a bare defaults object.
    return parsed.defaults ?? parsed;
  } catch (err) {
    log(`failed to load WXMP_FIXTURE_DEFAULTS: ${err.message}`);
    return {};
  }
}

const FIXTURE = loadFixtureDefaults();

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
      "Go to a page by its route name: login, home, chat, mine, luxxTest, " +
      "unregister, webview. Prefer the name over a raw path. Tab pages are " +
      "switched to and other pages are pushed automatically, so you do not " +
      "pick a navigation method. Pass a path starting with / only for a page " +
      "that has no route name.",
    inputSchema: {
      type: "object",
      properties: {
        route: {
          type: "string",
          description: "Route name such as mine, or an absolute path",
        },
        method: {
          type: "string",
          enum: ["auto", "reLaunch", "navigateTo", "redirectTo", "switchTab", "navigateBack"],
          description: "Defaults to auto; override only when a test needs a specific method",
        },
      },
      required: ["route"],
    },
  },
  {
    name: "mp_snapshot",
    description:
      "Show what is on the current page: every visible text and control, with " +
      "the id needed to act on it. Call this before interacting with a page you " +
      "have not inspected yet, and again after an action changes the screen. " +
      "This is the mini program equivalent of looking at the app.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "mp_tap_text",
    description:
      "Tap the element showing this text, e.g. the button labelled '退出登录'. " +
      "Prefer this over mp_tap with a selector: it matches what the user sees " +
      "and does not depend on class names or element order. Run mp_snapshot " +
      "first if you are unsure of the exact wording.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Visible text of the target element" },
        exact: {
          type: "boolean",
          description: "Require the whole text to match; defaults to substring matching",
        },
      },
      required: ["text"],
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
      "Put the app into a known auth state before a test: 'logged-in' seeds the " +
      "signed-in user and lands on the home page, 'guest' clears everything. " +
      "Always start a test with this rather than driving the login screen — " +
      "WeChat's auth dialogs are native and cannot be tapped by automation.",
    inputSchema: {
      type: "object",
      properties: {
        preset: {
          type: "string",
          enum: ["logged-in", "guest"],
          description: "Auth state to set up",
        },
      },
      required: ["preset"],
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
        value: {
          description:
            "Value to store. Objects are stored as-is; the app's own login keys hold objects, not strings.",
        },
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
  async mp_navigate({ route, method = "auto" }) {
    const mp = await getMiniProgram();
    if (method === "navigateBack") {
      await mp.navigateBack();
      return "Navigated back.";
    }
    const isName = Object.prototype.hasOwnProperty.call(ROUTES, route);
    const path = isName ? ROUTES[route] : route;
    if (!path || !path.startsWith("/")) {
      throw new Error(
        `Unknown route "${route}". Use one of ${Object.keys(ROUTES).join(", ")} ` +
          "or an absolute path starting with /."
      );
    }
    const how =
      method !== "auto" ? method : isName && TAB_ROUTES.has(route) ? "switchTab" : "reLaunch";
    await mp[how](path);
    return `${how} -> ${path}`;
  },

  async mp_snapshot() {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const root = await page.$("page");
    if (!root) return `Page ${page.path} exposed no root node.`;
    const nodes = parseWxml(await root.wxml());
    return `Page: ${page.path}\n${formatSnapshot(nodes)}`;
  },

  async mp_tap_text({ text, exact = false }) {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const root = await page.$("page");
    if (!root) throw new Error(`Page ${page.path} exposed no root node.`);
    const nodes = parseWxml(await root.wxml());
    const hits = findByText(nodes, text, exact);
    if (!hits.length) {
      const visible = nodes.filter((n) => n.text).map((n) => n.text);
      throw new Error(
        `No element showing "${text}" on ${page.path}. Visible text: ` +
          (visible.length ? visible.map((t) => `"${t}"`).join(", ") : "(none)")
      );
    }
    const target = hits[0];
    const el = await page.$(`#${target.id}`);
    if (!el) throw new Error(`Element #${target.id} disappeared before it could be tapped.`);
    await el.tap();
    return `Tapped ${target.tag} "${target.text}"`;
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

  async mp_login_as({ preset }) {
    const mp = await getMiniProgram();
    await mp.callWxMethod("clearStorageSync");

    if (preset === "guest") {
      await mp.switchTab(ROUTES.home);
      return "Guest state: storage cleared, on the home page.";
    }

    const tokens = FIXTURE.tokens ?? {};
    const user = FIXTURE.user ?? {};
    if (!Object.keys(tokens).length && !Object.keys(user).length) {
      throw new Error(
        "No fixture defaults loaded, so there is nothing to sign in with. " +
          "Point WXMP_FIXTURE_DEFAULTS at the e2e fixture defaults.json."
      );
    }
    if (Object.keys(tokens).length) await mp.callWxMethod("setStorageSync", TOKENS_KEY, tokens);
    if (Object.keys(user).length) await mp.callWxMethod("setStorageSync", USER_KEY, user);
    for (const [k, v] of Object.entries(FIXTURE.storage ?? {})) {
      await mp.callWxMethod("setStorageSync", k, v);
    }
    await mp.reLaunch(ROUTES.home);
    return `Signed in as ${user.nickname || user.user_id || "the fixture user"}, on the home page.`;
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
log(
  `ready (endpoint=${WS_ENDPOINT}, fixture=${Object.keys(FIXTURE).length ? "loaded" : "none"})`
);
