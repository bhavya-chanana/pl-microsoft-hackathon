import { backendUrl } from "@/lib/server-config";

/**
 * Server-Sent Events bridge for the backend's live WebSocket. The browser
 * subscribes with EventSource (auto-reconnect), the backend stays private.
 * Each message is forwarded verbatim as one `data:` line.
 */
export async function GET(request: Request): Promise<Response> {
  const wsUrl = `${backendUrl().replace(/^http/, "ws")}/ws`;
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

      controller.enqueue(encoder.encode("retry: 2000\n\n"));
      try {
        socket = new WebSocket(wsUrl);
      } catch {
        send(JSON.stringify({ type: "feed_status", status: "unreachable" }));
        close();
        return;
      }
      socket.onmessage = (message) => {
        if (typeof message.data === "string") send(message.data);
      };
      socket.onerror = () => send(JSON.stringify({ type: "feed_status", status: "unreachable" }));
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
