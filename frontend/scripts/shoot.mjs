// Screenshot helper for the verification loop: node scripts/shoot.mjs <path> <out-prefix> [waitMs]
import { chromium } from "playwright";

const [, , path = "/", prefix = "/tmp/shot", wait = "6000", full = "1"] = process.argv;
const browser = await chromium.launch({ channel: "chrome" });
const errors = [];
for (const scheme of ["dark", "light"]) {
  for (const width of [375, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: width < 600 ? 812 : 900 }, colorScheme: scheme });
    await context.addInitScript((t) => localStorage.setItem("theme", t), scheme);
    const page = await context.newPage();
    page.on("console", (m) => { if (["error", "warning"].includes(m.type())) errors.push(`[${scheme} ${width}] ${m.type()}: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`[${scheme} ${width}] pageerror: ${e.message}`));
    await page.goto(`http://localhost:3000${path}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(Number(wait));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 0) errors.push(`[${scheme} ${width}] horizontal overflow ${overflow}px`);
    await page.screenshot({ path: `${prefix}-${scheme}-${width}.png`, fullPage: full === "1" });
    await context.close();
  }
}
await browser.close();
console.log(errors.length ? errors.join("\n") : "no console errors, no overflow");
