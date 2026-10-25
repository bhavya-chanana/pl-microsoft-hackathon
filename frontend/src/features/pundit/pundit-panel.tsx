"use client";

import { useMutation } from "@tanstack/react-query";
import { MessageCircleQuestion, SendHorizontal, Square, Video, Volume2 } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { FactCheckBadge } from "@/features/companion/fact-check-badge";
import { useReplay } from "@/features/broadcast/replay-store";
import { useLive } from "@/features/live/live-store";
import { profileQuery, useFanProfile } from "@/features/profile/profile-store";
import { ApiError, postJson } from "@/lib/api";
import { type Language, PunditAnswer } from "@/lib/contract";
import { copy, signalLabels } from "@/lib/copy";
import { sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

const MAX_QUESTION = 200;
const LOCALES = { en: "en-GB", hi: "hi-IN", es: "es-ES" } satisfies Record<Language, string>;

const noSubscribe = () => () => {};

/** Read one answer aloud with the browser's voice; the Listen switch owns the Azure voice. */
function useReadAloud() {
  const supported = useSyncExternalStore(noSubscribe, () => "speechSynthesis" in window, () => false);
  const [speaking, setSpeaking] = useState<string | null>(null);
  useEffect(() => () => {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }, []);
  const toggle = (id: string, text: string, language: Language) => {
    window.speechSynthesis.cancel();
    if (speaking === id) return setSpeaking(null);
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = LOCALES[language];
    utterance.onend = () => setSpeaking((s) => (s === id ? null : s));
    utterance.onerror = utterance.onend;
    setSpeaking(id);
    window.speechSynthesis.speak(utterance);
  };
  return { supported, speaking, toggle };
}

type Turn = { id: string; question: string; language: Language; answer?: PunditAnswer; error?: string };

function AnswerCard({
  turn,
  reader,
  onWatch,
}: {
  turn: Turn & { answer: PunditAnswer };
  reader: ReturnType<typeof useReadAloud>;
  onWatch: (insightId: string) => void;
}) {
  const meta = useLive((s) => s.meta);
  const { answer } = turn;
  const spoken = [answer.answer, ...answer.points].join(" ");
  return (
    <article lang={turn.language} className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-raised">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="tabular-nums">{copy.pundit.asOf(answer.minute_label)}</span>
        <span>{answer.source === "agent" ? copy.pundit.agentAnswer : copy.pundit.dataAnswer}</span>
        <FactCheckBadge status={answer.fact_check.status} className="ml-auto" />
      </header>
      <p className="max-w-prose text-base leading-relaxed">{answer.answer}</p>
      {answer.points.length > 0 && (
        <ul className="flex max-w-prose list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
          {answer.points.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {answer.key_numbers.length > 0 && (
        <dl aria-label={copy.pundit.numbers} className="flex flex-wrap gap-x-5 gap-y-1 border-t pt-3">
          {answer.key_numbers.slice(0, 4).map((n) => (
            <div key={n.label} className="flex items-baseline gap-1.5">
              <dt className="text-xs text-muted-foreground">{n.label}</dt>
              <dd className="font-display text-lg font-semibold tabular-nums">
                {n.value}
                {n.unit && <span className="ml-0.5 text-xs font-normal">{n.unit}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {answer.cited_moments.map((m) => {
          const side = sideOf(meta, m.team_id);
          const label = signalLabels[m.type] ?? m.type;
          return (
            <Button
              key={m.insight_id}
              variant="outline"
              size="sm"
              className="min-h-11 gap-1.5 px-3"
              aria-label={copy.instantReplay.watchMoment(label, m.minute_label)}
              onClick={() => onWatch(m.insight_id)}
            >
              <span aria-hidden className={cn("size-2 rounded-full", side ? sideTone[side].bg : "bg-muted-foreground")} />
              <Video aria-hidden />
              {label} <span className="text-muted-foreground tabular-nums">{m.minute_label}</span>
            </Button>
          );
        })}
        {reader.supported && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto min-h-11 gap-1.5 px-3"
            aria-pressed={reader.speaking === turn.id}
            onClick={() => reader.toggle(turn.id, spoken, turn.language)}
          >
            {reader.speaking === turn.id ? <Square aria-hidden /> : <Volume2 aria-hidden />}
            {reader.speaking === turn.id ? copy.pundit.stopReading : copy.pundit.readAloud}
          </Button>
        )}
      </div>
    </article>
  );
}

/** Ask the Pundit: a fan asks in their own words, the answer comes from the stats engine and is fact-checked. */
export function PunditPanel({ className }: { className?: string }) {
  const profile = useFanProfile();
  const hasMatch = useLive((s) => s.meta !== null);
  const play = useReplay((s) => s.play);
  const reader = useReadAloud();
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const end = useRef<HTMLDivElement>(null);
  const turnCount = useRef(0);

  const ask = useMutation({
    mutationFn: (turn: Turn) =>
      postJson("ask", { ...profileQuery(profile), question: turn.question }, PunditAnswer),
    onSuccess: (answer, turn) => setTurns((ts) => ts.map((t) => (t.id === turn.id ? { ...t, answer } : t))),
    onError: (error, turn) => {
      const message = error instanceof ApiError && error.status === 429 ? copy.pundit.tooMany : copy.pundit.error;
      setTurns((ts) => ts.map((t) => (t.id === turn.id ? { ...t, error: message } : t)));
    },
  });

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns]);

  const submit = (text: string) => {
    const q = text.trim();
    if (q.length < 2 || q.length > MAX_QUESTION || ask.isPending) return;
    const turn: Turn = { id: `q${++turnCount.current}`, question: q, language: profile.language };
    setTurns((ts) => [...ts.slice(-5), turn]);
    setQuestion("");
    ask.mutate(turn);
  };

  const watch = (insightId: string) => {
    play(insightId, { reveal: true });
    setOpen(false);
  };

  const suggestions = copy.pundit.suggestions[profile.language];
  const tooLong = question.length > MAX_QUESTION;

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger render={<Button variant="outline" size="lg" className={cn("h-12 w-full gap-2 text-base", className)} disabled={!hasMatch} />}>
        <MessageCircleQuestion aria-hidden />
        {copy.pundit.open}
      </SheetTrigger>
      <SheetContent side="right" className="w-full gap-0 p-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b p-5 pr-14">
          <SheetTitle className="font-display text-2xl font-semibold">{copy.pundit.heading}</SheetTitle>
          <SheetDescription className="max-w-prose">{copy.pundit.description}</SheetDescription>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5" aria-live="polite">
          {turns.length === 0 && (
            <div className="flex flex-wrap gap-2" lang={profile.language}>
              {suggestions.map((s) => (
                <Button key={s} variant="secondary" className="h-auto min-h-11 px-3 py-2 text-left whitespace-normal" onClick={() => submit(s)}>
                  {s}
                </Button>
              ))}
            </div>
          )}
          {turns.map((turn) => (
            <div key={turn.id} className="flex flex-col gap-2">
              <p lang={turn.language} className="max-w-[85%] self-end rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                {turn.question}
              </p>
              {turn.answer ? (
                <AnswerCard turn={{ ...turn, answer: turn.answer }} reader={reader} onWatch={watch} />
              ) : turn.error ? (
                <p className="text-sm text-destructive">{turn.error}</p>
              ) : (
                <div className="flex flex-col gap-2 rounded-lg border bg-card p-4" aria-label={copy.pundit.asking}>
                  <p className="text-xs text-muted-foreground">{copy.pundit.asking}</p>
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-4/5" />
                </div>
              )}
            </div>
          ))}
          <div ref={end} />
        </div>
        <form
          className="flex flex-col gap-1 border-t p-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
        >
          <div className="flex items-center gap-2">
            <label htmlFor="pundit-question" className="sr-only">
              {copy.pundit.heading}
            </label>
            <input
              id="pundit-question"
              lang={profile.language}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={suggestions[1] ?? copy.pundit.placeholder}
              autoComplete="off"
              aria-invalid={tooLong}
              aria-describedby={tooLong ? "pundit-too-long" : undefined}
              className="h-11 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button type="submit" size="icon-lg" className="size-11" aria-label={copy.pundit.ask} disabled={ask.isPending || question.trim().length < 2 || tooLong}>
              <SendHorizontal aria-hidden />
            </Button>
          </div>
          {tooLong && (
            <p id="pundit-too-long" className="text-xs text-destructive">
              {copy.pundit.tooLong(MAX_QUESTION)}
            </p>
          )}
        </form>
      </SheetContent>
    </Sheet>
  );
}
