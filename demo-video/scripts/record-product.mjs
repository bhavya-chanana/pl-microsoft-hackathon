import { createRequire } from "node:module";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require("../../frontend/node_modules/playwright");
const root = resolve(import.meta.dirname, "..");
const base = process.env.MATCHLENS_CAPTURE_URL ?? "http://127.0.0.1:3100";
const backend =
  process.env.MATCHLENS_CAPTURE_API ?? "http://127.0.0.1:8877/api";
const api = async (path, body) => {
  const res = await fetch(
    `${backend}/${path}`,
    body
      ? {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }
      : {},
  );
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
};
await mkdir(`${root}/public/footage`, { recursive: true });
await mkdir(`${root}/out`, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  colorScheme: "dark",
});
await context.addInitScript(() => {
  localStorage.setItem("theme", "dark");
  localStorage.setItem("matchlens.onboarded.v1", "1");
});
const page = await context.newPage();
const report = {
  source: "ui-redesign-codex working tree, isolated frontend snapshot",
  recordedAt: new Date().toISOString(),
  base,
  clips: [],
  errors: [],
};
const go = async (path) => {
  await page.goto(base + path, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  await page.evaluate(() => document.fonts.ready);
};

// Capture real Chrome frames at high JPEG quality and retain their timestamps.
// This avoids the low-bitrate default of browser test recordings.
async function record(file, seconds, action = async () => {}, crop) {
  const dir = `${root}/capture-cache/${file.replace(".mp4", "")}`;
  await mkdir(dir, { recursive: true });
  const frames = [];
  const started = performance.now();
  frames.push({
    data: await page.screenshot({ type: "jpeg", quality: 95 }),
    time: 0,
  });
  const cdp = await context.newCDPSession(page);
  cdp.on("Page.screencastFrame", (event) => {
    frames.push({
      data: Buffer.from(event.data, "base64"),
      time: (performance.now() - started) / 1000,
    });
    cdp
      .send("Page.screencastFrameAck", { sessionId: event.sessionId })
      .catch(() => {});
  });
  const size = page.viewportSize();
  await cdp.send("Page.startScreencast", {
    format: "jpeg",
    quality: 95,
    maxWidth: size.width,
    maxHeight: size.height,
    everyNthFrame: 1,
  });
  await action();
  const remaining = seconds * 1000 - (performance.now() - started);
  if (remaining > 0) await page.waitForTimeout(remaining);
  await cdp.send("Page.stopScreencast");
  await cdp.detach();
  const ordered = frames.filter(
    (f, i) =>
      f.time < seconds && (i === 0 || f.time > frames[i - 1].time + 0.001),
  );
  const lines = [];
  for (let i = 0; i < ordered.length; i++) {
    const filename = `frame-${String(i).padStart(5, "0")}.jpg`;
    await writeFile(`${dir}/${filename}`, ordered[i].data);
    const end = ordered[i + 1]?.time ?? seconds;
    lines.push(
      `file '${filename}'`,
      `duration ${Math.max(0.001, end - ordered[i].time).toFixed(6)}`,
    );
  }
  lines.push(`file 'frame-${String(ordered.length - 1).padStart(5, "0")}.jpg'`);
  await writeFile(`${dir}/frames.txt`, lines.join("\n"));
  const cropFilter = crop
    ? `crop=${Math.floor(crop.width / 2) * 2}:${Math.floor(crop.height / 2) * 2}:${Math.floor(crop.x / 2) * 2}:${Math.floor(crop.y / 2) * 2},`
    : "";
  execFileSync("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-f",
    "concat",
    "-safe",
    "0",
    "-i",
    `${dir}/frames.txt`,
    "-vf",
    `${cropFilter}fps=30,format=yuv420p`,
    "-c:v",
    "libx264",
    "-crf",
    "16",
    "-preset",
    "fast",
    "-movflags",
    "+faststart",
    "-t",
    String(seconds),
    `${root}/public/footage/${file}`,
  ]);
  report.clips.push({
    file,
    seconds,
    frames: ordered.length,
    crop: crop ?? null,
    state: await api("state"),
  });
  console.log(
    `Recorded ${file}: ${seconds}s, ${ordered.length} actual browser frames`,
  );
  await writeFile(
    `${root}/out/capture-report.json`,
    JSON.stringify(report, null, 2),
  );
}

const selected = process.argv.slice(2);
async function shot(id, task) {
  if (selected.length && !selected.includes(id)) return;
  try {
    await task();
  } catch (e) {
    console.error(`SHOT ${id}: ${e.message}`);
    report.errors.push({ id, error: e.message });
    await page
      .screenshot({ path: `${root}/out/capture-error-${id}.png` })
      .catch(() => {});
  }
}

try {
  const health = await api("health");
  if (health.store.backend !== "memory")
    throw new Error("Capture requires the isolated memory backend.");
  if (!selected.length || selected.includes("opening")) {
    await api("replay", { seed: 7, speed: 1, start_minute: 60 });
    await page.waitForTimeout(3000);
  }
  await shot("opening", async () => {
    await go("/");
    await api("replay/control", { paused: false, speed: 1 });
    await record("01-opening.mp4", 10);
  });
  await shot("live", async () => {
    await go("/");
    await record("02-live.mp4", 14);
    await api("replay/control", { paused: true });
  });
  await shot("evidence", async () => {
    await go("/moments");
    const checked = page
      .getByRole("button", { name: /Sorokine booked as Northbridge/ })
      .first();
    if (await checked.count()) await checked.click();
    else
      await page
        .getByRole("button", { name: /GOAL!.*Quilmes/ })
        .first()
        .click();
    await page.getByRole("dialog").waitFor();
    await page.waitForTimeout(800);
    console.log(
      "EVIDENCE",
      (await page.getByRole("dialog").innerText()).slice(0, 4000),
    );
    await record("03-evidence.mp4", 9);
    await page.screenshot({ path: `${root}/out/evidence-open.png` });
    await page.keyboard.press("Escape");
  });
  await shot("replay", async () => {
    await go("/moments");
    const card = page
      .locator("article")
      .filter({ hasText: /GOAL!.*Quilmes/ })
      .first();
    await card.scrollIntoViewIfNeeded();
    const replay = card.getByRole("button", { name: /replay|watch/i }).first();
    await replay.click();
    await page.waitForTimeout(1800);
    await record("04-replay.mp4", 8);
    await page.screenshot({ path: `${root}/out/replay-open.png` });
  });
  await shot("agents", async () => {
    await go("/agents");
    await record("05-agents.mp4", 8);
  });
  await shot("multiverse", async () => {
    await page.setViewportSize({ width: 1600, height: 2100 });
    await go("/explore/multiverse");
    await api("replay/control", { paused: true });
    await page.waitForTimeout(3500);
    const boxes = await page
      .locator('section[aria-label^="Screens"]')
      .evaluateAll((els) =>
        els.map((el) => {
          const r = el.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        }),
      );
    await record("07-multiverse.mp4", 18);
    for (let i = 0; i < boxes.length; i++) {
      const b = boxes[i];
      execFileSync("ffmpeg", [
        "-y",
        "-loglevel",
        "error",
        "-i",
        `${root}/public/footage/07-multiverse.mp4`,
        "-vf",
        `crop=${Math.floor(b.width / 2) * 2}:${Math.floor(b.height / 2) * 2}:${Math.floor(b.x / 2) * 2}:${Math.floor(b.y / 2) * 2}`,
        "-c:v",
        "libx264",
        "-crf",
        "16",
        "-preset",
        "fast",
        `${root}/public/footage/07-multiverse-${i + 1}.mp4`,
      ]);
    }
    console.log("MULTIVERSE BOXES", JSON.stringify(boxes));
    await api("replay/control", { paused: true });
    await page.setViewportSize({ width: 1920, height: 1080 });
  });
  await shot("pundit", async () => {
    await go("/");
    await page
      .getByRole("button", { name: "Ask the pundit", exact: true })
      .first()
      .click();
    await page.getByRole("dialog").waitFor();
    await record("08-pundit.mp4", 13, async () => {
      await page
        .getByRole("button", {
          name: "Why did the momentum change?",
          exact: true,
        })
        .click();
      await page.waitForTimeout(4500);
    });
    console.log(
      "PUNDIT",
      (await page.getByRole("dialog").innerText()).slice(0, 2500),
    );
    await page.screenshot({ path: `${root}/out/pundit-open.png` });
    await page.keyboard.press("Escape");
  });
  await shot("catchup", async () => {
    await go("/");
    await page
      .getByRole("button", { name: "Catch me up", exact: true })
      .first()
      .click();
    await page.getByRole("dialog").waitFor();
    await page.waitForTimeout(2500);
    await record("09-catchup.mp4", 8);
    await page.screenshot({ path: `${root}/out/catchup-open.png` });
    await page.keyboard.press("Escape");
  });
  await shot("correction", async () => {
    await api("replay", { seed: 7, speed: 1, start_minute: 65.5 });
    await page.waitForTimeout(2000);
    await go("/moments");
    await record("06-correction.mp4", 18, async () => {
      await api("replay/control", { speed: 5, paused: false });
      await page.waitForTimeout(14000);
      await api("replay/control", { paused: true });
    });
    console.log(
      "CORRECTION",
      (await page.locator("body").innerText()).slice(0, 4500),
    );
    await page.screenshot({ path: `${root}/out/correction-open.png` });
  });
  await shot("winner", async () => {
    await api("replay", { seed: 7, speed: 1, start_minute: 92 });
    await page.waitForTimeout(1800);
    await go("/");
    await record("10-winner.mp4", 14, async () => {
      await api("replay/control", { speed: 10, paused: false });
      await page.waitForTimeout(10000);
      await api("replay/control", { paused: true });
    });
    await page.screenshot({ path: `${root}/out/winner-open.png` });
  });
} finally {
  await writeFile(
    `${root}/out/capture-report.json`,
    JSON.stringify(report, null, 2),
  );
  await browser.close();
}
if (report.errors.length) process.exitCode = 1;
