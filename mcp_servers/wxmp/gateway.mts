/**
 * Standalone launcher for the mini program mock gateway.
 *
 * The e2e suite starts this gateway per vitest run and tears it down after
 * (test-env.ts), which does not fit Hercules: it drives a long-lived DevTools
 * instance and respawns the MCP server once per scenario. So we run the same
 * gateway out of band, on the ports baked into the dist-scenario bundle.
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
 *   FIXTURE_SCENE     fixture directory under tests/fixtures (default "base")
 */
import { startGateway } from "../../../mini/esm_mini_app/fe_app/packages/gateway/src/server"
import { loadMergedFixture } from "../../../mini/esm_mini_app/fe_app/packages/e2e/tests/utils/fixture"

const PORT = Number(process.env.GATEWAY_PORT || 39100)
const WS_PORT = Number(process.env.GATEWAY_WS_PORT || 39101)
const SCENE = process.env.FIXTURE_SCENE || "base"

const fixture = await loadMergedFixture(SCENE)
const handle = await startGateway({
  preferredPort: PORT,
  preferredWsPort: WS_PORT,
  fixture,
})

const routeCount = fixture.routes?.length ?? 0
const user = (fixture.defaults as any)?.user
console.log(`gateway  https://localhost:${handle.port}`)
console.log(`ws       wss://localhost:${WS_PORT}`)
console.log(`fixture  scene="${SCENE}", ${routeCount} routes, user=${user?.nickname ?? "(none)"}`)
console.log("ready - leave this running while Hercules drives the mini program")

for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    handle.close()
    process.exit(0)
  })
}
