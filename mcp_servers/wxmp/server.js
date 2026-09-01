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
import { normalizeConsoleEvent, normalizeExceptionEvent } from "./mp_events.mjs";
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
// Whether anyone actually said where proofs go. Falling back to cwd means
// nobody did -- a hand-run server, `npm run smoke`, a debugging session --
// and those should not drop evidence into the source tree. Hercules always
// passes --proof-dir, so this only ever suppresses noise, never real proofs.
const PROOF_DIR_EXPLICIT =
  process.argv.includes("--proof-dir") || Boolean(process.env.WXMP_PROOF_DIR);
// Screenshots are written wherever this points, and mkdirSync happily creates
// whatever it is told to -- so a placeholder that never got expanded, or an
// undefined value from a hand-rolled invocation, silently produces a junk
// directory like ./undefined/ or ./${proof_path}/ while every capture still
// reports success. Evidence then goes missing in a way nothing surfaces.
// Warn loudly rather than throw: a bad proof dir should not abort a run whose
// assertions are otherwise fine.
const PROOF_DIR_SUSPECT =
  PROOF_DIR.includes("${") || PROOF_DIR === "undefined" || PROOF_DIR.endsWith("/undefined");

/**
 * Whether proofs may be written, checked once and remembered.
 *
 * A recognisably wrong path is refused outright rather than created: the whole
 * failure mode being guarded against is mkdirSync happily materialising
 * ./undefined/ or ./${proof_path}/ and every capture then reporting success
 * into it. Anything else is verified by actually creating the directory and
 * checking it is writable, so a path that only looks fine still fails here
 * rather than silently at the first screenshot.
 */
let proofDirState = null;
function proofDirReady() {
  if (proofDirState !== null) return proofDirState;
  if (PROOF_DIR_SUSPECT) {
    log(
      `WARNING: --proof-dir looks wrong (${PROOF_DIR}) -- refusing to write ` +
        "proofs there. This run will produce no screenshots."
    );
    proofDirState = false;
    return proofDirState;
  }
  try {
    fs.mkdirSync(PROOF_DIR, { recursive: true });
    fs.accessSync(PROOF_DIR, fs.constants.W_OK);
    proofDirState = true;
  } catch (err) {
    log(`WARNING: proof dir is not writable (${PROOF_DIR}): ${err.message}`);
    proofDirState = false;
  }
  return proofDirState;
}
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
/**
 * Vision model, used by mp_visual_check.
 *
 * Some of the app is drawn outside the page's WXML tree -- the tabBar is
 * rendered by the mini program shell, so a snapshot cannot see it however the
 * assertion is phrased. A screenshot can. Configured separately from Hercules'
 * own models because this server is a different process and receives nothing
 * from that config.
 */
const VISION_MODEL = flag("vision-model", process.env.WXMP_VISION_MODEL || "");
const VISION_BASE = flag("vision-base-url", process.env.WXMP_VISION_BASE_URL || "");
const VISION_KEY = flag("vision-key", process.env.WXMP_VISION_KEY || "");
const VISION_READY = Boolean(VISION_MODEL && VISION_BASE && VISION_KEY);
const BASELINE_DIR = flag("baseline-dir", process.env.WXMP_BASELINE_DIR || "");

const AUTO_SHOT = flag("auto-screenshot", process.env.WXMP_AUTO_SCREENSHOT || "on") !== "off";

// mp-real (dist-integration + a real lane) needs a real credential, not a mock
// token. mp_use_real_account reads it from a local, gitignored pool file
// instead of taking it as a tool argument -- design constraint ③ (see this
// server's header comment): token text must never enter LLM context. baseUrl
// and lane are also read from flags so the tool can refuse to serve an
// account built against a different lane instead of failing inside an
// assertion.
const ACCOUNT_POOL_PATH = flag("account-pool", process.env.WXMP_ACCOUNT_POOL || "");
const REAL_BASE_URL = flag("real-base-url", process.env.WXMP_REAL_BASE_URL || "https://zmapi-test.daqian369.com");
const REAL_LANE = flag("real-lane", process.env.WXMP_REAL_LANE || "feat-esm-v3-0");
// Must match the productId compiled into the bundle under test
// (TARO_APP_PRODUCT_ID; dist-integration builds "esm_miniprogram"), not the
// one run-esm-api.sh uses for the API suites ("narnia"). Accounts are scoped
// per product: registering a phone under "narnia" and then letting the app --
// which stamps every request with its own productId -- call GetMe returns no
// user for that token. The app reads that as a dead session and calls
// logout(), which wipes the credentials just written, drops back to guest,
// and leaves the home page on "正在加载..." forever. The token is valid the
// whole time, which is what makes the mismatch so hard to see from outside.
const REAL_PRODUCT_ID = flag("real-product-id", process.env.WXMP_REAL_PRODUCT_ID || "esm_miniprogram");

/** Tools that change what is on screen and are therefore worth capturing. */
const CAPTURED = new Set([
  "mp_login_as",
  "mp_use_real_account",
  "mp_login_with_phone",
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
  if (!proofDirReady()) {
    noteEvidenceGap(`${toolName}: proof dir unusable (${PROOF_DIR})`);
    return null;
  }
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
    // Never fail the step over a screenshot. But a scenario that passes with no
    // evidence is the same shape as a false green -- the conclusion stands and
    // the thing supporting it is not there -- so leave a marker the report can
    // pick up instead of only a line in a log nobody reads afterwards.
    log(`auto screenshot after ${toolName} failed: ${err.message}`);
    noteEvidenceGap(`${toolName}: ${err.message}`);
    return null;
  }
}

/** Record a failed capture where the report generator will find it. */
function noteEvidenceGap(detail) {
  if (PROOF_DIR_SUSPECT) return;
  try {
    fs.mkdirSync(PROOF_DIR, { recursive: true });
    fs.appendFileSync(
      path.join(PROOF_DIR, "EVIDENCE_MISSING.txt"),
      `${new Date().toISOString()} ${detail}\n`
    );
  } catch {
    // The proofs directory being unwritable is the very thing being reported;
    // there is nowhere left to record it.
  }
}

/**
 * Named routes, resolved against the bundle that is actually loaded.
 *
 * This was a hardcoded path table mirroring
 * fe_app/packages/e2e/tests/utils/test-router.ts, and it went silently wrong
 * the moment a different version of the app was opened in DevTools. V3.0 moved
 * home from /pages/index/index to /features/home/pages/index, replaced the
 * login page with an entry page, renamed luxx_test to assessment, and cut the
 * tab bar from three tabs to two -- so navigating to 对话 still called
 * switchTab on a page that is no longer a tab. Nothing reported a version
 * mismatch; pages just failed to open.
 *
 * The bundle already carries all of this in __wxConfig. Read it once per
 * connection and resolve names against it, so whichever build is open is the
 * one being navigated, and a name the build has no page for fails saying so.
 *
 * Candidates are listed newest first; the first one present in the bundle wins.
 */
const ROUTE_CANDIDATES = {
  entry: ["features/entry/pages/index", "features/auth/pages/login/index"],
  home: ["features/home/pages/index", "pages/index/index"],
  chat: ["features/chat/pages/index"],
  mine: ["features/mine/pages/index"],
  profile: ["features/profile/pages/required/index"],
  assessment: ["features/assessment/pages/index", "features/luxx_test/pages/index"],
  result: ["features/result/pages/index"],
  report: ["features/report/pages/personal/index"],
  externalReport: ["features/report/pages/external/index"],
  unregister: ["features/account/pages/unregister/index"],
  webview: ["features/webview/pages/index"],
  privacy: ["features/agreements/pages/privacy/index"],
  agreement: ["features/agreements/pages/agreement/index"],
};

/**
 * Chinese aliases so scenarios can be written the way the app labels things.
 * The wording follows the route vocabulary the cases are generated against
 * (openspec/prds/esm-v3-0/v3.0/gherkin-map.yaml). Without these a model has to
 * translate "我的" to "mine" on its own, which it usually gets right and
 * occasionally does not -- not a good trade for test assets that must behave
 * the same every run.
 */
const ROUTE_ALIASES = {
  启动页: "entry",
  登录: "entry",
  登录页: "entry",
  登录注册: "entry",
  login: "entry",
  首页: "home",
  主页: "home",
  对话: "chat",
  聊天: "chat",
  我的: "mine",
  个人中心: "mine",
  信息采集: "profile",
  填写信息: "profile",
  资料: "profile",
  量表: "assessment",
  答题: "assessment",
  量表测试: "assessment",
  luxxTest: "assessment",
  测量结果: "result",
  结果: "result",
  个人报告: "report",
  报告: "report",
  我的报告: "report",
  解读报告: "report",
  他人报告: "externalReport",
  对方报告: "externalReport",
  注销: "unregister",
  注销账号: "unregister",
  隐私政策: "privacy",
  用户协议: "agreement",
};

// One bundle per connection, and the process is respawned per scenario, so a
// plain memo is enough; there is no invalidation case to handle.
let routeTable = null;

async function getRouteTable() {
  if (routeTable) return routeTable;
  const mp = await getMiniProgram();
  const config = await mp.evaluate(function () {
    const c = typeof __wxConfig !== "undefined" ? __wxConfig : null;
    if (!c) return null;
    return {
      pages: c.pages || [],
      tabs: c.tabBar && c.tabBar.list ? c.tabBar.list.map((t) => t.pagePath) : [],
    };
  });
  if (!config || !config.pages.length) {
    throw new Error(
      "Could not read the bundle's page list from __wxConfig, so named routes " +
        "cannot be resolved. Absolute paths starting with / still work."
    );
  }
  // DevTools reports tab pagePaths with a .html suffix that the page list does
  // not have, so both sides get normalised before they are compared.
  const strip = (p) => String(p).replace(/^\//, "").replace(/\.html$/, "");
  const pages = config.pages.map(strip);
  const pageSet = new Set(pages);
  const tabSet = new Set(config.tabs.map(strip));

  const paths = {};
  const tabs = new Set();
  for (const [key, candidates] of Object.entries(ROUTE_CANDIDATES)) {
    const hit = candidates.find((candidate) => pageSet.has(candidate));
    if (!hit) continue;
    paths[key] = `/${hit}`;
    if (tabSet.has(hit)) tabs.add(key);
  }
  routeTable = { paths, tabs, pages };
  log(`routes ${Object.keys(paths).join(", ")} | tabs ${[...tabs].join(", ") || "(none)"}`);
  return routeTable;
}

async function resolveRoute(input) {
  // An absolute path is taken as given: it needs no table, and it stays usable
  // on a bundle whose page list could not be read.
  if (typeof input === "string" && input.startsWith("/")) {
    return { key: null, path: input, isTab: false };
  }
  const key = ROUTE_ALIASES[input] ?? input;
  const { paths, tabs, pages } = await getRouteTable();
  if (paths[key]) return { key, path: paths[key], isTab: tabs.has(key) };

  const why = ROUTE_CANDIDATES[key]
    ? `"${input}" is a known route, but this bundle has no page for it -- ` +
      `looked for ${ROUTE_CANDIDATES[key].join(" or ")}. The project open in ` +
      "DevTools is probably a different version of the app than the case expects."
    : `"${input}" is not a route name.`;
  throw new Error(
    `${why}\nRoutes this bundle has: ${Object.keys(paths).join(", ")}\n` +
      `Its pages: ${pages.join(", ")}\n` +
      "Chinese names and absolute paths starting with / also work."
  );
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

/**
 * One door to the mock gateway's control port: fixture swaps and the websocket
 * controls both go through it. The port is plain HTTP on 127.0.0.1 -- the
 * gateway's own /__push sits behind its self-signed cert, and gateway.mts
 * re-exposes it here so this process never has to opt out of TLS checking.
 */
async function gatewayCall(method, route, body) {
  const url = `${GATEWAY_CONTROL.replace(/\/$/, "")}${route}`;
  let res;
  try {
    res = await fetch(url, {
      method,
      ...(body === undefined
        ? {}
        : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
    });
  } catch (err) {
    throw new Error(
      `Could not reach the gateway control port at ${url} (${err.message}). ` +
        "The mock gateway has to be running with a control port: start it with " +
        "gateway.mts, which opens 39102 by default."
    );
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok || payload.ok === false) {
    throw new Error(payload.error || `Gateway refused ${method} ${route} (${res.status}).`);
  }
  return payload;
}

/* ------------------------------------------------------------------ *
 * Connection (lazy singleton)
 * ------------------------------------------------------------------ */

let miniProgram = null;

/**
 * 把小程序的 console 和未捕获异常写进 proofs 目录，交给 Hercules 的 observer 判定。
 *
 * 为什么不在这里判红：判定口径只有一处，在 testzeus_hercules/core/invariants.py。
 * 这边只负责把原始事件按约定的行形状吐出去（和 playwright_manager 写 console_logs.json
 * 用的是同一种形状），什么算判红、什么只留痕由那边决定。两边各判一遍的话迟早会漂移，
 * 而漂移的方向通常是各自变宽。
 *
 * 单独写 mp_console_logs.json 而不是追加到 console_logs.json：那份归 playwright 写，
 * 两个进程各写各的文件，谁都不用去争同一个句柄。observer 两份都读。
 *
 * 这条链路是小程序套件目前唯一的自动判定源。在它之前，observer 读的两份证据文件只有
 * playwright 会写，而小程序套件走的是开发者工具，所以 observer 在那边什么也看不到——
 * 而 67 条缺陷里有 60 条在小程序端。
 */
const MP_CONSOLE_LOG = "mp_console_logs.json";

function recordMiniProgramEvent(entry) {
  if (!proofDirReady()) return;
  try {
    fs.appendFileSync(path.join(PROOF_DIR, MP_CONSOLE_LOG), JSON.stringify(entry) + "\n", "utf8");
  } catch (err) {
    // 采证失败不能反过来搞垮正在跑的用例。
    log(`WARNING: could not record mini program event: ${err.message}`);
  }
}

function attachEventCapture(mp) {
  // on("console") 会让 automator 发 App.enableLog，所以必须在连上之后主动订阅，
  // 不订阅一条都收不到。归一化放在 mp_events.mjs 里，那边有 node --test 覆盖——
  // 这两个事件的负载形状拿不到确切定义，猜错的后果是静默无输出。
  mp.on("console", (msg) => {
    const entry = normalizeConsoleEvent(msg);
    if (entry) recordMiniProgramEvent(entry);
  });
  mp.on("exception", (err) => recordMiniProgramEvent(normalizeExceptionEvent(err)));
}

async function getMiniProgram() {
  if (miniProgram) return miniProgram;
  try {
    miniProgram = await automator.connect({
      wsEndpoint: WS_ENDPOINT,
      timeout: CONNECT_TIMEOUT,
    });
    attachEventCapture(miniProgram);
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

/** Ask the vision model about one or two images. */
async function askVision(prompt, images) {
  if (!VISION_READY) {
    throw new Error(
      "No vision model configured. Pass --vision-model, --vision-base-url and " +
        "--vision-key in this server's args in mcp_servers.json."
    );
  }
  const content = [{ type: "text", text: prompt }];
  for (const b64 of images) {
    content.push({ type: "image_url", image_url: { url: `data:image/png;base64,${b64}` } });
  }
  const res = await fetch(`${VISION_BASE.replace(/\/$/, "")}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${VISION_KEY}` },
    body: JSON.stringify({
      model: VISION_MODEL,
      max_completion_tokens: 3000,
      messages: [{ role: "user", content }],
    }),
  });
  if (!res.ok) {
    throw new Error(`Vision model returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  const answer = (await res.json())?.choices?.[0]?.message?.content?.trim();
  if (!answer) throw new Error("Vision model returned an empty answer.");
  return answer;
}

/** Fresh screenshot of the simulator, as base64. */
async function screenBase64() {
  const mp = await getMiniProgram();
  const page = await mp.currentPage();
  const shot = await mp.screenshot();
  const image = typeof shot === "string" ? shot : shot?.data;
  if (!image) throw new Error("The simulator returned no image; is it rendering?");
  return { page, image };
}

/**
 * The page's top-level nodes, with their wxml already fetched.
 *
 * V1 bundles wrapped every page in a `<page>` element, so one `$("page")` got
 * the whole tree. The V3.0 bundle renders with `navigationStyle: 'custom'` and
 * has no `<page>` wrapper at all: `$("page")` returns null, and every snapshot,
 * tap-by-text and page-level gesture failed with "exposed no root node" while
 * the simulator was visibly rendering the screen. Fall back to the top-level
 * views, which is where that tree actually starts.
 *
 * `$$("view")` matches nested views too and `wxml()` renders an element's whole
 * subtree, so taking every match would repeat most of the page several times
 * over -- and a duplicated tree makes every "how many X are on screen" count
 * wrong, which is most of what these cases assert. Keep only the matches that
 * no other match already contains.
 */
async function pageRoots(page) {
  const wrapper = await page.$("page");
  if (wrapper) return [{ el: wrapper, wxml: String(await wrapper.wxml()) }];

  const views = (await page.$$("view")) ?? [];
  const found = [];
  for (const el of views) found.push({ el, wxml: String(await el.wxml()) });
  return found.filter(
    (candidate, i) =>
      !found.some(
        (other, j) =>
          j !== i && other.wxml.length > candidate.wxml.length && other.wxml.includes(candidate.wxml)
      )
  );
}

/** Parsed nodes for the whole page, however the bundle roots it. */
async function pageNodes(page) {
  const roots = await pageRoots(page);
  if (!roots.length) {
    throw new Error(
      `Page ${page.path} rendered no nodes. It may still be loading, or the mock ` +
        "gateway may not be serving the data this page needs -- check mp_use_fixture."
    );
  }
  return roots.flatMap((root) => parseWxml(root.wxml));
}

/** Current page plus its parsed nodes; one round trip for the wxml. */
async function readPage() {
  const mp = await getMiniProgram();
  const page = await mp.currentPage();
  return { page, nodes: await pageNodes(page) };
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
async function elementTarget({ text, into, selector }) {
  // A selector addresses the component itself. Text lookup resolves to the
  // node that *shows* the text, which for a composite control is usually a
  // <text> nested inside it -- firing the event there hits a node with no
  // handler, so the call reports success and nothing happens. Native-backed
  // controls (a chooseAvatar <button>, a <picker mode="date">) are exactly
  // that shape, and they are also the ones that can only be driven by event,
  // never by tapping, so they need a way to name the component directly.
  if (selector) {
    const mp = await getMiniProgram();
    const page = await mp.currentPage();
    const el = await page.$(selector);
    if (!el) throw new Error(`No element matching selector "${selector}" on ${page.path}.`);
    return { el, target: { tag: selector, text: "", label: "" } };
  }
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
    name: "mp_restart",
    description:
      "Restart the mini program from scratch and land on `route`, the way " +
      "killing it and reopening it would.\n" +
      "Needed because clearing storage does NOT sign out a running instance. " +
      "The app's session store keeps the token in memory and prefers it over " +
      "storage, so after mp_clear_storage the app still boots as whoever was " +
      "signed in before, and a case asserting the signed-out screen gets the " +
      "signed-in one. This is invisible against the mock gateway, where every " +
      "fixture shares one fake token and a stale in-memory token is " +
      "indistinguishable from a fresh one -- it only bites once real accounts " +
      "with different tokens are involved.\n" +
      "So: to get a genuine cold start, clear storage first, then restart. To " +
      "switch between two different accounts, restart between them.",
    inputSchema: {
      type: "object",
      properties: {
        route: {
          type: "string",
          description: "Route name or absolute path to land on after restarting. Defaults to the entry page.",
        },
      },
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
    name: "mp_visual_check",
    description:
      "Look at the screen and answer a question about it. Use this for what a " +
      "snapshot cannot see: the bottom tab bar and other native chrome drawn " +
      "outside the page, whether an image actually rendered or came out broken, " +
      "whether something is covered or cut off. For text and controls inside the " +
      "page, mp_snapshot is cheaper and more exact -- reach for this only when " +
      "the thing you need to judge is visual.",
    inputSchema: {
      type: "object",
      properties: {
        looking_for: {
          type: "string",
          description:
            "What to judge, as a question, e.g. 底部导航栏有哪几个标签？ or 头像图片加载出来了吗？",
        },
      },
      required: ["looking_for"],
    },
  },
  {
    name: "mp_visual_compare",
    description:
      "Compare the screen against a stored reference image and report what " +
      "changed. Use for visual regression -- layout shifting, elements " +
      "overlapping or disappearing, colours or spacing going wrong -- the kind " +
      "of thing that is obvious to look at and hard to phrase as a text " +
      "assertion. For asking whether something specific is on screen, " +
      "mp_visual_check is more direct.",
    inputSchema: {
      type: "object",
      properties: {
        reference: {
          type: "string",
          description:
            "Name of the reference image, without extension, e.g. mine_page_logged_in",
        },
        focus: {
          type: "string",
          description:
            "Optional: what to pay attention to, e.g. 只看底部导航栏. Narrow this " +
            "when the page has content that legitimately changes between runs.",
        },
      },
      required: ["reference"],
    },
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
        selector: {
          type: "string",
          description:
            "WXML selector naming the component itself, e.g. 'picker' or " +
            "\"button[open-type='chooseAvatar']\". Use this for native-backed " +
            "controls (date picker, chooseAvatar button): they cannot be tapped, " +
            "and matching on their visible text lands on an inner <text> node " +
            "that has no handler, so the event silently does nothing.",
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
      "Set the backend state the app talks to, by naming one or more fixtures. " +
      "Use this when a case needs an account or server state the default data " +
      "does not have -- an expired token, an empty list, a quota that is used " +
      "up, a report that is ready, an error response. Call it BEFORE " +
      "mp_login_as, because the login state is seeded from this data.\n" +
      "Names compose: pass several and they are layered left to right onto the " +
      "base data, so ['state-scale-done', 'state-report-ready'] gives an " +
      "account that has both. Later names win where two set the same field. " +
      "Prefer composing small state fragments over hunting for one scene that " +
      "happens to have everything, because most named states differ only in a " +
      "field or two of the same home-state response.\n" +
      "With no argument it reports what is loaded and every name available.",
    inputSchema: {
      type: "object",
      properties: {
        scenes: {
          type: "array",
          items: { type: "string" },
          description:
            "Fixture names to layer, in order. Omit to just report what is loaded.",
        },
        scene: {
          type: "string",
          description:
            "A single fixture name, for when only one is needed. Ignored if `scenes` is given.",
        },
      },
    },
  },
  {
    name: "mp_calls",
    description:
      "List the backend calls the page has made so far, with how many times each " +
      "was called and whether the fixture actually had an answer for it. Use this " +
      "to assert on traffic rather than on pixels: that tapping send fired exactly " +
      "one SendMessage, that entering a page fetched its data at all, that a retry " +
      "did not fire the same write twice. Also the fastest way to explain a blank " +
      "or spinning page -- an uncovered route returned a bare envelope with no " +
      "payload, so the page had nothing to render and no error to show. Pass the " +
      "result to assert_ui, or read `uncovered`. Only the mock gateway records " +
      "this; on the real-backend suite there is nothing to list.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "mp_ws_push",
    description:
      "Push one chat stream event to the app over the websocket, right now. " +
      "Use this when a case is about the STREAM ITSELF rather than about a " +
      "response body: the same event arriving twice, deltas that never get a " +
      "commit, a snapshot landing after deltas, a whole generation replayed in " +
      "order. Fixtures cannot express those -- a fixture describes a response, " +
      "not a moment -- so drive them here, one call per event, in the order the " +
      "case names.\n" +
      "`event` is the wire envelope, in snake_case:\n" +
      '  {"event_id": "e1", "chat_id": "<id>", "generation_id": "gen-1", ' +
      '"seq_id": "0", "event_type": "CHAT_STREAM_EVENT_TYPE_ASSISTANT_DELTA", ' +
      '"delta": "你"}\n' +
      "event_type is one of ASSISTANT_DELTA, MESSAGE_SNAPSHOT, COMPLETED, " +
      "FINAL_MESSAGE_COMMITTED, BLOCKED, FAILED, HEARTBEAT, SUBSCRIBED, " +
      "RECOMMENDED_QUESTIONS_READY, each prefixed CHAT_STREAM_EVENT_TYPE_. " +
      "Keep generation_id the same across one turn's events, and give each " +
      "event a distinct event_id unless the case is specifically about a " +
      "duplicate. chat_id and generation_id have to match the chat that is " +
      "actually open -- read them off the page first; an event for some other " +
      "chat is delivered and then ignored, which looks exactly like a bug in " +
      "the app.\n" +
      "The app subscribes when a chat page opens. This fails if nothing is " +
      "subscribed, rather than letting the event go nowhere and the assertion " +
      "fail far from the cause.",
    inputSchema: {
      type: "object",
      properties: {
        event: {
          type: "object",
          description: "The event envelope to deliver, as described above.",
          additionalProperties: true,
        },
      },
      required: ["event"],
    },
  },
  {
    name: "mp_ws_disconnect",
    description:
      "Drop the app's websocket connection, for cases about reconnection and " +
      "catching up: what the client does when the stream dies mid-turn, and " +
      "whether it recovers from the committed message store rather than from " +
      "whatever it had buffered. Pair it with mp_ws_push to script the events " +
      "on either side of the break.",
    inputSchema: { type: "object", properties: {} },
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
    name: "mp_use_real_account",
    description:
      "mp-real only: sign in against the real backend with a credential from " +
      "the local account pool instead of a mock token. Consumes one unused " +
      "account, restarts the app cold, and self-verifies the token against the " +
      "real backend before returning -- the token itself never appears in the " +
      "result. Requires --account-pool to be configured; fails clearly if it " +
      "is not, or if the pool has no unconsumed account for the requested " +
      "state.",
    inputSchema: {
      type: "object",
      properties: {
        state: {
          type: "string",
          description:
            "Which pool account to consume, e.g. 'fresh', 'profile-done', " +
            "'scale-done'. Omit to take any unconsumed account.",
        },
      },
    },
  },
  {
    name: "mp_login_with_phone",
    description:
      "mp-real only: sign in against the real backend by phone number, with " +
      "no SMS code -- this lane's login-by-phone/register-by-phone RPCs accept " +
      "any phone with no verification (the same bypass the app's own '使用 " +
      "Narnia 测试账号' button uses). Pass 'phone' to log into that specific " +
      "account (registers it first if it does not exist yet, so the same " +
      "number can be reused across runs to land on the same account); omit it " +
      "to register a fresh, randomly generated one. Restarts the app cold and " +
      "confirms it landed on home before returning; the token never appears " +
      "in the result.",
    inputSchema: {
      type: "object",
      properties: {
        phone: {
          type: "string",
          description:
            "Phone number to sign in as, e.g. '13800001234'. Omit for a new " +
            "random test account.",
        },
      },
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

/**
 * Restarts the mini program and waits for it to settle on a page, dropping
 * and re-establishing the automation connection along the way. Shared by
 * mp_restart (explicit) and mp_login_as (implicit -- see the comment there
 * for why login needs this too, not just clearStorage + reLaunch).
 */
async function restartAndSettle(path) {
  const mp = await getMiniProgram();

  // DevTools tears the automation channel down as it restarts, so this call
  // often rejects even when the restart itself is fine. Drop the connection
  // either way and let the reconnect below decide whether it worked.
  try {
    await mp.callWxMethod("restartMiniProgram", { path });
  } catch {
    // expected when the channel goes first
  }
  try {
    mp.disconnect();
  } catch {
    // already gone
  }
  miniProgram = null;
  // The bundle has not changed, but the table was read over a connection
  // that no longer exists; re-read it rather than reason about what survives.
  routeTable = null;

  const deadline = Date.now() + CONNECT_TIMEOUT;
  let lastError;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1000));
    let fresh;
    try {
      fresh = await getMiniProgram();
      await fresh.currentPage();
    } catch (err) {
      lastError = err;
      miniProgram = null;
      continue;
    }
    // A freshly booted app is still deciding where to go -- the entry page
    // resolves the session and may route somewhere else entirely, which on a
    // real backend is the interesting part. Reporting the first page read
    // back would name a page the app is in the middle of leaving, so wait
    // until two reads in a row agree before saying where it landed.
    //
    // currentPage() itself can time out mid-settle -- the automation channel
    // is freshly reconnected and the app is still busy booting -- and that is
    // a transient hiccup, not proof the restart failed. Treat it the same as
    // a failed reconnect above: drop this connection and let the outer loop
    // try again, instead of letting it escape and fail the whole restart
    // (and with it every mp_login_as call, since that now restarts too).
    try {
      let previous = null;
      const settleBy = Date.now() + 8000;
      while (Date.now() < settleBy) {
        const here = (await fresh.currentPage()).path;
        if (here === previous) return here;
        previous = here;
        await new Promise((r) => setTimeout(r, 800));
      }
      return previous;
    } catch (err) {
      lastError = err;
      miniProgram = null;
      continue;
    }
  }
  throw new Error(
    `Restarted the mini program but could not reconnect within ${CONNECT_TIMEOUT}ms ` +
      `(${lastError?.message || "no error reported"}). DevTools may still be reloading -- ` +
      "check that its window is open and the project is still loaded."
  );
}

// Same phone-space the app's own "使用 Narnia 测试账号" button draws from
// (narniaIntegrationAuth.weapp.ts createNarniaTestIdentity), offset with a
// 198 prefix instead of 199 so a random run here can never collide with a
// number that button already registered.
function randomTestPhone() {
  const suffix = String(Math.floor(Math.random() * 1e8)).padStart(8, "0");
  return { phone: `198${suffix}`, nickname: `联调用户${suffix.slice(-6)}` };
}

function mapAuthGender(value) {
  if (value === "GENDER_MALE") return 1;
  if (value === "GENDER_FEMALE") return 2;
  return undefined;
}

async function authCall(pathSuffix, body) {
  const resp = await fetch(`${REAL_BASE_URL}${pathSuffix}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      lane: REAL_LANE,
      "x-lane": REAL_LANE,
      "X-Product-ID": REAL_PRODUCT_ID,
    },
    body: JSON.stringify({
      ...body,
      baseReq: { productId: REAL_PRODUCT_ID, requestId: `wxmp-${Date.now()}-${Math.random().toString(16).slice(2, 8)}` },
    }),
  });
  let json;
  try {
    json = await resp.json();
  } catch {
    json = null;
  }
  const ok = resp.ok && json?.baseResp?.statusCode === "0" && json?.user && json?.accessToken?.accessToken;
  return { ok, status: resp.status, json };
}

/**
 * Real-backend phone auth: no SMS code, no password -- login-by-phone hands
 * back a live token for any phone that has registered, and register-by-phone
 * takes any phone with no verification at all. This is the same lane bypass
 * the app's own "使用 Narnia 测试账号" button uses (narniaIntegrationAuth.
 * weapp.ts); calling the two RPCs directly just makes the phone number
 * choosable instead of always-random, so the same account can be logged
 * back into across runs.
 *
 * Mirrors that file's login() sequence exactly for the register path: call
 * register-by-phone and discard its response, then call login-by-phone
 * separately for the session -- matching production behavior rather than
 * optimizing away a round trip, in case the two are not equivalent for some
 * account state this lane cares about.
 */
async function phoneAuth(phone, nickname) {
  const existing = await authCall("/api/auth/login-by-phone", { phone });
  if (existing.ok) return { session: existing.json, isNew: false };

  // register-by-phone's own response is not a reliable success signal on
  // this lane -- observed returning a non-zero statusCode with no user/token
  // on a registration that had, in fact, just succeeded (confirmed by the
  // login-by-phone call right after it working). narniaIntegrationAuth.
  // weapp.ts already discovered this in production: it calls
  // registerByPhone() and discards the result outright, then makes a
  // separate loginByPhone() call for the actual session. Do the same --
  // fire it, ignore what it says, and let the follow-up login be the only
  // thing that decides success or failure.
  await authCall("/api/auth/register-by-phone", { phone, nickname }).catch(() => {});

  const loggedIn = await authCall("/api/auth/login-by-phone", { phone });
  if (!loggedIn.ok) {
    throw new Error(
      `Could not sign in with phone ${phone}: login-by-phone failed both before ` +
        `registration (HTTP ${existing.status}, statusCode ${existing.json?.baseResp?.statusCode ?? "?"}) ` +
        `and after it (HTTP ${loggedIn.status}, statusCode ${loggedIn.json?.baseResp?.statusCode ?? "?"}). ` +
        `Check --real-base-url/--real-lane point at a lane where phone auth is enabled.`
    );
  }
  return { session: loggedIn.json, isNew: true };
}

/**
 * Shared tail for both real-backend sign-in tools (mp_use_real_account,
 * mp_login_with_phone): cold-restart, seed storage with a real token, land
 * on home, confirm the app actually treated it as authenticated. Pulled out
 * once two callers needed the identical sequence rather than let them drift
 * apart under separate edits later.
 */
async function coldSignInWithToken({ tokens, user, describeFailure }) {
  // Order matters, and it is the opposite of what mp_login_as does.
  //
  // initSession() -- the only thing that reads credentials out of storage into
  // the session singleton -- runs exclusively from App.onLaunch (app.ts). A
  // reLaunch() is page navigation: it re-creates the page but never re-fires
  // onLaunch. So restarting first and writing credentials afterwards leaves
  // the app authenticated against whatever storage held at boot (nothing),
  // and the freshly written token is simply never read. That failure is
  // silent and looks like a backend hang: the tool reports success, the app
  // sits on the home route, and homeFlow.load()'s `if (!userId) return`
  // no-ops, so the page stays on "正在加载..." forever with no error and no
  // request. Write first, then restart, so onLaunch sees the credentials.
  const mp = await getMiniProgram();
  await mp.callWxMethod("clearStorageSync");
  // The app's storage contract is snake_case, and its readers bail to null on
  // the first missing key rather than tolerating another shape:
  // toTokenPairFromPayload() wants access_token/refresh_token, and
  // toUserFromStorage() returns null unless user_id is set (shared/utils/
  // storage.ts). Writing camelCase here produced a login that looked
  // successful from the outside -- the tool reported success and the app sat
  // on the home route -- but left the session as guest, so homeFlow.load()'s
  // `if (!userId) return` no-oped and the page stayed on "正在加载..." with
  // zero network calls ever issued. Callers pass ordinary camelCase; the
  // mapping to the on-disk shape belongs here, once, mirroring the app's own
  // writeTokenPairToStorage/writeUserToStorage.
  await mp.callWxMethod("setStorageSync", TOKENS_KEY, {
    access_token: tokens.accessToken,
    refresh_token: tokens.refreshToken,
    access_token_expires_at: tokens.accessTokenExpiresAt,
    refresh_token_expires_at: tokens.refreshTokenExpiresAt,
  });
  await mp.callWxMethod("setStorageSync", USER_KEY, {
    user_id: user.userId,
    phone: user.phone,
    nickname: user.nickname,
    gender: user.gender,
    has_profile_result: user.hasProfileResult,
  });
  // Restart *after* the write so App.onLaunch -> initSession() picks these up.
  // Boot straight into home: the app re-routes by entryStage on its own, so
  // where it actually settles is also the signal for whether it accepted the
  // session.
  const home = await resolveRoute("home");
  let landedOn = await restartAndSettle(home.path);

  if (/entry|login/i.test(landedOn)) {
    throw new Error(describeFailure(landedOn));
  }

  // The app resolves its session asynchronously off App.onLaunch (app.ts runs
  // `void bootstrapApplication().then(initSession)` without awaiting it),
  // while the landing page's onLoad/onShow fire straight away. When the page
  // wins that race, homeFlow.load() bails at `if (!userId) return`, leaves
  // phase on 'idle', and nothing re-runs it once the session does land -- the
  // page then sits on "正在加载..." indefinitely, with no error and no retry.
  // A cold start driven by automation loses that race far more often than a
  // human tapping through the entry page ever would. Bounce off the page and
  // back to re-fire onShow after the session has settled; harmless when the
  // race was already won, since refresh() is idempotent.
  // restartAndSettle() tore down the connection `mp` points at and opened a
  // new one, so re-acquire rather than reuse the dead handle.
  const live = await getMiniProgram();
  for (let attempt = 0; attempt < 3; attempt++) {
    const { nodes } = await readPage();
    if (!nodes.some((n) => (n.text || "").includes("正在加载"))) break;
    const other = await resolveRoute("mine");
    await live[other.isTab ? "switchTab" : "reLaunch"](other.path);
    await new Promise((r) => setTimeout(r, 1200));
    await live[home.isTab ? "switchTab" : "reLaunch"](home.path);
    await new Promise((r) => setTimeout(r, 2500));
    landedOn = (await live.currentPage()).path;
  }
  return landedOn;
}

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
    const { path, isTab } = await resolveRoute(route);
    const how = method !== "auto" ? method : isTab ? "switchTab" : "reLaunch";
    await mp[how](path);
    return `${how} -> ${path}`;
  },

  async mp_restart({ route } = {}) {
    const { path } = await resolveRoute(route || "entry");
    const landedOn = await restartAndSettle(path);
    return `Restarted, settled on ${landedOn}.`;
  },

  async mp_snapshot() {
    const { page, nodes } = await readPage();
    return `Page: ${page.path}\n${formatSnapshot(nodes)}`;
  },

  async mp_visual_check({ looking_for }) {
    const { page, image } = await screenBase64();
    const answer = await askVision(
      `这是微信小程序页面 ${page.path} 的截图。请回答：${looking_for}\n` +
        "只描述你在图上真正看到的内容。看不清或图上没有就直说，不要推测。",
      [image]
    );
    return `Looking at ${page.path}: ${answer}`;
  },

  async mp_visual_compare({ reference, focus = "" }) {
    if (!BASELINE_DIR) {
      throw new Error(
        "No baseline directory configured. Pass --baseline-dir in this server's " +
          "args in mcp_servers.json."
      );
    }
    const safe = String(reference).replace(/[^a-zA-Z0-9_-]/g, "_");
    const refPath = path.join(BASELINE_DIR, `${safe}.png`);
    const { page, image } = await screenBase64();

    if (!fs.existsSync(refPath)) {
      // Recording whatever is on screen right now would bless a broken page as
      // the reference, and every later run would agree with it. Save it beside
      // the baseline for a person to look at and promote deliberately.
      fs.mkdirSync(BASELINE_DIR, { recursive: true });
      const candidate = path.join(BASELINE_DIR, `${safe}.candidate.png`);
      fs.writeFileSync(candidate, Buffer.from(image, "base64"));
      throw new Error(
        `No reference image at ${refPath}. The current screen was saved to ` +
          `${candidate} -- check it looks right, then rename it to ${safe}.png ` +
          "to make it the baseline."
      );
    }

    const refB64 = fs.readFileSync(refPath).toString("base64");
    const answer = await askVision(
      `第一张是基准图，第二张是小程序页面 ${page.path} 的当前截图。\n` +
        (focus ? `重点比较：${focus}\n` : "") +
        "先用一行给出结论：一致 或 有差异。\n" +
        "如果有差异，逐条列出你在图上真正看到的不同之处。" +
        "只描述看到的内容，不要推测原因，也不要把两张图都有的东西写成差异。",
      [refB64, image]
    );

    // Keep both frames next to the verdict; a verdict nobody can check is not
    // evidence.
    // Evidence is a by-product of the verdict here, so an unusable proof dir
    // skips the artefacts instead of failing the comparison itself.
    if (proofDirReady()) {
      const dir = path.join(PROOF_DIR, "visual_comparisons");
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, `${safe}_current.png`), Buffer.from(image, "base64"));
      fs.writeFileSync(
        path.join(dir, `${safe}_verdict.txt`),
        `page: ${page.path}\nreference: ${refPath}\n\n${answer}\n`
      );
    }
    return `Compared ${page.path} against ${safe}:\n${answer}`;
  },

  async mp_tap_text({ text, exact = false }) {
    const { page, nodes } = await readPage();
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

  async mp_trigger({ text, into, selector, event, detail = {} }) {
    if (!text && !into && !selector) {
      throw new Error(
        "Name the target with 'text', with 'into' for an input, or with " +
          "'selector' for a component whose own text sits in a child node."
      );
    }
    const { el, target } = await elementTarget({ text, into, selector });
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

  async mp_use_fixture({ scenes, scene } = {}) {
    // Accepts an array, or a single name, or a comma separated string -- a
    // model that has only ever seen the old single-scene form still lands on
    // something the gateway understands.
    const names = (Array.isArray(scenes) ? scenes : String(scenes ?? scene ?? "").split(","))
      .map((s) => String(s).trim())
      .filter(Boolean);
    const body = names.length
      ? await gatewayCall("POST", "/fixture", { scenes: names })
      : await gatewayCall("GET", "/fixture");
    if (!names.length) {
      const { hercules = [], upstream = [] } = body.available ?? {};
      return (
        `Backend data is [${(body.scenes ?? []).join(" + ")}].\n` +
        `State fragments (compose these): ${hercules.join(", ") || "(none)"}\n` +
        `Whole scenes: ${upstream.join(", ")}`
      );
    }
    // The fixture can redefine the user that mp_login_as seeds storage from.
    if (body.defaults && Object.keys(body.defaults).length) FIXTURE = body.defaults;
    return (
      `Backend data is now [${(body.scenes ?? names).join(" + ")}]. Set the login ` +
      "state after this, not before, so it is seeded from this data."
    );
  },

  async mp_calls() {
    const body = await gatewayCall("GET", "/calls");
    const rows = body.calls ?? [];
    if (!rows.length) {
      return (
        "No backend calls recorded yet. Either the page has not loaded, or this " +
        "run is against the real backend, where the mock gateway sees nothing."
      );
    }
    const lines = rows
      .slice()
      .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
      .map((r) => `${r.method} ${r.path}  x${r.count}${r.covered ? "" : "  [uncovered]"}`);
    const uncovered = body.uncovered ?? [];
    // 未覆盖的路由要单独再说一遍，不能只靠上面那行末尾的标记。base fixture 给每条 proto
    // 路由都种了一个只有 baseResp 的空壳，所以没被 fixture 覆盖的接口不会 404，而是
    // "成功但没有数据" —— 页面于是转圈或空白，既不报错也没有线索。这正是一条用例明明
    // 走完了却什么也没验到的典型成因。
    return (
      lines.join("\n") +
      (uncovered.length
        ? `\n\nuncovered (fixture 没有给这些路由准备数据，它们返回的是空壳，` +
          `页面拿不到内容也不会报错): ${uncovered.join(", ")}`
        : "\n\nuncovered: none")
    );
  },

  async mp_ws_push({ event }) {
    if (!event || typeof event !== "object") {
      throw new Error("mp_ws_push needs an `event` object -- the wire envelope to deliver.");
    }
    // A push with nobody subscribed is accepted by the gateway and delivered to
    // no one. Say so here rather than letting the case fail three steps later
    // on a bubble that never appeared.
    const status = await gatewayCall("GET", "/ws");
    if (!(status.connected > 0)) {
      throw new Error(
        "No websocket client is connected, so this event would go nowhere. The " +
          "app subscribes when a chat is open and a turn is live -- open the " +
          "chat and send a message first."
      );
    }
    const result = await gatewayCall("POST", "/ws/push", event);
    if (!(result.pushed > 0)) {
      throw new Error("The gateway accepted the event but delivered it to no client.");
    }
    return `Pushed ${event.event_type || "event"} (event_id=${event.event_id ?? "-"}) to ${result.pushed} client(s).`;
  },

  async mp_ws_disconnect() {
    const result = await gatewayCall("POST", "/ws/disconnect");
    if (!(result.disconnected > 0)) {
      throw new Error("There was no connected websocket client to drop.");
    }
    return `Dropped ${result.disconnected} websocket client(s).`;
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
      const roots = await pageRoots(page);
      if (!roots.length) throw new Error(`Page ${page.path} rendered no nodes to gesture on.`);
      return { el: roots[0].el, label: `page ${page.path}` };
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
    // This used to be clearStorageSync + reLaunch, no restart. That looks like
    // a cold start but is not one: the app's session lives in a module-level
    // singleton (fe_app's useSession.ts `sessionState`), which page navigation
    // does not touch and which reads storage only once. Hercules runs every
    // scenario in this file's suite against the *same* DevTools process --
    // only the MCP server is respawned per scenario, per this file's own
    // design-constraint-① comment -- so a scenario that leaves the in-memory
    // session on a different user or an incomplete-profile state was leaking
    // that into every later scenario's mp_login_as, regardless of which
    // fixture or storage values that later scenario set. Symptom looked like
    // a product bug (report/chat entries stuck behind "complete your profile
    // first"), reproduced identically with hand-written storage that should
    // have satisfied the gate, and only went away after a full
    // restartMiniProgram. Restarting first makes every login an actual cold
    // start, matching what the README already prescribes for switching
    // accounts by hand.
    const restartPath = (await resolveRoute("entry")).path;
    await restartAndSettle(restartPath);
    const mp = await getMiniProgram();
    await mp.callWxMethod("clearStorageSync");

    if (preset === "guest") {
      const home = await resolveRoute("home");
      await mp[home.isTab ? "switchTab" : "reLaunch"](home.path);
      return "Guest state: restarted, storage cleared, on the home page.";
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
    await mp.reLaunch((await resolveRoute("home")).path);
    return `Signed in as ${user.nickname || user.user_id || "the fixture user"}, on the home page.`;
  },

  async mp_use_real_account({ state }) {
    if (!ACCOUNT_POOL_PATH) {
      throw new Error(
        "No account pool configured. Pass --account-pool <path-to-pool.json> in " +
          "this server's args in mcp_servers.json. See opt/.accounts/README.md."
      );
    }
    let pool;
    try {
      pool = JSON.parse(fs.readFileSync(ACCOUNT_POOL_PATH, "utf8"));
    } catch (err) {
      throw new Error(`Could not read account pool at ${ACCOUNT_POOL_PATH}: ${err.message}`);
    }
    if (pool.baseUrl !== REAL_BASE_URL || pool.lane !== REAL_LANE) {
      throw new Error(
        `Account pool was built against ${pool.baseUrl} / ${pool.lane}, but this ` +
          `server is configured for ${REAL_BASE_URL} / ${REAL_LANE}. Accounts from a ` +
          "recycled lane are dead tokens -- refusing to use it rather than fail " +
          "inside an assertion later."
      );
    }
    const accounts = Array.isArray(pool.accounts) ? pool.accounts : [];
    const account = accounts.find(
      (a) => a && a.consumedAtMs == null && (!state || a.state === state)
    );
    if (!account) {
      const byState = {};
      for (const a of accounts) {
        if (a?.consumedAtMs == null) byState[a.state] = (byState[a.state] || 0) + 1;
      }
      const available = Object.entries(byState).map(([k, n]) => `${k} (${n})`).join(", ") || "(none)";
      throw new Error(
        `No unconsumed account${state ? ` for state "${state}"` : ""} in the pool. ` +
          `Unconsumed states on hand: ${available}.`
      );
    }

    // Self-verify against the real backend before touching the app, so a dead
    // or misprovisioned token fails with a clear reason instead of surfacing
    // as "the app won't let me log in" three steps later. Token stays in this
    // process only -- never in the request the model sees.
    // Two things this call has to get right, both learned the hard way:
    //
    // 1. The gateway answers HTTP 200 even for an outright invalid token --
    //    the real verdict is baseResp.statusCode ("0" is success, and an
    //    unauthorized token comes back as 20200002001020). Checking resp.ok
    //    alone accepts literally any string as a valid credential, which
    //    makes this "self-verification" worse than none: it reports
    //    confidence it never established.
    // 2. baseReq is required, and on a GET it travels as query params. Omit
    //    it and every response is BIZ_ESM_GATEWAY_DEGRADED regardless of the
    //    token, so a good account looks broken.
    const verifyUrl =
      `${REAL_BASE_URL}/api/esm/v1/home/state?userId=${encodeURIComponent(account.userId ?? "0")}` +
      `&baseReq.productId=${encodeURIComponent(REAL_PRODUCT_ID)}` +
      `&baseReq.requestId=wxmp-verify-${Date.now()}`;
    let verify;
    try {
      const resp = await fetch(verifyUrl, {
        headers: {
          Authorization: `Bearer ${account.token}`,
          lane: REAL_LANE,
          "x-lane": REAL_LANE,
          "X-Product-ID": REAL_PRODUCT_ID,
        },
      });
      const json = await resp.json().catch(() => null);
      const code = json?.baseResp?.statusCode;
      verify = { ok: resp.ok && code === "0", status: resp.status, code, msg: json?.baseResp?.statusMsg };
    } catch (err) {
      verify = { ok: false, status: 0, error: err.message };
    }
    if (!verify.ok) {
      throw new Error(
        `Pool account "${account.id}" (state ${account.state}) failed self-verification ` +
          `against ${REAL_BASE_URL}/api/esm/v1/home/state: HTTP ${verify.status}` +
          (verify.code ? `, statusCode ${verify.code}` : "") +
          (verify.msg ? ` (${verify.msg})` : "") +
          (verify.error ? ` (${verify.error})` : "") +
          ". Token expired, lane recycled, or the pool was built under a different " +
          `productId than --real-product-id (${REAL_PRODUCT_ID}).`
      );
    }

    // Consume-on-read: mark it spent and write the pool back immediately, so a
    // second call in this or another run cannot hand out the same account.
    // Hercules runs one scenario at a time (no code path in runner.py/
    // __main__.py starts scenarios concurrently), so this simple
    // read-mark-write is enough -- see docs/mp-real-backend-plan.md.
    account.consumedAtMs = Date.now();
    fs.writeFileSync(ACCOUNT_POOL_PATH, JSON.stringify(pool, null, 2));

    // Matches narniaIntegrationAuth.weapp.ts's own real-login path: the
    // phone-registration RPCs this token comes from never return a refresh
    // token, so the app takes a fixed placeholder rather than an empty
    // string -- an empty one fails parseTokenPair's truthiness check and the
    // app falls back to guest.
    const landedOn = await coldSignInWithToken({
      tokens: { accessToken: account.token, refreshToken: "narnia-integration-no-refresh-token" },
      user: { userId: account.userId, phone: account.phone, nickname: account.nickname },
      describeFailure: (page) =>
        `Signed in with pool account "${account.id}" and the backend accepted the ` +
        `token, but the app landed back on ${page} instead of home -- it did not ` +
        "treat this as a logged-in session. Check that --real-base-url/--real-lane " +
        "match the dist-integration build, and that domain checking is off for " +
        "this project path.",
    });
    return `Signed in with real account "${account.id}" (state: ${account.state}), on ${landedOn}.`;
  },

  async mp_login_with_phone({ phone }) {
    const isRandom = !phone;
    const { phone: usePhone, nickname } = isRandom ? randomTestPhone() : { phone, nickname: "手动指定用户" };

    const { session, isNew } = await phoneAuth(usePhone, nickname);
    const user = session.user;
    const landedOn = await coldSignInWithToken({
      tokens: { accessToken: session.accessToken.accessToken, refreshToken: "narnia-integration-no-refresh-token" },
      user: {
        userId: String(user.userId),
        phone: user.phone,
        nickname: user.nickname,
        gender: mapAuthGender(user.gender),
      },
      describeFailure: (page) =>
        `Registered/logged in phone ${usePhone} against the real backend, but the app ` +
        `landed on ${page} instead of home -- it did not treat this as a logged-in ` +
        "session. Check that --real-base-url/--real-lane match the dist-integration " +
        "build, and that domain checking is off for this project path.",
    });
    const maskedPhone = usePhone.length >= 7 ? `${usePhone.slice(0, 3)}****${usePhone.slice(-4)}` : usePhone;
    return (
      `Signed in as ${user.nickname || maskedPhone} (phone ${maskedPhone}, ` +
      `${isNew ? "newly registered" : "existing account"}${isRandom ? ", random" : ""}), on ${landedOn}.`
    );
  },

  async mp_set_storage({ key, value }) {
    const mp = await getMiniProgram();
    await mp.callWxMethod("setStorageSync", key, value);
    return `Set storage ${key}`;
  },

  async mp_get_storage({ key }) {
    if (key === TOKENS_KEY) {
      // Real accounts carry a live credential here (mock fixtures only ever
      // hold access-token-001), and this tool's whole job is to hand its
      // return value to the model -- the one place mp_login_as/
      // mp_use_real_account deliberately avoid. Redact rather than trust the
      // caller to know not to read this key.
      return JSON.stringify({ redacted: "esm_tokens withheld -- contains a live credential" });
    }
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
    // Unlike the automatic capture, this one was explicitly asked for, so a
    // proof dir that cannot be written to is reported rather than swallowed:
    // returning "saved" for a file that went into ./undefined/ is worse than
    // failing, because the case then passes citing evidence nobody can find.
    if (!proofDirReady()) {
      throw new Error(
        `Cannot save a screenshot: proof dir is unusable (${PROOF_DIR}). ` +
          "Check --proof-dir in this server's args in mcp_servers.json."
      );
    }
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
    // This used to ship a fixed callback that ran eval(code) inside the mini
    // program. The logic layer has no eval (nor new Function), so every call
    // died with "Uncaught eval is not a function" -- the escape hatch was
    // unusable for its entire life.
    //
    // miniprogram-automator serializes the callback with
    // Function.prototype.toString() and hands that source to DevTools to
    // evaluate, so building the function here in Node bakes the expression
    // into the source that gets sent. Nothing needs eval on the app side.
    let fn;
    try {
      fn = new Function(`return (${expression});`);
    } catch (err) {
      throw new Error(
        `Not a valid JS expression: ${err.message}. This runs as an expression, ` +
          "not a statement block -- use an IIFE if you need statements."
      );
    }
    const result = await mp.evaluate(fn);
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

/**
 * Final-state evidence, captured as this process is killed.
 *
 * Auto-capture only fires on tools in CAPTURED -- all of them actions. A
 * scenario's last step is almost always an assertion, which goes through
 * mp_snapshot and takes no picture, so the last screenshot on disk shows the
 * page *before* the final navigation. 124 of 126 mp cases end that way, which
 * makes this a property of the mechanism rather than a per-case oversight:
 * the most important moment of a run is the one nothing photographs.
 *
 * Hercules kills this server after every scenario (MCPHelper.destroy), so
 * SIGTERM is exactly "the scenario just ended" -- one extra picture, no case
 * edits. Best-effort throughout: a screenshot must never delay or break exit,
 * and the connection may already be half dead, hence the timeout race.
 */
async function captureFinalState() {
  // Skipped when no proof dir was configured: this fires on every SIGTERM, so
  // without the guard `npm run smoke` and every manual run would leave a stray
  // screenshot in mcp_servers/wxmp/. Per-action captures keep their old cwd
  // behaviour -- those happen because someone drove a step and may want to see
  // it; this one is automatic and unasked for.
  if (!PROOF_DIR_EXPLICIT) return;
  if (!AUTO_SHOT || !miniProgram || !proofDirReady()) return;
  const name = `${RUN_STAMP}_999_final_state.png`;
  try {
    await Promise.race([
      miniProgram.screenshot({ path: path.join(PROOF_DIR, name) }),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("timed out after 5s")), 5000)
      ),
    ]);
    log(`final-state screenshot: ${name}`);
  } catch (err) {
    log(`final-state screenshot failed: ${err.message}`);
    noteEvidenceGap(`final state: ${err.message}`);
  }
}

let shuttingDown = false;
async function shutdown() {
  // SIGINT then SIGTERM in quick succession would otherwise run this twice and
  // race the screenshot against its own disconnect.
  if (shuttingDown) return;
  shuttingDown = true;
  await captureFinalState();
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
  }, auto-screenshot=${AUTO_SHOT ? PROOF_DIR : "off"}, vision=${VISION_READY ? VISION_MODEL : "off"})`
);
