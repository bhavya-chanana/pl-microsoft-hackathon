"use client";

import { useMutation } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { toast } from "sonner";

import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import { postJson } from "@/lib/api";
import { type Language, Poll } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";

import { useFans } from "./fan-store";

const T = copy.fans;
/** Open polls first, newest first, then resolved ones ("62% of fans called it"). */
const SHOWN = 2;

function text(localized: Record<string, string>, language: Language): string {
  return localized[language] ?? localized.en ?? Object.values(localized)[0] ?? "";
}

function pct(votes: number, total: number): number {
  return total ? Math.round((votes / total) * 100) : 0;
}

function PollCard({ poll, runId, language }: { poll: Poll; runId: string; language: Language }) {
  const mine = useFans((s) => s.picks[runId]?.[poll.poll_id]);
  const vote = useMutation({
    mutationFn: (optionId: string) =>
      postJson(`polls/${poll.poll_id}/vote`, { voter_id: useFans.getState().ensureVoterId(), option_id: optionId }, Poll),
    onSuccess: (updated, optionId) => {
      useFans.getState().pick(runId, poll.poll_id, optionId);
      useLive.setState((s) => ({ polls: { ...s.polls, [updated.poll_id]: updated } }));
    },
    onError: () => toast.error(T.voteFailed),
  });
  const showResults = Boolean(mine) || poll.closed;
  const answerLabel = poll.kind === "result" ? T.finalResult : T.dataPick;
  const called = poll.kind === "result" && poll.answer ? poll.options.find((o) => o.option_id === poll.answer) : null;
  const headingId = `poll-${poll.poll_id}`;

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-0.5">
        <h3 id={headingId} className="font-display text-base font-semibold">
          {text(poll.question, language)}
        </h3>
        <p className="text-xs text-muted-foreground">
          {T.opened(text(poll.context, language), poll.minute_label)} · {T.votes(poll.total_votes)}
          {poll.closed && ` · ${T.closed}`}
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {poll.options.map((o) => {
          const share = pct(o.votes, poll.total_votes);
          const isMine = mine === o.option_id;
          const isAnswer = poll.answer === o.option_id;
          return (
            <li key={o.option_id}>
              <button
                type="button"
                aria-pressed={isMine}
                disabled={poll.closed || vote.isPending}
                onClick={() => vote.mutate(o.option_id)}
                className={cn(
                  "relative flex min-h-11 w-full items-center gap-2 overflow-hidden rounded-md border px-3 text-left text-sm transition-colors",
                  "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none enabled:hover:bg-muted disabled:cursor-default",
                  isMine && "border-primary",
                )}
              >
                {showResults && (
                  <span
                    aria-hidden
                    className={cn("absolute inset-y-0 left-0 transition-[width] duration-500", isMine ? "bg-primary/20" : "bg-muted")}
                    style={{ width: `${share}%` }}
                  />
                )}
                <span className="relative flex-1 font-medium">{text(o.label, language)}</span>
                {isAnswer && showResults && (
                  <span className="relative inline-flex items-center gap-1 text-xs text-muted-foreground">
                    <Check aria-hidden className="size-3.5" />
                    {answerLabel}
                  </span>
                )}
                {isMine && !isAnswer && <span className="relative text-xs text-muted-foreground">{T.yourPick}</span>}
                {showResults && <span className="relative w-10 text-right tabular-nums">{share}%</span>}
              </button>
            </li>
          );
        })}
      </ul>
      {called && poll.total_votes > 0 && <p className="text-sm font-medium">{T.calledIt(pct(called.votes, poll.total_votes))}</p>}
    </section>
  );
}

/** Live fan polls, opened by the match itself (kick-off, goals, half-time, full-time). */
export function FanPolls() {
  const polls = useLive((s) => s.polls);
  const runId = useLive((s) => s.runId);
  const language = useProfile((s) => s.language);
  const all = Object.values(polls).reverse();
  const open = all.filter((p) => !p.closed);
  const resolved = all.filter((p) => p.closed && p.answer);
  const shown = [...open, ...resolved].slice(0, SHOWN);

  return (
    <section aria-labelledby="polls-heading" className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 id="polls-heading" className="font-display text-lg font-semibold">
          {T.pollsHeading}
        </h2>
        <p className="text-xs text-muted-foreground">{T.anonymous}</p>
      </div>
      {shown.length && runId ? (
        <div className="grid items-start gap-3 md:grid-cols-2">
          {shown.map((p) => (
            <PollCard key={p.poll_id} poll={p} runId={runId} language={language} />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">{T.noPolls}</p>
      )}
    </section>
  );
}
