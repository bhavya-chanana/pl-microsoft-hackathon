"use client";

import { Hammer, LoaderCircle, Sparkles } from "lucide-react";
import { useMemo, useState, useSyncExternalStore } from "react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { TextShimmer } from "@/components/ui/text-shimmer";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useCreateBoard, useTacticsBoard } from "@/features/live/queries";
import { useProfile } from "@/features/profile/profile-store";
import { ApiError } from "@/lib/api";
import { BASELINE_ID, type BoardRequest, type BoardSource, type RolloutVariant, type TacticResult, type TacticsBoard } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { AgentTrace } from "./agent-trace";
import { BranchCompare, type CompareBranch } from "./branch-compare";
import { FIXTURE_MOMENTS, FIXTURE_TEAMS } from "./constants";
import { GhostPitch } from "./ghost-pitch";
import { labelOf, rosterOf } from "./lib";
import { type MomentOption, MomentPicker, type TeamOption } from "./moment-picker";
import { OutcomeBar } from "./outcome-bar";
import { RolloutStage } from "./rollout-stage";
import { type PromptSettings, MAX_PROMPT, ScenarioPrompt } from "./scenario-prompt";
import { BaselineCard, TacticCard } from "./tactic-card";

const t = copy.tactics;
const DEFAULT_MAX_ELAPSED = 5400;

/** Insights worth freezing: the most important ones so far, newest first. */
function useKeyMoments(enabled: boolean): MomentOption[] {
  const insights = useLive((s) => s.insights);
  return useMemo(() => {
    if (!enabled) return FIXTURE_MOMENTS;
    return Object.values(insights)
      .filter((i) => i.stage !== "retracted")
      .sort((a, b) => b.priority - a.priority)
      .slice(0, 8)
      .sort((a, b) => b.elapsed - a.elapsed)
      .map((i) => ({ insight_id: i.insight_id, minute_label: i.minute_label, headline: i.headline, elapsed: i.elapsed }));
  }, [insights, enabled]);
}

function SituationBar({ board, teams }: { board: Pick<TacticsBoard, "situation">; teams: TeamOption[] }) {
  const s = board.situation;
  const team = (id: string) => teams.find((x) => x.team_id === id);
  const sides = teams.length ? teams : Object.keys(s.score).map((id): TeamOption => ({ team_id: id, name: id, side: "home" }));
  return (
    <section aria-labelledby="situation-heading" className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border bg-card px-4 py-3 shadow-raised">
      <h2 id="situation-heading" className="sr-only">
        {t.situation.heading}
      </h2>
      <p className="font-display text-2xl font-semibold tabular-nums">{s.minute_label}</p>
      <ul className="flex items-center gap-3 font-display text-lg font-semibold tabular-nums" aria-label={t.situation.score}>
        {sides.map((x) => (
          <li key={x.team_id} className="flex items-center gap-1.5">
            <span aria-hidden className={cn("size-2.5 rounded-full", sideTone[x.side].bg)} />
            <span className="sr-only">{x.name} </span>
            <span aria-hidden className="text-sm font-medium text-muted-foreground">{x.name}</span>
            {s.score[x.team_id] ?? 0}
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted-foreground">{labelOf(t.situation.phases, s.phase)}</p>
      <p className="text-sm text-muted-foreground">{t.situation.inPossession(team(s.possession_team_id)?.name ?? s.possession_team_id)}</p>
      <p className="ml-auto text-sm font-medium">{t.situation.coaching(team(s.coaching_team_id)?.name ?? s.coaching_team_id)}</p>
    </section>
  );
}

/** True when the recommended idea's shot and concede ranges overlap the baseline's: no real difference to act on. */
function withinNoise(board: TacticsBoard): boolean {
  const base = board.baseline;
  const pick = board.tactics.find((x) => x.tactic_id === board.verdict?.recommended_tactic_id)?.outcome;
  if (!base || !pick) return false;
  const overlaps = (a: { low: number; high: number }, b: { low: number; high: number }) => a.low <= b.high && b.low <= a.high;
  return overlaps(pick.p_shot, base.p_shot) && overlaps(pick.p_concede_shot, base.p_concede_shot);
}

function VerdictPanel({ board, titleOf, onShow }: { board: TacticsBoard; titleOf: (id: string) => string; onShow: (id: string) => void }) {
  const v = board.verdict;
  const noise = v ? withinNoise(board) : false;
  if (!v) {
    return board.status === "failed" ? null : (
      <section aria-label={t.verdict.heading} className="flex flex-col gap-2 rounded-lg border bg-card p-4 shadow-raised">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </section>
    );
  }
  return (
    <section aria-labelledby="verdict-heading" className="flex flex-col gap-3 rounded-lg border border-verified/40 bg-card p-4 shadow-raised">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="verdict-heading" className="font-display text-xl font-semibold">
          {t.verdict.heading}: {noise ? t.verdict.noClearWinner : v.headline}
        </h2>
      </div>
      <p className="max-w-prose text-sm">{noise ? t.verdict.withinNoise : v.text}</p>
      <div className="flex flex-wrap items-center gap-2">
        {v.recommended_tactic_id && !noise ? (
          <Button variant="secondary" className="h-11 gap-2 px-4" onClick={() => onShow(v.recommended_tactic_id!)}>
            <Sparkles aria-hidden />
            {t.verdict.show}: {titleOf(v.recommended_tactic_id)}
          </Button>
        ) : (
          <p className="text-sm text-muted-foreground">{t.verdict.none}</p>
        )}
        {v.cited_metrics.length > 0 && (
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            {t.verdict.cited}
            {v.cited_metrics.map((m) => (
              <Badge key={m} variant="outline">
                {labelOf(t.outcome.metrics, m)}
              </Badge>
            ))}
          </p>
        )}
      </div>
    </section>
  );
}

function EmptyStage() {
  return (
    <EmptyState title={t.stage.emptyHeading}>{t.stage.empty}</EmptyState>
  );
}

function errorText(error: unknown): string {
  if (error instanceof ApiError && (error.status === 502 || error.status === 503)) return t.createOffline;
  return error instanceof ApiError && error.status === 422 ? error.message : t.createFailed;
}

/** The tactics room: set the scene, watch ideas arrive and get scored, then play any of them out on the pitch. */
export function TacticsBoardView({ fixture }: { fixture: boolean }) {
  const meta = useLive((s) => s.meta);
  const clock = useLive((s) => s.state?.elapsed);
  const language = useProfile((s) => s.language);
  const moments = useKeyMoments(!fixture);

  const teams = useMemo<TeamOption[]>(() => {
    if (fixture) return FIXTURE_TEAMS;
    return meta ? [meta.home, meta.away].map((x) => ({ team_id: x.team_id, name: x.name, side: x.side })) : [];
  }, [fixture, meta]);
  const maxElapsed = fixture ? 4260 : (clock ?? DEFAULT_MAX_ELAPSED);

  const [source, setSource] = useState<BoardSource>({ kind: "live" });
  const [pickedTeam, setPickedTeam] = useState<string | null>(null);
  const [settings, setSettings] = useState<PromptSettings>({ prompt: "", risk: "balanced", n: 4, horizon: 10 });
  const [boardId, setBoardId] = useState<string | null>(null);
  const [pickedBranch, setPickedBranch] = useState<string | null>(null);
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [comparing, setComparing] = useState<string | null>(null);
  const [variant, setVariant] = useState<RolloutVariant>("typical");

  const teamId = pickedTeam ?? teams[0]?.team_id ?? null;
  const create = useCreateBoard(fixture);
  const query = useTacticsBoard(boardId);
  const board = query.data ?? null;
  const situation = board?.situation ?? create.data?.situation ?? null;

  const tooLong = settings.prompt.length > MAX_PROMPT;
  const canBuild = Boolean(teamId) && !tooLong && !create.isPending;

  const build = () => {
    if (!teamId) return;
    const request: BoardRequest = {
      source,
      team_id: teamId,
      prompt: settings.prompt.trim() || undefined,
      n: settings.n,
      horizon_s: settings.horizon,
      risk: settings.risk,
      language,
    };
    create.mutate(request, {
      onSuccess: (created) => {
        setBoardId(created.board_id);
        setPickedBranch(null);
        setComparing(null);
        setCompareWith(null);
      },
    });
  };

  const roster = useMemo(() => (situation ? rosterOf(situation) : null), [situation]);
  const tactics = useMemo(() => board?.tactics ?? [], [board]);
  const byId = useMemo(() => new Map(tactics.map((x) => [x.tactic_id, x])), [tactics]);
  const done = board?.status === "done";
  const working = create.isPending || Boolean(boardId && board && !done && board.status !== "failed");

  const ordered = useMemo<TacticResult[]>(() => {
    if (!done) return tactics;
    return [...tactics].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  }, [tactics, done]);

  const recommended = board?.verdict && !withinNoise(board) ? (board.verdict.recommended_tactic_id ?? null) : null;
  const firstReady = ordered.find((x) => x.status === "ready")?.tactic_id ?? null;
  const validPick = pickedBranch === BASELINE_ID ? Boolean(board?.baseline) : pickedBranch && byId.get(pickedBranch)?.status === "ready";
  const activeId = validPick ? pickedBranch : recommended && byId.get(recommended)?.status === "ready" ? recommended : firstReady;

  const titleOf = (id: string) => (id === BASELINE_ID ? t.cards.baselineTitle : (byId.get(id)?.intent.title ?? ""));
  const branchOf = (id: string): CompareBranch => {
    const x = byId.get(id);
    const ids = x ? [...new Set([...x.intent.directives.map((d) => d.player_id), ...x.intent.ball_plan.flatMap((b) => [b.from_player_id, b.to_player_id].filter((p): p is string => Boolean(p)))])] : [];
    return { id, title: titleOf(id), highlightIds: ids, stats: id === BASELINE_ID ? (board?.baseline ?? null) : (x?.outcome ?? null) };
  };

  const compareOptions = [
    { id: BASELINE_ID, title: t.cards.baselineTitle },
    ...ordered.filter((x) => x.status === "ready").map((x) => ({ id: x.tactic_id, title: x.intent.title })),
  ];
  const startCompare = (leftId: string) => {
    const right = leftId === BASELINE_ID ? (firstReady ?? BASELINE_ID) : BASELINE_ID;
    setPickedBranch(leftId);
    setCompareWith(right);
    setComparing(leftId);
  };
  const exitCompare = () => setComparing(null);
  const compareLeft = comparing && comparing === activeId ? comparing : null;
  const rightId = compareWith && compareWith !== compareLeft ? compareWith : BASELINE_ID;

  const show = (id: string) => {
    setPickedBranch(id);
    setComparing(null);
  };

  const active = activeId ? branchOf(activeId) : null;
  const activeStats = active?.stats ?? null;
  const canCompare = Boolean(board?.baseline) && compareOptions.length > 1;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="sr-only">{t.heading}</h1>
          {fixture && <Badge className="bg-rewritten/15 text-rewritten">{t.fixtureBanner}</Badge>}
        </div>
        <p className="max-w-prose text-sm text-muted-foreground">{t.intro}</p>
      </div>

      <div className="flex flex-col gap-6">
        <form
          aria-label={t.setup}
          className="flex min-w-0 flex-col gap-5 rounded-2xl border bg-card p-4 shadow-raised sm:p-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (canBuild) build();
          }}
        >
          <ScenarioPrompt
            {...settings}
            onChange={(next) => setSettings((s) => ({ ...s, ...next }))}
            extra={<MomentPicker source={source} onSource={setSource} moments={moments} maxElapsed={maxElapsed} teams={teams} teamId={teamId} onTeam={setPickedTeam} />}
          />
          <div className="flex flex-col gap-3">
            <Button type="submit" size="lg" className="h-12 w-full gap-2 px-6 text-base sm:w-fit" disabled={!canBuild || working}>
              {working ? <LoaderCircle aria-hidden className="motion-safe:animate-spin" /> : <Hammer aria-hidden />}
              {working ? t.generating : boardId ? t.rebuild : t.goalFirst.build}
            </Button>
            {working && (
              <div role="status" aria-live="polite" className="flex flex-col gap-2">
                <div className="relative h-1.5 w-full max-w-md overflow-hidden rounded-full bg-raised">
                  <span className="absolute inset-y-0 w-1/3 rounded-full bg-primary motion-safe:animate-[indeterminate_1.4s_ease-in-out_infinite]" />
                </div>
                <TextShimmer as="p" className="text-sm tabular-nums" duration={2.2}>
                  {t.goalFirst.testing(board?.tactics.filter((x) => x.status === "ready" || x.status === "rejected").length ?? 0, board?.tactics.length ?? 0)}
                </TextShimmer>
              </div>
            )}
          </div>
          {!teamId && <p className="text-sm text-muted-foreground">{t.noMatch}</p>}
          {create.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorText(create.error)}
            </p>
          )}
        </form>

        <div className="flex min-w-0 flex-col gap-6">
          {!situation || !roster ? (
            <EmptyStage />
          ) : (
            <>
              <SituationBar board={{ situation }} teams={teams} />
              {board && <AgentTrace board={board} />}
              {query.isError && !board && (
                <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-blocked/40 p-4 text-sm">
                  {t.stage.loadFailed}
                  <Button variant="outline" className="h-11 px-4" onClick={() => void query.refetch()}>
                    {t.stage.retry}
                  </Button>
                </div>
              )}

              {comparing && compareLeft && board ? (
                <BranchCompare
                    boardId={board.board_id}
                    situation={situation}
                    left={branchOf(compareLeft)}
                    right={branchOf(rightId)}
                    options={compareOptions}
                    onRight={setCompareWith}
                    variant={variant}
                    onVariant={setVariant}
                    onExit={exitCompare}
                />
              ) : null}

              <div className={cn("grid items-start gap-6", !comparing && "xl:grid-cols-[minmax(0,1fr)_24rem]")}>
                {!comparing && (
                  <div className="flex min-w-0 flex-col gap-4 xl:sticky xl:top-[calc(var(--stack-h)+1rem)]">
                    {board && active ? (
                      <>
                        <div className="rounded-lg border bg-card p-4 shadow-raised">
                          <RolloutStage
                            boardId={board.board_id}
                            situation={situation}
                            branch={active}
                            variant={variant}
                            onVariant={setVariant}
                            onCompare={canCompare ? () => startCompare(active.id) : undefined}
                          />
                        </div>
                        <section aria-labelledby="outcome-heading" className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
                          <h2 id="outcome-heading" className="font-display text-lg font-semibold">
                            {t.outcome.heading}
                          </h2>
                          <OutcomeBar stats={activeStats} baseline={active.id === BASELINE_ID ? null : (board.baseline ?? null)} />
                        </section>
                      </>
                    ) : (
                      <section aria-labelledby="stage-heading" className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
                        <h2 id="stage-heading" className="font-display text-xl font-semibold">
                          {t.stage.heading}
                        </h2>
                        <div className="aspect-[111/74] w-full overflow-hidden rounded-lg border">
                          <GhostPitch situation={situation} rollout={null} time={0} label={t.stage.pitchLabel(t.situation.heading, 0)} />
                        </div>
                        <p role="status" className="text-sm text-muted-foreground">
                          {t.stage.pickOne}
                        </p>
                      </section>
                    )}
                  </div>
                )}

                <div className="flex min-w-0 flex-col gap-4">
                  {board && <VerdictPanel board={board} titleOf={titleOf} onShow={show} />}
                  <section aria-labelledby="cards-heading" className="flex flex-col gap-3">
                    <h2 id="cards-heading" className="font-display text-xl font-semibold">
                      {t.cards.heading}
                    </h2>
                    <ul aria-busy={!done} className={cn("grid gap-4", comparing && "md:grid-cols-2 xl:grid-cols-3")}>
                      <BaselineCard stats={board?.baseline ?? null} selected={activeId === BASELINE_ID} onShow={() => show(BASELINE_ID)} />
                      {ordered.map((x) => (
                        <TacticCard
                          key={x.tactic_id}
                          tactic={x}
                          roster={roster}
                          selected={activeId === x.tactic_id}
                          recommended={recommended === x.tactic_id}
                          onShow={() => show(x.tactic_id)}
                          onCompare={() => startCompare(x.tactic_id)}
                        />
                      ))}
                      {!board &&
                        [0, 1, 2].map((i) => (
                          <li key={i} aria-hidden>
                            <Skeleton className="h-44" />
                          </li>
                        ))}
                    </ul>
                  </section>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const noSubscription = () => () => {};
const readFixtureFlag = () => new URLSearchParams(window.location.search).get("fixture") === "1";

/** The /tactics page body. `?fixture=1` switches to bundled sample data, so the board can be shown without the match engine. */
export function TacticsRoute() {
  const fixture = useSyncExternalStore(noSubscription, readFixtureFlag, () => false);
  return <TacticsBoardView fixture={fixture} />;
}
