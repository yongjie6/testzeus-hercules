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
 * scene names and the gateway swaps its fixture in place, which is what
 * mp_use_fixture calls so a case can ask for the account state it needs.
 *
 * Run it with the e2e package's tsx, which already has the deps installed:
 *
 *   cd <miniapp>/packages/e2e
 *   ./node_modules/.bin/tsx <this file>
 *
 * Env:
 *   MINIAPP_REPO      fe_app checkout to read from (default: ../../../mini/esm_fe_app)
 *   GATEWAY_PORT      HTTPS port, must match the bundle (default 39100)
 *   GATEWAY_WS_PORT   WSS port, must match the bundle  (default 39101)
 *   GATEWAY_CTRL_PORT plain-HTTP control port for fixture swaps (default 39102)
 *   FIXTURE_SCENE     comma-separated scene/fragment names (default "base")
 */
import fs from "node:fs"
import path from "node:path"
import { createServer } from "node:http"
import https from "node:https"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))

/**
 * Which fe_app checkout to read the gateway and fixtures from.
 *
 * This used to be hardcoded to ../../../mini/esm_mini_app/fe_app. That is a
 * *second checkout of the same repo* pinned to a v2-era branch, so once the
 * v3.0 bundle was loaded in DevTools the run served v1 fixtures against a v3.0
 * app -- pages rendered, data was wrong, and nothing anywhere said so. The env
 * var was already documented above; it just was not wired to anything.
 */
const MINIAPP_REPO = path.resolve(
  process.env.MINIAPP_REPO || path.join(HERE, "../../../mini/esm_fe_app")
)

const PORT = Number(process.env.GATEWAY_PORT || 39100)
const WS_PORT = Number(process.env.GATEWAY_WS_PORT || 39101)
const CTRL_PORT = Number(process.env.GATEWAY_CTRL_PORT || 39102)
const SCENE = process.env.FIXTURE_SCENE || "base"

const upstreamDir = path.join(MINIAPP_REPO, "packages/e2e/tests/fixtures")

/**
 * Fixture fragments Hercules owns.
 *
 * They deliberately do NOT live in the fe_app repo. That repo's
 * scripts/check-spec-mappings.mjs rejects any TC-* fixture directory that is
 * not also a row in its own TC registry ("orphan fixture"), so a QA-only scene
 * cannot sit beside theirs without fabricating a registry row and a vitest
 * spec to match. These layer on top of theirs instead: we consume what they
 * publish, and own what only Hercules needs.
 */
const overlayDir = path.join(HERE, "fixtures")

for (const [what, dir] of [
  ["gateway/fixture source", upstreamDir],
  ["gateway package", path.join(MINIAPP_REPO, "packages/gateway/src")],
] as const) {
  if (!fs.existsSync(dir)) {
    throw new Error(
      `Cannot find the ${what} at ${dir}. Point MINIAPP_REPO at the fe_app ` +
        `checkout you are testing (the directory that contains packages/).`
    )
  }
}

/**
 * Structural copies of the upstream types. Naming them here rather than
 * importing them keeps MINIAPP_REPO the single place the checkout is spelled
 * out -- a type import would reintroduce a hardcoded path, and it is exactly a
 * hardcoded path that served v1 fixtures to a v3.0 app.
 */
type Route = { path: string; method?: string; response?: any; responseSequence?: any[]; stream?: unknown; [key: string]: unknown }
type Fixture = { baseResp?: Record<string, unknown>; routes?: Route[]; defaults?: Record<string, any> }
type GatewayHandle = { port: number; close: () => void; setFixture: (fixture: Fixture) => void }

/**
 * Loaded through createRequire, not import().
 *
 * Both upstream packages are "type": "module", but their sources still use
 * __dirname (tests/utils/fixture.ts:7). tsx shims that when it transpiles a
 * module to CJS, which is what require() gets you. A dynamic import() of an
 * absolute .ts path is instead handled by Node 24's native type stripping,
 * which bypasses tsx entirely and leaves __dirname undefined -- so it throws
 * at load. Static imports worked for the same reason require does; they just
 * cannot take a path computed at runtime, which MINIAPP_REPO has to be.
 */
const require = createRequire(import.meta.url)
const load = (rel: string): any => require(path.join(MINIAPP_REPO, rel))

const { startGateway } = load("packages/gateway/src/server.ts") as {
  startGateway: (opts: { preferredPort?: number; preferredWsPort?: number; fixture?: Fixture }) => Promise<GatewayHandle>
}
const { loadBaseFixture, loadFixture } = load("packages/e2e/tests/utils/fixture.ts") as {
  loadBaseFixture: () => Promise<Fixture>
  loadFixture: (scene: string) => Promise<Fixture>
}

/* ------------------------------------------------------------------ *
 * Scene composition
 * ------------------------------------------------------------------ */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  Boolean(v) && typeof v === "object" && !Array.isArray(v)

/**
 * The upstream loader merges `defaults` exactly one level deep (mergeObjects
 * in tests/utils/fixture.ts) and merges a route with a flat spread. That is
 * enough when one scene replaces another wholesale, which is all their vitest
 * suite ever does.
 *
 * Composing fragments needs more. The state axes a case names -- 量表已完成,
 * 报告已就绪, 候选关系 1 条 -- are mostly *different fields of the same*
 * GetHomeState response, because that endpoint aggregates the whole home
 * state. Under a shallow merge the last fragment listed would silently drop
 * every field the earlier ones set. Arrays still replace rather than
 * concatenate: a fragment saying "候选关系 6 条" means exactly six, not six
 * appended to whatever was there.
 */
function deepMerge(base: unknown, override: unknown): unknown {
  if (!isRecord(base) || !isRecord(override)) return override === undefined ? base : override
  const out: Record<string, unknown> = { ...base }
  for (const [key, value] of Object.entries(override)) {
    out[key] = key in out ? deepMerge(out[key], value) : value
  }
  return out
}

const routeKey = (route: Route): string => `${(route.method ?? "POST").toUpperCase()} ${route.path}`

function mergeFixture(base: Fixture, override: Fixture): Fixture {
  const routes = new Map<string, Route>()
  for (const route of base.routes ?? []) if (route?.path) routes.set(routeKey(route), route)
  for (const route of override.routes ?? []) {
    if (!route?.path) continue
    const key = routeKey(route)
    const prev = routes.get(key)
    routes.set(key, prev ? (deepMerge(prev, route) as Route) : route)
  }
  return {
    baseResp: override.baseResp ?? base.baseResp,
    defaults: deepMerge(base.defaults ?? {}, override.defaults ?? {}) as Fixture["defaults"],
    routes: Array.from(routes.values()),
  }
}

const dayMs = 24 * 60 * 60 * 1000

/**
 * Mirrors resolvePlaceholders in tests/utils/fixture.ts, which is not exported.
 * loadMergedFixture applies it before handing the fixture over, so composing
 * by hand has to as well or every `__TS_TODAY__` reaches the app verbatim.
 */
function resolvePlaceholders<T>(payload: T): T {
  const todayStart = new Date().setHours(0, 0, 0, 0)
  const twoAm = 2 * 60 * 60 * 1000
  const baseMap: Record<string, number> = {
    __TS_TODAY__: todayStart + twoAm,
    __TS_YESTERDAY__: todayStart - dayMs + twoAm,
    __TS_LAST7__: todayStart - 3 * dayMs + twoAm,
    __TS_LAST30__: todayStart - 10 * dayMs + twoAm,
    __TS_EARLIER__: todayStart - 45 * dayMs + twoAm,
  }
  const resolve = (value: unknown): unknown => {
    if (value === null || value === undefined) return value
    if (typeof value === "string") {
      const match = value.match(/^(__TS_[A-Z0-9]+__)([+-]\d+)?$/)
      if (!match) return value
      const base = baseMap[match[1]]
      if (base === undefined) return value
      return String(base + (match[2] ? Number(match[2]) : 0) * 1000)
    }
    if (Array.isArray(value)) return value.map(resolve)
    if (typeof value === "object") {
      return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, resolve(v)]))
    }
    return value
  }
  return resolve(payload) as T
}

const isLeafFixtureDir = (dir: string): boolean =>
  fs.existsSync(path.join(dir, "defaults")) || fs.existsSync(path.join(dir, "routes"))

/**
 * A scene name is normally a single top-level directory, but a handful of
 * upstream cases nest several scenes under one TC directory --
 * TC-HOME-001/snapshot and TC-HOME-001/candidate-mutation are two different
 * scenes sharing one owner. Their own vitest specs already reference them by
 * that slashed path (tests/TC-HOME-001.spec.ts: `scene: 'TC-HOME-001/snapshot'`)
 * and loadFixtureParts resolves it fine, since path.join does not care whether
 * its second argument has a slash in it. Only listing top-level directories
 * meant these were invisible to requireScene/known-scenes reporting -- present
 * in the fixture tree, usable if you already knew the exact string, but absent
 * from every error message and from `available.upstream`, so the "check
 * upstream before writing a new fragment" workflow silently missed them.
 * One level of nesting is what exists today; recurse only that far rather
 * than walking the whole tree.
 *
 * A namespace directory (TC-HOME-001) turned out to also carry its own
 * `defaults/defaults.json` -- but an empty one (`{"defaults":{}}`), present
 * only because check-spec-mappings.mjs requires a fixture directory to exist
 * for every registry row and this TC id is only ever used through its nested
 * scenes. So a directory is a namespace, not a leaf, whenever it has any
 * nested leaf children -- checked first -- regardless of whether it also
 * happens to satisfy isLeafFixtureDir itself.
 */
function listScenes(dir: string): string[] {
  if (!fs.existsSync(dir)) return []
  const found: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith("_")) continue
    const full = path.join(dir, entry.name)
    const nested = fs
      .readdirSync(full, { withFileTypes: true })
      .filter((sub) => sub.isDirectory() && isLeafFixtureDir(path.join(full, sub.name)))
    if (nested.length) {
      for (const sub of nested) found.push(`${entry.name}/${sub.name}`)
    } else if (isLeafFixtureDir(full)) {
      found.push(entry.name)
    }
  }
  return found.sort()
}

const upstreamScenes = () => listScenes(upstreamDir)
const overlayScenes = () => listScenes(overlayDir)

/**
 * A name defined in both places would resolve to one of them and quietly
 * ignore the other, which is the same class of failure as the stale-checkout
 * bug above. Refuse to start instead.
 */
{
  const clash = overlayScenes().filter((name) => upstreamScenes().includes(name))
  if (clash.length) {
    throw new Error(
      `Fixture names defined both in ${overlayDir} and upstream: ${clash.join(", ")}. ` +
        `Rename the Hercules-owned ones.`
    )
  }
}

const readJsonDir = (dir: string): unknown[] =>
  fs.existsSync(dir)
    ? fs
        .readdirSync(dir)
        .filter((f) => f.endsWith(".json"))
        .sort()
        .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")))
    : []

/** Same layout as an upstream scene: defaults/*.json plus routes/*.json. */
function loadOverlay(name: string): Fixture {
  const dir = path.join(overlayDir, name)
  const fixture: Fixture = {}
  for (const data of readJsonDir(path.join(dir, "defaults"))) {
    if (!isRecord(data)) continue
    if (isRecord(data.baseResp)) fixture.baseResp = { ...(fixture.baseResp ?? {}), ...data.baseResp }
    if (isRecord(data.defaults)) fixture.defaults = deepMerge(fixture.defaults ?? {}, data.defaults) as Fixture["defaults"]
  }
  const routes: Route[] = []
  for (const data of readJsonDir(path.join(dir, "routes"))) {
    if (Array.isArray(data)) routes.push(...(data.filter(isRecord) as Route[]))
    else if (isRecord(data)) routes.push(data as Route)
  }
  if (routes.length) fixture.routes = routes
  return fixture
}

const parseScenes = (input: unknown): string[] => {
  const raw = Array.isArray(input) ? input : String(input ?? "").split(",")
  return raw.map((s) => String(s).trim()).filter(Boolean)
}

/**
 * loadFixture falls back to base for a scene it cannot find instead of
 * failing, so a typo would quietly serve the wrong data and the case would
 * fail somewhere far away. Check the name ourselves and say what exists.
 */
async function compose(names: string[]): Promise<Fixture> {
  let acc = await loadBaseFixture()
  for (const name of names) {
    if (name === "base") continue
    if (overlayScenes().includes(name)) {
      acc = mergeFixture(acc, loadOverlay(name))
      continue
    }
    if (!upstreamScenes().includes(name)) {
      throw new Error(
        `Unknown fixture "${name}". Hercules fragments: ${overlayScenes().join(", ") || "(none)"}. ` +
          `Upstream scenes: ${upstreamScenes().join(", ")}`
      )
    }
    acc = mergeFixture(acc, await loadFixture(name))
  }
  return resolvePlaceholders(acc)
}

/* ------------------------------------------------------------------ *
 * Serve
 * ------------------------------------------------------------------ */

let currentScenes = parseScenes(SCENE)
let currentFixture = await compose(currentScenes)
const fixture = currentFixture
const handle = await startGateway({ preferredPort: PORT, preferredWsPort: WS_PORT, fixture })

const describe = (f: Fixture) => ({
  routes: f.routes?.length ?? 0,
  user: (f.defaults as any)?.user?.nickname ?? "(none)",
})

/**
 * The gateway serves its own websocket controls -- /__push, /__disconnect,
 * /__ws_status -- on the HTTPS listener, behind a self-signed cert. Re-expose
 * them on the plain-HTTP control port instead of making the MCP server deal
 * with the cert: that process is spawned by Hercules across an stdio boundary
 * which forwards almost no environment, so it cannot rely on
 * NODE_TLS_REJECT_UNAUTHORIZED being set. Here the exemption is one explicit
 * option on one localhost request.
 */
function gatewayControl(pathname: string, body?: string): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    const request = https.request(
      {
        host: "127.0.0.1",
        port: handle.port,
        path: pathname,
        method: body === undefined ? "GET" : "POST",
        rejectUnauthorized: false,
        headers:
          body === undefined
            ? {}
            : { "content-type": "application/json", "content-length": Buffer.byteLength(body) },
      },
      (res) => {
        let raw = ""
        res.on("data", (chunk) => (raw += chunk))
        res.on("end", () => resolve({ status: res.statusCode ?? 0, text: raw }))
      }
    )
    request.on("error", reject)
    if (body !== undefined) request.write(body)
    request.end()
  })
}

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
      return send(200, {
        ok: true,
        scenes: currentScenes,
        scene: currentScenes.join(","),
        available: { hercules: overlayScenes(), upstream: upstreamScenes() },
      })
    }
    if (req.method === "POST" && req.url === "/fixture") {
      const body = JSON.parse((await readBody(req)) || "{}")
      // `scene` (one name, or comma separated) stays accepted so older callers
      // keep working; `scenes` is the composable form.
      const names = parseScenes(body.scenes ?? body.scene)
      if (!names.length) {
        return send(400, { ok: false, error: 'body must be {"scenes": ["<name>", ...]} or {"scene": "<name>"}' })
      }
      const next = await compose(names)
      handle.setFixture(next)
      currentFixture = next
      currentScenes = names
      console.log(`fixture  -> [${names.join(" + ")}] (${describe(next).routes} routes, user=${describe(next).user})`)
      // The MCP server seeds login storage from these, so hand them back
      // rather than making it re-read a file that only describes base.
      return send(200, { ok: true, scenes: names, scene: names.join(","), defaults: next.defaults ?? {} })
    }
    /**
     * What the app actually asked for, and whether the fixture had an answer.
     *
     * base/proto/defaults.json seeds every proto route with a bare
     * `{baseResp}` and nothing else, so an uncovered endpoint does not 404 --
     * it returns success with an empty payload, and the page renders a spinner
     * or a retry prompt with no hint about which call came back hollow. This
     * turns "the page is blank, which fixture is missing?" into a list.
     */
    if (req.method === "GET" && req.url === "/calls") {
      // A route counts as answered if it carries a payload beyond the bare
      // baseResp -- through `response`, through `responseSequence` (per-call
      // answers), or through `stream` (chunked output). Checking only
      // `response` reports routes as uncovered that are in fact fully
      // specified, which is worse than not checking at all.
      const hasPayload = (body: unknown) =>
        Boolean(body) && typeof body === "object" && Object.keys(body as object).some((k) => k !== "baseResp")
      const payloadRoutes = new Set(
        (currentFixture.routes ?? [])
          .filter(
            (r) =>
              hasPayload(r.response) ||
              (Array.isArray(r.responseSequence) && r.responseSequence.some(hasPayload)) ||
              Boolean(r.stream)
          )
          .map((r) => r.path)
      )
      const seen = new Map<string, { path: string; method: string; count: number; covered: boolean }>()
      for (const call of handle.calls ?? []) {
        if (call.path.startsWith("/__")) continue
        const key = `${call.method} ${call.path}`
        const prev = seen.get(key)
        if (prev) prev.count += 1
        else seen.set(key, { path: call.path, method: call.method, count: 1, covered: payloadRoutes.has(call.path) })
      }
      const rows = [...seen.values()]
      return send(200, {
        ok: true,
        scenes: currentScenes,
        calls: rows,
        uncovered: rows.filter((r) => !r.covered).map((r) => `${r.method} ${r.path}`),
      })
    }

    // Websocket controls, for cases that drive the stream event by event:
    // duplicate an event_id, push deltas without a commit, replay a whole
    // generation in order. Fixtures cannot express any of that -- they
    // describe a response, not a moment.
    if (req.method === "POST" && req.url === "/ws/push") {
      const out = await gatewayControl("/__push", (await readBody(req)) || "{}")
      return send(out.status, JSON.parse(out.text || "{}"))
    }
    if (req.method === "POST" && req.url === "/ws/disconnect") {
      const out = await gatewayControl("/__disconnect", "{}")
      return send(out.status, JSON.parse(out.text || "{}"))
    }
    if (req.method === "GET" && req.url === "/ws") {
      const out = await gatewayControl("/__ws_status")
      return send(out.status, JSON.parse(out.text || "{}"))
    }
    send(404, { ok: false, error: `no control route for ${req.method} ${req.url}` })
  } catch (err) {
    send(400, { ok: false, error: (err as Error).message })
  }
})
control.listen(CTRL_PORT, "127.0.0.1")

console.log(`repo     ${MINIAPP_REPO}`)
console.log(`gateway  https://localhost:${handle.port}`)
console.log(`ws       wss://localhost:${WS_PORT}`)
console.log(`control  http://127.0.0.1:${CTRL_PORT}/fixture  (POST {"scenes":[...]} to swap)`)
console.log(`fixture  [${currentScenes.join(" + ")}], ${describe(fixture).routes} routes, user=${describe(fixture).user}`)
console.log(`scenes   ${overlayScenes().length} Hercules fragments, ${upstreamScenes().length} upstream`)
console.log("ready - leave this running while Hercules drives the mini program")

for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    control.close()
    handle.close()
    process.exit(0)
  })
}
