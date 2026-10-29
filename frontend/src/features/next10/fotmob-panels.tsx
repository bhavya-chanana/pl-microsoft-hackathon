"use client";

import { useMemo, useState } from "react";

import { Markings } from "@/features/broadcast/pitch";
import { Segmented } from "@/components/ui/segmented";
import { cn } from "@/lib/utils";

import { PITCH_X, PITCH_Y } from "./config";
import type { Side } from "./contract";
import { type FotmobDoc, type FotmobEvent, type FotmobShot, type Player, type ShootoutKick, type StatRow, playersOf, statRowsOf } from "./fotmob-contract";
import { Panel } from "./ui";

type Names = Record<Side, string>;

const sideBg = { home: "bg-home", away: "bg-away" } as const;
const sideText = { home: "text-home-text", away: "text-away-text" } as const;

// ---------------------------------------------------------------------------------------------------------------
// Match facts: venue, referee, weather
// ---------------------------------------------------------------------------------------------------------------

export function InfoCard({ doc }: { doc: FotmobDoc }) {
  const h = doc.header;
  if (!h) return null;
  const rows: Array<[string, string]> = [];
  if (h.venue?.name) rows.push(["Venue", [h.venue.name, h.venue.city].filter(Boolean).join(", ")]);
  if (h.venue?.capacity) rows.push(["Capacity", h.venue.capacity.toLocaleString("en-GB")]);
  if (h.attendance) rows.push(["Attendance", h.attendance.toLocaleString("en-GB")]);
  if (h.referee?.name) rows.push(["Referee", [h.referee.name, h.referee.country].filter(Boolean).join(" · ")]);
  if (h.weather) {
    const w = h.weather;
    const parts = [w.temperature_c != null ? `${Math.round(w.temperature_c)}°C` : null, w.description, w.wind_kph != null ? `wind ${Math.round(w.wind_kph)} km/h${w.wind_dir ? ` ${w.wind_dir}` : ""}` : null].filter(Boolean);
    if (parts.length) rows.push(["Weather", parts.join(" · ")]);
  }
  if (h.venue?.surface) rows.push(["Pitch", h.venue.surface]);
  if (rows.length === 0) return null;
  return (
    <Panel title="Match facts">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-[15px] md:grid-cols-3 xl:grid-cols-6">
        {rows.map(([k, v]) => (
          <div key={k} className="flex min-w-0 flex-col">
            <dt className="text-[13px] text-subtle">{k}</dt>
            <dd className="min-w-0 break-words font-medium">{v}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Shot map
// ---------------------------------------------------------------------------------------------------------------

type ShotKind = "goal" | "on_target" | "blocked" | "off_target";

function kindOf(s: FotmobShot): ShotKind {
  if (s.outcome === "goal") return "goal";
  if (s.outcome === "blocked") return "blocked";
  if (s.outcome === "saved" || s.outcome === "post" || s.on_target) return "on_target";
  return "off_target";
}

const kindLabel: Record<ShotKind, string> = { goal: "Goal", on_target: "On target", blocked: "Blocked", off_target: "Off target" };

const radiusOf = (xg: number | null | undefined) => 1.1 + 3.4 * Math.sqrt(Math.min(1, Math.max(0, xg ?? 0.03)));

function shotText(s: FotmobShot, names: Names): string {
  const when = `${Math.floor(s.minute)}${s.added ? `+${Math.floor(s.added)}` : ""}′`;
  const bits = [
    `${s.player ?? "Unknown player"} (${names[s.team]})`,
    when,
    kindLabel[kindOf(s)] + (s.own_goal ? " (own goal)" : ""),
    s.xg != null ? `xG ${s.xg.toFixed(2)}` : null,
    s.xgot ? `xGOT ${s.xgot.toFixed(2)}` : null,
    s.situation && s.situation !== "RegularPlay" ? s.situation.replace(/([a-z])([A-Z])/g, "$1 $2") : null,
    s.shot_type ? s.shot_type.replace(/([a-z])([A-Z])/g, "$1 $2") : null,
  ];
  return bits.filter(Boolean).join(" · ");
}

export function ShotMapPanel({ shots, names }: { shots: FotmobShot[]; names: Names }) {
  const [filter, setFilter] = useState<"both" | Side>("both");
  const [picked, setPicked] = useState<string | number | null>(null);
  const shown = useMemo(() => shots.filter((s) => filter === "both" || s.team === filter).slice().sort((a, b) => a.minute - b.minute), [shots, filter]);
  const sel = shown.find((s) => s.id === picked) ?? null;
  const stats = (side: Side) => {
    const mine = shots.filter((s) => s.team === side);
    return { n: mine.length, on: mine.filter((s) => ["goal", "on_target"].includes(kindOf(s))).length, goals: mine.filter((s) => kindOf(s) === "goal").length, xg: mine.reduce((a, s) => a + (s.xg ?? 0), 0) };
  };
  const step = (dir: 1 | -1) => {
    if (shown.length === 0) return;
    const i = sel ? shown.findIndex((s) => s.id === sel.id) : dir === 1 ? -1 : 0;
    setPicked(shown[(i + dir + shown.length) % shown.length].id);
  };
  return (
    <Panel
      title="Shot map"
      help="Every shot by where it was taken. Bigger means a better chance (xG). Home attacks to the right."
      actions={
        <Segmented
          label="Shots shown"
          size="sm"
          value={filter}
          onChange={(v) => {
            setFilter(v);
            setPicked(null);
          }}
          options={[
            { value: "both", label: "Both" },
            { value: "home", label: "Home" },
            { value: "away", label: "Away" },
          ]}
          className="min-w-0"
        />
      }
    >
      <div className="grid grid-cols-2 gap-2 text-[13px] tnum">
        {(["home", "away"] as const).map((side) => {
          const st = stats(side);
          return (
            <p key={side} className={cn("flex flex-col rounded-lg bg-raised px-3 py-2 ring-1 ring-border", side === "away" && "text-right")}>
              <span className={cn("font-medium", sideText[side])}>{names[side]}</span>
              <span className="text-muted-foreground">{`${st.n} shots · ${st.on} on target · ${st.xg.toFixed(2)} xG`}</span>
            </p>
          );
        })}
      </div>
      <div className="overflow-hidden rounded-xl">
        <svg
          viewBox={`${-PITCH_X - 3} ${-PITCH_Y - 3} ${2 * PITCH_X + 6} ${2 * PITCH_Y + 6}`}
          role="group"
          aria-label={`Shot map, ${shown.length} shots. Use the left and right arrow keys to move from shot to shot.`}
          tabIndex={0}
          className="block h-auto w-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowDown") {
              e.preventDefault();
              step(1);
            } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
              e.preventDefault();
              step(-1);
            } else if (e.key === "Escape") setPicked(null);
          }}
        >
          <defs>
            <pattern id="fm-stripes" x={-PITCH_X} y={-PITCH_Y} width={(2 * PITCH_X) / 6} height={2 * PITCH_Y} patternUnits="userSpaceOnUse">
              <rect width={PITCH_X / 6} height={2 * PITCH_Y} className="fill-turf" />
              <rect x={PITCH_X / 6} width={PITCH_X / 6} height={2 * PITCH_Y} className="fill-turf-stripe" />
            </pattern>
          </defs>
          <rect x={-PITCH_X - 3} y={-PITCH_Y - 3} width={2 * PITCH_X + 6} height={2 * PITCH_Y + 6} fill="url(#fm-stripes)" />
          <g transform={`translate(${-PITCH_X} ${-PITCH_Y})`} aria-hidden>
            <Markings />
          </g>
          {shown
            .slice()
            .sort((a, b) => (kindOf(a) === "goal" ? 1 : 0) - (kindOf(b) === "goal" ? 1 : 0))
            .map((s) => {
              const r = radiusOf(s.xg);
              const k = kindOf(s);
              const fill = s.team === "home" ? "fill-home" : "fill-away";
              const stroke = s.team === "home" ? "stroke-home" : "stroke-away";
              const on = sel?.id === s.id;
              return (
                <g key={s.id} className="cursor-pointer" onClick={() => setPicked(on ? null : s.id)}>
                  <circle cx={s.x} cy={s.y} r={Math.max(r, 2.6) + 0.8} className="fill-transparent" />
                  {k === "goal" && <circle cx={s.x} cy={s.y} r={r + 1.1} className="fill-none stroke-graphic-foreground" strokeWidth={0.5} />}
                  {k === "blocked" ? (
                    <rect x={s.x - r * 0.9} y={s.y - r * 0.9} width={r * 1.8} height={r * 1.8} rx={0.3} className={cn("fill-none", stroke)} strokeWidth={0.55} />
                  ) : k === "off_target" ? (
                    <circle cx={s.x} cy={s.y} r={r} className={cn("fill-none", stroke)} strokeWidth={0.55} strokeDasharray="0.9 0.7" />
                  ) : (
                    <circle cx={s.x} cy={s.y} r={r} className={cn(fill, "stroke-graphic-foreground")} fillOpacity={k === "goal" ? 1 : 0.6} strokeWidth={k === "goal" ? 0.4 : 0.25} />
                  )}
                  {on && <circle cx={s.x} cy={s.y} r={r + 2.4} className="fill-none stroke-graphic-foreground" strokeWidth={0.5} strokeDasharray="0.8 0.6" />}
                  <title>{shotText(s, names)}</title>
                </g>
              );
            })}
        </svg>
      </div>
      <p role="status" aria-live="polite" className={cn("min-h-11 rounded-lg px-3 py-2 text-[13px] ring-1 ring-border", sel ? "bg-raised text-foreground" : "text-subtle")}>
        {sel ? shotText(sel, names) : shown.length ? "Tap a shot, or use the arrow keys, for the player, minute and xG." : "No shots yet."}
      </p>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-muted-foreground" aria-label="Key to the shot map">
        <li className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="16" height="16"><circle cx="8" cy="8" r="5" className="fill-foreground" /><circle cx="8" cy="8" r="7" className="fill-none stroke-foreground" strokeWidth="1" /></svg>
          Goal
        </li>
        <li className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="14" height="14"><circle cx="7" cy="7" r="5" className="fill-foreground" fillOpacity={0.55} /></svg>
          On target
        </li>
        <li className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="14" height="14"><rect x="2" y="2" width="10" height="10" rx="1" className="fill-none stroke-foreground" strokeWidth="1.5" /></svg>
          Blocked
        </li>
        <li className="inline-flex items-center gap-1.5">
          <svg aria-hidden width="14" height="14"><circle cx="7" cy="7" r="5" className="fill-none stroke-foreground" strokeWidth="1.5" strokeDasharray="2.4 1.8" /></svg>
          Off target
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block size-2.5 rounded-full bg-home" />
          {names.home}
        </li>
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="inline-block size-2.5 rounded-full bg-away" />
          {names.away}
        </li>
      </ul>
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------------------------------------------

function minuteOf(e: FotmobEvent): string {
  const m = Math.floor(e.minute);
  return `${m}${e.added ? `+${Math.floor(e.added)}` : ""}′`;
}

function eventLine(e: FotmobEvent): { kind: string; text: string } {
  if (e.type === "goal") {
    const kind = e.own_goal ? "Own goal" : e.penalty ? "Penalty" : "Goal";
    const assist = e.assist?.name ? e.assist.name.replace(/^assist by /i, "") : null;
    return { kind, text: `${e.player?.name ?? ""}${assist ? ` (assist ${assist})` : ""}${e.score && e.score.length === 2 ? ` · ${e.score[0]}–${e.score[1]}` : ""}`.trim() };
  }
  if (e.type === "card") return { kind: e.card === "yellow" ? "Yellow card" : "Red card", text: `${e.player?.name ?? ""}${e.reason ? ` · ${e.reason}` : ""}`.trim() };
  if (e.type === "sub") return { kind: "Substitution", text: `${e.player_in?.name ?? "?"} on for ${e.player_out?.name ?? "?"}${e.injury ? " (injury)" : ""}` };
  return { kind: e.label ?? e.type, text: "" };
}

export function EventsPanel({ events, names }: { events: FotmobEvent[]; names: Names }) {
  const list = events.slice().sort((a, b) => b.minute + (b.added ?? 0) / 60 - (a.minute + (a.added ?? 0) / 60));
  return (
    <Panel title="Events" help="Goals, cards and substitutions, newest first.">
      <ol tabIndex={0} aria-label="Match events" className="flex max-h-[34rem] flex-col gap-1 overflow-y-auto rounded-lg pr-1 outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {list.map((e, i) => {
          if (e.type === "half") {
            return (
              <li key={i} className="my-1 flex items-center gap-3 text-[13px] text-subtle">
                <span className="h-px flex-1 bg-border" />
                {e.label ?? "Half"}
                <span className="h-px flex-1 bg-border" />
              </li>
            );
          }
          const { kind, text } = eventLine(e);
          const side = e.team;
          return (
            <li key={i} className="grid grid-cols-[3.25rem_1fr] items-start gap-2 rounded-lg px-1 py-1.5">
              <span className="font-display text-base font-semibold tnum text-muted-foreground">{minuteOf(e)}</span>
              <span className="flex min-w-0 flex-col">
                <span className="flex flex-wrap items-center gap-x-2 text-[15px] font-medium">
                  {side && <span aria-hidden className={cn("inline-block h-4 w-1.5 shrink-0 rounded-full", sideBg[side])} />}
                  <span className={cn(e.type === "goal" && "font-semibold")}>{kind}</span>
                  {side && <span className={cn("text-[13px] font-normal", sideText[side])}>{names[side]}</span>}
                </span>
                {text && <span className="text-[13px] text-muted-foreground">{text}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------------------------------------------

const PERIODS = [
  { value: "all", label: "All" },
  { value: "first_half", label: "1st half" },
  { value: "second_half", label: "2nd half" },
] as const;

function PairedRow({ row }: { row: StatRow }) {
  const h = typeof row.home === "number" ? row.home : Number.parseFloat(String(row.home ?? ""));
  const a = typeof row.away === "number" ? row.away : Number.parseFloat(String(row.away ?? ""));
  const ok = Number.isFinite(h) && Number.isFinite(a) && h + a > 0;
  const share = ok ? h / (h + a) : 0.5;
  const text = (t: string | null | undefined, v: unknown) => t ?? (v == null ? "–" : String(v));
  const homeText = text(row.home_text, row.home);
  const awayText = text(row.away_text, row.away);
  return (
    <li className="flex flex-col gap-1 py-2">
      <div className="grid grid-cols-[1fr_auto_1fr] items-baseline gap-3 text-[15px]">
        <span className={cn("font-display text-lg tnum", row.highlighted === "home" ? "font-bold" : "font-medium text-muted-foreground")}>{homeText}</span>
        <span className="text-center text-[13px] text-muted-foreground">{row.title}</span>
        <span className={cn("text-right font-display text-lg tnum", row.highlighted === "away" ? "font-bold" : "font-medium text-muted-foreground")}>{awayText}</span>
      </div>
      {ok && (
        <div aria-hidden className="flex h-1.5 gap-0.5 overflow-hidden rounded-full">
          <span className="rounded-l-full bg-home" style={{ width: `${share * 100}%`, opacity: row.highlighted === "away" ? 0.55 : 1 }} />
          <span className="rounded-r-full bg-away" style={{ width: `${(1 - share) * 100}%`, opacity: row.highlighted === "home" ? 0.55 : 1 }} />
        </div>
      )}
    </li>
  );
}

export function StatsPanel({ stats, names }: { stats: NonNullable<FotmobDoc["stats"]>; names: Names }) {
  const [period, setPeriod] = useState<(typeof PERIODS)[number]["value"]>("all");
  const available = PERIODS.filter((p) => (stats[p.value] ?? []).length > 0);
  const groups = stats[period] ?? stats.all ?? [];
  if (available.length === 0) return null;
  return (
    <Panel
      title="Match stats"
      actions={available.length > 1 ? <Segmented label="Period" size="sm" value={period} onChange={setPeriod} options={available.map((p) => ({ value: p.value, label: p.label }))} className="min-w-0" /> : undefined}
    >
      <div className="grid grid-cols-[1fr_1fr] gap-3 text-sm font-medium">
        <span className={cn("flex items-center gap-2", sideText.home)}>
          <span aria-hidden className="h-3 w-1.5 rounded-full bg-home" />
          {names.home}
        </span>
        <span className={cn("flex items-center justify-end gap-2 text-right", sideText.away)}>
          {names.away}
          <span aria-hidden className="h-3 w-1.5 rounded-full bg-away" />
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {groups.map((g, gi) => {
          const rows = statRowsOf(g);
          if (rows.length === 0) return null;
          const top = g.key === "top_stats" || gi === 0;
          return (
            <details key={g.title} open={top} className="group rounded-xl border bg-background/40 px-3">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-[15px] font-semibold [&::-webkit-details-marker]:hidden">
                {g.title}
                <span aria-hidden className="text-subtle transition-transform group-open:rotate-180">⌄</span>
              </summary>
              <ul className="divide-y pb-1">
                {rows.map((r) => (
                  <PairedRow key={`${r.key ?? r.title}`} row={r} />
                ))}
              </ul>
            </details>
          );
        })}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Lineups
// ---------------------------------------------------------------------------------------------------------------

function ratingBg(r: number): string {
  return r >= 7 ? "bg-[#14804a]" : r >= 6 ? "bg-[#8a6200]" : "bg-[#b3261e]";
}

function surname(name: string): string {
  const parts = name.trim().split(/\s+/);
  return parts.length > 1 ? parts.slice(1).join(" ") : name;
}

function PlayerNode({ p, side, colour }: { p: Player; side: Side; colour: string | null }) {
  const l = p.layout!;
  return (
    <li className="absolute flex w-[25%] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-0.5" style={{ left: `${l.y * 100}%`, top: `${(1 - l.x) * 100}%` }}>
      <span className="relative">
        <span className={cn("grid size-8 place-items-center rounded-full font-display text-sm font-bold text-white ring-2 ring-white", !colour && sideBg[side])} style={colour ? { backgroundColor: colour } : undefined}>
          {p.shirt ?? ""}
        </span>
        {(p.goals ?? 0) > 0 && <span aria-label={`${p.goals} goals`} className="absolute -top-1.5 -right-2 grid size-4 place-items-center rounded-full bg-white text-[10px] leading-none text-black">⚽</span>}
        {(p.yellow_cards ?? 0) > 0 && <span aria-label="Yellow card" className="absolute -bottom-0.5 -left-1.5 h-3.5 w-2.5 rounded-[2px] bg-[#e9b200] ring-1 ring-black/40" />}
        {(p.red_cards ?? 0) > 0 && <span aria-label="Red card" className="absolute -bottom-0.5 -left-1.5 h-3.5 w-2.5 rounded-[2px] bg-[#d6322b] ring-1 ring-black/40" />}
      </span>
      <span className="max-w-full truncate rounded bg-black/55 px-1 text-[11px] leading-4 font-medium text-white">{surname(p.name)}</span>
      {p.rating != null && <span className={cn("rounded px-1 text-[11px] leading-4 font-bold text-white tnum", ratingBg(p.rating))}>{p.rating.toFixed(1)}</span>}
    </li>
  );
}

function LineupPitch({ starters, side, colour }: { starters: Player[]; side: Side; colour: string | null }) {
  const placed = starters.filter((p) => p.layout);
  return (
    <div className="relative mx-auto aspect-[68/100] w-full max-w-md overflow-hidden rounded-xl bg-turf">
      <svg aria-hidden viewBox="0 0 68 100" preserveAspectRatio="none" className="absolute inset-0 size-full">
        <rect width="68" height="100" className="fill-turf" />
        {Array.from({ length: 5 }, (_, i) => (
          <rect key={i} y={i * 20 + 10} width="68" height="10" className="fill-turf-stripe" />
        ))}
        <g className="fill-none stroke-graphic-foreground" strokeOpacity={0.45} strokeWidth="0.4">
          <rect x="1.5" y="1.5" width="65" height="97" />
          <line x1="1.5" x2="66.5" y1="50" y2="50" />
          <circle cx="34" cy="50" r="8" />
          <rect x="14" y="1.5" width="40" height="15.5" />
          <rect x="24" y="1.5" width="20" height="5.5" />
          <rect x="14" y="83" width="40" height="15.5" />
          <rect x="24" y="93" width="20" height="5.5" />
        </g>
      </svg>
      <ul className="absolute inset-0">
        {placed.map((p) => (
          <PlayerNode key={`${p.id}-${p.name}`} p={p} side={side} colour={colour} />
        ))}
      </ul>
    </div>
  );
}

export function LineupsPanel({ doc, names }: { doc: FotmobDoc; names: Names }) {
  const [side, setSide] = useState<Side>("home");
  const lineup = doc.lineups?.[side];
  if (!doc.lineups || (!doc.lineups.home && !doc.lineups.away)) return null;
  const starters = playersOf(lineup?.starters);
  const subs = playersOf(lineup?.subs);
  return (
    <Panel
      title="Lineups"
      help={lineup ? `${names[side]}${lineup.formation ? ` · ${lineup.formation}` : ""}${lineup.rating != null ? ` · team rating ${lineup.rating.toFixed(1)}` : ""}` : undefined}
      actions={
        <Segmented
          label="Team"
          size="sm"
          value={side}
          onChange={setSide}
          options={[
            { value: "home", label: <span className="truncate">{names.home}</span> },
            { value: "away", label: <span className="truncate">{names.away}</span> },
          ]}
          className="w-full min-w-0 sm:w-auto sm:max-w-[26rem]"
        />
      }
    >
      {starters.length === 0 ? (
        <p className="rounded-xl border border-dashed px-4 py-6 text-center text-[15px] text-muted-foreground">The lineup has not been announced yet.</p>
      ) : (
        <div className="grid items-start gap-5 md:grid-cols-[minmax(0,26rem)_1fr]">
          <LineupPitch starters={starters} side={side} colour={null} />
          <div className="flex flex-col gap-4">
            {lineup?.coach?.name && (
              <p className="text-[15px]">
                <span className="text-muted-foreground">Coach </span>
                <span className="font-medium">{lineup.coach.name}</span>
              </p>
            )}
            {subs.length > 0 && (
              <div className="flex flex-col gap-1">
                <h3 className="text-sm font-semibold">Substitutes</h3>
                <ul className="divide-y">
                  {subs.map((p) => (
                    <li key={`${p.id}-${p.name}`} className="flex min-h-11 items-center gap-3 text-[15px]">
                      <span className="w-7 text-center font-display text-base font-semibold text-muted-foreground tnum">{p.shirt ?? ""}</span>
                      <span className="min-w-0 flex-1 truncate">{p.name}</span>
                      {p.position && <span className="text-[13px] text-subtle">{p.position}</span>}
                      {p.rating != null && <span className={cn("rounded px-1.5 py-0.5 text-[12px] font-bold text-white tnum", ratingBg(p.rating))}>{p.rating.toFixed(1)}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Smaller cards: attacking zones, form, head to head
// ---------------------------------------------------------------------------------------------------------------

export function ZonesCard({ doc, names }: { doc: FotmobDoc; names: Names }) {
  const z = doc.attacking_zones;
  const rows = (["home", "away"] as const).flatMap((s) => (z?.[s]?.total ? [{ side: s, t: z[s]!.total! }] : []));
  if (rows.length === 0) return null;
  return (
    <Panel title="Where they attack" help="Share of attacks down each third of the pitch.">
      <div className="flex flex-col gap-4">
        {rows.map(({ side, t }) => {
          const total = Math.max(1, t.left + t.center + t.right);
          const parts = [
            ["Left", t.left],
            ["Centre", t.center],
            ["Right", t.right],
          ] as const;
          return (
            <div key={side} className="flex flex-col gap-1.5">
              <span className={cn("text-sm font-medium", sideText[side])}>{names[side]}</span>
              <div className="flex h-7 gap-0.5 overflow-hidden rounded-lg" role="img" aria-label={`${names[side]}: ${parts.map(([n, v]) => `${n} ${v}%`).join(", ")}`}>
                {parts.map(([n, v], i) => (
                  <span key={n} className={cn("grid min-w-0 place-items-center text-[12px] font-bold text-white tnum", sideBg[side])} style={{ width: `${(v / total) * 100}%`, opacity: [0.7, 1, 0.85][i] }}>
                    {v}%
                  </span>
                ))}
              </div>
              <div className="flex text-[12px] text-subtle" aria-hidden>
                {parts.map(([n, v]) => (
                  <span key={n} className="text-center" style={{ width: `${(v / total) * 100}%` }}>
                    {n}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

const resultName: Record<string, string> = { W: "Win", D: "Draw", L: "Loss" };
const resultTone: Record<string, string> = { W: "bg-[#14804a]", D: "bg-[#6b7280]", L: "bg-[#b3261e]" };

export function FormCard({ doc, names }: { doc: FotmobDoc; names: Names }) {
  const f = doc.team_form;
  const sides = (["home", "away"] as const).filter((s) => (f?.[s] ?? []).length > 0);
  if (sides.length === 0) return null;
  return (
    <Panel title="Recent form" help="Last five results, newest first.">
      <div className="flex flex-col gap-4">
        {sides.map((side) => (
          <div key={side} className="flex flex-col gap-1">
            <span className={cn("text-sm font-medium", sideText[side])}>{names[side]}</span>
            <ul className="flex flex-col divide-y">
              {(f?.[side] ?? []).map((m, i) => (
                <li key={i} className="flex min-h-9 items-center gap-3 text-[14px]">
                  <span aria-label={resultName[m.result] ?? m.result} className={cn("grid size-6 shrink-0 place-items-center rounded text-[12px] font-bold text-white", resultTone[m.result] ?? "bg-raised")}>
                    {m.result}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{`${m.venue === "A" ? "at" : "v"} ${m.opponent?.name ?? "?"}`}</span>
                  <span className="font-display text-base font-semibold tnum">{m.score ?? `${m.goals_for ?? "?"} - ${m.goals_against ?? "?"}`}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}

export function H2HCard({ doc, names }: { doc: FotmobDoc; names: Names }) {
  const h = doc.h2h;
  const s = h?.summary;
  const last = h?.last ?? [];
  if (!s && last.length === 0) return null;
  const total = s ? Math.max(1, s.home_wins + s.draws + s.away_wins) : 1;
  return (
    <Panel title="Head to head" help="All meetings, and the latest five.">
      {s && (
        <div className="flex flex-col gap-1.5">
          <div className="flex h-7 gap-0.5 overflow-hidden rounded-lg" role="img" aria-label={`${names.home} ${s.home_wins} wins, ${s.draws} draws, ${names.away} ${s.away_wins} wins`}>
            {[
              [s.home_wins, "bg-home"],
              [s.draws, "bg-muted-foreground"],
              [s.away_wins, "bg-away"],
            ].map(([n, c], i) => (
              <span key={i} className={cn("grid min-w-0 place-items-center text-[12px] font-bold text-white tnum", c as string)} style={{ width: `${((n as number) / total) * 100}%` }}>
                {n as number}
              </span>
            ))}
          </div>
          <div className="flex justify-between gap-2 text-[12px] text-subtle">
            <span className="truncate">{names.home} wins</span>
            <span>Draws</span>
            <span className="truncate text-right">{names.away} wins</span>
          </div>
        </div>
      )}
      {last.length > 0 && (
        <ul className="divide-y">
          {last.map((m, i) => (
            <li key={i} className="grid min-h-11 grid-cols-[1fr_auto] items-center gap-3 py-1.5 text-[14px]">
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{`${m.home?.name ?? "?"} v ${m.away?.name ?? "?"}`}</span>
                <span className="text-[12px] text-subtle">{[m.date ? new Date(m.date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : null, m.competition].filter(Boolean).join(" · ")}</span>
              </span>
              <span className="font-display text-base font-semibold tnum">{m.score ?? `${m.home_goals ?? "?"} - ${m.away_goals ?? "?"}`}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// Penalty shootout
// ---------------------------------------------------------------------------------------------------------------

/** Kick by kick, each team on its own row: a tick for a goal, a cross for a miss or a save. */
export function ShootoutPanel({ kicks, names, summary }: { kicks: ShootoutKick[]; names: Names; summary: string }) {
  return (
    <Panel title="Penalty shootout" help={summary}>
      <div className="flex flex-col gap-3">
        {(["home", "away"] as const).map((side) => {
          const mine = kicks.filter((k) => k.team_side === side);
          const goals = mine.filter((k) => k.scored).length;
          return (
            <div key={side} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className={cn("min-w-0 text-sm font-medium", sideText[side])}>{names[side]}</span>
                <span className="font-display text-lg font-semibold tnum">{goals}</span>
              </div>
              <ol className="flex flex-wrap gap-1.5" aria-label={`${names[side]} penalties in order`}>
                {mine.map((k, i) => {
                  const scored = k.scored;
                  const what = scored ? "Scored" : "Missed or saved";
                  return (
                    <li
                      key={k.order}
                      title={`${i + 1}. ${k.player?.name ?? "Unknown player"}: ${what}`}
                      aria-label={`Kick ${i + 1}, ${k.player?.name ?? "unknown player"}: ${what}`}
                      className={cn("grid size-9 place-items-center rounded-full text-sm font-bold ring-2", scored ? cn(sideBg[side], "text-white ring-transparent") : "bg-transparent text-muted-foreground ring-border")}
                    >
                      <span aria-hidden>{scored ? "✓" : "✕"}</span>
                    </li>
                  );
                })}
              </ol>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
