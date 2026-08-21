/**
 * Standalone launcher for the mini program mock gateway.
 *
 * The e2e suite starts this gateway per vitest run and tears it down after
 * (test-env.ts), which does not fit Hercules: it drives a long-lived DevTools
 * instance and respawns the MCP server once per scenario. So we run the same
 * gateway out of band, on the ports baked into the dist-scenario bundle.
 *
 * Because it is long-lived it also has to do what per-run gateways got for
 * free: serve a different fixture scene. A control port answers that -- POST
 * a scene name and the gateway swaps its fixture in place, which is what
 * mp_use_fixture calls so a case can ask for the account state it needs.
 *
 * Run it with the e2e package's tsx, which already has the deps installed:
 *
 *   cd <miniapp>/fe_app/packages/e2e
 *   ./node_modules/.bin/tsx <this file>
 *
 * Env:
 *   MINIAPP_REPO      repo root (default: the path this was developed against)
 *   GATEWAY_PORT      HTTPS port, must match the bundle (default 39100)
 *   GATEWAY_WS_PORT   WSS port, must match the bundle  (default 39101)
 *   GATEWAY_CTRL_PORT plain-HTTP control port for fixture swaps (default 39102)
 *   FIXTURE_SCENE     fixture directory under tests/fixtures (default "base")
 */
import fs from "node:fs"
import path from "node:path"
import { createServer } from "node:http"
import { fileURLToPath } from "node:url"
import { startGateway } from "../../../mini/esm_mini_app/fe_app/packages/gateway/src/server"
import { loadMergedFixture } from "../../../mini/esm_mini_app/fe_app/packages/e2e/tests/utils/fixture"

const PORT = Number(process.env.GATEWAY_PORT || 39100)
const WS_PORT = Number(process.env.GATEWAY_WS_PORT || 39101)
const CTRL_PORT = Number(process.env.GATEWAY_CTRL_PORT || 39102)
const SCENE = process.env.FIXTURE_SCENE || "base"

const fixturesDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../mini/esm_mini_app/fe_app/packages/e2e/tests/fixtures"
)

/**
 * loadMergedFixture falls back to base for a scene it cannot find instead of
 * failing, so a typo would quietly serve the wrong data and the case would
 * fail somewhere far away. Check the directory ourselves and say what exists.
 */
function knownScenes(): string[] {
  return fs
    .readdirSync(fixturesDir, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith("_"))
    .map((e) => e.name)
    .sort()
}

function requireScene(scene: string): void {
  if (knownScenes().includes(scene)) return
  throw new Error(
    `Unknown fixture scene "${scene}". Available: ${knownScenes().join(", ")}`
  )
}

requireScene(SCENE)
let currentScene = SCENE
const fixture = await loadMergedFixture(SCENE)
const handle = await startGateway({
  preferredPort: PORT,
  preferredWsPort: WS_PORT,
  fixture,
})

const describe = (f: Awaited<ReturnType<typeof loadMergedFixture>>) => ({
  routes: f.routes?.length ?? 0,
  user: (f.defaults as any)?.user?.nickname ?? "(none)",
})

const readBody = (req: Parameters<Parameters<typeof createServer>[0]>[0]) =>
  new Promise<string>((resolve, reject) => {
    let raw = ""
    req.on("data", (chunk) => {
      raw += chunk
      if (raw.length > 1e6) reject(new Error("control request body too large"))
    })
    req.on("end", () => resolve(raw))
    req.on("error", reject)
  })

const control = createServer(async (req, res) => {
  const send = (status: number, payload: unknown) => {
    res.writeHead(status, { "content-type": "application/json" })
    res.end(JSON.stringify(payload))
  }
  try {
    if (req.method === "GET" && req.url === "/fixture") {
      return send(200, { ok: true, scene: currentScene, scenes: knownScenes() })
    }
    if (req.method === "POST" && req.url === "/fixture") {
      const { scene } = JSON.parse((await readBody(req)) || "{}")
      if (!scene || typeof scene !== "string") {
        return send(400, { ok: false, error: "body must be {\"scene\": \"<name>\"}" })
      }
      requireScene(scene)
      const next = await loadMergedFixture(scene)
      handle.setFixture(next)
      currentScene = scene
      console.log(`fixture  -> "${scene}" (${describe(next).routes} routes, user=${describe(next).user})`)
      // The MCP server seeds login storage from these, so hand them back
      // rather than making it re-read a file that only describes base.
      return send(200, { ok: true, scene, defaults: next.defaults ?? {} })
    }
    send(404, { ok: false, error: `no control route for ${req.method} ${req.url}` })
  } catch (err) {
    send(400, { ok: false, error: (err as Error).message })
  }
})
control.listen(CTRL_PORT, "127.0.0.1")

console.log(`gateway  https://localhost:${handle.port}`)
console.log(`ws       wss://localhost:${WS_PORT}`)
console.log(`control  http://127.0.0.1:${CTRL_PORT}/fixture  (POST {"scene":"..."} to swap)`)
console.log(
  `fixture  scene="${SCENE}", ${describe(fixture).routes} routes, user=${describe(fixture).user}`
)
console.log(`scenes   ${knownScenes().length} available under tests/fixtures`)
console.log("ready - leave this running while Hercules drives the mini program")

for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    control.close()
    handle.close()
    process.exit(0)
  })
}
