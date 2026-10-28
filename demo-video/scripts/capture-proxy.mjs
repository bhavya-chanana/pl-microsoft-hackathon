import http from "node:http";

// Keep filming isolated from the developer's current match and database.
// UI bytes come unchanged from the ui-redesign-codex preview.
const ui = new URL(process.env.MATCHLENS_CAPTURE_UI ?? "http://127.0.0.1:3030");
const backend = new URL(
  process.env.MATCHLENS_CAPTURE_BACKEND ?? "http://127.0.0.1:8877",
);
const port = Number(process.env.MATCHLENS_CAPTURE_PORT ?? 3100);

http
  .createServer((req, res) => {
    const path = req.url ?? "/";
    if (path.startsWith("/api/stream")) {
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache, no-transform",
        connection: "keep-alive",
      });
      res.write("retry: 2000\n\n");
      const ws = new WebSocket(
        `${backend.href.replace(/^http/, "ws").replace(/\/$/, "")}/ws`,
      );
      ws.onmessage = (event) => {
        if (!res.destroyed) res.write(`data: ${event.data}\n\n`);
      };
      ws.onerror = () => res.end();
      ws.onclose = () => res.end();
      res.on("close", () => ws.close());
      return;
    }
    const api = path.startsWith("/api/backend/");
    const target = api ? backend : ui;
    const proxyPath = api ? path.replace("/api/backend/", "/api/") : path;
    const upstream = http.request(
      {
        hostname: target.hostname,
        port: target.port,
        path: proxyPath,
        method: req.method,
        headers: { ...req.headers, host: target.host },
      },
      (upstreamRes) => {
        res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers);
        upstreamRes.pipe(res);
      },
    );
    upstream.on("error", (e) => {
      res.writeHead(502);
      res.end(e.message);
    });
    req.pipe(upstream);
  })
  .listen(port, "127.0.0.1", () =>
    console.log(
      `Capture view http://127.0.0.1:${port}; UI ${ui}; isolated backend ${backend}`,
    ),
  );
