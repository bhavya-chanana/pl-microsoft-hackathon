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

import { useFotmob, useFramesWindow, useLatest, useMatch, useMoments, useNext10Window, useShadowPredictions, useStats } from "./api";
import { type Entity, type Prediction, type Side, SHADOW_HEADS, phaseOf, predictionT } from "./contract";
import { n10 } from "./copy";
import { entitiesOf, minuteLabel, nearestByTime, nearestFrame, nearestPrediction, pct, pickFrame, possessionSide } from "./lib";
import { EventsPanel, FormCard, H2HCard, InfoCard, LineupsPanel, ShootoutPanel, ShotMapPanel, StatsPanel, ZonesCard } from "./fotmob-panels";
import { MomentumChart, axisMax, lastMinuteOf, marksOf } from "./fotmob-charts";
import { regularShots, shootoutOf } from "./fotmob-contract";
import { pathsFrom } from "./paths";
import { Next10Pitch, type ReceiverMark } from "./pitch";
import { TimelineChart } from "./timeline-chart";
import { useLiveFeed } from "./use-live-feed";
import { ErrorState, FrameNote, LayerChips, PageFrame, Panel, PitchLegend, ProbBar, StatsChip } from "./ui";

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

/**
 * The score line. `score` is the text in the middle (null shows "v"), `sub` the small line under it, `footnote` a
 * full-width line under the teams (a penalty shootout, or what a video clip covers).
 */
function Scoreline({ home, away, score, sub, live, footnote }: { home: string; away: string; score: string | null; sub: string | null; live: boolean; footnote?: string | null }) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-2xl border bg-card p-4 shadow-raised md:gap-6 md:p-5">
      <p className="min-w-0 text-right font-display text-display font-bold tracking-tight text-balance uppercase">
        <span aria-hidden className="mr-2 inline-block h-5 w-1.5 rounded-full bg-home align-middle" />
        {home}
      </p>
      <div className="flex flex-col items-center gap-1">
        {score ? (
          <span className="font-display text-score font-bold tnum" aria-label={score.replace("–", " ")}>
            {score}
          </span>
        ) : (
          <span className="font-display text-2xl font-semibold text-subtle">v</span>
        )}
        {sub && (
          <span className={cn("flex items-center gap-1.5 text-[13px] font-medium tnum", live ? "text-live" : "text-subtle")}>
            {live && <span aria-hidden className="size-2 rounded-full bg-live motion-safe:animate-pulse" />}
            {sub}
          </span>
        )}
      </div>
      <p className="min-w-0 font-display text-display font-bold tracking-tight text-balance uppercase">
        {away}
        <span aria-hidden className="ml-2 inline-block h-5 w-1.5 rounded-full bg-away align-middle" />
      </p>
      {footnote && <p className="col-span-3 border-t pt-3 text-center text-[13px] text-muted-foreground tnum">{footnote}</p>}
    </div>
  );
}

function ForecastPanel({ doc, names, side, eventOnly = false }: { doc: Prediction | null; names: Record<Side, string>; side: Side | null; eventOnly?: boolean }) {
  if (!doc) {
    return (
      <Panel title={T.statsTitle}>
        <p className="text-[15px] text-muted-foreground">{eventOnly ? T.noNext10Event : T.noNext10}</p>
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

/** A tracking id means nothing to a viewer: say which team the player is on, from the frame being drawn. */
function teamNameOf(id: string, entities: Entity[], names: Record<Side, string>): string | null {
  const team = entities.find((e) => e.id === id)?.team;
  return team === "home" || team === "away" ? names[team] : null;
}

function ReceiversPanel({ doc, entities, names }: { doc: Prediction | null; entities: Entity[]; names: Record<Side, string> }) {
  const receivers = doc?.receivers ?? [];
  if (receivers.length === 0) return null;
  return (
    <Panel title={T.receiversTitle}>
      <ol className="flex flex-col divide-y">
        {receivers.map((r, i) => (
          <li key={r.player_id} className="flex min-h-11 items-center justify-between gap-3 text-[15px]">
            <span className="flex items-center gap-2.5">
              <span aria-hidden className="grid size-6 place-items-center rounded-full bg-raised font-display text-sm font-bold ring-1 ring-border">{i + 1}</span>
              {T.receiverRow(teamNameOf(r.player_id, entities, names))}
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

/** An event-only match has no player tracking: one line says so instead of an empty pitch. */
function NoTrackingNote() {
  return <p role="status" className="rounded-2xl border bg-card px-4 py-3 text-[15px] text-muted-foreground shadow-raised">{T.noFramesEvent}</p>;
}

export function MatchCompanion({ matchId }: { matchId: string }) {
  const matchQ = useMatch(matchId);
  const match = matchQ.data?.match ?? null;
  const phase = match ? phaseOf(match) : "upcoming";
  const live = phase === "live";
  const shadowQ = useShadowPredictions(matchId, live);
  const feed = useLiveFeed(matchId, live);
  // A live match follows the newest frame; a stored one asks once for its last frame, to size the clock.
  const latest = useLatest(matchId, live ? feed.pollMs : false, Boolean(match), live);
  const statsQ = useStats(matchId, live);
  const stats = feed.stats ?? statsQ.data ?? null;

  const [head, setHead] = useState<string>("shot_5m");
  const [layers, setLayers] = useState({ zone: true, receivers: true, paths: true });
  const [pickedAt, setPickedAt] = useState<number | null>(null);

  const shadow = shadowQ.data;
  const lastMinute = useMemo(() => Math.max(0, ...(shadow ?? []).map((d) => d.minute)), [shadow]);
  const rawOutcomes = matchQ.data?.score?.outcomes ?? null;

  // The pitch shows the live edge, or the second the slider picked. A stored match opens at its first chance.
  // Video matches count in video seconds (frame t_s), event matches in match-clock seconds (minute * 60).
  const lastFrameS = latest.frame?.t_s ?? 0;
  const liveEdgeS = latest.frame ? latest.frame.t_s : lastMinute * 60;
  const dataEndS = Math.max(lastMinute * 60, lastFrameS);
  const momentsQ = useMoments(matchId);
  const firstMoment = (momentsQ.data ?? []).find((m) => m.t_s != null)?.t_s ?? null;
  const storedDefault = rawOutcomes?.find((o) => o.t_s != null)?.t_s ?? firstMoment ?? (lastMinute > 0 ? Math.min(lastMinute * 60, 20 * 60) : Math.min(dataEndS, 10));
  const following = live && pickedAt === null;
  const at = following ? null : (pickedAt ?? storedDefault);
  const bucket = at === null ? 0 : Math.round(at / 5) * 5;
  const windowEnabled = at !== null && match?.source !== "fotmob" && Boolean(match);
  const framesQ = useFramesWindow(matchId, bucket - 4, bucket + 4, false, windowEnabled);
  const isVideo = match?.source === "video";
  // Sources: "video" and the dev mock have player tracking; "fotmob" matches have only the event feed.
  const eventOnly = match?.source === "fotmob";
  const tracked = Boolean(match) && !eventOnly;
  // The FotMob page also exists for a video clip of a FotMob match: its goals give the score on the match clock.
  const fotmobQ = useFotmob(matchId, live, eventOnly || (isVideo && match?.fotmob_id != null));
  const fm = fotmobQ.data ?? null;
  // Shootout kicks sit in the scored shots with invented minutes after 90: keep only what has a real shot to match.
  const outcomes = useMemo(() => {
    if (!rawOutcomes || !shootoutOf(fm) || regularShots(fm).length === 0) return rawOutcomes;
    const real = new Set(regularShots(fm).flatMap((k) => [0, 1, -1].map((d) => `${k.team}:${Math.floor(k.minute) + d}`)));
    return rawOutcomes.filter((o) => o.minute <= 90 || real.has(`${o.team_side}:${Math.floor(o.minute)}`));
  }, [rawOutcomes, fm]);
  const firstQ = useFramesWindow(matchId, 0, 2, false, isVideo);
  const n10Q = useNext10Window(matchId, bucket - 15, bucket + 5, false, windowEnabled);

  // Tracker ids churn every second or two, so a forecast only belongs on the frame it was made from: choose the
  // forecast first, then draw the frame at exactly its time. Without one close enough, draw the frame bare.
  const storedDoc = following ? null : nearestPrediction(n10Q.data ?? [], at ?? 0, 1);
  const exact = storedDoc ? nearestFrame(framesQ.data ?? [], predictionT(storedDoc), 0.3) : null;
  const picked = following ? null : exact ? { frame: exact, shifted: 0 } : pickFrame(framesQ.data ?? [], at ?? 0, 3);
  const frame = following ? (feed.frame ?? latest.frame) : (picked?.frame ?? null);
  const shifted = picked?.shifted ?? 0;
  const doc = following ? (feed.prediction ?? latest.prediction) : storedDoc;
  const aligned = Boolean(doc && frame && Math.abs(predictionT(doc) - frame.t_s) <= 1);
  const overlay = aligned ? doc : null;
  const entities = entitiesOf(frame);
  const side = possessionSide(doc, match);
  const noForecastHere = Boolean(frame) && !overlay && tracked && frame!.entities.length > 0;

  const marks: ReceiverMark[] = layers.receivers ? (overlay?.receivers ?? []).map((r, i) => ({ entityId: r.player_id, rank: i + 1, label: `${i + 1} · ${pct(r.p)}` })) : [];
  const paths = layers.paths ? pathsFrom(overlay, entities, { modes: "best" }) : [];
  const zone = layers.zone ? (overlay?.ball_zone10 ?? null) : null;

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

  // On video, forecasts exist only for seconds with a mapped main camera view. A step of the slider moves on to the
  // next such second in the direction it was going (so it can cross a gap); a drag lands on the nearest one.
  const snapToForecast = (t: number) => {
    if (!tracked) return t;
    const times = (n10Q.data ?? []).map(predictionT);
    if (nearestByTime(times, t, (x) => x, 0.6) !== null) return t;
    const delta = t - sliderValue;
    if (Math.abs(delta) <= 2) {
      const ahead = times.filter((x) => (delta > 0 ? x >= t : x <= t));
      return nearestByTime(ahead, t, (x) => x, 12) ?? t;
    }
    return nearestByTime(times, t, (x) => x, 3) ?? t;
  };
  const names = { home: match.home.name, away: match.away.name };
  const hasBlocks = Boolean(fm && (regularShots(fm).length > 0 || (fm.events ?? []).length > 0));
  const finished = phase === "finished";
  const sharedMax = eventOnly ? axisMax(Math.max(lastMinuteOf(fm), lastMinute), finished) : 0;
  const chartMarks = marksOf(fm);
  const hasMomentum = (fm?.momentum ?? []).some((p) => p.value != null);
  const clockFrom = match.clock_from_s ?? (firstQ.data ?? []).find((f) => f.match_clock_s != null)?.match_clock_s ?? null;
  const clockTo = match.clock_to_s ?? latest.frame?.match_clock_s ?? null;
  const clockRange = clockFrom != null && clockTo != null && clockTo > clockFrom ? `${minuteLabel(clockFrom)}–${minuteLabel(clockTo)}` : null;
  const maxS = Math.max(30, live ? Math.max(liveEdgeS, dataEndS) : dataEndS);
  const sliderValue = following ? liveEdgeS : (at ?? 0);
  const noFrame = !frame;
  const noPlayers = Boolean(frame) && frame!.entities.length === 0;
  const loadingWindow = windowEnabled && framesQ.isFetching && !frame;
  const layerChips = [
    { id: "zone", label: T.layers.zone, on: layers.zone, reason: !overlay?.ball_zone10 ? T.layerEmpty.zone : undefined },
    { id: "receivers", label: T.layers.receivers, on: layers.receivers, reason: !(overlay?.receivers?.length) ? T.layerEmpty.receivers : undefined },
    { id: "paths", label: T.layers.paths, on: layers.paths, reason: !overlay?.trajectories ? T.layerEmpty.paths : undefined },
  ];

  // Header: a stored match shows its score; a clip shows the score at the slider's moment on the match clock.
  const shootout = shootoutOf(fm);
  const shootoutSummary = shootout ? `${names[shootout.home > shootout.away ? "home" : "away"]} win ${Math.max(shootout.home, shootout.away)}–${Math.min(shootout.home, shootout.away)} on penalties` : null;
  const finalScore = match.score?.home != null && match.score?.away != null ? `${match.score.home}–${match.score.away}` : null;
  const clockNow = frame?.match_clock_s ?? (clockFrom != null ? clockFrom + sliderValue : null);
  const goalList = (fm?.events ?? []).filter((e) => e.type === "goal" && !e.shootout && e.team);
  let headScore: string | null = finalScore;
  let headSub: string | null = phase === "live" ? T.liveLabel : phase === "finished" ? T.ftLabel : T.notStarted;
  let headFoot: string | null = shootoutSummary;
  if (isVideo) {
    // A goal whose second was found in the footage (a pinned goal_conceded moment) flips the score exactly there;
    // any other goal counts from the middle of its FotMob minute, which is only good to about 30 s.
    const pinned = (momentsQ.data ?? []).filter((m) => m.kind === "goal_conceded" && m.placed === "observed");
    const scorerOf = (m: (typeof pinned)[number]): Side | null => m.scored_by ?? (m.team_side === "home" ? "away" : m.team_side === "away" ? "home" : null);
    const reached = (g: (typeof goalList)[number]) => {
      const pin = pinned.find((m) => m.minute === g.minute && scorerOf(m) === g.team);
      if (pin) return sliderValue >= pin.t_s;
      return clockNow != null && (g.minute + (g.added ?? 0) - 1) * 60 + 30 <= clockNow;
    };
    const running = fm ? (["home", "away"] as const).map((side) => goalList.filter((g) => g.team === side && reached(g)).length) : null;
    headScore = running ? `${running[0]}–${running[1]}` : null;
    headSub = phase === "live" ? T.liveLabel : finalScore ? T.finalLabel(finalScore) : null;
    headFoot = T.videoHeader(clockRange);
  }
  return (
    <PageFrame
      title={`${names.home} v ${names.away}`}
      intro={[match.competition, eventOnly ? T.eventFeedNote : null].filter(Boolean).join(" · ") || undefined}
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
      <Scoreline home={names.home} away={names.away} score={headScore} sub={headSub} live={phase === "live"} footnote={headFoot} />

      {eventOnly ? (
        <>
          {fm && <InfoCard doc={fm} />}
          {fotmobQ.isPending ? (
            <Skeleton className="h-96 w-full rounded-2xl" />
          ) : hasBlocks ? (
            <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
              {regularShots(fm).length > 0 ? <ShotMapPanel shots={regularShots(fm)} names={names} /> : <NoTrackingNote />}
              {(fm?.events ?? []).length > 0 && <EventsPanel events={fm!.events!} names={names} />}
            </div>
          ) : (
            <NoTrackingNote />
          )}
          {shootout && shootoutSummary && <ShootoutPanel kicks={shootout.kicks} names={names} summary={shootoutSummary} />}
        </>
      ) : (
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <Panel
          title={T.pitch}
          help={T.pitchHelp}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {live && <StatsChip stats={stats} />}
              <ConnectionChip live={live} status={feed.status} />
            </div>
          }
        >
          <div className="relative overflow-hidden rounded-xl">
            <Next10Pitch
              entities={frame?.calib_status === "none" ? [] : entities}
              zone={zone}
              paths={paths}
              receivers={marks}
              label={noFrame ? T.noFrames : `${entities.filter((e) => e.team === "home" || e.team === "away").length} players on the pitch at ${minuteLabel(frame?.t_s ?? 0)}`}
              className={cn(noFrame && "opacity-60")}
              lowConfidence={frame?.calib_status === "interpolated"}
            />
            {noFrame && (
              <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl bg-graphic px-4 py-3 text-center text-[15px] text-graphic-foreground">
                {eventOnly ? T.noFramesEvent : loadingWindow || (live && latest.loading) ? T.noFramesLive : live && pickedAt === null ? T.noFramesLive : T.noFrames}
              </p>
            )}
          </div>
          {noPlayers && <p role="status" className="rounded-lg bg-raised px-3 py-2 text-[13px] text-muted-foreground ring-1 ring-border">{T.noPlayers}</p>}
          {noForecastHere && <p role="status" className="text-[13px] text-subtle">{T.noForecastHere}</p>}
          {shifted > 0.3 && <p role="status" className="text-[13px] text-subtle">{T.shifted(shifted)}</p>}
          <FrameNote status={frame?.calib_status} />
          {tracked && <PitchLegend text={T.legend} zone={layers.zone && zone ? T.zoneKey : null} />}
          {tracked && <LayerChips label="Pitch layers" chips={layerChips} onToggle={(id) => setLayers((l) => ({ ...l, [id]: !l[id as keyof typeof l] }))} />}
          <div className="flex flex-col gap-2 border-t pt-3">
            <div className="flex items-center justify-between gap-3 text-sm">
              <label htmlFor="n10-clock" className="text-muted-foreground">{match.source === "video" ? T.videoClock : T.clock}</label>
              <span className="flex items-center gap-3">
                <span className="font-display text-lg font-semibold tnum">{minuteLabel(sliderValue)}</span>
                {match.source === "video" && frame?.match_clock_s != null && (
                  <span className="text-xs text-muted-foreground tnum">{`Match clock ${minuteLabel(frame.match_clock_s)}`}</span>
                )}
                {live && (
                  <Button variant={following ? "secondary" : "default"} className="h-11 px-4" disabled={following} onClick={() => setPickedAt(null)}>
                    {T.followLive}
                  </Button>
                )}
              </span>
            </div>
            <Slider
              id="n10-clock"
              thumbLabel={match.source === "video" ? T.videoClock : T.clock}
              getAriaValueText={(_, v) => minuteLabel(v)}
              min={0}
              max={maxS}
              step={tracked ? 1 : 5}
              value={[Math.min(sliderValue, maxS)]}
              onValueChange={(v) => setPickedAt(snapToForecast(Array.isArray(v) ? v[0] : v))}
              className="py-3"
            />
            {live && !following && <p className="text-[13px] text-subtle">{T.liveBehind(Math.max(0, Math.round(liveEdgeS - (at ?? 0))))}</p>}
          </div>
        </Panel>

        <div className="flex flex-col gap-4">
          <ForecastPanel doc={doc} names={names} side={side} eventOnly={eventOnly} />
          <ReceiversPanel doc={overlay} entities={entities} names={names} />
        </div>
      </div>
      )}

      <Panel
        title={T.timeline}
        help={T.timelineHelp}
        actions={<Segmented label={T.timeline} size="sm" value={head} onChange={setHead} options={SHADOW_HEADS.map((h) => ({ value: h, label: T.heads[h] }))} className="w-full min-w-0 sm:w-auto" />}
      >
        {eventOnly && fm && hasMomentum && (
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold">Momentum</h3>
            <MomentumChart doc={fm} marks={chartMarks} names={names} xMax={sharedMax} />
            <h3 className="mt-2 text-sm font-semibold">Chance of a shot or goal</h3>
          </div>
        )}
        {shadowQ.isPending ? (
          <Skeleton className="h-56 w-full rounded-lg" />
        ) : (shadow ?? []).length === 0 ? (
          <p className="rounded-xl border border-dashed px-4 py-6 text-center text-[15px] text-muted-foreground">{T.noShadow}</p>
        ) : (
          <TimelineChart
            xMax={sharedMax}
            marks={chartMarks}
            docs={shadow ?? []}
            head={head}
            outcomes={outcomes}
            names={names}
            nowMinute={live ? liveEdgeS / 60 : null}
            cursorMinute={at !== null ? at / 60 : null}
          />
        )}
        {shadowQ.isError && <p className="text-[13px] text-subtle">{n10.empty.error}</p>}
      </Panel>

      {eventOnly && fm && (
        <>
          {fm.stats && <StatsPanel stats={fm.stats} names={names} />}
          <LineupsPanel doc={fm} names={names} />
          <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
            <ZonesCard doc={fm} names={names} />
            <FormCard doc={fm} names={names} />
            <H2HCard doc={fm} names={names} />
          </div>
        </>
      )}
    </PageFrame>
  );
}
