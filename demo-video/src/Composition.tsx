import { AbsoluteFill, Sequence, Track, staticFile } from "remotion";
import { Audio } from "@remotion/media";
import { Background, Chrome, FontGate } from "./kit";
import {
  Agents,
  Closing,
  Correction,
  Evidence,
  Introduction,
  Multiverse,
  Opening,
  Platform,
  Pundit,
} from "./scenes";
import { voice } from "./media-manifest";
import { VoiceCaptions } from "./VoiceCaptions";
import { voiceTiming } from "./voice-timing";
import "./film.css";
export type FilmProps = { demoUrl: string; showGuide: boolean };
export const MatchlensFilm = ({ demoUrl, showGuide }: FilmProps) => {
  return (
    <AbsoluteFill style={{ background: "#080D14" }}>
      <FontGate />
      <Background />
      <Track name="Story">
        <Sequence
          name="01 — The score is only the start"
          from={voiceTiming.opening.startFrame}
          durationInFrames={voiceTiming.opening.durationInFrames}
        >
          <Opening />
        </Sequence>
        <Sequence
          name="02 — Meet Matchlens"
          from={voiceTiming.introduction.startFrame}
          durationInFrames={voiceTiming.introduction.durationInFrames}
        >
          <Introduction />
        </Sequence>
        <Sequence
          name="03 — Explain, inspect, replay"
          from={voiceTiming.evidence.startFrame}
          durationInFrames={voiceTiming.evidence.durationInFrames}
        >
          <Evidence />
        </Sequence>
        <Sequence
          name="04 — Agent collaboration"
          from={voiceTiming.agents.startFrame}
          durationInFrames={voiceTiming.agents.durationInFrames}
        >
          <Agents />
        </Sequence>
        <Sequence
          name="05 — Correct the story"
          from={voiceTiming.correction.startFrame}
          durationInFrames={voiceTiming.correction.durationInFrames}
        >
          <Correction />
        </Sequence>
        <Sequence
          name="06 — Four perspectives"
          from={voiceTiming.multiverse.startFrame}
          durationInFrames={voiceTiming.multiverse.durationInFrames}
        >
          <Multiverse />
        </Sequence>
        <Sequence
          name="07 — Questions and catch-up"
          from={voiceTiming.pundit.startFrame}
          durationInFrames={voiceTiming.pundit.durationInFrames}
        >
          <Pundit />
        </Sequence>
        <Sequence
          name="08 — Microsoft and Azure"
          from={voiceTiming.platform.startFrame}
          durationInFrames={voiceTiming.platform.durationInFrames}
        >
          <Platform />
        </Sequence>
        <Sequence
          name="09 — Every moment, understood"
          from={voiceTiming.closing.startFrame}
          durationInFrames={voiceTiming.closing.durationInFrames}
        >
          <Closing demoUrl={demoUrl} />
        </Sequence>
      </Track>
      <Track name="Your voice">
        {voice.opening && (
          <Audio
            name="01 — Opening narration"
            src={staticFile(voice.opening)}
            from={voiceTiming.opening.startFrame}
            durationInFrames={voiceTiming.opening.durationInFrames}
          />
        )}
        {voice.introduction && (
          <Audio
            name="02 — Product introduction"
            src={staticFile(voice.introduction)}
            from={voiceTiming.introduction.startFrame}
            durationInFrames={voiceTiming.introduction.durationInFrames}
          />
        )}
        {voice.evidence && (
          <Audio
            name="03 — Evidence narration"
            src={staticFile(voice.evidence)}
            from={voiceTiming.evidence.startFrame}
            durationInFrames={voiceTiming.evidence.durationInFrames}
          />
        )}
        {voice.agents && (
          <Audio
            name="04 — Agent narration"
            src={staticFile(voice.agents)}
            from={voiceTiming.agents.startFrame}
            durationInFrames={voiceTiming.agents.durationInFrames}
          />
        )}
        {voice.correction && (
          <Audio
            name="05 — Correction narration"
            src={staticFile(voice.correction)}
            from={voiceTiming.correction.startFrame}
            durationInFrames={voiceTiming.correction.durationInFrames}
          />
        )}
        {voice.multiverse && (
          <Audio
            name="06 — Multiverse narration"
            src={staticFile(voice.multiverse)}
            from={voiceTiming.multiverse.startFrame}
            durationInFrames={voiceTiming.multiverse.durationInFrames}
          />
        )}
        {voice.pundit && (
          <Audio
            name="07 — Pundit narration"
            src={staticFile(voice.pundit)}
            from={voiceTiming.pundit.startFrame}
            durationInFrames={voiceTiming.pundit.durationInFrames}
          />
        )}
        {voice.platform && (
          <Audio
            name="08 — Platform narration"
            src={staticFile(voice.platform)}
            from={voiceTiming.platform.startFrame}
            durationInFrames={voiceTiming.platform.durationInFrames}
          />
        )}
        {voice.closing && (
          <Audio
            name="09 — Closing narration"
            src={staticFile(voice.closing)}
            from={voiceTiming.closing.startFrame}
            durationInFrames={voiceTiming.closing.durationInFrames}
          />
        )}
      </Track>
      <VoiceCaptions />
      <Chrome guide={showGuide} />
    </AbsoluteFill>
  );
};
