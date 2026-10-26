import { cn } from "@/lib/utils";

import { hash } from "./hash";

const SKIN = ["#f1c9a5", "#e0ac83", "#c68b5e", "#a26b43", "#7a4b2a", "#5a3520"];
const HAIR = ["#1d1611", "#3b2717", "#6b4524", "#a87436", "#d9b66f", "#2b2b2b"];
type Hair = "short" | "buzz" | "curly" | "long" | "bald" | "fade";
const STYLES: Hair[] = ["short", "buzz", "curly", "long", "bald", "fade"];

/**
 * Illustrated portrait for a fictional player: deterministic from the id, wearing the club kit.
 * Demo players have no photos; swap for real headshots when a licensed feed provides them.
 */
export function PlayerFace({ id, shirt, kit, className, label }: {
  id: string; shirt: number; kit: "home" | "away"; className?: string; label?: string;
}) {
  const pick = <T,>(xs: T[], salt: string) => xs[Math.floor(hash(id + salt) * xs.length)];
  const skin = pick(SKIN, "skin"), hair = pick(HAIR, "hair"), style = pick(STYLES, "style");
  const beard = hash(id + "beard") > 0.65;
  const kitColor = kit === "home" ? "var(--team-home)" : "var(--team-away)";
  const numColor = kit === "home" ? "var(--graphic-foreground)" : "var(--graphic)";

  return (
    <svg viewBox="0 0 64 64" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}
      className={cn("shrink-0 rounded-full bg-muted", className)}>
      <clipPath id={`face-${id}`}><circle cx="32" cy="32" r="32" /></clipPath>
      <g clipPath={`url(#face-${id})`}>
        <rect width="64" height="64" fill={kitColor} opacity="0.18" />
        {/* shirt */}
        <path d="M6 64 C8 50 18 45 32 45 C46 45 56 50 58 64 Z" fill={kitColor} />
        <path d="M26 45 L32 52 L38 45" fill="none" stroke={numColor} strokeWidth="1.6" opacity="0.6" />
        <text x="32" y="62" textAnchor="middle" fontSize="8" fontWeight="700" fill={numColor} fontFamily="var(--font-display)">{shirt}</text>
        {/* neck + head */}
        <rect x="27" y="36" width="10" height="11" rx="3" fill={skin} />
        <ellipse cx="32" cy="27" rx="11.5" ry="13" fill={skin} />
        <ellipse cx="20.5" cy="28" rx="2" ry="3" fill={skin} /><ellipse cx="43.5" cy="28" rx="2" ry="3" fill={skin} />
        {/* hair */}
        {style === "short" && <path d="M20.5 24 C20 13 27 11 32 11 C38 11 44 13 43.5 24 C41 18 36 17 32 17 C27 17 23 18 20.5 24 Z" fill={hair} />}
        {style === "buzz" && <path d="M21 22 C21 14 27 12.5 32 12.5 C37 12.5 43 14 43 22 C39 18 25 18 21 22 Z" fill={hair} opacity="0.75" />}
        {style === "curly" && <g fill={hair}>{[20, 25, 30, 35, 40, 44].map((x, i) => <circle key={x} cx={x} cy={i % 2 ? 14 : 16.5} r="5" />)}</g>}
        {style === "long" && <path d="M19 34 C17 15 25 10 32 10 C40 10 47 15 45 34 L42 34 C43 22 38 17 32 17 C26 17 21 22 22 34 Z" fill={hair} />}
        {style === "fade" && <path d="M21 21 C21 12 27 10 32 10 C37 10 43 12 43 21 L41 17 L23 17 Z" fill={hair} />}
        {beard && <path d="M21.5 29 C22 39 27 41 32 41 C37 41 42 39 42.5 29 C40 35 36 36.5 32 36.5 C28 36.5 24 35 21.5 29 Z" fill={hair} opacity="0.85" />}
        {/* face */}
        <circle cx="27.5" cy="27" r="1.3" fill="#1b1b1b" /><circle cx="36.5" cy="27" r="1.3" fill="#1b1b1b" />
        <path d="M25.5 23.3 L29.5 22.8 M34.5 22.8 L38.5 23.3" stroke={hair} strokeWidth="1.3" strokeLinecap="round" />
        <path d="M29 33.5 Q32 35.5 35 33.5" fill="none" stroke="#5b2f25" strokeWidth="1.2" strokeLinecap="round" />
      </g>
    </svg>
  );
}
