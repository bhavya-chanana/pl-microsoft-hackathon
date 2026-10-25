"use client";

import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Download, PenLine, Play, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useScenarios, useScriptCheck } from "@/features/live/queries";
import { ApiError, postJson } from "@/lib/api";
import { Kickoff, type RosterTeam, type Scenarios, type ScriptCheck, Storyline } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { sideTone } from "@/lib/match";
import { routes, serverRoutes } from "@/lib/routes";
import { cn } from "@/lib/utils";

const SPEEDS = [5, 15, 30, 60] as const;
const MAX_PROMPT = 400;
const FULL_TIME = 95;
const Drafted = z.object({ storyline: Storyline });

type Roster = Scenarios["roster"];

function nameOf(roster: Roster, playerId: string): string {
  for (const team of [roster.home, roster.away]) {
    const p = team.players.find((x) => x.player_id === playerId);
    if (p) return p.name;
  }
  return playerId;
}

function teamOf(roster: Roster, teamId: string): RosterTeam | null {
  return roster.home.team_id === teamId ? roster.home : roster.away.team_id === teamId ? roster.away : null;
}

function scriptedScore(story: Storyline, roster: Roster) {
  const goals = story.beats.filter((b) => b.type === "goal");
  return {
    home: goals.filter((b) => b.team_id === roster.home.team_id).length,
    away: goals.filter((b) => b.team_id === roster.away.team_id).length,
  };
}

/** Who the script puts on top, phase by phase: home above the line, away below. */
function DominanceRibbon({ story }: { story: Storyline }) {
  const W = 400;
  const H = 40;
  const x = (m: number) => (Math.min(m, FULL_TIME) / FULL_TIME) * W;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-10 w-full" role="img" aria-label={copy.lab.dominance}>
      <line x1={0} x2={W} y1={H / 2} y2={H / 2} className="stroke-foreground/30" vectorEffect="non-scaling-stroke" />
      <line x1={x(45)} x2={x(45)} y1={0} y2={H} className="stroke-foreground/20" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
      {story.phases.map((p, i) => {
        const lean = (p.home_dominance - 0.5) * 2; // -1 away .. +1 home
        const h = Math.abs(lean) * (H / 2 - 2);
        return (
          <rect
            key={i}
            x={x(p.start_minute)}
            width={Math.max(1, x(p.end_minute) - x(p.start_minute))}
            y={lean >= 0 ? H / 2 - h : H / 2}
            height={Math.max(0.5, h)}
            className={lean >= 0 ? "fill-home" : "fill-away"}
            fillOpacity={0.85}
          />
        );
      })}
    </svg>
  );
}

function BeatTimeline({ story, roster }: { story: Storyline; roster: Roster }) {
  return (
    <ol className="flex flex-col gap-1.5 text-sm">
      {story.beats
        .slice()
        .sort((a, b) => a.minute - b.minute)
        .map((b, i) => {
          const team = teamOf(roster, b.team_id);
          const tone = team?.side === "away" ? "bg-away" : "bg-home";
          return (
            <li key={i} className="flex items-baseline gap-3">
              <span className="w-8 shrink-0 text-right font-display font-semibold tabular-nums">{b.minute}&apos;</span>
              <span aria-hidden className={cn("size-2 shrink-0 translate-y-[-1px] rounded-full", tone)} />
              <span className="min-w-0">
                <span className="font-medium">{copy.lab.beatTypes[b.type] ?? b.type}</span>
                <span className="text-muted-foreground">, {nameOf(roster, b.player_id)}</span>
                {b.related_player_id && <span className="text-muted-foreground"> ({nameOf(roster, b.related_player_id)})</span>}
              </span>
            </li>
          );
        })}
    </ol>
  );
}

function StoryCard({
  story,
  roster,
  onKickoff,
  pending,
  badge,
}: {
  story: Storyline;
  roster: Roster;
  onKickoff: () => void;
  pending: boolean;
  badge?: string;
}) {
  const score = scriptedScore(story, roster);
  return (
    <article className="flex flex-col gap-4 rounded-lg border bg-card p-4 shadow-raised">
      <header className="flex flex-col gap-1">
        {badge && <span className="text-xs font-medium text-muted-foreground">{badge}</span>}
        <h3 className="font-display text-2xl leading-tight font-semibold">{story.title}</h3>
        <p className="max-w-prose text-sm text-muted-foreground">{story.summary}</p>
      </header>
      <div className="flex flex-col gap-1">
        <p className="font-display text-base font-semibold tabular-nums">{copy.lab.goalsScript(score.home, score.away)}</p>
        <div className="flex justify-between gap-3 text-xs text-muted-foreground">
          <span className={sideTone.home.text}>{roster.home.name}</span>
          <span className={cn("text-right", sideTone.away.text)}>{roster.away.name}</span>
        </div>
        <DominanceRibbon story={story} />
      </div>
      <details className="group">
        <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium">
          {copy.lab.beats} ({story.beats.length})
        </summary>
        <div className="pt-2">
          <BeatTimeline story={story} roster={roster} />
        </div>
      </details>
      <Button size="lg" className="mt-auto h-11 gap-2 self-start px-4" onClick={onKickoff} disabled={pending}>
        <Play aria-hidden />
        {pending ? copy.lab.kickingOff : copy.lab.kickoff}
      </Button>
    </article>
  );
}

const STATUS_ICON = { delivered: CheckCircle2, missed: XCircle, pending: CircleDashed } as const;
const STATUS_TONE = { delivered: "text-verified", missed: "text-blocked", pending: "text-muted-foreground" } as const;

function RealityCheck({ check, roster }: { check: ScriptCheck; roster: Roster | null }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-display text-xl font-semibold">{check.title}</p>
        <p className="text-sm font-medium tabular-nums">{copy.lab.delivered(check.delivered, check.total)}</p>
      </div>
      <div aria-hidden className="h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-verified transition-[width] duration-500" style={{ width: `${(check.delivered / Math.max(1, check.total)) * 100}%` }} />
      </div>
      <ol className="flex flex-col gap-1.5 text-sm">
        {check.beats.map((b, i) => {
          const Icon = STATUS_ICON[b.status];
          return (
            <li key={i} className="flex items-center gap-3">
              <Icon aria-hidden className={cn("size-4 shrink-0", STATUS_TONE[b.status])} />
              <span className="w-8 shrink-0 text-right font-display font-semibold tabular-nums">{b.minute}&apos;</span>
              <span className="min-w-0 flex-1">
                {copy.lab.beatTypes[b.type] ?? b.type}
                {roster && <span className="text-muted-foreground">, {nameOf(roster, b.player_id)}</span>}
              </span>
              <span className={cn("shrink-0 text-xs", STATUS_TONE[b.status])}>
                {copy.lab.beatStatus[b.status]}
                {b.minute_label && b.status === "delivered" && ` ${b.minute_label}`}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function ScenarioLab() {
  const scenarios = useScenarios();
  const check = useScriptCheck();
  const [speed, setSpeed] = useState<number>(15);
  const [prompt, setPrompt] = useState("");
  const [draft, setDraft] = useState<Storyline | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const roster = scenarios.data?.roster ?? null;

  const kickoff = useMutation({
    mutationFn: (body: { key: string; preset_id?: string; storyline?: Storyline }) =>
      postJson("scenarios/kickoff", { preset_id: body.preset_id, storyline: body.storyline, speed }, Kickoff),
    onSuccess: (k) =>
      toast.success(copy.lab.kickedOff(k.title), {
        action: { label: copy.lab.watchLive, onClick: () => window.location.assign(routes.matchCentre) },
      }),
    onError: () => toast.error(copy.lab.kickoffFailed),
  });

  const write = useMutation({
    mutationFn: (text: string) => postJson("scenarios/draft", { prompt: text }, Drafted),
    onMutate: () => {
      setProblems([]);
      setDraft(null);
    },
    onSuccess: (d) => setDraft(d.storyline),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 503) return toast.error(copy.lab.draftOffline);
      const found = error instanceof ApiError ? (error.detail as { problems?: unknown } | null)?.problems : null;
      if (Array.isArray(found)) setProblems(found.map(String));
      toast.error(copy.lab.draftFailed);
    },
  });

  const tooLong = prompt.length > MAX_PROMPT;
  const pendingKey = kickoff.isPending ? kickoff.variables?.key : null;

  return (
    <main className="mx-auto flex max-w-screen-2xl flex-col gap-8 px-4 py-4 sm:px-6 lg:py-6">
      <div className="flex flex-col gap-1">
        <h1 className="font-display text-display font-semibold">{copy.lab.heading}</h1>
        <p className="max-w-prose text-sm text-muted-foreground">{copy.lab.intro}</p>
      </div>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="flex min-w-0 flex-col gap-8">
          <section aria-labelledby="presets-heading" className="flex flex-col gap-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h2 id="presets-heading" className="font-display text-xl font-semibold">
                {copy.lab.presets}
              </h2>
              <div className="flex items-center gap-2">
                <span id="speed-label" className="text-sm text-muted-foreground">
                  {copy.lab.speed}
                </span>
                <ToggleGroup
                  aria-labelledby="speed-label"
                  value={[String(speed)]}
                  onValueChange={(v) => v[0] && setSpeed(Number(v[0]))}
                  variant="outline"
                >
                  {SPEEDS.map((s) => (
                    <ToggleGroupItem key={s} value={String(s)} className="h-11 min-w-11 px-2 tabular-nums aria-pressed:bg-primary aria-pressed:text-primary-foreground">
                      {copy.replay.speedValue(s)}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>
            </div>
            {scenarios.isPending ? (
              <div className="grid gap-4 md:grid-cols-2">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-64" />
                ))}
              </div>
            ) : scenarios.isError || !roster ? (
              <p className="text-sm text-muted-foreground">{copy.connection.offlineHelp}</p>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                {scenarios.data.presets.map((p) => (
                  <StoryCard
                    key={p.preset_id}
                    story={p.storyline}
                    roster={roster}
                    pending={pendingKey === p.preset_id}
                    onKickoff={() => kickoff.mutate({ key: p.preset_id, preset_id: p.preset_id })}
                  />
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="custom-heading" className="flex flex-col gap-3">
            <h2 id="custom-heading" className="font-display text-xl font-semibold">
              {copy.lab.custom}
            </h2>
            <p className="max-w-prose text-sm text-muted-foreground">{copy.lab.customHelp}</p>
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (prompt.trim().length >= 8 && !tooLong) write.mutate(prompt.trim());
              }}
            >
              <label htmlFor="scenario-prompt" className="sr-only">
                {copy.lab.custom}
              </label>
              <textarea
                id="scenario-prompt"
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={copy.lab.customPlaceholder}
                aria-invalid={tooLong}
                className="w-full max-w-2xl resize-y rounded-md border border-input bg-background px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" variant="secondary" className="h-11 gap-2 px-4" disabled={write.isPending || prompt.trim().length < 8 || tooLong}>
                  <PenLine aria-hidden />
                  {write.isPending ? copy.lab.drafting : copy.lab.draft}
                </Button>
                <span className={cn("text-xs tabular-nums", tooLong ? "text-destructive" : "text-muted-foreground")}>
                  {prompt.length}/{MAX_PROMPT}
                </span>
              </div>
            </form>
            {write.isPending && <Skeleton className="h-64 max-w-2xl" />}
            {problems.length > 0 && (
              <div role="alert" className="max-w-2xl rounded-lg border border-blocked/40 p-3 text-sm">
                <p className="font-medium">{copy.lab.problems}</p>
                <ul className="list-disc pl-5 text-muted-foreground">
                  {problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </div>
            )}
            {draft && roster && (
              <div className="max-w-2xl">
                <StoryCard
                  story={draft}
                  roster={roster}
                  badge={copy.lab.preview}
                  pending={pendingKey === "draft"}
                  onKickoff={() => kickoff.mutate({ key: "draft", storyline: draft })}
                />
              </div>
            )}
          </section>
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-20 lg:self-start">
          <section aria-labelledby="check-heading" className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
            <div className="flex flex-col gap-0.5">
              <h2 id="check-heading" className="font-display text-xl font-semibold">
                {copy.lab.check}
              </h2>
              <p className="text-xs text-muted-foreground">{copy.lab.checkHelp}</p>
            </div>
            {check.isPending ? (
              <Skeleton className="h-40" />
            ) : check.data ? (
              <RealityCheck check={check.data} roster={roster} />
            ) : (
              <p className="text-sm text-muted-foreground">{copy.lab.noCheck}</p>
            )}
            <Button variant="outline" className="h-11 gap-2 self-start px-4" nativeButton={false} render={<Link href={routes.matchCentre} />}>
              <Play aria-hidden />
              {copy.lab.watchLive}
            </Button>
          </section>
          <section className="flex flex-col gap-2 rounded-lg border bg-card p-4 shadow-raised">
            <h2 className="font-display text-lg font-semibold">{copy.lab.dataset}</h2>
            <p className="text-xs text-muted-foreground">{copy.lab.datasetHelp}</p>
            <Button
              variant="outline"
              className="h-11 gap-2 self-start px-4"
              nativeButton={false}
              render={<a href={serverRoutes.backend("dataset")} download />}
            >
              <Download aria-hidden />
              {copy.lab.dataset}
            </Button>
          </section>
        </aside>
      </div>
    </main>
  );
}
