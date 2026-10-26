/** Cadences from docs/POLLING.md, mirrored by the backend snapshot rules. */
export const cadence = {
  viewingDelayMs: 90_000,
  physicalSnapshotMs: 15_000,
  fullSnapshotMs: 30_000,
  snapshotLagMs: 20_000,
  statsRefreshMs: 15_000,
  pulseBucketMs: 60_000,
  overlayDwellMs: 8_000,
  boardRefreshMs: 30_000,
} as const;
