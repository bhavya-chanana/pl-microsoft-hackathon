"use client";

import { Pause, Play, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { useFramesWindow, useMatch, useMoments, useNext10Window } from "./api";
import type { Moment, Next10Match, Outcome, Prediction } from "./contract";
import { n10 } from "./copy";
import { entitiesOf, minuteLabel, nearestFrame, nearestPrediction, pct } from "./lib";
import { hasAlternatives, pathsFrom } from "./paths";
import { Next10Pitch, type PathLine } from "./pitch";
import { ErrorState, LayerChips, PageFrame, Panel, PitchLegend, ProbBar } from "./ui";

const T = n10.moments;
const BEFORE = 10;
const AFTER = 5;
const XG_MIN = 0.15;

/** Moments from the score's outcomes, for matches whose `moments` container is still empty: goals and shots with xG >= 0.15. */
function deriveMoments(matchId: string, match: Next10Match, outcomes: Outcome[] | null): Moment[] {
  return (outcomes ?? [])
    .filter((o) => o.t_s != null && (o.type === "goal" || (o.type === "shot" && (o.xg ?? 0) >= XG_MIN)))
    .map((o, i) => {
      const conceder = o.team_side === "home" ? "away" : "home";
      const goal = o.type === "goal";
      return {
        id: `derived-${i}`,
        match_id: matchId,
        kind: goal ? "goal_conceded" : "chance",
        team_side: goal ? conceder : o.team_side,
        team: match[goal ? conceder : o.team_side].name,
        minute: o.minute,
        t_s: o.t_s as number,
        xg: o.xg ?? null,
        label: goal ? T.goal_conceded : T.chance,
      } satisfies Moment;
    });
}

/** Keep the first `n` path points (the model's forecast, revealed as the clip plays). */
function revealed(lines: PathLine[], seconds: number, dt: number): PathLine[] {
  const count = Math.floor(seconds / dt) + 1;
  if (count <= 1) return [];
  return lines.map((l) => ({ ...l, points: l.points.slice(0, count) }));
}

function MomentList({ moments, selected, onSelect }: { moments: Moment[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <Panel title={T.list}>
      <ul role="radiogroup" aria-label={T.list} className="flex flex-col gap-2">
        {moments.map((m) => {
          const on = m.id === selected;
          return (
            <li key={m.id}>
              <button
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => onSelect(m.id)}
                className={cn("flex min-h-14 w-full flex-col items-start gap-0.5 rounded-xl border px-3 py-2 text-left transition-colors", on ? "border-primary/60 bg-primary/10" : "bg-card hover:bg-raised")}
              >
                <span className="flex w-full items-center justify-between gap-2 text-sm font-medium">
                  <span>{m.label ?? (m.kind === "goal_conceded" ? T.goal_conceded : T.chance)}</span>
                  <span className="font-display text-base tnum">{Math.floor(m.minute)}&prime;</span>
                </span>
                <span className="text-[13px] text-subtle">
                  {[m.team, m.player, m.xg != null ? T.xg(m.xg) : null].filter(Boolean).join(" · ")}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function Playout({ matchId, moment }: { matchId: string; moment: Moment }) {
  const T0 = moment.t_s;
  const [rel, setRel] = useState(-BEFORE);
  const [playing, setPlaying] = useState(false);
  const [layers, setLayers] = useState({ expected: true, ghost: true, alt: false });

  const framesQ = useFramesWindow(matchId, T0 - BEFORE - 1, T0 + AFTER + 1);
  const forecastQ = useNext10Window(matchId, T0 - BEFORE - 15, T0 - BEFORE + 3);
  const frames = framesQ.data;
  const doc: Prediction | null = nearestPrediction(forecastQ.data ?? [], T0 - BEFORE, 10);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setRel((r) => {
        if (r >= AFTER) {
          setPlaying(false);
          return AFTER;
        }
        return Math.min(AFTER, r + 0.1);
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing]);

  const frame = nearestFrame(frames ?? [], T0 + rel, 2);
  const start = nearestFrame(frames ?? [], T0 - BEFORE, 2);
  const entities = entitiesOf(frame);
  const dt = doc?.trajectories?.dt_s ?? 1;
  const elapsed = rel + BEFORE;

  const ghost = useMemo(() => {
    if (!frames) return [];
    const ballOf = (f: (typeof frames)[number]) => f.entities.find((e) => e.team === "ball");
    return frames
      .filter((f) => f.t_s >= T0 - BEFORE && f.t_s <= T0 + rel)
      .flatMap((f) => {
        const b = ballOf(f);
        return b ? [[b.x, b.y] as [number, number]] : [];
      });
  }, [frames, T0, rel]);

  const best = revealed(pathsFrom(doc, entitiesOf(start), { modes: "best", prefix: "x" }), elapsed, dt);
  const alts = revealed(pathsFrom(doc, entitiesOf(start), { modes: "alternatives", prefix: "a" }), elapsed, dt);
  const paths = [...(layers.alt ? alts : []), ...(layers.expected ? best : [])];

  const noFrames = !framesQ.isPending && (frames?.length ?? 0) === 0;
  const chips = [
    { id: "expected", label: T.layer.expected.label, on: layers.expected, reason: !doc ? T.layerEmpty.expected : T.layer.expected.help },
    { id: "ghost", label: T.layer.ghost.label, on: layers.ghost, reason: noFrames ? T.layerEmpty.ghost : T.layer.ghost.help },
    { id: "alt", label: T.layer.alt.label, on: layers.alt, reason: !hasAlternatives(doc) ? T.layerEmpty.alt : T.layer.alt.help },
    { id: "whatif", label: T.layer.whatif.label, on: false, disabled: true, reason: T.layerEmpty.whatif },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Panel title={`${moment.label ?? ""} ${Math.floor(moment.minute)}′`.trim()} help={[moment.team, moment.player, moment.xg != null ? T.xg(moment.xg) : null].filter(Boolean).join(" · ")}>
        <div className="relative overflow-hidden rounded-xl">
          {framesQ.isPending ? (
            <Skeleton className="aspect-[111/74] w-full" />
          ) : (
            <Next10Pitch
              entities={entities}
              zone={layers.expected ? (doc?.ball_zone10 ?? null) : null}
              paths={paths}
              ghost={layers.ghost ? ghost : undefined}
              label={`Play-out at ${T.clockLabel(Math.round(rel))} relative to the moment`}
              className={cn(noFrames && "opacity-60")}
            />
          )}
          {noFrames && <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl bg-graphic px-4 py-3 text-center text-[15px] text-graphic-foreground">{T.noFrames}</p>}
        </div>
        <PitchLegend text={n10.match.legend} />

        <div className="flex flex-col gap-1 border-t pt-3">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="n10-scrub" className="text-sm text-muted-foreground">{T.scrub}</label>
            <span className="font-display text-lg font-semibold tnum" aria-live="off">{T.clockLabel(Math.round(rel))}</span>
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              className="size-11 shrink-0 rounded-full p-0"
              aria-label={playing ? T.pauseAria : T.playAria}
              onClick={() => {
                if (!playing && rel >= AFTER) setRel(-BEFORE);
                setPlaying((p) => !p);
              }}
            >
              {playing ? <Pause aria-hidden /> : <Play aria-hidden />}
            </Button>
            <Slider
              id="n10-scrub"
              aria-label={T.scrub}
              min={-BEFORE}
              max={AFTER}
              step={0.5}
              value={[rel]}
              onValueChange={(v) => {
                setPlaying(false);
                setRel(Array.isArray(v) ? v[0] : v);
              }}
              className="py-3"
            />
            <Button variant="ghost" className="size-11 shrink-0 rounded-full p-0" aria-label={T.restart} onClick={() => { setPlaying(false); setRel(-BEFORE); }}>
              <RotateCcw aria-hidden />
            </Button>
          </div>
          <div className="flex justify-between pl-14 pr-14 text-[13px] text-subtle tnum" aria-hidden>
            {[-10, -5, 0, 5].map((s) => (
              <span key={s}>{T.clockLabel(s)}</span>
            ))}
          </div>
        </div>
      </Panel>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title={T.layers}>
          <LayerChips label={T.layers} chips={chips} onToggle={(id) => setLayers((l) => ({ ...l, [id]: !l[id as keyof typeof l] }))} />
        </Panel>
        <Panel title={T.expectationHeading} help={doc ? `${minuteLabel(Math.max(0, T0 - BEFORE))} · ${doc.model_version ?? ""}` : undefined}>
          {doc ? (
            <div className="flex flex-col gap-3">
              <ProbBar label={T.chanceOfShot} value={doc.probs.shot10} std={doc.prob_std?.shot10} />
              <ProbBar label={T.chanceOfGoal} value={doc.probs.goal10} std={doc.prob_std?.goal10} />
              <p className="text-[13px] text-subtle">{moment.xg != null ? `${T.xg(moment.xg)} · ${pct(moment.xg)}` : null}</p>
            </div>
          ) : (
            <p className="text-[15px] text-muted-foreground">{T.layerEmpty.expected}</p>
          )}
        </Panel>
      </div>
    </div>
  );
}

/** Play it out: the moments of a match, each rewound to ten seconds before and played to five seconds after. */
export function MomentsPlayout({ matchId }: { matchId: string }) {
  const matchQ = useMatch(matchId);
  const momentsQ = useMoments(matchId);
  const match = matchQ.data?.match ?? null;
  const moments = useMemo(() => {
    if (momentsQ.data && momentsQ.data.length) return momentsQ.data;
    return match ? deriveMoments(matchId, match, matchQ.data?.score?.outcomes ?? null) : [];
  }, [momentsQ.data, match, matchId, matchQ.data?.score?.outcomes]);
  const [picked, setPicked] = useState<string | null>(null);
  const selected = moments.find((m) => m.id === picked) ?? moments[0] ?? null;
  const back = { href: routes.next10Match(matchId), label: T.back };
  const title = match ? `${T.heading}: ${match.home.name} v ${match.away.name}` : T.heading;

  if (matchQ.isPending || momentsQ.isPending) {
    return (
      <PageFrame title={T.heading} back={back}>
        <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <Skeleton className="h-64 rounded-2xl" />
          <Skeleton className="h-96 rounded-2xl" />
        </div>
      </PageFrame>
    );
  }
  if (matchQ.isError || momentsQ.isError) {
    return (
      <PageFrame title={T.heading} back={back}>
        <ErrorState title={n10.match.notFound} onRetry={() => { void matchQ.refetch(); void momentsQ.refetch(); }} />
      </PageFrame>
    );
  }
  return (
    <PageFrame title={title} intro={T.intro} back={back}>
      {selected ? (
        <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)] lg:items-start">
          <MomentList moments={moments} selected={selected.id} onSelect={setPicked} />
          <Playout key={selected.id} matchId={matchId} moment={selected} />
        </div>
      ) : (
        <EmptyState title={T.emptyTitle}>{T.emptyBody}</EmptyState>
      )}
    </PageFrame>
  );
}
