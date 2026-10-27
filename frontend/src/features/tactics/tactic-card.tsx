"use client";

import { AlertTriangle, Eye, GitCompareArrows, LoaderCircle, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { OutcomeStats, TacticResult } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { type Roster, TONE_CLASS, directiveText, labelOf, liftText, liftTone, nameOf, pct, zoneLabel } from "./lib";

const t = copy.tactics;
/** The lifts worth a chip on the card, in order. */
const LIFT_ORDER = ["p_shot", "mean_xg", "p_box_entry", "p_turnover", "p_concede_shot"];

const CONFIDENCE_TONE = {
  high: "bg-verified/15 text-verified",
  medium: "bg-data/15 text-data",
  low: "bg-muted text-muted-foreground",
} as const;

function Directives({ tactic, roster }: { tactic: TacticResult; roster: Roster }) {
  const list = tactic.intent.directives;
  if (list.length === 0) return <p className="text-sm text-muted-foreground">{t.cards.noDirectives}</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {list.map((d, i) => {
        const text = directiveText(d, roster);
        return (
          <li key={i} className="flex flex-wrap items-baseline gap-x-1.5">
            <span className="font-semibold">{text.who}</span>
            <span>{text.what}</span>
            <span className="text-xs text-muted-foreground tabular-nums">{text.when}</span>
          </li>
        );
      })}
    </ul>
  );
}

function MoreDetail({ tactic, roster }: { tactic: TacticResult; roster: Roster }) {
  const { ball_plan, press, rest_defence, cites } = tactic.intent;
  if (ball_plan.length === 0 && !press && rest_defence === 0 && cites.length === 0) return null;
  return (
    <details className="group">
      <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">{t.cards.expand}</summary>
      <dl className="flex flex-col gap-3 pb-1 text-sm">
        {ball_plan.length > 0 && (
          <div>
            <dt className="text-xs font-medium text-muted-foreground">{t.cards.ballPlan}</dt>
            <dd>
              <ol className="list-decimal pl-5">
                {ball_plan.map((s, i) => (
                  <li key={i}>
                    {labelOf(t.ballActions, s.action)}
                    {s.from_player_id && ` ${nameOf(roster, s.from_player_id)}`}
                    {s.to_player_id && s.to_player_id !== s.from_player_id && ` → ${nameOf(roster, s.to_player_id)}`}
                    {s.to_zone && `, ${t.cards.toZone} ${zoneLabel(s.to_zone)}`}
                  </li>
                ))}
              </ol>
            </dd>
          </div>
        )}
        {press && (
          <div>
            <dt className="text-xs font-medium text-muted-foreground">{t.cards.press}</dt>
            <dd>
              {labelOf(t.cards.pressTrigger, press.trigger)}, {t.cards.pressIntensity(press.intensity)}
              {press.cover_shadow_on && `, ${nameOf(roster, press.cover_shadow_on)}`}
            </dd>
          </div>
        )}
        {rest_defence > 0 && <p>{t.cards.restDefence(rest_defence)}</p>}
        {cites.length > 0 && (
          <div>
            <dt className="text-xs font-medium text-muted-foreground">{t.cards.cites}</dt>
            <dd>{cites.join("; ")}</dd>
          </div>
        )}
      </dl>
    </details>
  );
}

function Lifts({ lift }: { lift: Record<string, number> }) {
  const keys = LIFT_ORDER.filter((k) => k in lift).slice(0, 4);
  if (keys.length === 0) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted-foreground">{t.cards.lift}</p>
      <ul className="flex flex-wrap gap-2">
        {keys.map((k) => (
          <li key={k} className="rounded-md border px-2.5 py-1 text-xs">
            <span className="text-muted-foreground">{labelOf(copy.tactics.outcome.metrics, k)} </span>
            <span className={cn("font-semibold tabular-nums", TONE_CLASS[liftTone(k, lift[k])])}>{liftText(k, lift[k])}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type CardProps = {
  tactic: TacticResult;
  roster: Roster;
  selected: boolean;
  recommended: boolean;
  /** No clear winner: drop the confidence badge so nothing reads as a verdict. */
  quiet?: boolean;
  onShow: () => void;
  onCompare: () => void;
};

/** One idea, in whichever state it has reached: thinking, evaluating, ready or rejected. */
export function TacticCard({ tactic, roster, selected, recommended, quiet = false, onShow, onCompare }: CardProps) {
  const { status, intent } = tactic;
  const persona = labelOf(t.personas, tactic.persona);
  const rejected = status === "rejected";
  const ready = status === "ready";
  const title = intent.title || persona;

  return (
    <li
      aria-busy={status === "thinking" || status === "evaluating"}
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised transition-colors",
        selected && "border-ring ring-2 ring-ring/40",
        rejected && "border-dashed bg-card/60",
      )}
    >
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {tactic.rank !== null && ready && (
            <span className="font-display text-lg leading-none font-bold text-muted-foreground tabular-nums" title={t.cards.rank(tactic.rank)}>
              <span className="sr-only">{t.cards.rank(tactic.rank)}. </span>
              <span aria-hidden>{tactic.rank}</span>
            </span>
          )}
          <Badge variant="outline">{persona}</Badge>
          {status !== "thinking" && <Badge variant="secondary">{labelOf(t.mechanisms, intent.mechanism)}</Badge>}
          {recommended && (
            <Badge className="gap-1 bg-verified/15 text-verified">
              <Sparkles aria-hidden data-icon="inline-start" />
              {t.cards.recommended}
            </Badge>
          )}
          {ready && tactic.confidence && !quiet && (
            <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", CONFIDENCE_TONE[tactic.confidence])} title={t.cards.confidenceHelp}>
              {t.cards.confidence[tactic.confidence]}
            </span>
          )}
        </div>
        {status === "thinking" ? (
          <div className="flex flex-col gap-2" role="status">
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" />
              {t.cards.thinking}
            </p>
            <Skeleton className="h-6 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
          </div>
        ) : (
          <h3 className="font-display text-xl leading-tight font-semibold">{title}</h3>
        )}
      </header>

      {status !== "thinking" && <p className="max-w-prose text-sm">{intent.idea}</p>}

      {rejected && (
        <div className="rounded-md border border-blocked/40 p-3 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle aria-hidden className="size-4 shrink-0 text-blocked" />
            {t.cards.rejected}
          </p>
          <p className="text-xs text-muted-foreground">{t.cards.rejectedHelp}</p>
          {tactic.problems.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {tactic.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {(status === "evaluating" || ready) && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">{t.cards.directives}</p>
          <Directives tactic={tactic} roster={roster} />
        </div>
      )}

      {status === "evaluating" && (
        <div role="status" className="flex flex-col gap-1.5">
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" />
            {t.cards.evaluating}
          </p>
          <div aria-hidden className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full w-1/3 rounded-full bg-home motion-safe:animate-pulse" />
          </div>
        </div>
      )}

      {ready && <Lifts lift={tactic.lift} />}
      {ready && tactic.outcome && (
        <p className="text-sm tabular-nums">
          <span className="text-muted-foreground">{labelOf(copy.tactics.outcome.metrics, "p_shot")} </span>
          <span className="font-semibold">{pct(tactic.outcome.p_shot.value)}</span>
          <span className="text-muted-foreground"> · xG </span>
          <span className="font-semibold">{tactic.outcome.mean_xg.toFixed(2)}</span>
        </p>
      )}
      {(status === "evaluating" || ready) && <MoreDetail tactic={tactic} roster={roster} />}

      {ready && (
        <div className="mt-auto flex flex-wrap gap-2">
          <Button variant={selected ? "default" : "secondary"} className="h-11 gap-2 px-4" onClick={onShow} aria-pressed={selected}>
            <Eye aria-hidden />
            {selected ? t.cards.showing : t.cards.show}
          </Button>
          <Button variant="outline" className="h-11 gap-2 px-4" onClick={onCompare}>
            <GitCompareArrows aria-hidden />
            {t.stage.compare}
          </Button>
        </div>
      )}
    </li>
  );
}

/** The do-nothing branch: always there, always the yardstick. */
export function BaselineCard({ stats, selected, onShow }: { stats: OutcomeStats | null; selected: boolean; onShow: () => void }) {
  return (
    <li className={cn("flex flex-col gap-3 rounded-lg border border-dashed bg-card/60 p-4", selected && "border-solid border-ring ring-2 ring-ring/40")}>
      <h3 className="font-display text-xl leading-tight font-semibold">{t.cards.baselineTitle}</h3>
      <p className="max-w-prose text-sm text-muted-foreground">{t.cards.baselineIdea}</p>
      {stats ? (
        <p className="text-sm tabular-nums">
          <span className="text-muted-foreground">{labelOf(copy.tactics.outcome.metrics, "p_shot")} </span>
          <span className="font-semibold">{pct(stats.p_shot.value)}</span>
          <span className="text-muted-foreground"> · xG </span>
          <span className="font-semibold">{stats.mean_xg.toFixed(2)}</span>
        </p>
      ) : (
        <Skeleton className="h-5 w-40" />
      )}
      <Button variant={selected ? "default" : "outline"} className="h-11 gap-2 self-start px-4" onClick={onShow} aria-pressed={selected} disabled={!stats}>
        <Eye aria-hidden />
        {selected ? t.cards.showing : t.cards.show}
      </Button>
    </li>
  );
}
