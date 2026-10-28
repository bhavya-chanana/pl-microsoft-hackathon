"use client";

import { Radio, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { useFramesWindow, useLatest, useMatch, useNext10Window, useShadowPredictions } from "./api";
import { type Prediction, type Side, SHADOW_HEADS, phaseOf } from "./contract";
import { n10 } from "./copy";
import { entitiesOf, minuteLabel, nearestFrame, nearestPrediction, pct, possessionSide } from "./lib";
import { pathsFrom } from "./paths";
import { Next10Pitch, type ReceiverMark } from "./pitch";
import { TimelineChart } from "./timeline-chart";
import { useLiveFeed } from "./use-live-feed";
import { ErrorState, LayerChips, PageFrame, Panel, PitchLegend, ProbBar } from "./ui";

const T = n10.match;

function ConnectionChip({ live, status }: { live: boolean; status: "connecting" | "live" | "polling" }) {
  const text = !live ? T.conn.stored : T.conn[status];
  return (
    <span className={cn("inline-flex items-center gap-2 rounded-full bg-raised px-3 py-1.5 text-[13px] ring-1 ring-border", live && status === "live" ? "text-foreground" : "text-muted-foreground")}>
      {live ? <Radio aria-hidden className={cn("size-3.5", status === "live" ? "text-live" : "text-subtle")} /> : null}
      {text}
    </span>
  );
}

function Scoreline({ home, away, score, phase }: { home: string; away: string; score: { home?: number | null; away?: number | null } | null | undefined; phase: string }) {
  const has = score?.home != null && score?.away != null;
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-2xl border bg-card p-4 shadow-raised md:gap-6 md:p-5">
      <p className="min-w-0 text-right font-display text-display font-bold tracking-tight text-balance uppercase">
        <span aria-hidden className="mr-2 inline-block h-5 w-1.5 rounded-full bg-home align-middle" />
        {home}
      </p>
      <div className="flex flex-col items-center gap-1">
        <span className="font-display text-score font-bold tnum" aria-label={has ? `${score.home} ${score.away}` : undefined}>
          {has ? `${score.home}-${score.away}` : "–"}
        </span>
        <span className={cn("flex items-center gap-1.5 text-[13px] font-medium", phase === "live" ? "text-live" : "text-subtle")}>
          {phase === "live" && <span aria-hidden className="size-2 rounded-full bg-live motion-safe:animate-pulse" />}
          {phase === "live" ? T.liveLabel : phase === "finished" ? T.ftLabel : T.notStarted}
        </span>
      </div>
      <p className="min-w-0 font-display text-display font-bold tracking-tight text-balance uppercase">
        {away}
        <span aria-hidden className="ml-2 inline-block h-5 w-1.5 rounded-full bg-away align-middle" />
      </p>
    </div>
  );
}

function ForecastPanel({ doc, names, side }: { doc: Prediction | null; names: Record<Side, string>; side: Side | null }) {
  if (!doc) {
    return (
      <Panel title={T.statsTitle}>
        <p className="text-[15px] text-muted-foreground">{T.noNext10}</p>
      </Panel>
    );
  }
  const p = doc.probs;
  const sd = doc.prob_std ?? {};
  const team = side ? names[side] : null;
  const tone = side === "away" ? "bg-away" : "bg-home";
  return (
    <Panel title={T.statsTitle} help={team ? T.inPossession(team) : T.possessionUnknown}>
      <div className="flex flex-col gap-3">
        <ProbBar label={T.stats.shot10} value={p.shot10} std={sd.shot10} tone={tone} />
        <ProbBar label={T.stats.turn5} value={p.turn5} std={sd.turn5} tone={tone} />
        <ProbBar label={T.stats.poss10} value={p.poss10} std={sd.poss10} tone={tone} />
        <ProbBar label={T.stats.goal10} value={p.goal10} std={sd.goal10} tone={tone} />
      </div>
    </Panel>
  );
}

function ReceiversPanel({ doc }: { doc: Prediction | null }) {
  const receivers = doc?.receivers ?? [];
  if (receivers.length === 0) return null;
  return (
    <Panel title={T.receiversTitle}>
      <ol className="flex flex-col divide-y">
        {receivers.map((r, i) => (
          <li key={r.player_id} className="flex min-h-11 items-center justify-between gap-3 text-[15px]">
            <span className="flex items-center gap-2.5">
              <span aria-hidden className="grid size-6 place-items-center rounded-full bg-raised font-display text-sm font-bold ring-1 ring-border">{i + 1}</span>
              {r.player_id}
            </span>
            <span className="font-display text-lg font-semibold tnum">
              {pct(r.p)}
              {r.std ? <span className="ml-1 text-xs font-normal text-subtle">±{Math.round(r.std * 100)}</span> : null}
            </span>
          </li>
        ))}
        {doc?.receiver_lost != null && (
          <li className="flex min-h-11 items-center justify-between gap-3 text-[15px] text-muted-foreground">
            <span>{T.receiverLost}</span>
            <span className="font-display text-lg font-semibold tnum">{pct(doc.receiver_lost)}</span>
          </li>
        )}
      </ol>
    </Panel>
  );
}

export function MatchCompanion({ matchId }: { matchId: string }) {
  const matchQ = useMatch(matchId);
  const match = matchQ.data?.match ?? null;
  const phase = match ? phaseOf(match) : "upcoming";
  const live = phase === "live";
  const shadowQ = useShadowPredictions(matchId, live);
  const feed = useLiveFeed(matchId, live);
  const latest = useLatest(matchId, feed.pollMs, live);

  const [head, setHead] = useState<string>("shot_5m");
  const [layers, setLayers] = useState({ zone: true, receivers: true, paths: true });
  const [pickedAt, setPickedAt] = useState<number | null>(null);

  const shadow = shadowQ.data;
  const lastMinute = useMemo(() => Math.max(0, ...(shadow ?? []).map((d) => d.minute)), [shadow]);
  const outcomes = matchQ.data?.score?.outcomes ?? null;

  // The pitch shows the live edge, or the second the slider picked. A stored match opens at its first chance.
  const liveEdgeS = latest.frame ? (latest.frame.match_clock_s ?? latest.frame.t_s) : lastMinute * 60;
  const storedDefault = outcomes?.find((o) => o.t_s != null)?.t_s ?? Math.min(lastMinute * 60, 20 * 60);
  const following = live && pickedAt === null;
  const at = following ? null : (pickedAt ?? storedDefault);
  const bucket = at === null ? 0 : Math.round(at / 5) * 5;
  const windowEnabled = at !== null && Boolean(match);
  const framesQ = useFramesWindow(matchId, bucket - 4, bucket + 4, false, windowEnabled);
  const n10Q = useNext10Window(matchId, bucket - 15, bucket + 5, false, windowEnabled);

  const frame = following ? (feed.frame ?? latest.frame) : nearestFrame(framesQ.data ?? [], at ?? 0, 4);
  const doc = following ? (feed.prediction ?? latest.prediction) : nearestPrediction(n10Q.data ?? [], at ?? 0, 12);
  const entities = entitiesOf(frame);
  const side = possessionSide(doc, match);

  const marks: ReceiverMark[] = layers.receivers ? (doc?.receivers ?? []).map((r, i) => ({ entityId: r.player_id, rank: i + 1, label: `${i + 1} · ${pct(r.p)}` })) : [];
  const paths = layers.paths ? pathsFrom(doc, entities, { modes: "all" }) : [];
  const zone = layers.zone ? (doc?.ball_zone10 ?? null) : null;

  if (matchQ.isPending) {
    return (
      <PageFrame title={<Skeleton className="h-8 w-72" />} back={{ href: routes.next10, label: n10.nav.back }}>
        <Skeleton className="h-24 rounded-2xl" />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <Skeleton className="aspect-[111/74] rounded-2xl" />
          <Skeleton className="h-80 rounded-2xl" />
        </div>
      </PageFrame>
    );
  }
  if (matchQ.isError || !match) {
    return (
      <PageFrame title={n10.product} back={{ href: routes.next10, label: n10.nav.back }}>
        <ErrorState title={T.notFound} onRetry={() => void matchQ.refetch()} />
      </PageFrame>
    );
  }

  const names = { home: match.home.name, away: match.away.name };
  const maxS = Math.max(60, live ? liveEdgeS : lastMinute * 60);
  const sliderValue = following ? liveEdgeS : (at ?? 0);
  const noFrame = !frame;
  const loadingWindow = windowEnabled && framesQ.isFetching && !frame;
  const layerChips = [
    { id: "zone", label: T.layers.zone, on: layers.zone, reason: !doc?.ball_zone10 ? T.layerEmpty.zone : undefined },
    { id: "receivers", label: T.layers.receivers, on: layers.receivers, reason: !(doc?.receivers?.length) ? T.layerEmpty.receivers : undefined },
    { id: "paths", label: T.layers.paths, on: layers.paths, reason: !doc?.trajectories ? T.layerEmpty.paths : undefined },
  ];

  return (
    <PageFrame
      title={`${names.home} v ${names.away}`}
      intro={match.competition ?? undefined}
      back={{ href: routes.next10, label: n10.nav.back }}
      actions={
        <>
          <Link href={routes.next10Moments(matchId)} className="inline-flex h-11 items-center gap-2 rounded-full bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85">
            <Sparkles aria-hidden className="size-4" />
            {T.openMoments}
          </Link>
          <Link href={routes.next10Verify} className="inline-flex h-11 items-center gap-2 rounded-full border bg-card px-4 text-sm font-medium transition-colors hover:bg-raised">
            <ShieldCheck aria-hidden className="size-4" />
            {T.openVerify}
          </Link>
        </>
      }
    >
      <Scoreline home={names.home} away={names.away} score={match.score} phase={phase} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <Panel
          title={T.pitch}
          help={T.pitchHelp}
          actions={<ConnectionChip live={live} status={feed.status} />}
        >
          <div className="relative overflow-hidden rounded-xl">
            <Next10Pitch
              entities={entities}
              zone={zone}
              paths={paths}
              receivers={marks}
              label={noFrame ? T.noFrames : `${entities.filter((e) => e.team === "home" || e.team === "away").length} players on the pitch at ${minuteLabel(frame?.t_s ?? 0)}`}
              className={cn(noFrame && "opacity-60")}
            />
            {noFrame && (
              <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl bg-graphic px-4 py-3 text-center text-[15px] text-graphic-foreground">
                {loadingWindow || (live && latest.loading) ? T.noFramesLive : live && pickedAt === null ? T.noFramesLive : T.noFrames}
              </p>
            )}
          </div>
          <PitchLegend text={T.legend} />
          <LayerChips label="Pitch layers" chips={layerChips} onToggle={(id) => setLayers((l) => ({ ...l, [id]: !l[id as keyof typeof l] }))} />
          <div className="flex flex-col gap-2 border-t pt-3">
            <div className="flex items-center justify-between gap-3 text-sm">
              <label htmlFor="n10-clock" className="text-muted-foreground">{T.clock}</label>
              <span className="flex items-center gap-3">
                <span className="font-display text-lg font-semibold tnum">{minuteLabel(sliderValue)}</span>
                {live && (
                  <Button variant={following ? "secondary" : "default"} className="h-11 px-4" disabled={following} onClick={() => setPickedAt(null)}>
                    {T.followLive}
                  </Button>
                )}
              </span>
            </div>
            <Slider
              id="n10-clock"
              aria-label={T.clock}
              min={0}
              max={maxS}
              step={5}
              value={[Math.min(sliderValue, maxS)]}
              onValueChange={(v) => setPickedAt(Array.isArray(v) ? v[0] : v)}
              className="py-3"
            />
            {live && !following && <p className="text-[13px] text-subtle">{T.liveBehind(Math.max(0, Math.round(liveEdgeS - (at ?? 0))))}</p>}
          </div>
        </Panel>

        <div className="flex flex-col gap-4">
          <ForecastPanel doc={doc} names={names} side={side} />
          <ReceiversPanel doc={doc} />
        </div>
      </div>

      <Panel
        title={T.timeline}
        help={T.timelineHelp}
        actions={<Segmented label={T.timeline} size="sm" value={head} onChange={setHead} options={SHADOW_HEADS.map((h) => ({ value: h, label: T.heads[h] }))} className="w-full min-w-0 sm:w-auto" />}
      >
        <TimelineChart
          docs={shadow ?? []}
          head={head}
          outcomes={outcomes}
          names={names}
          nowMinute={live ? liveEdgeS / 60 : null}
          cursorMinute={at !== null ? at / 60 : null}
        />
        {shadowQ.isError && <p className="text-[13px] text-subtle">{n10.empty.error}</p>}
      </Panel>
    </PageFrame>
  );
}
