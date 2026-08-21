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
import { parseWxml, formatSnapshot, findByText, findInputs } from "./wxml.mjs";

/**
 * Config comes from CLI flags first, environment second.
 *
 * The flags exist because environment variables do not survive the MCP stdio
 * boundary: the Python SDK's get_default_environment() forwards only HOME,
 * LOGNAME, PATH, SHELL and USER to the child, and Hercules' server config
 * (mcp_help.py) has no `env` field to override that. So anything this server
 * needs has to travel in argv, spelled out in mcp_servers.json.
 */
function flag(name, fallback = "") {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && process.argv[i + 1] && !process.argv[i + 1].startsWith("--")) {
    return process.argv[i + 1];
  }
  return fallback;
}

const WS_ENDPOINT = flag("endpoint", process.env.WXMP_WS_ENDPOINT || "ws://localhost:9420");
const PROOF_DIR = flag("proof-dir", process.env.WXMP_PROOF_DIR || process.cwd());
const CONNECT_TIMEOUT = Number(flag("connect-timeout", process.env.WXMP_CONNECT_TIMEOUT || 30000));
const FIXTURE_DEFAULTS = flag("fixture", process.env.WXMP_FIXTURE_DEFAULTS || "");
// The mock gateway is long-lived here (gateway.mts), so a case cannot get a
// different account state by restarting it the way the vitest suite does.
// Its control port swaps the fixture in place instead; mp_use_fixture calls it.
const GATEWAY_CONTROL = flag(
  "gateway-control",
  process.env.WXMP_GATEWAY_CONTROL || "http://127.0.0.1:39102"
);
// Browser runs get a screenshot per step from PlaywrightManager; mini program
// runs had evidence only when a case asked for it, so reports came back with
// nothing to look at. Capture around actions here instead, which keeps the
// evidence chain without every case having to spell it out.
const AUTO_SHOT = flag("auto-screenshot", process.env.WXMP_AUTO_SCREENSHOT || "on") !== "off";

/** Tools that change what is on screen and are therefore worth capturing. */
const CAPTURED = new Set([
  "mp_login_as",
  "mp_navigate",
  "mp_tap_text",
  "mp_tap",
  "mp_input_text",
  "mp_input",
  "mp_swipe",
  "mp_trigger",
  "mp_long_press",
  "mp_auth_phone",
]);

let shotSeq = 0;
// Hercules respawns this server once per scenario, so a sequence number alone
// restarts at 1 every time and later scenarios overwrite earlier screenshots.
// Stamping the process start keeps each scenario's evidence separate.
const RUN_STAMP = new Date()
  .toISOString()
  .replace(/[-:T]/g, "")
  .slice(4, 13);

async function captureAfter(toolName) {
  if (!AUTO_SHOT || !CAPTURED.has(toolName)) return null;
  try {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const slug = String(page.path).replace(/[^a-zA-Z0-9]/g, "_").slice(-40);
    shotSeq += 1;
    const name = `${RUN_STAMP}_${String(shotSeq).padStart(3, "0")}_${toolName}_${slug}.png`;
    fs.mkdirSync(PROOF_DIR, { recursive: true });
    await mp.screenshot({ path: path.join(PROOF_DIR, name) });
    return name;
  } catch (err) {
    // Evidence is a nice-to-have; never fail the step over it.
    log(`auto screenshot after ${toolName} failed: ${err.message}`);
    return null;
  }
}

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

/**
 * Chinese aliases so scenarios can be written the way the app labels things.
 * The tab names come from the bundle's app.json tabBar; the rest match the
 * wording used in the UI. Without these a model has to translate "我的" to
 * "mine" on its own, which it usually gets right and occasionally does not --
 * not a good trade for test assets that must behave the same every run.
 */
const ROUTE_ALIASES = {
  首页: "home",
  主页: "home",
  对话: "chat",
  聊天: "chat",
  我的: "mine",
  个人中心: "mine",
  登录: "login",
  登录页: "login",
  量表: "luxxTest",
  注销: "unregister",
  注销账号: "unregister",
};

function resolveRoute(input) {
  const key = ROUTE_ALIASES[input] ?? input;
  if (Object.prototype.hasOwnProperty.call(ROUTES, key)) {
    return { key, path: ROUTES[key], named: true };
  }
  if (typeof input === "string" && input.startsWith("/")) {
    return { key: null, path: input, named: false };
  }
  return null;
}

// Gesture tuning, both settled by repeated runs against the app: a single
// touchmove almost never registers, and more than a handful stretches the
// gesture until it reads as a slow pan instead of a swipe.
const SWIPE_STEPS = 5;
const SWIPE_ATTEMPTS = 4;
// A held touch is as unreliable as a dragged one: the same hold lands as a
// long press on one run and as a plain tap on the next, so it retries too.
const PRESS_ATTEMPTS = 4;

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
    log(`failed to load fixture defaults: ${err.message}`);
    return {};
  }
}

// Reassigned by mp_use_fixture: a scene can override the user the login state
// is seeded from, and seeding a base user against a scene's backend data is
// exactly the kind of mismatch that fails far away from its cause.
let FIXTURE = loadFixtureDefaults();

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

/** Current page plus its parsed nodes; one round trip for the wxml. */
async function readPage() {
  const mp = await getMiniProgram();
  const page = await mp.currentPage();
  const root = await page.$("page");
  if (!root) throw new Error(`Page ${page.path} exposed no root node.`);
  return { page, nodes: parseWxml(await root.wxml()) };
}

/** Resolve the element showing `text`, or explain what is on screen instead. */
async function elementShowing(text, exact = false) {
  const { page, nodes } = await readPage();
  const hits = findByText(nodes, text, exact);
  if (!hits.length) {
    const visible = nodes
      .filter((n) => n.text || n.label)
      .map((n) => (n.text ? `"${n.text}"` : `"${n.label}"(icon)`));
    throw new Error(
      `No element showing "${text}" on ${page.path}. Visible text: ` +
        (visible.length ? visible.join(", ") : "(none)")
    );
  }
  const target = hits[0];
  const el = await page.$(`#${target.id}`);
  if (!el) throw new Error(`Element #${target.id} disappeared before it could be used.`);
  return { page, el, target };
}

/**
 * Resolve a target by visible text, or -- for a field that has none -- by its
 * placeholder. Inputs are the one control a user reads by placeholder rather
 * than by text, so events aimed at them (blur, focus) were unreachable while
 * text was the only way to name a target.
 */
async function elementTarget({ text, into }) {
  if (text) return elementShowing(text);
  const { page, nodes } = await readPage();
  const matches = findInputs(nodes, into || "");
  if (!matches.length) {
    const known = findInputs(nodes)
      .map((n) => (n.placeholder ? `"${n.placeholder}"` : `<${n.tag}>`))
      .join(", ");
    throw new Error(
      `No input matching "${into}" on ${page.path}. Inputs here: ${known || "(none)"}`
    );
  }
  if (!into && matches.length > 1) {
    const known = matches.map((n) => `"${n.placeholder || n.id}"`).join(", ");
    throw new Error(
      `${matches.length} inputs on ${page.path}; say which one via 'into'. Options: ${known}`
    );
  }
  const target = matches[0];
  const el = await page.$(`#${target.id}`);
  if (!el) throw new Error(`Input #${target.id} disappeared before it could be used.`);
  return { page, el, target };
}

const describeTarget = (target) =>
  target.text || target.label || (target.placeholder ? `"${target.placeholder}"` : target.tag);

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
      "unregister, webview. Chinese names also work: 首页, 对话, 我的, 登录, " +
      "量表, 注销账号. Prefer the name over a raw path. Tab pages are " +
      "switched to and other pages are pushed automatically, so you do not " +
      "pick a navigation method. Pass a path starting with / only for a page " +
      "that has no route name.",
    inputSchema: {
      type: "object",
      properties: {
        route: {
          type: "string",
          description: "Route name such as mine or 我的, or an absolute path",
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
      "Icon-only buttons match on their accessible name instead, which snapshot " +
      "shows as (icon) -- '复制' or '会话' reach them the same way. " +
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
    name: "mp_input_text",
    description:
      "Type into an input or textarea. Identify the field the way a user would: " +
      "pass `into` with its placeholder or the label next to it. If the page has " +
      "exactly one input you may omit `into`. Run mp_snapshot first to see the " +
      "fields and their placeholders.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Text to type" },
        into: {
          type: "string",
          description: "Placeholder or nearby label of the target field",
        },
      },
      required: ["text"],
    },
  },
  {
    name: "mp_trigger",
    description:
      "Fire a component event on an element, e.g. a custom event a component " +
      "listens for, or blur on a field to dismiss the keyboard. Name the target " +
      "with `text` when it shows text, or with `into` (its placeholder) when it " +
      "is an input, which shows none. Use the ordinary tools first: mp_tap_text " +
      "for taps, mp_input_text for typing, mp_swipe for gestures, mp_long_press " +
      "for a press-and-hold. This is for events those do not cover.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Visible text of the target element" },
        into: {
          type: "string",
          description:
            "Placeholder of the target input, for fields that show no text of " +
            "their own. Give this or `text`.",
        },
        event: {
          type: "string",
          description: "Event name without the bind prefix, e.g. blur, focus, change",
        },
        detail: { type: "object", description: "Event detail payload, optional" },
      },
      required: ["event"],
    },
  },
  {
    name: "mp_long_press",
    description:
      "Press and hold the element showing this text, for menus and actions " +
      "bound to a long press. This holds the touch for real time rather than " +
      "announcing the event, so a component that decides on duration sees what " +
      "a finger would. WeChat's own longpress fires at 350ms, so a hold shorter " +
      "than that is how you check a press is NOT treated as a long one.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Visible text of the target element" },
        duration: {
          type: "number",
          description: "Milliseconds to hold, defaults to 400 (WeChat fires at 350)",
        },
        until: {
          type: "string",
          description:
            "Text the press is meant to reveal, e.g. '重命名'. Strongly " +
            "recommended: the same hold lands as a long press on one run and " +
            "as a tap on the next, and with this the press is repeated until " +
            "the text appears. Leave it off only when checking that a SHORT " +
            "hold is not treated as a long press.",
        },
      },
      required: ["text"],
    },
  },
  {
    name: "mp_use_fixture",
    description:
      "Switch the backend data the app talks to, by naming a fixture scene such " +
      "as 'TC-AUTH-004' or 'base'. Use this when a case needs an account or " +
      "server state the default data does not have -- an expired token, an empty " +
      "list, a quota that is used up, an error response. Call it BEFORE " +
      "mp_login_as, because the login state is seeded from the scene's data. " +
      "With no argument it reports the current scene and every scene available.",
    inputSchema: {
      type: "object",
      properties: {
        scene: {
          type: "string",
          description: "Fixture scene name; omit to just report what is loaded",
        },
      },
    },
  },
  {
    name: "mp_swipe",
    description:
      "Swipe, for gestures such as sliding a list row open or revealing a side " +
      "panel. Target it with `text` when the element shows text, or `selector` " +
      "for the layout container a page-level gesture is bound to; with neither, " +
      "the page root is used. Mini programs have no real pointer, so this plays " +
      "a touch sequence the component sees as a swipe. Pass `until` with the " +
      "text the gesture should reveal so it can retry, because the gesture is " +
      "not reliable on a single attempt.",
    inputSchema: {
      type: "object",
      properties: {
        text: {
          type: "string",
          description: "Visible text of the element to swipe on",
        },
        selector: {
          type: "string",
          description:
            "WXML selector of the container to swipe, e.g. '.chat-page'. Gestures " +
            "are usually bound to a layout container with no text of its own, and " +
            "the compiled markup does not say which one, so naming it is allowed " +
            "here even though selectors are avoided elsewhere.",
        },
        direction: {
          type: "string",
          enum: ["left", "right", "up", "down"],
          description: "Swipe direction",
        },
        distance: {
          type: "number",
          description: "Pixels to travel, defaults to 80",
        },
        until: {
          type: "string",
          description:
            "Text the swipe is meant to reveal, e.g. '对话列表'. Strongly " +
            "recommended: the gesture only lands about 7 times in 8, and with " +
            "this the swipe is repeated until the text appears.",
        },
      },
      required: ["direction"],
    },
  },
  {
    name: "mp_auth_phone",
    description:
      "Complete WeChat's phone number authorisation on the button showing this " +
      "text. The real dialog is native and cannot be tapped, so this delivers " +
      "the result the button's handler expects.",
    inputSchema: {
      type: "object",
      properties: {
        text: {
          type: "string",
          description: "Button text, e.g. 授权手机号快捷登录",
        },
        code: { type: "string", description: "Auth code to return, defaults to a test code" },
      },
      required: ["text"],
    },
  },
  {
    name: "mp_get_clipboard",
    description:
      "Read the clipboard, to assert on something the app copied for the user.",
    inputSchema: { type: "object", properties: {} },
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
    const resolved = resolveRoute(route);
    if (!resolved) {
      throw new Error(
        `Unknown route "${route}". Use one of ${Object.keys(ROUTES).join(", ")}, ` +
          `a Chinese name such as ${Object.keys(ROUTE_ALIASES).slice(0, 4).join("/")}, ` +
          "or an absolute path starting with /."
      );
    }
    const { key, path } = resolved;
    const how =
      method !== "auto" ? method : key && TAB_ROUTES.has(key) ? "switchTab" : "reLaunch";
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
    return `Tapped ${target.tag} "${target.text || target.label}"`;
  },

  async mp_input_text({ text, into = "" }) {
    const { page, nodes } = await readPage();
    const matches = findInputs(nodes, into);
    if (!matches.length) {
      const known = findInputs(nodes)
        .map((n) => (n.placeholder ? `"${n.placeholder}"` : `<${n.tag}>`))
        .join(", ");
      throw new Error(
        into
          ? `No input matching "${into}" on ${page.path}. Inputs here: ${known || "(none)"}`
          : `No input on ${page.path}.`
      );
    }
    if (!into && matches.length > 1) {
      const known = matches.map((n) => `"${n.placeholder || n.id}"`).join(", ");
      throw new Error(
        `${matches.length} inputs on ${page.path}; say which one via 'into'. Options: ${known}`
      );
    }
    const target = matches[0];
    const el = await page.$(`#${target.id}`);
    if (!el) throw new Error(`Input #${target.id} disappeared before it could be filled.`);
    await el.input(text);
    return `Typed into ${target.placeholder ? `"${target.placeholder}"` : target.tag}`;
  },

  async mp_trigger({ text, into, event, detail = {} }) {
    if (!text && !into) {
      throw new Error("Name the target with 'text', or with 'into' for an input.");
    }
    const { el, target } = await elementTarget({ text, into });
    await el.trigger(event, detail);
    return `Fired ${event} on ${target.tag} ${describeTarget(target)}`;
  },

  async mp_long_press({ text, duration = 400, until }) {
    // Element.longpress() holds for a fixed 350ms, which cannot say whether a
    // shorter press is rejected. Playing the hold ourselves keeps the duration
    // in the case, where the threshold being tested belongs.
    const hold = async () => {
      const { el, target } = await elementShowing(text);
      await el.touchstart();
      await new Promise((resolve) => setTimeout(resolve, duration));
      await el.touchend();
      return target;
    };
    const seen = async (needle) => {
      const { nodes } = await readPage();
      return nodes.some((n) => (n.text && n.text.includes(needle)) || (n.label && n.label.includes(needle)));
    };

    if (!until) {
      const target = await hold();
      return `Held ${describeTarget(target)} for ${duration}ms`;
    }

    let target;
    for (let attempt = 1; attempt <= PRESS_ATTEMPTS; attempt++) {
      target = await hold();
      if (await seen(until)) {
        return (
          `Held ${describeTarget(target)} for ${duration}ms; "${until}" is now showing` +
          (attempt > 1 ? ` (took ${attempt} attempts)` : "")
        );
      }
    }
    throw new Error(
      `Held ${describeTarget(target)} for ${duration}ms ${PRESS_ATTEMPTS} times but ` +
        `"${until}" never appeared. Either the press is not what this component ` +
        "listens for, the hold is too short to count as a long press (WeChat " +
        "fires at 350ms), or the expected text is wrong."
    );
  },

  async mp_use_fixture({ scene } = {}) {
    const url = `${GATEWAY_CONTROL.replace(/\/$/, "")}/fixture`;
    let res;
    try {
      res = scene
        ? await fetch(url, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ scene }),
          })
        : await fetch(url);
    } catch (err) {
      throw new Error(
        `Could not reach the gateway control port at ${url} (${err.message}). ` +
          "The mock gateway has to be running with a control port: start it with " +
          "gateway.mts, which opens 39102 by default."
      );
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok || body.ok === false) {
      throw new Error(body.error || `Gateway refused the fixture switch (${res.status}).`);
    }
    if (!scene) {
      return `Fixture scene "${body.scene}". Available: ${(body.scenes ?? []).join(", ")}`;
    }
    // The scene can redefine the user that mp_login_as seeds storage from.
    if (body.defaults && Object.keys(body.defaults).length) FIXTURE = body.defaults;
    return (
      `Backend data is now scene "${body.scene}". Set the login state after this, ` +
      "not before, so it is seeded from this scene."
    );
  },

  async mp_swipe({ text, selector, direction, distance = 80, until }) {
    const locate = async () => {
      if (text) {
        const found = await elementShowing(text);
        return { el: found.el, label: `"${found.target.text}"` };
      }
      const mp = await getMiniProgram();
      const page = await mp.currentPage();
      if (selector) {
        const el = await page.$(selector);
        if (!el) throw new Error(`No element matched "${selector}" on ${page.path}.`);
        return { el, label: selector };
      }
      // Page-level gestures are bound to a container with no text of its own.
      const el = await page.$("page");
      if (!el) throw new Error(`Page ${page.path} exposed no root node.`);
      return { el, label: `page ${page.path}` };
    };

    const axis = direction === "left" || direction === "right" ? "clientX" : "clientY";
    const sign = direction === "left" || direction === "up" ? -1 : 1;

    const play = async (el) => {
      // Travel always runs through non-negative coordinates: a right swipe
      // starts at the edge and moves in, a left swipe starts `distance` in and
      // moves out to the edge. Signing off a fixed 0 origin instead sends
      // clientX: -80 for left and up, which no real touch ever produces, and
      // handlers that read the start point (the drawer gesture wants
      // startX < 40) then see something impossible.
      const origin = sign < 0 ? distance : 0;
      const at = (travelled) => {
        const point = { clientX: 0, clientY: 200, [axis]: origin + sign * travelled };
        // Components read touchend off changedTouches, and automator's real
        // touch methods produce a truer event than the generic trigger();
        // smart-page.ts does both and the gesture does not register without it.
        return { touches: [point], changedTouches: [point] };
      };
      await el.touchstart(at(0));
      // A single jump is read as a teleport rather than a swipe, and too many
      // small ones drag the gesture out until it reads as a slow pan. Five
      // measured best across repeated runs against the app.
      for (let i = 1; i <= SWIPE_STEPS; i++) {
        await el.touchmove(at(Math.round((distance * i) / SWIPE_STEPS)));
      }
      await el.touchend(at(distance));
    };

    const seen = async (needle) => {
      const { nodes } = await readPage();
      return nodes.some((n) => n.text && n.text.includes(needle));
    };

    if (!until) {
      const { el, label } = await locate();
      await play(el);
      return `Swiped ${direction} ${distance}px on ${label}`;
    }

    let label = "";
    for (let attempt = 1; attempt <= SWIPE_ATTEMPTS; attempt++) {
      const found = await locate();
      label = found.label;
      await play(found.el);
      if (await seen(until)) {
        return `Swiped ${direction} on ${label}; "${until}" is now showing` +
          (attempt > 1 ? ` (took ${attempt} attempts)` : "");
      }
    }
    throw new Error(
      `Swiped ${direction} on ${label} ${SWIPE_ATTEMPTS} times but "${until}" ` +
        "never appeared. Either the gesture is not what this component listens " +
        "for, or the expected text is wrong."
    );
  },

  async mp_auth_phone({ text, code = "wxmp-test-code" }) {
    const { el, target } = await elementShowing(text);
    await el.trigger("getphonenumber", { code });
    return `Delivered phone authorisation to "${target.text || target.label}"`;
  },

  async mp_get_clipboard() {
    const mp = await getMiniProgram();
    const res = await mp.callWxMethod("getClipboardData");
    return JSON.stringify(res?.data ?? res ?? null);
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
          "Pass --fixture <path-to-defaults.json> in this server's args in " +
          "mcp_servers.json. An environment variable is not enough: MCP does " +
          "not forward it across the stdio boundary."
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
    // waitFor lives on Page, not on MiniProgram, for both the selector and the
    // millisecond form.
    const page = await mp.currentPage();
    if (selector) {
      await page.waitFor(selector);
      return `Waited for ${selector}`;
    }
    await page.waitFor(ms ?? 500);
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
    const shot = await captureAfter(name);
    return {
      content: [{ type: "text", text: String(text) + (shot ? `\n[screenshot: ${shot}]` : "") }],
    };
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
  `ready (endpoint=${WS_ENDPOINT}, fixture=${
    Object.keys(FIXTURE).length ? FIXTURE_DEFAULTS : "NONE - pass --fixture"
  }, auto-screenshot=${AUTO_SHOT ? PROOF_DIR : "off"})`
);
