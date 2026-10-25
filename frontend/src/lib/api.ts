import type { z } from "zod";

import { serverRoutes } from "@/lib/routes";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The response's `detail` as sent, for errors that carry more than a message. */
    readonly detail: unknown = null,
  ) {
    super(message);
  }
}

async function parse<S extends z.ZodType>(response: Response, schema: S): Promise<z.infer<S>> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(detail, response.status, body?.detail ?? null);
  }
  return schema.parse(await response.json());
}

type Query = Record<string, string | number | null | undefined>;

function withQuery(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return serverRoutes.backend(path) + (qs ? `?${qs}` : "");
}

export async function getJson<S extends z.ZodType>(
  path: string,
  schema: S,
  query?: Query,
  signal?: AbortSignal,
): Promise<z.infer<S>> {
  return parse(await fetch(withQuery(path, query), { signal, cache: "no-store" }), schema);
}

export async function postJson<S extends z.ZodType>(path: string, body: unknown, schema: S): Promise<z.infer<S>> {
  const response = await fetch(withQuery(path), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return parse(response, schema);
}
