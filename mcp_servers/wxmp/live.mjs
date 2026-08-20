#!/usr/bin/env node
/**
 * Live check: proves the full path to a REAL WeChat DevTools instance.
 * Unlike smoke.mjs this needs DevTools running with the automation port open.
 *
 *   node live.mjs
 *
 * Read-only: it reports the current page, storage keys and any web-view URL.
 * It does not tap, navigate or write anything.
 */
import automator from "miniprogram-automator";
import { applyDevToolsCompat } from "./compat.mjs";

applyDevToolsCompat((msg) => console.log(`  ${msg}`));

const WS = process.env.WXMP_WS_ENDPOINT || "ws://localhost:9420";

const step = (label, value) => console.log(`  ${label.padEnd(16)} ${value}`);

try {
  console.log(`connecting to ${WS} ...`);
  const mp = await automator.connect({ wsEndpoint: WS, timeout: 15000 });
  console.log("connected\n");

  const page = await mp.currentPage();
  step("current page", page.path);
  step("query", JSON.stringify(page.query || {}));

  const info = await mp.systemInfo();
  step("platform", `${info.platform} ${info.SDKVersion || ""}`.trim());

  const storage = await mp.callWxMethod("getStorageInfoSync");
  step("storage keys", (storage?.keys || []).join(", ") || "(empty)");

  const wv = await page.$("web-view");
  step("web-view", wv ? await wv.attribute("src") : "none on this page");

  await mp.disconnect();
  console.log("\nOK - the automation path works end to end.");
  process.exit(0);
} catch (err) {
  console.error(`\nFAILED: ${err.message}\n`);
  console.error("Checklist:");
  console.error("  1. DevTools open with the project loaded");
  console.error("  2. Settings -> Security -> Service Port (CLI/HTTP) is ON");
  console.error("  3. An automation session is running on that port:");
  console.error(
    "     /Applications/wechatwebdevtools.app/Contents/MacOS/cli auto \\"
  );
  console.error("       --project <project-dir> --auto-port 9420");
  process.exit(1);
}
