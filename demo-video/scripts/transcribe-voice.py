"""Local-only word-timed transcription; dependencies live outside the product."""
import argparse
import dataclasses
import json
import subprocess
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

parser = argparse.ArgumentParser()
parser.add_argument("audio", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--model", default="small.en")
args = parser.parse_args()
model = WhisperModel(
    args.model, device="cpu", compute_type="int8", cpu_threads=6,
    download_root="/private/tmp/matchlens-whisper-models",
)
audio = np.frombuffer(subprocess.check_output([
    "ffmpeg", "-v", "error", "-i", str(args.audio), "-ac", "1", "-ar", "16000",
    "-f", "f32le", "-",
]), dtype=np.float32)
segments, info = model.transcribe(
    audio, language="en", word_timestamps=True, beam_size=5,
    initial_prompt=(
        "Matchlens. Northbridge. Football-realistic synthetic events. "
        "Coordinator, tactical, narrator, fact-checker, producer, personaliser. "
        "Microsoft Agent Framework. Azure OpenAI in Microsoft Foundry. "
        "Azure Container Apps. Cosmos DB. Hindi, English, Spanish."
    ),
)
result = {"duration": info.duration, "language": info.language, "segments": []}
for segment in segments:
    item = dataclasses.asdict(segment)
    result["segments"].append(item)
    print(f"{segment.start:7.2f}–{segment.end:7.2f} {segment.text}", flush=True)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2))
print(f"Saved {args.output}", flush=True)
