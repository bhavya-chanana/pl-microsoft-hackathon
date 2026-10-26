"use client";

import { ArrowDownWideNarrow, Star } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useState } from "react";

import { cn } from "@/lib/utils";
import { routes } from "@/lib/routes";

import { useMatchDetail, useMatchStats, useMatchView, usePlayersById } from "./api";
import { PLAYER_CATEGORIES } from "./player-categories";
import { formatClock, snapshotThrough } from "./time";
import type { Period, Persona, PlayerRow, Row, Section, Shot, Side, Source } from "./types";
import { usePersonaText, useT } from "./i18n";
import { PlayerFace } from "./player-face";
import { type KeyStat, PERSONAS, SOURCE_LABELS, type StatsLayout } from "./personas";
import { favoriteSide, usePlayback, usePrefs } from "./stores";
import { useMatchParams } from "./use-match-params";

/** Stats come from the backend's last complete snapshot at or before the playhead, so they may lag but never lead. */
export function Freshness({ throughMs, refreshedAt }: { throughMs: number; refreshedAt: string }) {
  const playhead = usePlayback((s) => s.playheadMs);
  return (
    <p className="text-xs text-muted-foreground">
      Stats through <span className="font-medium tabular-nums text-foreground">{formatClock(throughMs)}</span> (video at {formatClock(playhead)})
      · refreshed {new Date(refreshedAt).toLocaleTimeString()}
    </p>
  );
}

export function SourceTag({ source }: { source: Source }) {
  return <span className="rounded border px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground">{SOURCE_LABELS[source]}</span>;
}

export function StatsView() {
  const { matchId } = useMatchParams();
  const match = useMatchDetail(matchId).data!;
  const persona = usePrefs((s) => s.persona);
  const fav = favoriteSide(usePrefs((s) => s.favoriteTeam), match);
  const playhead = usePlayback((s) => s.playheadMs);
  const mode = usePlayback((s) => s.mode);
  const [period, setPeriod] = useState<Period>("all");
  const h2Started = mode === "full_review" || snapshotThrough(playhead) > 45 * 60_000;
  const effective = period === "h2" && !h2Started ? "all" : period;
  const stats = useMatchStats(effective);
  const layout = PERSONAS[persona].stats;
  const tr = useT();
  if (stats.isError) return <p role="alert" className="mt-6 text-sm text-destructive">Stats could not be loaded. {stats.error.message}</p>;
  if (!stats.data) return <div className="mt-6 min-h-[60dvh]" aria-busy="true" />;
  const data = stats.data;
  const rows = data.players;
  const pick = (ids: string[]) => ids.map((id) => data.sections.find((s) => s.id === id)).filter((s): s is Section => !!s);

  const panels: Record<StatsLayout["panels"][number], ReactNode> = {
    myClub: fav && <MyClub key="myClub" side={fav} sections={data.sections} possession={data.possession} name={match[fav].name} />,
    mySquad: <FantasySquad key="mySquad" matchId={matchId} rows={rows} />,
    myPlayers: <MyPlayers key="myPlayers" matchId={matchId} rows={rows} />,
    shotMap: <ShotMap key="shotMap" shots={data.shots} matchId={matchId} />,
    differentials: <Differentials key="differentials" matchId={matchId} rows={rows} />,
  };
  const aside: Record<StatsLayout["aside"][number], ReactNode> = {
    bestPlayer: <BestPlayer key="bestPlayer" rows={rows} matchId={matchId} />,
    zones: <AttackingZones key="zones" zones={data.zones} home={match.home.short} away={match.away.short} />,
    sources: <SensorNote key="sources" />,
    cleanSheet: <CleanSheetWatch key="cleanSheet" sections={data.sections} home={match.home.short} away={match.away.short} />,
    glossary: <Glossary key="glossary" />,
  };

  return (
    <main className="mt-4 flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Period" className="flex gap-1">
          {(["all", "h1", "h2"] as const).map((p) => (
            <button key={p} type="button" aria-pressed={effective === p} disabled={p === "h2" && !h2Started} onClick={() => setPeriod(p)}
              className={cn("min-h-9 rounded-full border px-4 text-sm font-medium disabled:opacity-40",
                effective === p ? "border-foreground bg-foreground text-background" : "text-muted-foreground hover:bg-muted")}>
              {p === "all" ? "All" : p === "h1" ? "1st" : "2nd"}
            </button>
          ))}
        </div>
        <Freshness throughMs={data.throughMs} refreshedAt={data.refreshedAt} />
      </div>

      <Priorities persona={persona} />
      <KeyStats stats={layout.keyStats} sections={data.sections} fav={fav} home={match.home.short} away={match.away.short} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-4">
          {layout.panels.map((p) => panels[p])}
          <div className="rounded-xl border bg-card">
            <div className="px-4 pt-4">
              <p className="text-center text-sm font-semibold">Ball possession</p>
              <div className="mt-2 flex h-8 overflow-hidden rounded-full text-sm font-semibold">
                <div className="flex items-center bg-home pl-3 text-graphic-foreground" style={{ width: `${data.possession[0]}%` }}>{data.possession[0]}%</div>
                <div className="flex flex-1 items-center justify-end bg-away pr-3 text-graphic">{data.possession[1]}%</div>
              </div>
              <p className="mt-1 text-center"><SourceTag source="fusion" /></p>
            </div>
            {pick(layout.sections).map((s) => <StatSection key={s.id} section={s} />)}
            {layout.more.length > 0 && (
              <details className="group border-t">
                <summary className="flex min-h-11 cursor-pointer items-center justify-center px-4 text-sm font-medium text-muted-foreground hover:text-foreground">
                  <span className="group-open:hidden">{tr.moreStats} ({pick(layout.more).map((s) => s.title).join(", ")})</span>
                  <span className="hidden group-open:inline">{tr.fewerStats}</span>
                </summary>
                {pick(layout.more).map((s) => <StatSection key={s.id} section={s} />)}
              </details>
            )}
          </div>
        </div>
        <aside className="flex flex-col gap-4">{layout.aside.map((a) => aside[a])}</aside>
      </div>

      <PlayerStatsTable key={persona} rows={rows} matchId={matchId}
        initialCategory={layout.playerTable.category} initialSort={layout.playerTable.sort} />
    </main>
  );
}

function Priorities({ persona }: { persona: Persona }) {
  const cfg = usePersonaText(persona);
  const tr = useT();
  return (
    <section aria-labelledby="prio-h" className="rounded-xl border border-l-4 border-l-[var(--persona)] bg-[color-mix(in_oklch,var(--persona)_8%,var(--card))] p-4">
      <h2 id="prio-h" className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {tr.orderedFor}<span className="rounded-full bg-[var(--persona)] px-2 py-0.5 font-semibold text-background">{cfg.label}</span>
      </h2>
      <ol className="mt-2 flex gap-2 overflow-x-auto pb-1">
        {cfg.priorities.map((p, i) => (
          <li key={p.title} title={p.why} className="flex min-w-48 shrink-0 gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <span className="font-display font-semibold tabular-nums text-[var(--persona)]">{i + 1}</span>
            <span><span className="block font-medium">{p.title}</span><span className="block text-xs text-muted-foreground">{p.why}</span></span>
          </li>
        ))}
      </ol>
    </section>
  );
}

const findRow = (sections: Section[], section: string, label: string) =>
  sections.find((s) => s.id === section)?.rows.find((r) => r.label === label);

function KeyStats({ stats, sections, fav, home, away }: { stats: KeyStat[]; sections: Section[]; fav: Side | null; home: string; away: string }) {
  return (
    <section aria-label="Key stats" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {stats.map((k) => {
        const row = findRow(sections, k.section, k.row);
        if (!row) return null;
        return (
          <div key={k.caption} className="rounded-xl border border-t-4 border-t-[var(--persona)] bg-card p-4">
            <p className="text-xs text-muted-foreground">{k.caption}</p>
            <div className="mt-2 flex items-baseline justify-between gap-2 font-display text-2xl font-semibold tabular-nums">
              {(["home", "away"] as const).map((side) => (
                <span key={side} className={cn("flex flex-col", side === "away" && "items-end", fav && fav !== side && "text-muted-foreground")}>
                  {fmtValue(row, side)}
                  <span className={cn("font-sans text-xs font-medium", side === "home" ? "text-home" : "text-away")}>{side === "home" ? home : away}{fav === side && " ★"}</span>
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}

/** Diehard: the game told from your club's side, as wins and worries. */
function MyClub({ side, sections, possession, name }: { side: Side; sections: Section[]; possession: [number, number]; name: string }) {
  const other: Side = side === "home" ? "away" : "home";
  const checks: Array<[string, string, string]> = [
    ["top", "Expected goals (xG)", "Chance quality"], ["top", "Big chances", "Big chances"], ["top", "Shots on target", "Shots on target"],
    ["duels", "Duels won", "Duels"], ["physical", "Distance covered", "Running"], ["discipline", "Fouls committed", "Discipline"],
  ];
  const items = checks.map(([sec, label, caption]) => {
    const row = findRow(sections, sec, label);
    if (!row || row[side] === null || row[other] === null) return null;
    const us = row[side]!, them = row[other]!;
    const ahead = row.lowerIsBetter ? us < them : us > them;
    return { caption, ahead, level: us === them, us: fmtValue(row, side), them: fmtValue(row, other) };
  }).filter((x) => !!x);
  const pos = side === "home" ? possession[0] : possession[1];
  const tr = useT();
  return (
    <section aria-labelledby="club-h" className="overflow-hidden rounded-xl border bg-card">
      <div className="bg-tally px-4 py-3 text-tally-foreground">
        <h2 id="club-h" className="font-display text-lg font-semibold">{tr.whereWeStand(name)}</h2>
        <p className="text-sm opacity-90">{tr.battles(pos, items.filter((i) => i.ahead).length, items.length)}</p>
      </div>
      <div className="p-4 pt-0">
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {items.map((i) => (
          <li key={i.caption} className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <span className="flex items-center gap-2">
              <span aria-hidden className={cn("size-2 rounded-full", i.level ? "bg-muted-foreground" : i.ahead ? "bg-verified" : "bg-blocked")} />
              {i.caption}<span className="sr-only">{i.level ? "level" : i.ahead ? "ahead" : "behind"}</span>
            </span>
            <span className="tabular-nums"><span className="font-semibold">{i.us}</span> <span className="text-muted-foreground">v {i.them}</span></span>
          </li>
        ))}
      </ul>
      </div>
    </section>
  );
}

/** Diehard + casual: the players picked in onboarding, with plain numbers. */
function MyPlayers({ matchId, rows: all }: { matchId: string; rows: PlayerRow[] }) {
  const followed = usePrefs((s) => s.followedPlayers);
  const rows = all.filter((r) => followed.includes(r.id));
  const tr = useT();
  if (rows.length === 0) return null;
  return (
    <section aria-labelledby="mine-h" className="rounded-xl border bg-card p-4">
      <h2 id="mine-h" className="font-display text-lg font-semibold">{tr.playersYouFollow}</h2>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {rows.map((r) => (
          <li key={r.id}>
            <Link href={routes.player(matchId, r.id)} className="flex items-center gap-3 rounded-lg bg-muted/50 px-3 py-2 text-sm hover:bg-muted">
              <PlayerFace id={r.id} shirt={r.shirt} kit={r.team} className="size-12" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{r.name}</span>
                <span className="block text-xs text-muted-foreground tabular-nums">
                  {r.values.goals ?? 0} G · {r.values.assists ?? 0} A · {r.values.shots ?? 0} shots · {r.values.distance ?? 0} km
                </span>
              </span>
              <Rating value={r.rating} />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Fantasy: best performers the viewer doesn't own. */
function Differentials({ matchId, rows }: { matchId: string; rows: PlayerRow[] }) {
  const followed = usePrefs((s) => s.followedPlayers);
  const top = rows.filter((r) => !followed.includes(r.id))
    .map((r) => ({ ...r, pts: r.values.fantasy ?? 0 })).sort((a, b) => b.pts - a.pts).slice(0, 4);
  return (
    <section aria-labelledby="diff-h" className="rounded-xl border bg-card p-4">
      <h2 id="diff-h" className="font-display text-lg font-semibold">{useT().differentials}</h2>
      <ul className="mt-3 flex flex-col divide-y text-sm">
        {top.map((r) => (
          <li key={r.id}>
            <Link href={routes.player(matchId, r.id)} className="flex min-h-11 items-center gap-3 hover:underline">
              <PlayerFace id={r.id} shirt={r.shirt} kit={r.team} className="size-9" />
              <span className="flex-1 font-medium">{r.name}<span className="ml-2 text-xs text-muted-foreground">{r.position}</span></span>
              <span className="text-xs text-muted-foreground tabular-nums">xG {r.values.xg ?? 0}</span>
              <span className="w-14 text-right font-semibold tabular-nums">{r.pts} pts</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Fantasy: is each defence's clean sheet still alive, and how much pressure is it under? */
function CleanSheetWatch({ sections, home, away }: { sections: Section[]; home: string; away: string }) {
  const score = useMatchView().data?.score ?? { home: 0, away: 0 };
  const onTarget = findRow(sections, "top", "Shots on target");
  const xg = findRow(sections, "top", "Expected goals (xG)");
  return (
    <section className="rounded-xl border bg-card p-4">
      <h2 className="font-display text-lg font-semibold">{useT().cleanSheet}</h2>
      {(["home", "away"] as const).map((side) => {
        const opp: Side = side === "home" ? "away" : "home";
        const alive = score[opp] === 0;
        return (
          <div key={side} className="mt-3 flex items-center justify-between text-sm">
            <span className="font-medium">{side === "home" ? home : away} defence</span>
            <span className="text-xs text-muted-foreground tabular-nums">faced {onTarget?.[opp] ?? 0} on target · xG {xg?.[opp]?.toFixed(2) ?? "0.00"}</span>
            <span className={cn("rounded px-2 py-0.5 text-xs font-semibold", alive ? "bg-verified text-background" : "bg-blocked text-background")}>{alive ? "Alive" : "Gone"}</span>
          </div>
        );
      })}
    </section>
  );
}

function Glossary() {
  const terms: Array<[string, string]> = [
    ["xG", "How likely a shot was to go in, from 0 to 1. Add them up to see who made better chances."],
    ["Big chance", "A shot most players would be expected to score."],
    ["On target", "Would have gone in without the goalkeeper."],
    ["Possession", "Share of the time each team had the ball."],
  ];
  return (
    <section className="rounded-xl border bg-card p-4">
      <h2 className="font-display text-lg font-semibold">{useT().glossary}</h2>
      <dl className="mt-2 flex flex-col gap-2 text-sm">
        {terms.map(([k, v]) => <div key={k}><dt className="font-medium">{k}</dt><dd className="text-muted-foreground">{v}</dd></div>)}
      </dl>
    </section>
  );
}

function fmtValue(row: Row, side: Side) {
  const v = row[side];
  if (v === null) return "—";
  switch (row.fmt) {
    case "pct": return `${v}%`;
    case "km": return `${v} km`;
    case "kmh": return `${v} km/h`;
    case "bpm": return `${v} bpm`;
    case "dec": return v.toFixed(2);
    case "ofTotal": {
      const total = side === "home" ? row.homeTotal : row.awayTotal;
      return total ? `${v} (${Math.round((v / total) * 100)}%)` : `${v}`;
    }
    default: return `${v}`;
  }
}

function StatSection({ section }: { section: Section }) {
  return (
    <section aria-labelledby={`sec-${section.id}`} className="border-t px-4 py-4 first:border-t-0">
      <h2 id={`sec-${section.id}`} className="flex items-center justify-center gap-2 text-sm font-semibold">
        {section.title}<SourceTag source={section.source} />
      </h2>
      <dl className="mt-3 flex flex-col gap-2.5">
        {section.rows.map((row) => {
          const h = row.home ?? 0, a = row.away ?? 0;
          const homeLeads = row.home !== null && row.away !== null && (row.lowerIsBetter ? h < a : h > a);
          const awayLeads = row.home !== null && row.away !== null && (row.lowerIsBetter ? a < h : a > h);
          return (
            <div key={row.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 text-sm">
              <dd className="justify-self-start tabular-nums">
                <span className={cn("rounded-full px-2 py-0.5", homeLeads && "bg-home font-semibold text-graphic-foreground")}>{fmtValue(row, "home")}</span>
              </dd>
              <dt className="text-center text-muted-foreground">{row.label}</dt>
              <dd className="justify-self-end tabular-nums">
                <span className={cn("rounded-full px-2 py-0.5", awayLeads && "bg-away font-semibold text-graphic")}>{fmtValue(row, "away")}</span>
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

const OUTCOME_LABEL: Record<Shot["outcome"], string> = { goal: "Goal", saved: "Saved", off: "Off target", blocked: "Blocked", woodwork: "Hit woodwork" };

function ShotMap({ shots, matchId }: { shots: Shot[]; matchId: string }) {
  const byId = usePlayersById(matchId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = shots.find((s) => s.id === selectedId) ?? shots.findLast((s) => s.outcome === "goal") ?? shots.at(-1);
  const idx = selected ? shots.indexOf(selected) : -1;
  const player = selected ? byId.get(selected.playerId) : undefined;
  // Pitch is 105 × 68. Home attacks the left goal, away the right (as on FotMob).
  const pos = (s: Shot) => ({ cx: s.team === "home" ? ((100 - s.x) / 100) * 105 : (s.x / 100) * 105, cy: (s.y / 100) * 68 });

  return (
    <section aria-labelledby="shotmap-h" className="rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 id="shotmap-h" className="font-display text-lg font-semibold">{useT().shotMap}</h2>
        <SourceTag source="ball" />
      </div>
      {shots.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">No shots yet.</p> : (
        <>
          {selected && player && (
            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg bg-muted/50 p-3 text-sm">
              <div className="flex items-center gap-2">
                <button type="button" aria-label="Previous shot" disabled={idx <= 0} onClick={() => setSelectedId(shots[idx - 1].id)}
                  className="grid size-9 place-items-center rounded-full border disabled:opacity-30">‹</button>
                <span className="w-12 text-center tabular-nums">{formatClock(selected.atMs)}</span>
                <button type="button" aria-label="Next shot" disabled={idx >= shots.length - 1} onClick={() => setSelectedId(shots[idx + 1].id)}
                  className="grid size-9 place-items-center rounded-full border disabled:opacity-30">›</button>
              </div>
              <Link href={routes.player(matchId, player.id)} className="font-semibold hover:underline">{player.name}</Link>
              <dl className="flex flex-wrap gap-x-5 gap-y-1">
                {[["Result", OUTCOME_LABEL[selected.outcome]], ["Shot type", selected.body], ["Situation", selected.situation],
                  ["xG", selected.xg.toFixed(2)], ["xGOT", selected.xgot?.toFixed(2) ?? "—"], ["Ball speed", `${selected.speedKmh} km/h`]].map(([k, v]) => (
                  <div key={k}><dt className="text-xs text-muted-foreground">{k}</dt><dd className="font-medium tabular-nums">{v}</dd></div>
                ))}
              </dl>
            </div>
          )}
          <svg viewBox="-2 -2 109 72" className="mt-3 w-full rounded-lg bg-turf" role="img" aria-label={`Shot map, ${shots.length} shots`}>
            <g fill="none" stroke="var(--chalk)" strokeWidth="0.4" opacity="0.7">
              <rect x="0" y="0" width="105" height="68" />
              <line x1="52.5" y1="0" x2="52.5" y2="68" />
              <circle cx="52.5" cy="34" r="9.15" />
              <rect x="0" y="13.84" width="16.5" height="40.32" /><rect x="88.5" y="13.84" width="16.5" height="40.32" />
              <rect x="0" y="24.84" width="5.5" height="18.32" /><rect x="99.5" y="24.84" width="5.5" height="18.32" />
            </g>
            {shots.map((s) => {
              const { cx, cy } = pos(s);
              const color = s.team === "home" ? "var(--team-home)" : "var(--team-away)";
              const isSel = s.id === selected?.id;
              return (
                <circle key={s.id} cx={cx} cy={cy} r={1.2 + s.xg * 4.5} fill={s.outcome === "goal" ? color : "transparent"}
                  stroke={isSel ? "var(--chalk)" : color} strokeWidth={isSel ? 0.9 : 0.6} className="cursor-pointer"
                  onClick={() => setSelectedId(s.id)}>
                  <title>{`${byId.get(s.playerId)?.name} ${formatClock(s.atMs)} · ${OUTCOME_LABEL[s.outcome]} · xG ${s.xg.toFixed(2)}`}</title>
                </circle>
              );
            })}
          </svg>
          <p className="mt-2 text-xs text-muted-foreground">Circle size = xG · filled = goal · positions from ball sensor at strike</p>
        </>
      )}
    </section>
  );
}

function BestPlayer({ rows, matchId }: { rows: PlayerRow[]; matchId: string }) {
  const tr = useT();
  const best = rows.filter((r) => r.rating !== null).sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))[0];
  if (!best) return null;
  return (
    <section className="rounded-xl border bg-card p-4">
      <p className="text-xs text-muted-foreground">{tr.topRated}</p>
      <Link href={routes.player(matchId, best.id)} className="mt-2 flex items-center gap-3 hover:underline">
        <PlayerFace id={best.id} shirt={best.shirt} kit={best.team} className="size-14" />
        <span className="flex-1 font-semibold">{best.name}</span>
        <Rating value={best.rating} />
      </Link>
    </section>
  );
}

function Rating({ value }: { value: number | null }) {
  if (value === null) return <span className="text-muted-foreground">—</span>;
  const tone = value >= 8 ? "bg-verified text-background" : value >= 7 ? "bg-verified/70 text-background" : value >= 6 ? "bg-rewritten text-background" : "bg-blocked text-background";
  return <span className={cn("inline-block min-w-10 rounded px-1.5 py-0.5 text-center text-sm font-semibold tabular-nums", tone)}>{value.toFixed(1)}</span>;
}

function AttackingZones({ zones, home, away }: { zones: Record<Side, [number, number, number]>; home: string; away: string }) {
  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg font-semibold">Attacking zones</h2>
        <SourceTag source="ball" />
      </div>
      {(["home", "away"] as const).map((side) => (
        <div key={side} className="mt-3">
          <p className="text-xs text-muted-foreground">{side === "home" ? home : away} · left / centre / right</p>
          <div className="mt-1 grid grid-cols-3 gap-1">
            {zones[side].map((v, i) => (
              <div key={i} className={cn("rounded py-2 text-center text-sm font-semibold tabular-nums", side === "home" ? "text-graphic-foreground" : "text-graphic")}
                style={{ background: `color-mix(in oklch, var(--team-${side}) ${35 + v}%, transparent)` }}>{v}%</div>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function SensorNote() {
  return (
    <section className="rounded-xl border bg-card p-4 text-sm text-muted-foreground">
      <h2 className="font-display text-lg font-semibold text-foreground">Where these come from</h2>
      <ul className="mt-2 flex flex-col gap-1.5">
        <li><SourceTag source="vest" /> distance, speed, sprints, heart rate</li>
        <li><SourceTag source="ball" /> shots, ball speed, zones</li>
        <li><SourceTag source="fusion" /> passes, duels, possession</li>
        <li><SourceTag source="llm_vision" /> fouls, cards, offsides</li>
        <li><SourceTag source="model" /> xG, ratings</li>
      </ul>
    </section>
  );
}

function PlayerStatsTable({ rows: all, matchId, initialCategory, initialSort }: {
  rows: PlayerRow[]; matchId: string; initialCategory: string; initialSort: string;
}) {
  const [cat, setCat] = useState<keyof typeof PLAYER_CATEGORIES>(initialCategory);
  const [team, setTeam] = useState<Side | "both">("both");
  const [sortKey, setSortKey] = useState<string>(initialSort);
  const match = useMatchDetail(matchId).data!;
  const cfg = PLAYER_CATEGORIES[cat];
  const rows = all
    .filter((r) => (team === "both" || r.team === team) && (cat !== "goalkeeping" || r.position === "GK"))
    .sort((a, b) => ((sortKey === "rating" ? b.rating : b.values[sortKey]) ?? -1) - ((sortKey === "rating" ? a.rating : a.values[sortKey]) ?? -1));

  return (
    <section aria-labelledby="ps-h" className="rounded-xl border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b p-4">
        <h2 id="ps-h" className="font-display text-lg font-semibold">{useT().playerStats}</h2>
        <div className="ml-auto flex gap-1 text-sm">
          {(["both", "home", "away"] as const).map((t) => (
            <button key={t} type="button" aria-pressed={team === t} onClick={() => setTeam(t)}
              className={cn("min-h-9 rounded-md px-3 text-muted-foreground", team === t && "bg-muted font-medium text-foreground")}>
              {t === "both" ? "Both" : match[t].short}
            </button>
          ))}
        </div>
      </div>
      <div role="tablist" aria-label="Player stat category" className="flex gap-1 overflow-x-auto border-b px-4 py-2">
        {Object.entries(PLAYER_CATEGORIES).map(([k, c]) => (
          <button key={k} role="tab" type="button" aria-selected={cat === k} onClick={() => { setCat(k); setSortKey(k === "goalkeeping" ? "saves" : "rating"); }}
            className={cn("min-h-9 shrink-0 rounded-full px-3 text-sm text-muted-foreground", cat === k && "bg-foreground font-medium text-background")}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th className="p-3 text-left font-medium">Player</th>
              {[{ key: "rating", label: "Rating", source: "model" as Source }, ...cfg.cols].map((c) => (
                <th key={c.key} className="p-3 text-right font-medium whitespace-nowrap" aria-sort={sortKey === c.key ? "descending" : undefined}>
                  <button type="button" onClick={() => setSortKey(c.key)} title={SOURCE_LABELS[c.source]}
                    className={cn("inline-flex items-center gap-1 hover:text-foreground", sortKey === c.key && "text-foreground")}>
                    {c.label}{sortKey === c.key && <ArrowDownWideNarrow aria-hidden className="size-3" />}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t hover:bg-muted/50">
                <td className="p-3">
                  <Link href={routes.player(matchId, r.id)} className="flex min-h-9 items-center gap-2 font-medium hover:underline">
                    <PlayerFace id={r.id} shirt={r.shirt} kit={r.team} className="size-8" />
                    {r.name}<span className="text-xs text-muted-foreground">{r.position}</span>
                  </Link>
                </td>
                <td className="p-3 text-right"><Rating value={r.rating} /></td>
                {cfg.cols.map((c) => <td key={c.key} className="p-3 text-right tabular-nums">{r.values[c.key] ?? "—"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t px-4 py-2 text-xs text-muted-foreground">Rating is a Matchlens model score from sensor data, not an official rating.</p>
    </section>
  );
}

function FantasySquad({ matchId, rows }: { matchId: string; rows: PlayerRow[] }) {
  const followed = usePrefs((s) => s.followedPlayers);
  const mine = rows.filter((p) => followed.includes(p.id));
  if (mine.length === 0) return <p className="rounded-xl border bg-card p-6 text-sm text-muted-foreground">Follow players (onboarding or the star on their page) to build your fantasy squad here.</p>;
  return (
    <section aria-label="My players" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {mine.map((p) => {
        const v = p.values;
        return (
          <Link key={p.id} href={routes.player(matchId, p.id)} className="flex items-center gap-4 rounded-xl border bg-card p-4 hover:bg-muted/50">
            <Star aria-hidden className="size-4 fill-current text-graphic-warn" />
            <div className="min-w-0 flex-1">
              <p className="truncate font-semibold">{p.name}</p>
              <p className="text-xs text-muted-foreground">{v.minutes ?? 0}′ · {v.goals ?? 0} G · {v.assists ?? 0} A · xG {v.xg ?? 0}</p>
            </div>
            <p className="font-display text-3xl font-semibold tabular-nums">{v.fantasy ?? 0}<span className="ml-1 text-sm text-muted-foreground">pts</span></p>
          </Link>
        );
      })}
    </section>
  );
}
