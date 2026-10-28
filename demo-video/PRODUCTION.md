# Matchlens replacement submission film

The only registered composition is **Matchlens-Submission**: 1920×1080, 30 fps, 3,346 frames (111.53 seconds). It replaces the previous submission edit.

## Current delivery state

The product footage was freshly recorded from an isolated snapshot of `ui-redesign-codex` at revision `50dfce526dd8d9f102c908e94518cf180d9a4cce`. Application development and the public demo were not restarted. The capture uses a memory store, synthetic seed 7, and the recorded-demo cassette with offline fallbacks. Source and model labels are retained in product footage.

Seven scenes reuse edited portions of the original human take at its original speed. The supplied `agent-recording.m4a` contains all three replacement sections (Agents, Coach and Platform). It is preserved locally as `public/audio/v2/replacement-takes-original.m4a`, split at natural pauses and transcribed word by word. All ten narration sections are present. See [VOICEOVER.md](VOICEOVER.md).

## Preview and export

```sh
npm run assets
npm run dev
```

Open http://localhost:3200/Matchlens-Submission.

```sh
npm run check
npm run render:preview
```

The review export is `out/matchlens-new-cut.mp4`. All replacement voice files have been transcribed and aligned. To reproduce the final export:

```sh
npm run assets
npm run check -- --final
npm run render:final
```

The final command refuses missing voice takes. It exports `out/matchlens-submission.mp4`; final listening and subtitle wording review remain necessary before publishing. Set the verified demo URL and `showGuide: false` in ignored `delivery-props.local.json`.

## Coach proof sequence

The actual application produced three playable tactical ideas against a frozen synthetic match situation. Each was evaluated over 24 simulations with a ten-second horizon; representative rollouts can terminate earlier at a shot or turnover. The recorded board returned **No clear winner**, with overlapping uncertainty ranges. The film preserves this verdict instead of manufacturing a winning tactic.

The 15-second Coach sequence shows advice with evidence and a trade-off, the Build ideas action, the baseline comparison, worst-case playback and the actual verdict. Evaluation wait is omitted between the setup and ready-state shots, and the edit labels that cut. The horizon is a simulation parameter, not a claim that real football can be predicted ten seconds ahead.

## Source and privacy

Editable film source, safe metadata, capture tooling and licensed fonts belong in Git. Raw captures, private human narration, local delivery settings, model transcripts, scratch frames and exports are ignored. Source provenance, board output, capture reports and visual review frames are local under `out/v2/`. Preserve the original voice recording for revisions.

Only original code graphics, actual product footage and the supplied human voice are used. No voice cloning, stock match footage, commercial music or third-party logos are added. Fonts are Barlow Condensed and IBM Plex Sans under their included SIL Open Font Licenses.

Publishing and hackathon submission are separate actions. Nothing in this project automatically uploads or submits a video.
