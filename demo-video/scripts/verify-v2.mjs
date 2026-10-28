import { readFileSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
const final = process.argv.includes("--final");
const edit = JSON.parse(readFileSync("out/v2/edit.json", "utf8"));
const failures = [];
const files = [
  "01-opening.mp4",
  "02-live.mp4",
  "03-evidence-detail.mp4",
  "04-replay.mp4",
  "05-agents.mp4",
  "06-correction-detail.mp4",
  "07-multiverse-1.mp4",
  "07-multiverse-2.mp4",
  "07-multiverse-3.mp4",
  "07-multiverse-4.mp4",
  "08-pundit-detail.mp4",
  "09-catchup-detail.mp4",
  "10-winner.mp4",
  "11-coach-advice-detail.mp4",
  "12-coach-build.mp4",
  "13-coach-compare-detail.mp4",
  "14-coach-risk-detail.mp4",
  "15-coach-verdict-detail.mp4",
];
const probe = (path) =>
  JSON.parse(
    execFileSync(
      "ffprobe",
      ["-v", "quiet", "-show_format", "-show_streams", "-of", "json", path],
      { encoding: "utf8" },
    ),
  );
for (const file of files) {
  const path = `public/footage/${file}`;
  if (!existsSync(path)) {
    failures.push(`Missing recording: ${file}`);
    continue;
  }
  const p = probe(path);
  if (
    !p.streams.some((s) => s.codec_type === "video") ||
    Number(p.format.duration) <= 0
  )
    failures.push(`Unusable recording: ${file}`);
}
if (edit.frames / edit.fps >= 120)
  failures.push("The video must be shorter than 120 seconds");
for (const scene of edit.scenes) {
  const path = edit.voice[scene.id];
  if (path) {
    const p = probe(`public/${path}`);
    if (Number(p.format.duration) > scene.durationInFrames / edit.fps + 0.025)
      failures.push(`${scene.id}: narration exceeds its scene`);
  }
  let previous = -1;
  for (const c of edit.captions[scene.id] ?? []) {
    if (
      c.startMs < 0 ||
      c.endMs <= c.startMs ||
      c.startMs < previous ||
      c.endMs > (scene.durationInFrames / edit.fps) * 1000 + 1
    )
      failures.push(`${scene.id}: invalid caption timing`);
    previous = c.startMs;
  }
}
if (final && edit.pending.length)
  failures.push(`New narration still needed: ${edit.pending.join(", ")}`);
const props = JSON.parse(readFileSync("delivery-props.local.json", "utf8"));
if (
  final &&
  (props.showGuide !== false || !/^https:\/\//.test(props.demoUrl ?? ""))
)
  failures.push("Final delivery settings are incomplete");
console.log(
  `${edit.frames} frames · ${(edit.frames / edit.fps).toFixed(2)} seconds · ${Object.keys(edit.voice).length}/${edit.scenes.length} human voice clips`,
);
if (edit.pending.length)
  console.log(
    `Review cut: replacement voice pending for ${edit.pending.join(", ")}`,
  );
if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    final
      ? "Final media preflight passed."
      : "Review-cut media preflight passed.",
  );
