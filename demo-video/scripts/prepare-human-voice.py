"""Fit the supplied human take by editing pauses, never speech playback speed."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "public/audio"
OUT = ROOT / "out"
FPS = 30
END_FRAME = 3555
original = AUDIO / "pl-demo-hackathon-original.m4a"
transcript = json.loads((OUT / "voice-transcript-original.json").read_text())
words = [dict(w) for s in transcript["segments"] for w in s["words"]]
# Retain at least 150 ms before the nearest transcribed word at each edit.
# Long pauses are shortened; speech, pitch and playback rate are unchanged.
kept = [(0.85, 35.55), (36.45, 54.05), (54.60, 115.05), (115.60, 120.80)]
removed = [(35.55, 36.45), (54.05, 54.60), (115.05, 115.60)]
for left, right in removed:
    if any(w["start"] < right and w["end"] > left for w in words):
        raise ValueError(f"Edit intersects a spoken word: {left}–{right}")

def time_after_edit(t):
    return max(0, t - 0.85 - sum(max(0, min(t, b) - a) for a, b in removed))

filters = []
for i, (start, end) in enumerate(kept):
    duration = end - start
    filters.append(
        f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
        f"afade=t=in:d=0.006,afade=t=out:st={duration - .006}:d=0.006[p{i}]"
    )
filters.append(
    "".join(f"[p{i}]" for i in range(len(kept)))
    + f"concat=n={len(kept)}:v=0:a=1,highpass=f=65,"
      "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[voice]"
)
subprocess.run([
    "ffmpeg", "-v", "error", "-y", "-i", str(original),
    "-filter_complex", ";".join(filters), "-map", "[voice]",
    "-c:a", "pcm_s24le", str(AUDIO / "narration-edited.wav"),
], check=True)

# These paragraph boundaries are taken from actual recognized speech, not the
# recording guide's estimated schedule. Each boundary lies in a natural pause.
ids = ["opening", "introduction", "evidence", "agents", "correction",
       "multiverse", "pundit", "platform", "closing"]
boundaries = [0, 8.85, 22.75, 35.55, 54.05, 68.40, 85.80, 99.00, 115.05]
frames = [0] + [round(time_after_edit(t) * FPS) for t in boundaries[1:]]
frames.append(END_FRAME)
captions = {}
timing = []

# Normalize only obvious recognition/formatting errors. Retain the speaker's
# extra words and phrasing instead of replacing the take with the written script.
corrections = {120: " goalscorer", 121: ".", 124: " affected",
               233: " the", 234: " score", 235: "line,"}
for i, w in enumerate(words):
    w["word"] = corrections.get(i, w["word"])
    if i == 87:
        # "Tactical" starts at the observed end of the preceding silence.
        w["start"] = 41.55
    if w["end"] <= w["start"]:
        w["end"] = min(words[i + 1]["start"] if i + 1 < len(words) else 120.8,
                       w["start"] + .08)
    w["edited_start"] = time_after_edit(w["start"])
    w["edited_end"] = time_after_edit(w["end"])

for i, scene_id in enumerate(ids):
    start, end = frames[i] / FPS, frames[i + 1] / FPS
    local = []
    for w in words:
        if start <= w["edited_start"] < end:
            local.append({
                "text": w["word"],
                "startMs": round((w["edited_start"] - start) * 1000),
                "endMs": round((min(w["edited_end"], end) - start) * 1000),
                "timestampMs": round((w["edited_start"] - start) * 1000),
                "confidence": w["probability"],
            })
    captions[scene_id] = local
    entry = {"id": scene_id, "startFrame": frames[i],
             "durationInFrames": frames[i + 1] - frames[i]}
    timing.append(entry)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(AUDIO / "narration-edited.wav"),
        "-af", f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS,apad=whole_dur={end-start}",
        "-t", str(end - start), "-c:a", "pcm_s24le",
        str(AUDIO / f"{i+1:02}-{scene_id}.wav"),
    ], check=True)

(AUDIO / "captions-reviewed.json").write_text(json.dumps(captions, indent=2))
(AUDIO / "voice-timing.json").write_text(json.dumps(timing, indent=2))
(ROOT / "src/voice-timing.ts").write_text(
    "// Aligned to the supplied human recording; frame values are at 30 fps.\n"
    + "export const voiceTiming = " + json.dumps({t["id"]: t for t in timing}, indent=2)
    + " as const;\nexport const filmFrames = 3555;\n"
)
report = {"source": str(original), "originalSeconds": transcript["duration"],
          "keptSourceRanges": kept, "removedPauses": removed,
          "playbackRate": 1, "filmSeconds": END_FRAME/FPS,
          "wordCount": len(words), "captionWordCount": sum(map(len, captions.values())),
          "lastSpokenWordEnd": words[-1]["edited_end"], "scenes": timing,
          "review": "ASR timing and word-boundary checks; final listening remains human review."}
(OUT / "human-voice-edit.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
