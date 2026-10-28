import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
const require = createRequire(import.meta.url);
const { chromium } = require("../../frontend/node_modules/playwright");
const root = new URL("../", import.meta.url).pathname;
await mkdir(`${root}out`, { recursive: true });
const api = async (path, body) => {
  const res = await fetch(
    `http://127.0.0.1:8877/api/${path}`,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {},
  );
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
};
await api("replay", { seed: 7, speed: 1, start_minute: 60 });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  colorScheme: "dark",
});
await context.addInitScript(() => localStorage.setItem("theme", "dark"));
await context.addInitScript(() =>
  localStorage.setItem("matchlens.onboarded.v1", "1"),
);
const page = await context.newPage();
page.on("pageerror", (e) => console.log("PAGE ERROR", e.message));
await page.goto("http://127.0.0.1:3100", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(6000);
await api("replay/control", { paused: true });
await page.screenshot({ path: `${root}out/product-inspection.png` });
console.log("STATE", JSON.stringify(await api("state")));
console.log((await page.locator("body").innerText()).slice(0, 11000));
await writeFile(
  `${root}out/product-dom.txt`,
  await page.locator("body").innerText(),
);
await browser.close();
