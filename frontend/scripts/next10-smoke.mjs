// Smoke test for the Next-10s pages against the dev mock (no backend needed):
//   NEXT_PUBLIC_NEXT10_MOCK=1 npx next dev --webpack --port 3417
//   node scripts/next10-smoke.mjs http://localhost:3417 [screenshot-dir]
// Checks each page renders its key content with no console errors and no horizontal overflow, at 375 and 1440 px.
import { chromium } from "playwright";

const [, , base = "http://localhost:3417", shots = ""] = process.argv;
const PAGES = [
  { path: "/next10", name: "matches", expect: ["Live now", "Northgate Rovers", "Finished"] },
  { path: "/next10/mock%3Alive", name: "companion", expect: ["Follow live", "Likely receivers", "Seen on video", "Estimated (off screen)"], selector: "[data-observed=false]" },
  { path: "/next10/mock%3Aft1", name: "companion-finished", expect: ["Full time", "Actual shots"] },
  { path: "/next10/verify", name: "verify", expect: ["Verification board", "Beat baseline", "Reliability"] },
  { path: "/next10/mock%3Aft1/moments", name: "moments", expect: ["Play it out", "Goal conceded", "Model expectation"], selector: "[role=radio]" },
];

const browser = await chromium.launch({ channel: "chrome" });
const failures = [];
for (const width of [375, 1440]) {
  for (const scheme of ["dark", "light"]) {
    const context = await browser.newContext({ viewport: { width, height: width < 600 ? 812 : 900 }, colorScheme: scheme });
    await context.addInitScript((t) => localStorage.setItem("theme", t), scheme);
    for (const { path, name, expect, selector } of PAGES) {
      const page = await context.newPage();
      const tag = `[${name} ${scheme} ${width}]`;
      // The app shell still follows the demo match and logs 502s when no backend runs; only Next-10s failures count.
      page.on("console", (m) => {
        if (m.type() === "error" && !/status of 502/.test(m.text())) failures.push(`${tag} console error: ${m.text()}`);
      });
      page.on("response", (r) => {
        if (r.url().includes("next10") && r.status() >= 400) failures.push(`${tag} ${r.status()} ${r.url()}`);
      });
      page.on("pageerror", (e) => failures.push(`${tag} pageerror: ${e.message}`));
      await page.goto(`${base}${path}`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(2500);
      const text = await page.evaluate(() => document.body.innerText);
      for (const needle of expect) if (!text.includes(needle)) failures.push(`${tag} missing text: ${needle}`);
      if (selector && (await page.locator(selector).count()) === 0) failures.push(`${tag} missing element: ${selector}`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (overflow > 0) failures.push(`${tag} horizontal overflow ${overflow}px`);
      if (shots) await page.screenshot({ path: `${shots}/${name}-${scheme}-${width}.png`, fullPage: true });
      await page.close();
    }
    await context.close();
  }
}
await browser.close();
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("next10 smoke: all pages render, no console errors, no overflow");
