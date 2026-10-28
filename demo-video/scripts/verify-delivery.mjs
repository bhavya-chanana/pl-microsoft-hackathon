import { existsSync, readFileSync, readdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
const final = process.argv.includes("--final");
const failures = [];
const { footage, voice } = JSON.parse(
  readFileSync("public/asset-manifest.local.json", "utf8"),
);
for (const [kind, entries] of [
  ["Footage", footage],
  ["Voice", voice],
]) {
  console.log(`${kind}: ${Object.keys(entries).length}`);
  for (const [name, path] of Object.entries(entries)) {
    if (!existsSync(`public/${path}`)) {
      failures.push(`${name}: missing ${path}`);
      continue;
    }
    const info = JSON.parse(
      execFileSync(
        "ffprobe",
        [
          "-v",
          "quiet",
          "-show_format",
          "-show_streams",
          "-of",
          "json",
          `public/${path}`,
        ],
        { encoding: "utf8" },
      ),
    );
    const duration = Number(info.format.duration);
    console.log(`  ${name}: ${duration.toFixed(2)}s`);
    if (!Number.isFinite(duration) || duration <= 0)
      failures.push(`${name}: unusable duration`);
  }
}
if (final) {
  if (
    [
      "opening",
      "introduction",
      "evidence",
      "replay",
      "agents",
      "correction",
      "multiverse",
      "pundit",
      "catchup",
      "winner",
    ].some((id) => !footage[id])
  )
    failures.push("All ten product recordings are required.");
  if (Object.keys(voice).length < 9)
    failures.push("All nine human narration segments are required.");
  if (!existsSync("delivery-props.local.json"))
    failures.push("Missing final demo URL and delivery settings.");
  else {
    const props = JSON.parse(readFileSync("delivery-props.local.json", "utf8"));
    if (props.showGuide !== false)
      failures.push("Remove working-cut labels for submission.");
    if (!/^https:\/\//.test(props.demoUrl ?? ""))
      failures.push("A verified public demo URL is required.");
  }
  if (!existsSync("public/audio/captions-reviewed.json"))
    failures.push("Final captions need timing review against the human voice.");
  else {
    const captions = JSON.parse(
      readFileSync("public/audio/captions-reviewed.json", "utf8"),
    );
    for (const id of [
      "opening",
      "introduction",
      "evidence",
      "agents",
      "correction",
      "multiverse",
      "pundit",
      "platform",
      "closing",
    ]) {
      if (!Array.isArray(captions[id]) || !captions[id].length)
        failures.push(`${id}: reviewed caption timings are missing.`);
    }
  }
}
for (const file of readdirSync("out").filter((f) => f.endsWith(".mp4"))) {
  const duration = Number(
    execFileSync(
      "ffprobe",
      [
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        `out/${file}`,
      ],
      { encoding: "utf8" },
    ),
  );
  console.log(`Export ${file}: ${duration.toFixed(2)}s`);
  if (duration >= 120) failures.push(`${file} exceeds the hackathon limit.`);
}
if (failures.length) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    final
      ? "Submission checks passed."
      : "Working-cut media checks passed. Voice, captions and public demo URL remain delivery inputs.",
  );
