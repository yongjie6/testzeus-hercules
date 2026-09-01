/**
 * 这两个 handler 的输入形状拿不到确切定义，而猜错的后果是静默无输出——观测链路自己的
 * 假绿。所以每一种可能的形状都要有用例，尤其是「形状没见过」那条兜底。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeConsoleEvent, normalizeExceptionEvent } from "./mp_events.mjs";

test("CDP 风格的 {type, args}", () => {
  const out = normalizeConsoleEvent({ type: "error", args: ["Uncaught TypeError:", "x is not a function"] });
  assert.equal(out.level, "error");
  assert.equal(out.text, "Uncaught TypeError: x is not a function");
  assert.equal(out.type, "console");
});

test("devtools 直接给 text", () => {
  assert.equal(normalizeConsoleEvent({ type: "warn", text: "deprecated" }).text, "deprecated");
});

test("给的是 message", () => {
  assert.equal(normalizeConsoleEvent({ level: "error", message: "boom" }).text, "boom");
});

test("非字符串参数会被序列化，不会变成 [object Object]", () => {
  assert.equal(normalizeConsoleEvent({ type: "log", args: [{ a: 1 }] }).text, '{"a":1}');
});

test("裸字符串", () => {
  assert.equal(normalizeConsoleEvent("hello").text, "hello");
});

test("形状没见过时整包留证，不静默丢掉", () => {
  // 丢掉它等于让观测链路悄悄断掉，而报告上会显示「跑过了、没发现异常」。
  const out = normalizeConsoleEvent({ weird: "shape", n: 1 });
  assert.ok(out, "不该返回 null");
  assert.ok(out.text.includes("weird"));
});

test("空事件返回 null", () => {
  assert.equal(normalizeConsoleEvent(null), null);
  assert.equal(normalizeConsoleEvent({ type: "log", args: [] }), null);
  assert.equal(normalizeConsoleEvent("   "), null);
});

test("异常永远带 Uncaught 前缀", () => {
  // 判定侧靠这些签名把「真的炸了」和「应用自己打的业务提示」分开，
  // 而异常对象自身的 message 未必含有它。
  const out = normalizeExceptionEvent({ message: "读不到 resultId", stack: "at useResult" });
  assert.equal(out.level, "error");
  assert.ok(out.text.startsWith("Uncaught "));
  assert.ok(out.text.includes("读不到 resultId"));
});

test("异常是裸字符串 / 空对象也要产出一行", () => {
  assert.ok(normalizeExceptionEvent("boom").text.startsWith("Uncaught boom"));
  assert.ok(normalizeExceptionEvent({}).text.startsWith("Uncaught "));
});

test("超长文本被截断", () => {
  assert.equal(normalizeConsoleEvent({ type: "log", args: ["x".repeat(5000)] }).text.length, 1000);
});

test("Error 对象被开发者工具压成 {} 时要标出来，不能写下一个没信息的 {}", () => {
  // 实测：console.error(new Error("x")) 收到的 arg 就是 {}，keys 和 ownProps 全空。
  // 写下 "{}" 等于在报告里留一条毫无信息的记录，而它其实说明「应用记了个错误，看不见内容」。
  const out = normalizeConsoleEvent({ type: "error", args: [{}] });
  assert.ok(out.text.includes("无法序列化"), out.text);
  assert.ok(out.text.includes("Error"), "要点明多半是什么");
});

test("能序列化的对象参数照常输出", () => {
  assert.equal(normalizeConsoleEvent({ type: "log", args: [{ a: 1 }] }).text, '{"a":1}');
});

test("混合参数里只把丢内容的那个标出来", () => {
  const out = normalizeConsoleEvent({ type: "error", args: ["请求失败", {}] });
  assert.ok(out.text.startsWith("请求失败"));
  assert.ok(out.text.includes("无法序列化"));
});
