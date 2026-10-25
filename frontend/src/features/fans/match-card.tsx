import type { MatchCard as MatchCardData } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

const T = copy.matchCard;

type Tier = keyof typeof T.tiers;

/** The usual card colours: 85+ elite, 75+ gold, 65+ silver, else bronze. */
export function tierOf(overall: number): Tier {
  return overall >= 85 ? "elite" : overall >= 75 ? "gold" : overall >= 65 ? "silver" : "bronze";
}

const TIER_TONE: Record<Tier, { band: string; tint: string; text: string }> = {
  elite: { band: "bg-tier-elite", tint: "bg-tier-elite/12", text: "text-tier-elite" },
  gold: { band: "bg-tier-gold", tint: "bg-tier-gold/12", text: "text-tier-gold" },
  silver: { band: "bg-tier-silver", tint: "bg-tier-silver/12", text: "text-tier-silver" },
  bronze: { band: "bg-tier-bronze", tint: "bg-tier-bronze/12", text: "text-tier-bronze" },
};

/** Small overall badge for lists, coloured by tier. */
export function OverallBadge({ overall }: { overall: number }) {
  const tier = tierOf(overall);
  return (
    <span
      title={`${T.overall} ${overall}, ${T.tiers[tier]}`}
      className={cn("inline-flex h-5 min-w-7 items-center justify-center rounded-sm px-1 text-xs font-bold tabular-nums", TIER_TONE[tier].tint)}
    >
      {overall}
    </span>
  );
}

/** FIFA-style card for one player, from this match's own data. */
export function MatchCard({ card, position }: { card: MatchCardData; position: string }) {
  const tier = tierOf(card.overall);
  const tone = TIER_TONE[tier];
  return (
    <section aria-labelledby="match-card-heading" className={cn("relative shrink-0 overflow-hidden rounded-lg border p-4", tone.tint)}>
      <span aria-hidden className={cn("absolute inset-x-0 top-0 h-1", tone.band)} />
      <div className="flex items-center gap-4">
        <div className="flex flex-col items-center">
          <span className="font-display text-score font-bold tabular-nums">{card.overall}</span>
          <span className="text-xs font-semibold text-muted-foreground">{position}</span>
        </div>
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 id="match-card-heading" className="text-sm font-semibold">
            {T.heading} <span className="font-normal text-muted-foreground">· {T.tiers[tier]}</span>
          </h3>
          <p className="text-xs text-muted-foreground">{T.help}</p>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-x-4 gap-y-2">
        {card.attributes.map((a) => (
          <div key={a.key} className="flex items-baseline justify-between gap-2 border-b border-border/60 pb-1">
            <dt className="text-xs text-muted-foreground">
              <abbr title={T.attributes[a.key]} className="no-underline">
                {a.key}
              </abbr>
            </dt>
            <dd className="font-display text-lg font-semibold tabular-nums" title={a.value === null ? T.noTracking : undefined}>
              {a.value ?? "–"}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
