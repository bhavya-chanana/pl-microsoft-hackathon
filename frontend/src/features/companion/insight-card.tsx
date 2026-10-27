"use client";

import { ArrowLeftRight, ChevronRight, Goal, Info, RectangleVertical, Share2, Star, Target, Video, Zap } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { m } from "motion/react";
import { useState } from "react";

import { useReplay } from "@/features/broadcast/replay-store";
import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import type { ClaimResult, FeedItem, Language, MatchMeta } from "@/lib/contract";
import { copy, signalLabels } from "@/lib/copy";
import { playerName, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";
import { routes } from "@/lib/routes";

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
                  r.verdict === "supported" ? "text-verified" : "text-muted-foreground",
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

/** The few moment types that deserve a full card; everything else is a quiet row. */
export const BIG_MOMENTS = new Set(["goal", "card", "big_chance"]);

const BIG_ICON: Record<string, typeof Zap> = { goal: Goal, card: RectangleVertical, big_chance: Target, substitution: ArrowLeftRight };

function useMomentActions(item: FeedItem) {
  const play = useReplay((s) => s.play);
  const router = useRouter();
  const replayable = REPLAYABLE.has(item.signal_type);
  const share = async () => {
    const text = `${item.minute_label} ${item.headline}`;
    try {
      if (navigator.share) await navigator.share({ title: copy.product, text });
      else {
        await navigator.clipboard.writeText(text);
        toast.success(copy.feed.copied);
      }
    } catch {
      /* the share sheet was dismissed */
    }
  };
  const replay = () => {
    play(item.insight_id, { reveal: true });
    router.push(routes.live);
  };
  return { replayable, share, replay };
}

/** "Details": the checks behind a moment, in a side sheet rather than inline small print. */
function DetailsSheet({ item, open, onOpenChange }: { item: FeedItem; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-4 overflow-y-auto overscroll-contain p-5 sm:max-w-md">
        <SheetHeader className="p-0 pr-10">
          <SheetTitle className="font-display text-2xl">
            {item.minute_label} {item.headline}
          </SheetTitle>
          <SheetDescription>{copy.feed.detailsHelp}</SheetDescription>
          <FactCheckBadge status={item.fact_check} className="mt-1" />
        </SheetHeader>
        {open && <Evidence insightId={item.insight_id} />}
      </SheetContent>
    </Sheet>
  );
}

function Ticks({ item }: { item: FeedItem }) {
  return (
    <>
      {item.favourite_player && (
        <span className="inline-flex items-center gap-1 text-[13px] font-medium">
          <Star aria-hidden className="size-3.5 fill-current" />
          {copy.feed.yourPlayer}
        </span>
      )}
    </>
  );
}

function MomentCard({ item, language }: { item: FeedItem; language: Language }) {
  const meta = useLive((s) => s.meta);
  const side = sideOf(meta, item.team_id);
  const [details, setDetails] = useState(false);
  const { replayable, share, replay } = useMomentActions(item);
  const Icon = BIG_ICON[item.signal_type] ?? Zap;
  const goal = item.signal_type === "goal";
  return (
    <m.article
      lang={language}
      layout="position"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={cn(
        "relative flex flex-col gap-3 overflow-hidden rounded-2xl border bg-card p-4 pl-5 shadow-raised sm:p-5 sm:pl-6",
        goal && (side === "away" ? "border-away/50" : "border-home/50"),
      )}
    >
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1.5", side ? sideTone[side].bg : "bg-border")} />
      <header className="flex items-center gap-3">
        <span className="font-display text-[40px] leading-10 font-bold tnum">{item.minute_label}</span>
        <Icon aria-hidden className={cn("size-6", item.signal_type === "card" ? "fill-amber-400 text-amber-400" : side ? sideTone[side].text : "text-muted-foreground")} />
        <span className="text-[13px] font-medium text-muted-foreground">{signalLabels[item.signal_type] ?? item.signal_type}</span>
      </header>
      <h3 className="text-xl leading-7 font-semibold">{item.headline}</h3>
      {item.body && <p className="line-clamp-2 max-w-prose text-[15px] text-muted-foreground">{item.body}</p>}
      {item.key_numbers.length > 0 && (
        <dl className="flex flex-wrap gap-x-5 gap-y-1">
          {item.key_numbers.slice(0, 3).map((n) => (
            <div key={n.label} className="flex items-baseline gap-1.5">
              <dt className="text-[13px] text-muted-foreground">{n.label}</dt>
              <dd className="font-display text-lg font-semibold tnum">
                {n.value}
                {n.unit && <span className="ml-0.5 text-xs font-normal">{n.unit}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="-mx-2 -mb-2 flex flex-wrap items-center gap-1">
        <Button variant="ghost" className="h-11 gap-1.5 px-3" onClick={() => void share()}>
          <Share2 aria-hidden />
          {copy.feed.share}
        </Button>
        {replayable && (
          <Button variant="ghost" className="h-11 gap-1.5 px-3" onClick={replay}>
            <Video aria-hidden />
            {copy.feed.replayOnPitch}
          </Button>
        )}
        <Button variant="ghost" className="h-11 gap-1.5 px-3 text-muted-foreground" onClick={() => setDetails(true)}>
          <Info aria-hidden />
          {copy.feed.details}
        </Button>
        <span className="ml-auto flex items-center gap-3 pr-2">
          <Ticks item={item} />
        </span>
      </div>
      <DetailsSheet item={item} open={details} onOpenChange={setDetails} />
    </m.article>
  );
}

function MomentRow({ item, language }: { item: FeedItem; language: Language }) {
  const meta = useLive((s) => s.meta);
  const side = sideOf(meta, item.team_id);
  const [details, setDetails] = useState(false);
  return (
    <m.article lang={language} layout="position" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="border-b last:border-0">
      <div
        role="button"
        tabIndex={0}
        aria-label={`${item.minute_label} ${item.headline}. ${copy.feed.details}`}
        onClick={() => setDetails(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setDetails(true);
          }
        }}
        className="group flex cursor-pointer items-start gap-3 rounded-lg py-3 outline-none hover:bg-raised/60 focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="w-12 shrink-0 pt-px font-display text-lg leading-6 font-semibold text-muted-foreground tnum">{item.minute_label}</span>
        <span aria-hidden className={cn("mt-2 size-2 shrink-0 rounded-full", side ? sideTone[side].bg : "bg-subtle")} />
        <div className="min-w-0 flex-1">
          <p className="text-[15px] leading-6 font-medium">{item.headline}</p>
          {item.body && <p className="line-clamp-1 text-[14px] text-muted-foreground">{item.body}</p>}
        </div>
        <span aria-hidden className="mt-0.5 flex shrink-0 items-center gap-1 text-[13px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          {copy.feed.details}
          <ChevronRight className="size-4" />
        </span>
      </div>
      <DetailsSheet item={item} open={details} onOpenChange={setDetails} />
    </m.article>
  );
}

export function InsightCard({ item: raw, language }: { item: FeedItem; language: Language }) {
  const analyst = useProfile((s) => s.mode === "analyst");
  // Raw momentum numbers are for Analyst mode; everyone else gets the words.
  const item = raw.signal_type === "momentum_swing" && !analyst ? { ...raw, body: "", key_numbers: [] } : raw;
  return BIG_MOMENTS.has(item.signal_type) ? <MomentCard item={item} language={language} /> : <MomentRow item={item} language={language} />;
}
