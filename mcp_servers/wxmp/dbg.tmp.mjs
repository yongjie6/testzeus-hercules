import automator from "miniprogram-automator";
try {
  const mp = await automator.connect({ wsEndpoint: "ws://localhost:9420", timeout: 15000 });
  console.log("connect OK");
  for (const [label, fn] of [
    ["currentPage", () => mp.currentPage()],
    ["systemInfo",  () => mp.systemInfo()],
    ["pageStack",   () => mp.pageStack()],
  ]) {
    try { const r = await fn(); console.log(`  ✅ ${label}:`, JSON.stringify(r)?.slice(0,120)); }
    catch (e) { console.log(`  ❌ ${label}: ${e.message}`); console.log(String(e.stack).split("\n").slice(1,4).join("\n")); }
  }
  await mp.disconnect();
} catch (e) {
  console.log("connect FAILED:", e.message);
  console.log(e.stack);
}
