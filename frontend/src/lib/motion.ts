export const spring = {
  snappy: { type: "spring", stiffness: 500, damping: 40, mass: 0.8 },
  smooth: { type: "spring", stiffness: 260, damping: 30 },
  gentle: { type: "spring", bounce: 0.15, visualDuration: 0.45 },
} as const;

export const fadeUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.35, ease: [0.32, 0.72, 0, 1] },
} as const;
