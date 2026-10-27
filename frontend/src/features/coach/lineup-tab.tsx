"use client";

import { ArrowDown, ArrowUp, RectangleVertical } from "lucide-react";
import { useMemo, useState } from "react";

import { useShell } from "@/components/shell/shell-store";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { usePlayersStats, useRatings } from "@/features/live/queries";
import { useMediaQuery } from "@/hooks/use-media-query";
import type { MatchEvent, MatchMeta, PlayerRating, PlayerStats, Team } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { useCoach } from "./coach-store";
import { LensBadge, RatingBadge } from "./rating";

type Marks = { goals: number; assists: number; yellow: number; red: boolean; offAt: string | null; onAt: string | null };
const NONE: Marks = { goals: 0, assists: 0, yellow: 0, red: false, offAt: null, onAt: null };
type Lens = "rating" | "distance" | "speed" | "passes" | "shots";

function marksFrom(timeline: MatchEvent[]): Map<string, Marks> {
  const out = new Map<string, Marks>();
  const get = (id: string) => out.get(id) ?? { ...NONE };
  for (const e of timeline) {
    if (e.type === "goal" && e.player_id) out.set(e.player_id, { ...get(e.player_id), goals: get(e.player_id).goals + 1 });
    if (e.type === "goal" && e.related_player_id) out.set(e.related_player_id, { ...get(e.related_player_id), assists: get(e.related_player_id).assists + 1 });
    if (e.type === "card" && e.player_id) {
      const red = e.outcome.includes("red") || e.outcome === "second_yellow";
      const m = get(e.player_id);
      out.set(e.player_id, red ? { ...m, red: true } : { ...m, yellow: m.yellow + 1 });
    }
    if (e.type === "substitution") {
      if (e.player_id) out.set(e.player_id, { ...get(e.player_id), offAt: e.minute_label });
      if (e.related_player_id) out.set(e.related_player_id, { ...get(e.related_player_id), onAt: e.minute_label });
    }
  }
  return out;
}

type Spot = { id: string; along: number; across: number };

/** Place the eleven from the formation: `along` runs own goal (0) to the far end (1), `across` left (0) to right (1) from the team's view. */
function place(meta: MatchMeta, team: Team): Spot[] {
  const lines = team.formation.split("-").map(Number).filter((n) => n > 0);
  const xi = team.starting_xi;
  const side = (id: string) => {
    const role = meta.players[id]?.role ?? "";
    return role.startsWith("L") ? 0 : role.startsWith("R") ? 2 : 1;
  };
  const out: Spot[] = [];
  if (xi[0]) out.push({ id: xi[0], along: 0, across: 0.5 });
  let i = 1;
  lines.forEach((count, li) => {
    const row = xi.slice(i, i + count);
    i += count;
    const sorted = row.map((id, k) => ({ id, k })).sort((a, b) => side(a.id) - side(b.id) || a.k - b.k);
    sorted.forEach((p, k) => out.push({ id: p.id, along: (li + 1) / lines.length, across: (k + 1) / (count + 1) }));
  });
  // Anyone the formation did not cover is spaced along the far end so nothing overlaps.
  xi.slice(i).forEach((id, k, rest) => out.push({ id, along: 1, across: (k + 1) / (rest.length + 1) }));
  return out;
}

type Layout = "vertical" | "home" | "away";
function toXY(s: Spot, layout: Layout): { x: number; y: number } {
  if (layout === "vertical") return { x: 12 + s.across * 76, y: 91 - s.along * 77 };
  const x = 5 + s.along * 37;
  // Home attacks right (its left is the top of the screen); away attacks left (its left is the bottom).
  return layout === "home" ? { x, y: 12 + s.across * 76 } : { x: 100 - x, y: 12 + (1 - s.across) * 76 };
}

function Icons({ m }: { m: Marks }) {
  return (
    <>
      {(m.yellow > 0 || m.red) && (
        <span className="absolute top-1/2 -left-4 -translate-y-1/2">
          <RectangleVertical aria-label={m.red ? "Red card" : "Yellow card"} className={cn("size-3.5", m.red ? "fill-red-500 text-red-500" : "fill-amber-400 text-amber-400")} />
        </span>
      )}
      {(m.goals > 0 || m.assists > 0) && (
        <span className="absolute -right-3 -bottom-1 flex items-center gap-0.5 rounded-full bg-foreground px-1 text-[11px] leading-[14px] font-bold text-background">
          {m.goals > 0 && <span aria-label={`${m.goals} goal`}>⚽{m.goals > 1 ? m.goals : ""}</span>}
          {m.assists > 0 && <span aria-label="assist">A</span>}
        </span>
      )}
    </>
  );
}

const short = (name: string) => (name.length > 9 ? `${name.slice(0, 8)}…` : name);

function lensText(lens: Lens, s: PlayerStats | undefined): string | null {
  if (!s) return null;
  switch (lens) {
    case "distance": return s.distance_km == null ? null : s.distance_km.toFixed(1);
    case "speed": return s.top_speed_kmh == null ? null : s.top_speed_kmh.toFixed(1);
    case "passes": return String(s.passes);
    case "shots": return String(s.shots);
    default: return null;
  }
}

function Marker({ spot, layout, meta, rating, stats, marks, lens, potm, side, index, onKey }: { spot: Spot; layout: Layout; meta: MatchMeta; rating?: PlayerRating; stats?: PlayerStats; marks: Marks; lens: Lens; potm: boolean; side: "home" | "away"; index: number; onKey: (e: React.KeyboardEvent, i: number) => void }) {
  const p = meta.players[spot.id];
  if (!p) return null;
  const { x, y } = toXY(spot, layout);
  const poor = rating && rating.data_rating < 5;
  const text = lens === "rating" ? null : lensText(lens, stats);
  return (
    <button
      type="button"
      data-marker
      onKeyDown={(e) => onKey(e, index)}
      onClick={() => useShell.getState().setPlayerId(spot.id)}
      aria-label={`${p.name}, ${p.position}${rating ? `, rating ${rating.data_rating.toFixed(1)}` : ""}${marks.offAt ? `, off at ${marks.offAt}` : ""}`}
      className={cn("group absolute flex w-[96px] max-sm:w-[68px] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-0.5 outline-none", marks.offAt && "opacity-60")}
      style={{ left: `${x}%`, top: `${y}%` }}
    >
      {marks.offAt && (
        <span className="flex items-center text-[11px] leading-3 text-white/85">
          <ArrowDown aria-hidden className="size-3 text-red-400" />
          {marks.offAt}
        </span>
      )}
      <span className="relative">
        <span
          className={cn(
            "grid size-10 place-items-center rounded-full font-display text-base font-bold ring-[1.5px] transition-transform group-hover:scale-105 group-focus-visible:ring-2 group-focus-visible:ring-ring",
            side === "home" ? "bg-home/35 ring-home" : "bg-away/35 ring-away",
            poor && "ring-2 ring-[var(--rating-poor)]",
            rating && rating.data_rating >= 7 && "ring-2 ring-[var(--rating-good)]",
          )}
        >
          {p.shirt}
        </span>
        {lens === "rating" && rating ? (
          <RatingBadge value={rating.data_rating} potm={potm} size="sm" className="absolute -top-2 -right-4" />
        ) : text ? (
          <LensBadge text={text} className="absolute -top-2 -right-4" />
        ) : null}
        <Icons m={marks} />
      </span>
      <span title={p.name} className="rounded-full bg-black/45 px-1.5 text-xs leading-5 font-medium whitespace-nowrap text-white">{short(p.short_name)}</span>
    </button>
  );
}

function PitchLines({ horizontal }: { horizontal: boolean }) {
  const style = { stroke: "var(--chalk)", opacity: 0.3, fill: "none", strokeWidth: 0.3 } as const;
  return horizontal ? (
    <svg aria-hidden viewBox="0 0 105 68" preserveAspectRatio="none" className="absolute inset-0 size-full" style={style}>
      <rect x="1.5" y="1.5" width="102" height="65" />
      <line x1="52.5" x2="52.5" y1="1.5" y2="66.5" />
      <circle cx="52.5" cy="34" r="9" />
      <rect x="1.5" y="14" width="16.5" height="40" />
      <rect x="1.5" y="24" width="5.5" height="20" />
      <rect x="87" y="14" width="16.5" height="40" />
      <rect x="98" y="24" width="5.5" height="20" />
    </svg>
  ) : (
    <svg aria-hidden viewBox="0 0 68 100" preserveAspectRatio="none" className="absolute inset-0 size-full" style={style}>
      <rect x="2" y="2" width="64" height="96" />
      <line x1="2" x2="66" y1="50" y2="50" />
      <circle cx="34" cy="50" r="8" />
      <rect x="15" y="2" width="38" height="15" />
      <rect x="25" y="2" width="18" height="6" />
      <rect x="15" y="83" width="38" height="15" />
      <rect x="25" y="92" width="18" height="6" />
    </svg>
  );
}

function PitchFrame({ horizontal, label, children }: { horizontal: boolean; label: string; children: React.ReactNode }) {
  return (
    <div
      className="relative mx-auto w-full overflow-hidden rounded-2xl border bg-turf"
      style={horizontal ? { aspectRatio: "105 / 68", width: "min(100%, calc(clamp(360px, calc(100dvh - 345px), 640px) * 105 / 68))" } : { aspectRatio: "3 / 4", width: "min(100%, calc(clamp(360px, calc(100dvh - 360px), 640px) * 3 / 4))" }}
    >
      <PitchLines horizontal={horizontal} />
      <div role="group" aria-label={label} className="absolute inset-0">
        {children}
      </div>
    </div>
  );
}

function moveFocus(e: React.KeyboardEvent, i: number) {
  const all = Array.from((e.currentTarget.parentElement as HTMLElement).querySelectorAll<HTMLElement>("[data-marker]"));
  const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? all[i + 1] : e.key === "ArrowLeft" || e.key === "ArrowUp" ? all[i - 1] : null;
  if (next) {
    e.preventDefault();
    next.focus();
  }
}

function Subs({ meta, team, ratings, marks, side }: { meta: MatchMeta; team: Team; ratings: Map<string, PlayerRating>; marks: Map<string, Marks>; side: "home" | "away" }) {
  return (
    <section aria-label={`${team.short_name} ${copy.coachPage.bench}`} className="flex min-w-0 flex-col">
      <h3 className="pb-1 font-display text-lg font-semibold">
        <span className={side === "home" ? "text-home-text" : "text-away-text"}>{team.short_name}</span> {copy.coachPage.bench}
      </h3>
      <ul className="divide-y">
        {team.bench.map((id) => {
          const p = meta.players[id];
          if (!p) return null;
          const m = marks.get(id) ?? NONE;
          const r = ratings.get(id);
          return (
            <li key={id}>
              <button type="button" onClick={() => useShell.getState().setPlayerId(id)} className="flex h-[54px] w-full items-center gap-3 text-left hover:bg-foreground/5">
                <span className={cn("grid size-9 shrink-0 place-items-center rounded-full font-display font-bold ring-[1.5px]", side === "home" ? "bg-home/35 ring-home" : "bg-away/35 ring-away")}>{p.shirt}</span>
                {m.onAt && r ? <RatingBadge value={r.data_rating} size="sm" /> : <span className="w-7" />}
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-base font-medium">{p.short_name}</span>
                  <span className="text-[13px] text-muted-foreground">{p.position}</span>
                </span>
                {m.onAt ? (
                  <span className="inline-flex items-center gap-0.5 pr-1 text-[13px] font-medium text-[var(--rating-good)]">
                    <ArrowUp aria-hidden className="size-3.5" />
                    {m.onAt}
                  </span>
                ) : (
                  <span className="pr-1 text-[13px] text-muted-foreground">{copy.coachPage.unused}</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function TeamStrip({ meta, side, ratings }: { meta: MatchMeta; side: "home" | "away"; ratings: Map<string, PlayerRating> }) {
  const t = meta[side];
  const values = t.starting_xi.map((id) => ratings.get(id)?.data_rating).filter((v): v is number => v !== undefined);
  const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  return (
    <div className={cn("flex items-center gap-2", side === "away" && "flex-row-reverse text-right")}>
      <span aria-hidden className={cn("size-3 rounded-full", side === "home" ? "bg-home" : "bg-away")} />
      <h2 className="font-display text-xl font-semibold">{t.name}</h2>
      <span className="text-base text-muted-foreground tabular-nums">{t.formation}</span>
      {avg !== null && <RatingBadge value={avg} size="lg" />}
    </div>
  );
}

/** Both XIs on one horizontal pitch from 768px; one vertical pitch per team below that. Lens chips swap the badge. */
export function LineupTab() {
  const meta = useLive((s) => s.meta);
  const timeline = useLive((s) => s.timeline);
  const finished = useLive((s) => s.status === "finished" || s.status === "archived");
  const picked = useCoach((s) => s.teamId);
  const wide = useMediaQuery("(min-width: 768px)");
  const ratings = useRatings().data;
  const [lens, setLens] = useState<Lens>("rating");
  const ids = useMemo(() => (meta ? [...meta.home.starting_xi, ...meta.away.starting_xi] : []), [meta]);
  const stats = usePlayersStats(ids);
  const map = useMemo(() => new Map((ratings ?? []).map((r) => [r.player_id, r])), [ratings]);
  const marks = useMemo(() => marksFrom(timeline), [timeline]);
  const potmId = useMemo(() => (finished && ratings?.length ? [...ratings].sort((a, b) => b.data_rating - a.data_rating)[0].player_id : null), [finished, ratings]);
  const lenses = useMemo(() => {
    const has = (l: Lens) => ids.some((id) => lensText(l, stats.get(id)) !== null);
    const all: [Lens, string][] = [["rating", copy.coachPage.lens.rating], ["distance", copy.coachPage.lens.distance], ["speed", copy.coachPage.lens.speed], ["passes", copy.coachPage.lens.passes], ["shots", copy.coachPage.lens.shots]];
    return all.filter(([l]) => l === "rating" || has(l));
  }, [ids, stats]);
  if (!meta) return <Skeleton className="mx-auto my-4 aspect-[105/68] w-full rounded-2xl" aria-hidden />;
  if (!meta.home.starting_xi.length) return <EmptyState>{copy.coachPage.noLineup}</EmptyState>;
  const single = picked === meta.away.team_id ? "away" : "home";
  const sides = (wide ? ["home", "away"] : [single]) as ("home" | "away")[];
  const marker = (side: "home" | "away") =>
    place(meta, meta[side]).map((spot, i) => (
      <Marker key={spot.id} spot={spot} layout={wide ? side : "vertical"} meta={meta} rating={map.get(spot.id)} stats={stats.get(spot.id)} marks={marks.get(spot.id) ?? NONE} lens={lens} potm={potmId === spot.id} side={side} index={i} onKey={moveFocus} />
    ));
  return (
    <div className="flex flex-col gap-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {sides.map((s) => (
          <TeamStrip key={s} meta={meta} side={s} ratings={map} />
        ))}
        {lenses.length > 1 && (
          <div role="radiogroup" aria-label={copy.coachPage.lensLabel} title={copy.coachPage.legend} className="order-last flex w-full gap-2 overflow-x-auto sm:order-none sm:w-auto [scrollbar-width:none]">
            {lenses.map(([l, label]) => (
              <button key={l} type="button" role="radio" aria-checked={lens === l} onClick={() => setLens(l)} className={cn("h-11 shrink-0 rounded-full px-4 text-sm font-medium ring-1", lens === l ? "bg-foreground text-background ring-foreground" : "bg-raised text-muted-foreground ring-border hover:text-foreground")}>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>
      <PitchFrame horizontal={wide} label={sides.map((s) => `${meta[s].name} ${meta[s].formation}`).join(", ")}>
        {sides.flatMap((s) => marker(s))}
      </PitchFrame>
      <div className={cn("grid gap-x-8 gap-y-4", wide && "grid-cols-2")}>
        {sides.map((s) => (
          <Subs key={s} meta={meta} team={meta[s]} ratings={map} marks={marks} side={s} />
        ))}
      </div>
    </div>
  );
}

/** A small formation pitch with a rating badge per starter, for the match report. Names live on the Lineup tab. */
export function LineupMini({ side }: { side: "home" | "away" }) {
  const meta = useLive((s) => s.meta);
  const ratings = useRatings().data;
  const map = useMemo(() => new Map((ratings ?? []).map((r) => [r.player_id, r])), [ratings]);
  if (!meta || !meta[side].starting_xi.length) return null;
  const team = meta[side];
  return (
    <figure className="flex min-w-0 flex-col gap-2">
      <figcaption className="flex items-center gap-2 text-sm font-medium">
        <span aria-hidden className={cn("size-2.5 rounded-full", side === "home" ? "bg-home" : "bg-away")} />
        <span className="truncate">{team.name}</span>
        <span className="text-muted-foreground tabular-nums">{team.formation}</span>
      </figcaption>
      <div className="relative mx-auto w-full max-w-[260px] overflow-hidden rounded-xl border bg-turf" style={{ aspectRatio: "3 / 4" }}>
        <PitchLines horizontal={false} />
        {place(meta, team).map((spot) => {
          const { x, y } = toXY(spot, "vertical");
          const r = map.get(spot.id);
          return (
            <span key={spot.id} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${x}%`, top: `${y}%` }} title={meta.players[spot.id]?.name}>
              {r ? <RatingBadge value={r.data_rating} size="sm" /> : <span aria-hidden className="block size-2.5 rounded-full bg-foreground/70" />}
            </span>
          );
        })}
      </div>
    </figure>
  );
}
