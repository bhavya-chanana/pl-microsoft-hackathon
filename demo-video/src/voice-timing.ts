// Aligned to the supplied human recording; frame values are at 30 fps.
export const voiceTiming = {
  opening: {
    id: "opening",
    startFrame: 0,
    durationInFrames: 240,
  },
  introduction: {
    id: "introduction",
    startFrame: 240,
    durationInFrames: 417,
  },
  evidence: {
    id: "evidence",
    startFrame: 657,
    durationInFrames: 384,
  },
  agents: {
    id: "agents",
    startFrame: 1041,
    durationInFrames: 528,
  },
  correction: {
    id: "correction",
    startFrame: 1569,
    durationInFrames: 414,
  },
  multiverse: {
    id: "multiverse",
    startFrame: 1983,
    durationInFrames: 522,
  },
  pundit: {
    id: "pundit",
    startFrame: 2505,
    durationInFrames: 396,
  },
  platform: {
    id: "platform",
    startFrame: 2901,
    durationInFrames: 481,
  },
  closing: {
    id: "closing",
    startFrame: 3382,
    durationInFrames: 173,
  },
} as const;
export const filmFrames = 3555;
