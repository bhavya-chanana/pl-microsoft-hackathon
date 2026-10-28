# Voice takes for the replacement Matchlens film

All three replacement takes have been supplied in `agent-recording.m4a` and are included in the 111.53-second final cut. Seven other sections reuse the original recording. The paragraphs below remain the editable script for future retakes; their wording may differ slightly from the natural delivered recording.

## Agents — up to 11 seconds

Specialist agents explain each moment. Their claims are checked against deterministic statistics before producer and personaliser agents create the audience experiences.

Local filename after import: `public/audio/v2/agents-raw.m4a`.

## Coach — up to 15 seconds

For coaches, freeze a match moment and ask for tactical ideas. Compare each idea with the baseline, watch ten-second simulated outcomes, and inspect the benefits, risks, and uncertainty.

Local filename after import: `public/audio/v2/coach-raw.m4a`.

## Platform — up to 8 seconds

Microsoft Agent Framework and Azure OpenAI power Matchlens, with Azure Container Apps and Cosmos DB.

Local filename after import: `public/audio/v2/platform-raw.m4a`.

## Assembly

Transcribe the real takes locally with `scripts/transcribe-voice.py`, saving one `<scene>-transcript.json` beside each raw file. Run `npm run assets:v2` to build aligned narration and captions. The preparation script refuses to truncate speech that exceeds a scene; adjust the authored timeline explicitly instead.

The original recording is preserved locally. No voice cloning or synthetic replacement is used.
