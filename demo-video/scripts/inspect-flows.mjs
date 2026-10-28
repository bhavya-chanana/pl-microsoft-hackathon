import { createRequire } from "node:module";
import { writeFile, mkdir } from "node:fs/promises";
const require = createRequire(import.meta.url);
const { chromium } = require("../../frontend/node_modules/playwright");
await mkdir("out", { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  colorScheme: "dark",
});
await context.addInitScript(() => {
  localStorage.setItem("theme", "dark");
  localStorage.setItem("matchlens.onboarded.v1", "1");
});
for (const [id, route] of [
  ["moments", "/moments"],
  ["multiverse", "/explore/multiverse"],
  ["agents", "/agents"],
]) {
  const page = await context.newPage();
  await page.goto(`http://127.0.0.1:3100${route}`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `out/${id}-inspection.png` });
  const text = await page.locator("body").innerText();
  console.log(id, text.slice(0, 6500));
  await writeFile(`out/${id}-dom.txt`, text);
  await page.close();
}
await browser.close();
