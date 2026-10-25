"use client";

import { ChevronDown, Star, Video } from "lucide-react";
import { motion } from "motion/react";
import { useId, useState } from "react";

import { useReplay } from "@/features/broadcast/replay-store";
import { useLive } from "@/features/live/live-store";
import type { ClaimResult, FeedItem, Language, MatchMeta } from "@/lib/contract";
import { copy, signalLabels } from "@/lib/copy";
import { playerName, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { FactCheckBadge } from "./fact-check-badge";

function describeClaim(result: ClaimResult, meta: MatchMeta | null): string {
  const c = result.claim;
  const kind = String(c.kind ?? "claim");
  if (kind === "score") return `Score ${String(c.home)}-${String(c.away)}`;
  if (kind === "stat") {
    const who = playerName(meta, String(c.subject_id ?? "")) || String(c.subject_id ?? "");
    return `${who} ${String(c.stat ?? "").replaceAll("_", " ")} ${String(c.value ?? "")}`.trim();
  }
  if (kind === "event") {
    const who = playerName(meta, typeof c.player_id === "string" ? c.player_id : null);
    return who ? `Event involving ${who}` : "Event in the match log";
  }
  if (kind === "momentum") return `Momentum ${String(c.team_id ?? "")}`.trim();
  return kind;
}

function Evidence({ insightId }: { insightId: string }) {
  const insight = useLive((s) => s.insights[insightId]);
  const meta = useLive((s) => s.meta);
  if (!insight) return null;
  const fc = insight.fact_check;
  return (
    <div className="flex flex-col gap-3 border-t pt-3 text-sm">
      <p className="text-muted-foreground">{copy.factCheckHelp[fc.status]}</p>
      {fc.results.length ? (
        <ul className="flex flex-col gap-1.5">
          {fc.results.map((r, i) => (
            <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span>{describeClaim(r, meta)}</span>
              <span
                className={cn(
                  "text-xs font-medium",
                  r.verdict === "supported" ? "text-verified" : r.verdict === "contradicted" ? "text-blocked" : "text-rewritten",
                )}
              >
                {copy.verdicts[r.verdict] ?? r.verdict}
                {r.verdict !== "supported" && r.actual !== null && ` (data: ${String(r.actual)})`}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">{copy.feed.noClaims}</p>
      )}
      {fc.unaccounted_numbers.length > 0 && (
        <p>
          <span className="text-muted-foreground">{copy.feed.unaccounted}: </span>
          {fc.unaccounted_numbers.join(", ")}
        </p>
      )}
      {insight.agents.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{copy.feed.agentsInvolved}:</span>
          {insight.agents.map((a) => (
            <span key={a} className="rounded-sm bg-muted px-1.5 py-0.5 text-foreground">
              {copy.agents.nodes[a as keyof typeof copy.agents.nodes]?.title ?? a}
            </span>
          ))}
          {insight.latency_ms !== null && <span>{copy.feed.latency(insight.latency_ms)}</span>}
        </div>
      )}
    </div>
  );
}

/** Moments the telestrator can draw; period markers and corrections have nothing to show. */
const REPLAYABLE = new Set([
  "goal", "big_chance", "big_save", "shot_speed_badge", "hard_pass", "pressure_spell", "control_spell",
  "chaos_spell", "momentum_swing", "speed_badge", "distance_badge", "card",
]);

export function InsightCard({ item, language }: { item: FeedItem; language: Language }) {
  const meta = useLive((s) => s.meta);
  const play = useReplay((s) => s.play);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const side = sideOf(meta, item.team_id);
  return (
    <motion.article
      lang={language}
      layout="position"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="relative flex flex-col gap-2 overflow-hidden rounded-lg border bg-card p-4 pl-5 shadow-raised"
    >
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1", side ? sideTone[side].bg : "bg-border")} />
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-display text-xl leading-none font-semibold tabular-nums">{item.minute_label}</span>
        <span className="text-xs text-muted-foreground">{signalLabels[item.signal_type] ?? item.signal_type}</span>
        {item.favourite_player && (
          <span className="inline-flex items-center gap-1 text-xs font-medium">
            <Star aria-hidden className="size-3.5 fill-current" />
            {copy.feed.yourPlayer}
          </span>
        )}
        <FactCheckBadge status={item.fact_check} className="ml-auto" />
      </header>
      <h3 className="font-display text-xl leading-snug font-semibold">{item.headline}</h3>
      {item.body && <p className="max-w-prose text-sm text-muted-foreground">{item.body}</p>}
      {item.club_angle && item.club_angle !== item.headline && (
        <p className="max-w-prose border-l-2 pl-3 text-sm">
          <span className="sr-only">{copy.feed.forYourClub}: </span>
          {item.club_angle}
        </p>
      )}
      {item.key_numbers.length > 0 && (
        <dl className="flex flex-wrap gap-x-5 gap-y-1">
          {item.key_numbers.slice(0, 4).map((n) => (
            <div key={n.label} className="flex items-baseline gap-1.5">
              <dt className="text-xs text-muted-foreground">{n.label}</dt>
              <dd className="font-display text-lg font-semibold tabular-nums">
                {n.value}
                {n.unit && <span className="ml-0.5 text-xs font-normal">{n.unit}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="-mx-2 -mb-2 flex flex-wrap items-center gap-1">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          className="flex min-h-11 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <ChevronDown aria-hidden className={cn("size-4 transition-transform", open && "rotate-180")} />
          {open ? copy.feed.hideEvidence : copy.feed.showEvidence}
        </button>
        {REPLAYABLE.has(item.signal_type) && (
          <button
            type="button"
            onClick={() => play(item.insight_id, { reveal: true })}
            className="flex min-h-11 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Video aria-hidden className="size-4" />
            {copy.instantReplay.watch}
          </button>
        )}
      </div>
      <div id={panelId} hidden={!open}>
        {open && <Evidence insightId={item.insight_id} />}
      </div>
    </motion.article>
  );
}
