/**
 * Compatibility shim for WeChat DevTools 2.02+.
 *
 * miniprogram-automator's connect() calls MiniProgram.checkVersion(), which
 * reads `SDKVersion` off `Tool.getInfo` and feeds it to licia/cmpVersion:
 *
 *   t = (await this.send("Tool.getInfo")).SDKVersion
 *   if (t !== "dev" && cmpVersion(t, "2.7.3") < 0) throw ...
 *
 * DevTools 2.02 renamed that field to `version`, so the SDK reads undefined and
 * cmpVersion dies with "Cannot read properties of undefined (reading 'split')"
 * before connect() ever returns. Observed on DevTools 2.02.2608040 with
 * miniprogram-automator 0.12.1.
 *
 * The guard only exists to reject base libraries older than 2.7.3, which no
 * current DevTools ships, and every automation API works normally once past it.
 * So we replace it with a version probe that reports what it found and never
 * throws. Revisit if a future automator release reads the new field itself.
 */
import MiniProgramModule from "miniprogram-automator/out/MiniProgram.js";

let applied = false;

export function applyDevToolsCompat(log = () => {}) {
  if (applied) return;
  const MiniProgram = MiniProgramModule.default || MiniProgramModule;
  MiniProgram.prototype.checkVersion = async function checkVersion() {
    try {
      const info = await this.send("Tool.getInfo");
      const version = info?.version || info?.SDKVersion || "unknown";
      log(`DevTools version ${version} (SDK version check bypassed)`);
    } catch (err) {
      log(`could not read DevTools version: ${err.message}`);
    }
  };
  applied = true;
}
