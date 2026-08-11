import type { NextRequest } from "next/server"

const DEFAULT_UPSTREAM = "http://127.0.0.1:8800"
const REQUEST_TIMEOUT_MS = 8_000
const REGISTRATION_WINDOW_MS = 60 * 60 * 1_000
const REGISTRATIONS_PER_WINDOW = 3
const MAX_TRACKED_ADDRESSES = 10_000

const ALLOWED = new Map<string, ReadonlySet<string>>([
  ["health", new Set(["GET"])],
  ["markets", new Set(["GET"])],
  ["leaderboard", new Set(["GET"])],
  ["strategies", new Set(["POST"])],
  ["stances", new Set(["POST"])],
  ["keys/rotate", new Set(["POST"])],
  ["portfolio", new Set(["GET"])],
])

const registrations = new Map<string, number[]>()

function json(error: string, status: number) {
  return Response.json({ error }, { status })
}

function enabled(value: string | undefined) {
  return value?.toLowerCase() === "true"
}

function clientAddress(request: NextRequest) {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    ?? request.headers.get("x-real-ip")
    ?? "unknown"
}

function registrationAllowed(request: NextRequest) {
  const now = Date.now()
  const address = clientAddress(request)
  if (!registrations.has(address) && registrations.size >= MAX_TRACKED_ADDRESSES) {
    const oldest = registrations.keys().next().value
    if (oldest) registrations.delete(oldest)
  }
  const recent = (registrations.get(address) ?? []).filter(
    (timestamp) => now - timestamp < REGISTRATION_WINDOW_MS,
  )
  if (recent.length >= REGISTRATIONS_PER_WINDOW) return false
  recent.push(now)
  registrations.set(address, recent)
  return true
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const path = (await context.params).path.join("/")
  if (!ALLOWED.get(path)?.has(request.method)) {
    return json("지원하지 않는 Stance API 경로입니다", 404)
  }

  const headers = new Headers({ "Content-Type": "application/json" })
  const authorization = request.headers.get("authorization")
  if (authorization) headers.set("Authorization", authorization)

  if (path === "strategies") {
    if (!enabled(process.env.STANCE_PUBLIC_REGISTRATION)) {
      return json("현재 공개 전략 등록을 받지 않습니다", 403)
    }
    const registrationToken = process.env.STANCE_REGISTRATION_TOKEN
    if (!registrationToken) {
      return json("전략 등록 서비스가 아직 준비되지 않았습니다", 503)
    }
    if (!registrationAllowed(request)) {
      return json("등록 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요", 429)
    }
    headers.set("X-Stance-Registration-Token", registrationToken)
  }

  const upstream = (process.env.STANCE_INTERNAL_URL ?? DEFAULT_UPSTREAM).replace(/\/$/, "")
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch(`${upstream}/${path}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      signal: controller.signal,
    })
    const responseHeaders = new Headers()
    responseHeaders.set(
      "Content-Type",
      response.headers.get("content-type") ?? "application/json",
    )
    responseHeaders.set("Cache-Control", "no-store")
    const retryAfter = response.headers.get("retry-after")
    if (retryAfter) responseHeaders.set("Retry-After", retryAfter)
    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders,
    })
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "AbortError"
    return json(
      timedOut ? "Stance 서버 응답 시간이 초과되었습니다" : "Stance 서버에 연결할 수 없습니다",
      timedOut ? 504 : 502,
    )
  } finally {
    clearTimeout(timeout)
  }
}

export const dynamic = "force-dynamic"

export const GET = proxy
export const POST = proxy
