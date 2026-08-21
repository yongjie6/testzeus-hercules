#!/usr/bin/env node
/**
 * Refuse to start a run against a half-working environment.
 *
 * A run once burned 24 minutes and reported 7 passes against a DevTools
 * instance launched with --cli, which has no renderer: currentPage and
 * snapshots answered normally because the logic layer was alive, so nothing
 * looked wrong until the proofs directory turned out to be empty.
 *
 * Screenshot capture is the one call that needs the renderer, which makes it
 * the only cheap proof that the simulator is really running. Checked last,
 * after the things whose absence would explain it.
 */
import automator from "miniprogram-automator";
import { applyDevToolsCompat } from "./compat.mjs";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";

const WS = process.env.WXMP_WS_ENDPOINT || "ws://localhost:9420";
const GATEWAY_PORT = Number(process.env.GATEWAY_PORT || 39100);

const listening = (port) =>
  new Promise((resolve) => {
    const socket = net.connect({ port, host: "127.0.0.1" });
    socket.setTimeout(2000);
    socket.on("connect", () => (socket.destroy(), resolve(true)));
    socket.on("error", () => resolve(false));
    socket.on("timeout", () => (socket.destroy(), resolve(false)));
  });

// disconnect() returns undefined rather than a promise, so it cannot be
// awaited or .catch()'d; ignore whatever it throws.
const bye = (mp) => { try { mp?.disconnect(); } catch { /* already gone */ } };

const fail = (what, why, fix) => {
  console.error(`\n✗ ${what}\n  ${why}\n  → ${fix}\n`);
  process.exit(1);
};

applyDevToolsCompat(() => {});

if (!(await listening(GATEWAY_PORT))) {
  fail(
    `mock 网关未监听 ${GATEWAY_PORT}`,
    "小程序包里烧死了这个地址，网关不在时页面会一路显示加载失败。",
    "cd <miniapp>/fe_app/packages/e2e && ./node_modules/.bin/tsx <repo>/mcp_servers/wxmp/gateway.mts"
  );
}
console.log(`  ✓ mock 网关 ${GATEWAY_PORT}`);

let mp;
try {
  mp = await automator.connect({ wsEndpoint: WS, timeout: 15000 });
} catch (err) {
  fail(
    `连不上自动化端口 ${WS}`,
    err.message,
    "确认开发者工具已打开项目，并起一个 auto 会话：cli auto --project <dist-scenario> --auto-port 9420"
  );
}

const page = await mp.currentPage();
console.log(`  ✓ 自动化连接，当前页 ${page.path}`);

const probe = path.join(os.tmpdir(), `wxmp-preflight-${process.pid}.png`);
try {
  await mp.screenshot({ path: probe });
  if (!fs.existsSync(probe) || fs.statSync(probe).size === 0) throw new Error("文件没有生成");
  console.log(`  ✓ 渲染层，截图 ${Math.round(fs.statSync(probe).size / 1024)}KB`);
  fs.unlinkSync(probe);
} catch (err) {
  bye(mp);
  fail(
    "截不了图，模拟器没有在渲染",
    `${err.message}。逻辑层是活的（上面刚读到页面路由），所以用例仍会「跑完」，` +
      "但拿不到任何证据，而且判定是否可信无法确认——多半是开发者工具被 --cli 拉起的无界面实例占着。",
    "pkill -f wechatwebdevtools，然后从启动台正常打开、登录、开项目，再起 auto 会话"
  );
}

bye(mp);
console.log("  环境完整，开始执行\n");
