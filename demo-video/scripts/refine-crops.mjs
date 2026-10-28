import { execFileSync } from "node:child_process";
const crops = [
  ["03-evidence", "384:540:1536:0"],
  ["06-correction", "820:600:340:180"],
  ["08-pundit", "512:570:1408:0"],
  ["09-catchup", "512:558:704:262"],
];
for (const [name, crop] of crops) {
  execFileSync("ffmpeg", [
    "-y",
    "-loglevel",
    "error",
    "-i",
    `public/footage/${name}.mp4`,
    "-vf",
    `crop=${crop}`,
    "-c:v",
    "libx264",
    "-crf",
    "16",
    "-preset",
    "fast",
    "-movflags",
    "+faststart",
    `public/footage/${name}-detail.mp4`,
  ]);
  console.log(`Refined ${name}: readable detail from actual recording`);
}
