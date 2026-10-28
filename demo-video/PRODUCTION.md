# Matchlens hackathon film

An editable 118.5-second, 1920×1080, 30 fps Remotion film for Inside the Game, aligned to the supplied human narration.

Product footage is recorded from **ui-redesign-codex**, including its local UI changes. Film source lives here; application development stays in its own worktree.

## Preview

```sh
npm ci
npm run assets
npm run dev
```

Open http://localhost:3200/Matchlens-Hackathon. The opening, agent workflow and closing also have individual compositions for editing.

## Version control

Keep source, package lock, fonts, recording guide and capture tooling. Dependencies, downloaded skills, capture scratch files, raw clips, human voice recordings and exports are ignored. This independent npm project does not add Remotion to the product's frontend or backend dependencies.

## Media workflow

1. Record the redesigned frontend against an isolated **memory** backend. Never restart another developer's match to film.
2. Put the ten clips under `public/footage/`, named `01-opening.mp4` through `10-winner.mp4`. See `src/storyboard.ts` for the capture list.
3. Record the nine segments in [VOICEOVER.md](VOICEOVER.md). Put the files under `public/audio/` with the numbered names in that guide.
4. Run `npm run assets` to refresh the local manifest, then review the composition.
5. Align captions to the delivered human voice. Draft scene timings are editorial targets, not a transcript.
6. Run `npm run check`. After reviewing the edit and audio, render a preview or final export.

The current human take is preserved as ignored `public/audio/pl-demo-hackathon-original.m4a`. Its 121.344 seconds are edited to 117.95 seconds by trimming the ends and shortening three pauses, with playback rate 1. The 118.5-second film leaves a closing hold. `scripts/prepare-human-voice.py` reproduces the edit from the ignored word transcript; `src/voice-timing.ts` keeps the visual scenes on the recorded paragraph boundaries. The local transcript uses faster-whisper small.en and includes pronunciation/recognition uncertainty; listen to the finished export before submission.

The capture tooling uses real Chrome frames and timestamps, encoded at CRF 16. It does not manufacture app states, answers, latency or statistics. The capture report stays in ignored `out/`.

The Azure scene describes implementation and deployment architecture. It does not assert a verified public deployment. Demo mode retains actual source labels.

## Final export

Create ignored `delivery-props.local.json` with `demoUrl` set to the verified public application URL and `showGuide` set to `false`. Supply reviewed captions in `public/audio/captions-reviewed.json`. `npm run render:final` checks required media before exporting `out/matchlens-submission.mp4`.

Upload the approved export to a permitted video platform and check public playback. Nothing here automatically publishes the video or submits the entry.

## Rights

Film graphics and monogram are authored in code. Fonts are the open-source Barlow Condensed and IBM Plex Sans used by the product, under the SIL Open Font License. Only original narration and product footage are planned. No stock match footage, third-party logos or music are included.
