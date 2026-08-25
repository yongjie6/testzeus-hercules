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

/**
 * mock（默认）跑 dist-scenario，数据来自本地 mock 网关；real 跑 dist-integration，
 * 数据来自真实泳道，那时候 39100 根本不该被要求在监听。
 *
 * real 模式只少了网关这一项，开发者工具和渲染层照查——「对着无渲染实例跑满 24 分钟」
 * 那个坑跟后端是谁无关。
 *
 * 这里不试图判断开发者工具里打开的到底是哪个包：cli 不暴露这个，磁盘上的构建产物又和
 * 已加载的项目没有绑定关系。那一层由用例自己的第一条断言兜底——「使用 Narnia 测试账号」
 * 按钮只在 integration 包里渲染，是可观测的包指纹。
 */
const MODE = process.argv.includes("--mode") ? process.argv[process.argv.indexOf("--mode") + 1] : "mock";
if (!["mock", "real"].includes(MODE)) {
  console.error(`\n✗ 未知的 --mode ${MODE}\n  可选: mock（默认，dist-scenario + mock 网关）/ real（dist-integration + 真实泳道）\n`);
  process.exit(1);
}

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

if (MODE === "mock") {
  if (!(await listening(GATEWAY_PORT))) {
    fail(
      `mock 网关未监听 ${GATEWAY_PORT}`,
      "小程序包里烧死了这个地址，网关不在时页面会一路显示加载失败。",
      "cd <miniapp>/packages/e2e && ./node_modules/.bin/tsx <repo>/mcp_servers/wxmp/gateway.mts"
    );
  }
  console.log(`  ✓ mock 网关 ${GATEWAY_PORT}`);
} else {
  console.log("  · real 模式，跳过 mock 网关检查（数据来自真实泳道）");
}

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
      "但拿不到任何证据，而且判定是否可信无法确认。见过两种原因：" +
      "一是开发者工具被 --cli 拉起的无界面实例占着；" +
      "二是界面开着但丢了渲染层（登录过期、项目没打开、或者长时间挂机后模拟器自己停了）——" +
      "这种在窗口里看得出来，模拟器那半边是空的。",
    "先看一眼开发者工具窗口：模拟器有画面就重新登录/重开项目；" +
      "没窗口或占着端口的是无界面实例，pkill -f wechatwebdevtools 后从启动台正常打开"
  );
}

bye(mp);
console.log("  环境完整，开始执行\n");
