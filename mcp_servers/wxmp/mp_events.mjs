/**
 * 把 miniprogram-automator 的 console / exception 事件，归一成 Hercules observer 认识的
 * 那一行 JSON。
 *
 * 单独成文件是为了能被 `node --test` 测到：这两个 handler 的输入形状拿不到确切定义
 * （automator 的 .d.ts 里 `on(event, listener: (...args: any[]) => void)`，负载是
 * 开发者工具协议原样透传的 `App.logAdded` / `App.exceptionThrown`），而形状猜错的后果
 * 不是报错，是**静默什么都不产出**——观测链路本身的假绿，比没有观测更糟，因为报告上
 * 会显示「跑过了、没发现异常」。
 *
 * 所以这里的原则是：多种形状都试，一种都对不上时也要把原始负载序列化出来，宁可留一行
 * 看不懂的证据，也不要悄悄丢掉。第一次真机跑起来时，看一眼 mp_console_logs.json 就知道
 * 实际形状是哪一种，再来收窄。
 *
 * 行形状和 playwright_manager 写的 console_logs.json 保持一致，因为判定口径只有一处，
 * 在 testzeus_hercules/core/invariants.py。这边不判红。
 */

const MAX_TEXT = 1000;

/**
 * 开发者工具把 Error 对象序列化成 `{}` —— message 和 stack 全丢，到手就是个空对象。
 * （实测：`console.error(new Error("x"))` 收到的 arg 是 `{}`，Object.keys 和
 * getOwnPropertyNames 都是空，String() 得到 "[object Object]"。）
 *
 * 直接写下 "{}" 的话，报告里留下的是一条毫无信息的记录，而它其实说明「应用记了一个错误，
 * 内容看不见」——那正是该有人去看一眼的情况。所以标出来，别让它伪装成普通日志。
 *
 * 未捕获的异常不走这条路，走 exception 事件，那边 message 和 stack 都在。这里丢内容的
 * 只有应用主动 console.error 出来的、已被它自己处理掉的错误。
 */
const UNSERIALIZABLE = "\u0000unserializable";

function stringifyArg(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") {
    try {
      const text = JSON.stringify(value);
      if (!text || text === "{}" || text === "[]") return UNSERIALIZABLE;
      return text;
    } catch {
      return UNSERIALIZABLE;
    }
  }
  return String(value);
}

function joinArgs(value) {
  const list = Array.isArray(value) ? value : [value];
  const parts = list.map(stringifyArg).filter(Boolean);
  if (parts.length && parts.every((p) => p === UNSERIALIZABLE)) {
    return "（参数无法序列化，多半是 Error 对象；开发者工具把它压成了空对象，message 和 stack 都取不到）";
  }
  return parts.map((p) => (p === UNSERIALIZABLE ? "（无法序列化的对象）" : p)).join(" ");
}

/** console 事件 -> 一行证据，取不出任何文本时返回 null。 */
export function normalizeConsoleEvent(msg) {
  if (msg === null || msg === undefined) return null;
  if (typeof msg === "string") {
    return msg.trim() ? line("log", msg) : null;
  }

  const level = String(msg.type ?? msg.level ?? msg.logType ?? "log");
  // 依次试：CDP 风格的 args、devtools 直接给的 text/message。
  let text = "";
  let recognized = false;
  for (const [present, extract] of [
    [msg.args !== undefined, () => joinArgs(msg.args)],
    [typeof msg.text === "string", () => msg.text],
    [typeof msg.message === "string", () => msg.message],
    [Array.isArray(msg.arguments), () => joinArgs(msg.arguments)],
  ]) {
    if (!present) continue;
    recognized = true;
    if (!text) text = extract();
  }
  if (!recognized) {
    // 一个已知字段都没有，说明形状没见过。整包塞进 text 而不是返回 null —— 丢掉它等于
    // 让观测链路悄悄断掉，而报告上会显示「跑过了、没发现异常」。这一行留在证据里，
    // 第一次真机跑完看一眼就知道该怎么收窄。
    //
    // 反过来，字段认得出但内容是空的（args: []），那是「这条事件确实没有文本」，
    // 直接丢掉即可，不该当成未知形状再倒一遍原始负载。
    text = stringifyArg(msg);
  }
  return text.trim() ? line(level, text) : null;
}

/** exception 事件 -> 一行证据。永远带 Uncaught 前缀。 */
export function normalizeExceptionEvent(err) {
  // 未捕获异常在 automator 里是独立事件，不走 console。判定侧靠 Uncaught / TypeError
  // 这类签名把「真的炸了」和「应用自己 console.error 出来的业务提示」分开，所以这里
  // 必须显式带上前缀 —— 异常对象自身的 message 未必含有它。
  let body = "";
  if (typeof err === "string") body = err;
  else if (err && typeof err === "object") {
    body = [err.message, err.stack, err.exceptionDetails && err.exceptionDetails.text].filter(Boolean).join("\n");
    if (!body) body = stringifyArg(err);
  }
  return line("error", `Uncaught ${body || "exception"}`);
}

function line(level, text) {
  return {
    type: "console",
    level: String(level),
    text: String(text).slice(0, MAX_TEXT),
    location: { url: "miniprogram" },
  };
}
