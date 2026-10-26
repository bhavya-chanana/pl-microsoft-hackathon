import type { NextRequest } from "next/server";

import { backendUrl, PROXY_GET, PROXY_POST } from "@/lib/server-config";

/**
 * Same-origin proxy to the Matchlens backend. Only allow-listed paths pass, so
 * the browser never learns the backend address and cannot reach /mcp or other
 * internal routes through here.
 */
async function forward(request: NextRequest, path: string[], allowed: RegExp[]): Promise<Response> {
  const joined = path.join("/");
  if (!allowed.some((rule) => rule.test(joined))) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  const target = `${backendUrl()}/api/${joined}${request.nextUrl.search}`;
  try {
    const headers = new Headers({ "content-type": "application/json" });
    const cookie = request.headers.get("cookie");
    if (cookie) headers.set("cookie", cookie);
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    const responseHeaders = new Headers({ "content-type": upstream.headers.get("content-type") ?? "application/json" });
    const setCookie = upstream.headers.get("set-cookie");
    if (setCookie) responseHeaders.set("set-cookie", setCookie);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json({ detail: "The match engine is not reachable." }, { status: 502 });
  }
}

export async function GET(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(request, path, PROXY_GET);
}

export async function POST(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return forward(request, path, PROXY_POST);
}
