import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
// Bounds inspected on the final stable 1920×1080 captures. Retain raw clips.
const crops = [
  ["11-coach-advice", "704:360:352:240"],
  ["12-coach-build", "1216:660:352:200"],
  ["13-coach-compare", "1216:610:352:162"],
  ["14-coach-risk", "1216:610:352:162"],
  ["15-coach-verdict", "1216:470:352:290"],
];
for (const [name, crop] of crops) {
  execFileSync("ffmpeg", [
    "-y",
    "-v",
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
  console.log(`Readable coaching detail: ${name}`);
}
writeFileSync("out/v2/coach-crops.json", JSON.stringify(crops, null, 2));
