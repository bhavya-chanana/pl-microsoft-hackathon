/** Deterministic 0–1 hash (32-bit FNV-1a), for stable per-id visuals such as player portraits. */
export const hash = (s: string) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10_000) / 10_000;
};
