import { voiceTiming, filmFrames } from "./voice-timing";

const guideScenes = [
  {
    id: "opening",
    title: "The story behind the score",
    start: 0,
    end: 9,
    narration:
      "Northbridge are two-nil down. The pressure is shifting. A comeback is building—but the scoreline hasn’t caught up.",
  },
  {
    id: "introduction",
    title: "Meet Matchlens",
    start: 9,
    end: 22,
    narration:
      "I built Matchlens to explain the game behind the score, for fans and broadcasters. This demonstration uses synthetic, football-realistic events and tracking, with entirely fictional teams and players.",
  },
  {
    id: "evidence",
    title: "Explain. Inspect. Replay.",
    start: 22,
    end: 36,
    narration:
      "Watch the momentum shift. Matchlens connects pressure and key events into an explanation. Open the evidence, inspect the supporting numbers, then replay the moment with tactical annotations.",
  },
  {
    id: "agents",
    title: "A team behind every insight",
    start: 36,
    end: 52,
    narration:
      "Here’s how the agents collaborate. A coordinator selects the moment. Tactical and narrator agents develop the explanation. Claims are checked against a deterministic statistics engine, before producer and personaliser agents create the audience experiences.",
  },
  {
    id: "correction",
    title: "When the facts change",
    start: 52,
    end: 64,
    narration:
      "Live data changes. Here, the feed corrects a goalscorer. Matchlens updates affected statistics and insights, and retracts explanations that no longer hold.",
  },
  {
    id: "multiverse",
    title: "One match. Four perspectives.",
    start: 64,
    end: 82,
    narration:
      "Now, one match becomes four experiences: a casual fan in Hindi, an analyst in English, a player-focused fan in Spanish, and a coach receiving tactical recommendations. Same match data. Different depth, language, and perspective.",
  },
  {
    id: "pundit",
    title: "Your questions. Match evidence.",
    start: 82,
    end: 97,
    narration:
      "Ask, ‘Why did the momentum change?’ The answer brings supporting numbers and links back to match moments. Missed the action? Generate a catch-up that explains the story so far.",
  },
  {
    id: "platform",
    title: "Built on Microsoft and Azure",
    start: 97,
    end: 109,
    narration:
      "The workflow uses Microsoft Agent Framework and Azure OpenAI in Microsoft Foundry. The application is built for Azure Container Apps, with Cosmos DB supporting persisted match history.",
  },
  {
    id: "closing",
    title: "Every moment, understood.",
    start: 109,
    end: 115,
    narration: "Matchlens. Beyond the scoreline. Every moment, understood.",
  },
] as const;

export const scenes = guideScenes.map((scene) => ({
  ...scene,
  start: voiceTiming[scene.id].startFrame / 30,
  end:
    (voiceTiming[scene.id].startFrame +
      voiceTiming[scene.id].durationInFrames) /
    30,
}));
export type SceneId = (typeof guideScenes)[number]["id"];
export const filmSeconds = filmFrames / 30;

export const shots = [
  {
    id: "opening",
    file: "01-opening.mp4",
    route: "/",
    action:
      "Northbridge 0–2 at roughly 60 minutes. Hold the live pitch and momentum.",
  },
  {
    id: "introduction",
    file: "02-live.mp4",
    route: "/",
    action:
      "Clean establishing shot of the redesigned Live page, match header and insights.",
  },
  {
    id: "evidence",
    file: "03-evidence.mp4",
    route: "/moments",
    action:
      "Open the evidence on an actual insight. Keep verdicts and source labels readable.",
  },
  {
    id: "replay",
    file: "04-replay.mp4",
    route: "/moments",
    action: "Click a replayable moment and record the actual tactical replay.",
  },
  {
    id: "agents",
    file: "05-agents.mp4",
    route: "/agents",
    action:
      "Hold the actual workflow and an actual result. Retain the recorded-demo source label.",
  },
  {
    id: "correction",
    file: "06-correction.mp4",
    route: "/moments",
    action:
      "Capture the seeded scorer correction and resulting actual update. No fabricated UI states.",
  },
  {
    id: "multiverse",
    file: "07-multiverse.mp4",
    route: "/explore/multiverse",
    action:
      "Establish all four perspectives and languages, then hold readable output.",
  },
  {
    id: "pundit",
    file: "08-pundit.mp4",
    route: "/",
    action:
      "Open Ask the pundit, ask the momentum question and hold the answer and cited moments.",
  },
  {
    id: "catchup",
    file: "09-catchup.mp4",
    route: "/",
    action:
      "Open What did I miss? and hold the generated catch-up, score and provenance.",
  },
  {
    id: "winner",
    file: "10-winner.mp4",
    route: "/",
    action: "Record the late winner and actual 3–2 score.",
  },
] as const;

export type ShotId = (typeof shots)[number]["id"];
