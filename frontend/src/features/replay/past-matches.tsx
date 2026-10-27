"use client";

import { useMutation } from "@tanstack/react-query";
import { History, Play, Tv } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/features/live/live-store";
import { useFeeds, useRuns } from "@/features/live/queries";
import { useShell } from "@/components/shell/shell-store";
import { postJson } from "@/lib/api";
import type { RunSummary } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { useFollowMatch } from "./follow-match";

const T = copy.archive;
const Opened = z.object({ match_id: z.string(), run_id: z.string() });
const WHEN = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** "Northbridge 3–2 Harbour"; runs stored before team names were kept fall back to team ids. */
function title(run: RunSummary): string {
  const teams = run.teams ?? Object.keys(run.score).map((id) => ({ team_id: id, short_name: id }));
  const [home, away] = teams;
  if (!home || !away) return run.match_id;
  return `${home.short_name} ${run.score[home.team_id] ?? 0}–${run.score[away.team_id] ?? 0} ${away.short_name}`;
}

/** A live run is stopped unless it is the one playing now: only one match runs at a time. */
function detail(run: RunSummary, showing: boolean): string {
  const status = run.status === "finished" && run.match_status !== "full_time" ? "live" : run.status;
  const name = showing && status === "live" ? T.inPlay : (T.status[status] ?? status);
  const label = status === "finished" ? T.status.finished : `${name} · ${run.minute_label}`;
  return `${label} · ${WHEN.format(new Date(run.created_at))}`;
}

/** The match archive: reopen a stored replay, or play a recorded match (demo only). */
export function PastMatches({ className, bare }: { className?: string; bare?: boolean }) {
  const current = useLive((s) => s.runId);
  const runs = useRuns();
  const feeds = useFeeds();
  const follow = useFollowMatch();
  const router = useRouter();

  // Each of these loads a match, waits until the stream carries it, then goes to the Live page.
  // A match that does not arrive leaves the sheet open with the error.
  const open = useMutation({
    mutationFn: async (runId: string) => follow(await postJson(`runs/${runId}/open`, {}, Opened)),
    onSuccess: () => toast.success(T.opened),
    onError: () => toast.error(T.failed),
  });
  const play = useMutation({
    mutationFn: async (name: string) => follow(await postJson("replay/feed", { name, speed: 5 }, Opened)),
    onSuccess: () => toast.success(T.playing),
    onError: () => toast.error(T.failed),
  });
  const busy = open.isPending || play.isPending;

  return (
    <section aria-labelledby="archive-heading" className={cn("flex flex-col gap-4", !bare && "rounded-lg border bg-card p-4", className)}>
      <div className={cn("flex flex-col gap-0.5", bare && "sr-only")}>
        <h2 id="archive-heading" className="inline-flex items-center gap-2 font-display text-lg font-semibold">
          <History aria-hidden className="size-4 text-muted-foreground" />
          {T.heading}
        </h2>
        <p className="text-xs text-muted-foreground">{T.help}</p>
      </div>
      {(open.isError || play.isError) && (
        <p role="alert" className="rounded-lg border border-blocked/40 p-3 text-sm">
          {T.failed}
        </p>
      )}
      {runs.isPending ? (
        <div className="flex flex-col gap-2" aria-hidden>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-11 w-full" />
          ))}
        </div>
      ) : runs.data?.length ? (
        <ul className="flex flex-col divide-y">
          {runs.data.map((run) => {
            const showing = run.run_id === current;
            return (
              <li key={run.run_id} className="flex items-center gap-3 py-2">
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm font-medium tabular-nums">{title(run)}</span>
                  <span className="truncate text-xs text-muted-foreground">{detail(run, showing)}</span>
                </div>
                {showing ? (
                  <Button
                    variant="secondary"
                    className="h-11 gap-2"
                    onClick={() => {
                      useShell.getState().setStream(false);
                      router.push(routes.live);
                    }}
                  >
                    <Tv aria-hidden />
                    {T.watch}
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    className="h-11"
                    disabled={busy}
                    aria-label={`${T.open} ${title(run)}`}
                    onClick={() => open.mutate(run.run_id)}
                  >
                    {open.isPending && open.variables === run.run_id ? T.loading : T.open}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{T.empty}</p>
      )}
      {feeds.data?.length ? (
        <div className="flex flex-col gap-2 border-t pt-4">
          <div className="flex flex-col gap-0.5">
            <h3 className="text-sm font-semibold">{T.recorded}</h3>
            <p className="text-xs text-muted-foreground">{T.recordedHelp}</p>
          </div>
          <ul className="flex flex-col divide-y">
            {feeds.data.map((name) => (
              <li key={name} className="flex items-center gap-3 py-2">
                <span className="min-w-0 flex-1 truncate text-sm">{name.replace(/^video-/, "").replaceAll(/[-_]+/g, " ")}</span>
                <Button variant="outline" className="h-11" disabled={busy} onClick={() => play.mutate(name)}>
                  <Play aria-hidden />
                  {play.isPending && play.variables === name ? T.loading : T.play}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
