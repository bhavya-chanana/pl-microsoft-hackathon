import { backendUrl } from "@/lib/server-config";

/**
 * Server-Sent Events bridge for the Next-10s live WebSocket (`/api/next10/live/{match_id}` on the backend), the
 * same pattern as /api/stream: the browser subscribes with EventSource, the backend address stays private, and
 * every message is forwarded verbatim as one `data:` line.
 */
export async function GET(request: Request, ctx: { params: Promise<{ matchId: string }> }): Promise<Response> {
  const { matchId } = await ctx.params;
  if (!/^[\w:.\-]+$/.test(matchId)) return Response.json({ detail: "Not found" }, { status: 404 });
  const wsUrl = `${backendUrl().replace(/^http/, "ws")}/api/next10/live/${encodeURIComponent(matchId)}`;
  const encoder = new TextEncoder();
  let socket: WebSocket | null = null;

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false;
      const close = () => {
        if (closed) return;
        closed = true;
        socket?.close();
        try {
          controller.close();
        } catch {
          // Already closed by the client.
        }
      };
      const send = (data: string) => {
        if (!closed) controller.enqueue(encoder.encode(`data: ${data}\n\n`));
      };

      controller.enqueue(encoder.encode("retry: 3000\n\n"));
      try {
        socket = new WebSocket(wsUrl);
      } catch {
        send(JSON.stringify({ type: "status", status: "unreachable" }));
        close();
        return;
      }
      socket.onmessage = (message) => {
        if (typeof message.data === "string") send(message.data);
      };
      socket.onerror = () => send(JSON.stringify({ type: "status", status: "unreachable" }));
      socket.onclose = close;
      request.signal.addEventListener("abort", close);
    },
    cancel() {
      socket?.close();
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
