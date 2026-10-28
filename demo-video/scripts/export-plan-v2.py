"""Write export QA inputs and a portable SRT from the actual voice-aligned edit."""
from pathlib import Path
import json

root = Path(__file__).resolve().parents[1]
out = root / "out/v2"
edit = json.loads((out / "edit.json").read_text())
flat = []
for scene in edit["scenes"]:
    for caption in edit["captions"][scene["id"]]:
        offset = scene["startFrame"] / edit["fps"] * 1000
        flat.append({**caption,"startMs":caption["startMs"]+offset,"endMs":caption["endMs"]+offset})
(out / "captions-global.json").write_text(json.dumps(flat,indent=2))
plan = {
    "title":"Matchlens replacement submission film",
    "video":{"width":1920,"height":1080,"fps":30,"target_frames":edit["frames"],"max_seconds":120,"strict_max":True,"codec":"h264"},
    "audio":{"required":True,"narration":"human"},"captions":{"required":True},
    "scenes":[{"id":s["id"],"start_frame":s["startFrame"],"duration_frames":s["durationInFrames"]} for s in edit["scenes"]],
    "assets":[{"id":key+"-voice","path":"../../public/"+path,"kind":"audio","role":"narration","scene_id":key} for key,path in edit["voice"].items()]+[{"id":"aligned-captions","path":"captions-global.json","kind":"captions"}],
    "review":{"visual_reviewed":False,"audio_reviewed":False,"captions_reviewed":False},
    "pending_voice":edit["pending"],
}
(out / "video-plan.json").write_text(json.dumps(plan,indent=2))
def stamp(ms):
    ms = round(ms)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
pages, page = [], []
for word in flat:
    if page and (word["startMs"]-page[0]["startMs"]>2800 or len("".join(x["text"] for x in page))+len(word["text"])>80):
        pages.append(page)
        page = []
    page.append(word)
    if word["text"].strip().endswith((".","?","!")):
        pages.append(page)
        page = []
if page:
    pages.append(page)
srt = "\n\n".join(f"{i+1}\n{stamp(p[0]['startMs'])} --> {stamp(max(p[-1]['endMs'],p[0]['startMs']+200))}\n"+"".join(w["text"] for w in p).strip() for i,p in enumerate(pages))+"\n"
(root / "out/matchlens-submission.srt").write_text(srt)
print(f"{edit['frames']/30:.2f}s, {len(edit['voice'])} voice tracks, {len(flat)} timed tokens, {len(pages)} SRT entries")
