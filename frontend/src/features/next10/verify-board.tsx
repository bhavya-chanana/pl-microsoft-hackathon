"use client";

import { Check, Minus, X } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";

import { useMatches, useVerify } from "./api";
import type { HeadScore, Reliability, ScoreDoc } from "./contract";
import { n10 } from "./copy";
import { CardsSkeleton, ErrorState, PageFrame } from "./ui";

const T = n10.verify;
const MIN_N = 30;

type Verdict = "beat" | "didNot" | "tooFew" | "unknown";

/** Beat baseline: lower Brier and log loss than the base-rate guess, and lower Brier than persistence when it exists. */
export function verdictOf(h: HeadScore): Verdict {
  if (h.n < MIN_N) return "tooFew";
  if (h.brier == null || h.brier_baseline == null) return "unknown";
  const beatsBrier = h.brier < h.brier_baseline && (h.brier_persistence == null || h.brier < h.brier_persistence);
  const beatsLoss = h.logloss == null || h.logloss_baseline == null || h.logloss < h.logloss_baseline;
  return beatsBrier && beatsLoss ? "beat" : "didNot";
}

function wavg(items: Array<[number, number | null | undefined]>): number | null {
  let num = 0;
  let den = 0;
  for (const [w, v] of items) {
    if (v == null || Number.isNaN(v)) continue;
    num += w * v;
    den += w;
  }
  return den ? num / den : null;
}

/** Pool one head across matches: error rates are means over predictions, so the pooled value is n-weighted. */
export function pool(scores: HeadScore[]): HeadScore {
  const w = (s: HeadScore) => s.n;
  const field = (key: keyof HeadScore) => wavg(scores.map((s) => [w(s), s[key] as number | null | undefined]));
  const bins = new Map<string, { lo: number; hi: number; n: number; p: number; f: number }>();
  for (const s of scores) {
    for (const r of s.reliability ?? []) {
      const k = `${r.lo}-${r.hi}`;
      const b = bins.get(k) ?? { lo: r.lo, hi: r.hi, n: 0, p: 0, f: 0 };
      b.n += r.n;
      b.p += r.n * r.mean_p;
      b.f += r.n * r.freq;
      bins.set(k, b);
    }
  }
  const reliability: Reliability[] = [...bins.values()].sort((a, b) => a.lo - b.lo).map((b) => ({ lo: b.lo, hi: b.hi, n: b.n, mean_p: b.n ? b.p / b.n : 0, freq: b.n ? b.f / b.n : 0 }));
  return {
    n: scores.reduce((a, s) => a + s.n, 0),
    base_rate: field("base_rate"),
    brier: field("brier"),
    logloss: field("logloss"),
    brier_baseline: field("brier_baseline"),
    brier_persistence: field("brier_persistence"),
    logloss_baseline: field("logloss_baseline"),
    logloss_persistence: field("logloss_persistence"),
    reliability: reliability.length ? reliability : null,
  };
}

const VERDICT_STYLE: Record<Verdict, { text: string; cls: string; Icon: typeof Check }> = {
  beat: { text: T.beat, cls: "bg-verified/15 text-verified ring-verified/40", Icon: Check },
  didNot: { text: T.didNot, cls: "bg-blocked/15 text-blocked ring-blocked/40", Icon: X },
  tooFew: { text: T.tooFew, cls: "bg-raised text-muted-foreground ring-border", Icon: Minus },
  unknown: { text: T.tooFew, cls: "bg-raised text-muted-foreground ring-border", Icon: Minus },
};

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const { text, cls, Icon } = VERDICT_STYLE[verdict];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[13px] font-medium ring-1", cls)}>
      <Icon aria-hidden className="size-3.5" />
      {text}
    </span>
  );
}

const f3 = (v: number | null | undefined) => (v == null ? "–" : v.toFixed(3));

/** Model against the two guesses, one bar each; the value is printed on every bar. */
function ErrorBars({ title, rows }: { title: string; rows: Array<{ label: string; value: number | null | undefined; model?: boolean }> }) {
  const max = Math.max(1e-9, ...rows.map((r) => r.value ?? 0));
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[13px] font-medium text-muted-foreground">
        {title} <span className="font-normal text-subtle">· {T.lowerIsBetter}</span>
      </p>
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-2 text-sm">
          <span className={cn("truncate", r.model ? "font-medium" : "text-muted-foreground")}>{r.label}</span>
          <span className="h-2 overflow-hidden rounded-full bg-muted" role="presentation">
            <span className={cn("block h-full rounded-full", r.model ? "bg-primary" : "bg-subtle")} style={{ width: `${((r.value ?? 0) / max) * 100}%` }} />
          </span>
          <span className="text-right font-display font-semibold tnum">{f3(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

const S = 220;
const P = { l: 46, r: 22, t: 10, b: 34 };

/** Reliability: predicted chance against how often it happened; dot area follows the number of predictions. */
export function ReliabilityPlot({ bins, label }: { bins: Reliability[] | null | undefined; label: string }) {
  const filled = (bins ?? []).filter((b) => b.n > 0);
  if (filled.length === 0) return <p className="rounded-lg border border-dashed px-3 py-6 text-center text-[13px] text-subtle">{T.reliabilityEmpty}</p>;
  const max = Math.min(1, Math.ceil(Math.max(0.05, ...filled.map((b) => Math.max(b.mean_p, b.freq))) * 1.1 * 20) / 20);
  const nMax = Math.max(...filled.map((b) => b.n));
  const x = (v: number) => P.l + (v / max) * (S - P.l - P.r);
  const y = (v: number) => S - P.b - (v / max) * (S - P.t - P.b);
  const ticks = [0, 0.5, 1].map((t) => t * max);
  return (
    <figure className="flex flex-col gap-1">
      <svg viewBox={`0 0 ${S} ${S}`} role="img" aria-label={`${label} reliability plot`} className="block h-auto w-full max-w-64">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={P.l} x2={S - P.r} y1={y(t)} y2={y(t)} className="stroke-border" />
            <line x1={x(t)} x2={x(t)} y1={P.t} y2={S - P.b} className="stroke-border" />
            <text x={P.l - 5} y={y(t) + 4} textAnchor="end" className="fill-subtle" style={{ fontSize: 10 }}>{Math.round(t * 1000) / 10}%</text>
            <text x={x(t)} y={S - P.b + 14} textAnchor="middle" className="fill-subtle" style={{ fontSize: 10 }}>{Math.round(t * 1000) / 10}%</text>
          </g>
        ))}
        <line x1={x(0)} y1={y(0)} x2={x(max)} y2={y(max)} className="stroke-subtle" strokeDasharray="4 3" />
        <polyline points={filled.map((b) => `${x(b.mean_p)},${y(b.freq)}`).join(" ")} className="fill-none stroke-primary" strokeWidth={1.5} />
        {filled.map((b) => (
          <circle key={b.lo} cx={x(b.mean_p)} cy={y(b.freq)} r={2.5 + 4.5 * Math.sqrt(b.n / nMax)} className="fill-primary stroke-card" strokeWidth={1.5}>
            <title>{`Predicted ${(b.mean_p * 100).toFixed(1)}%, happened ${(b.freq * 100).toFixed(1)}% (${b.n} predictions)`}</title>
          </circle>
        ))}
        <text x={(P.l + S - P.r) / 2} y={S - 3} textAnchor="middle" className="fill-muted-foreground" style={{ fontSize: 10 }}>{T.axisPredicted}</text>
        <text transform={`translate(11 ${(P.t + S - P.b) / 2}) rotate(-90)`} textAnchor="middle" className="fill-muted-foreground" style={{ fontSize: 10 }}>{T.axisObserved}</text>
      </svg>
    </figure>
  );
}

function HeadCard({ head, score }: { head: string; score: HeadScore }) {
  const label = T.heads[head] ?? head;
  return (
    <section aria-label={label} className="flex min-w-0 flex-col gap-4 rounded-2xl border bg-card p-4 shadow-raised md:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-lg font-semibold">{label}</h3>
          <p className="text-[13px] text-subtle tnum">
            {T.predictions} {score.n.toLocaleString("en-GB")} · {T.baseRate} {score.base_rate == null ? "–" : `${(score.base_rate * 100).toFixed(1)}%`}
          </p>
        </div>
        <VerdictChip verdict={verdictOf(score)} />
      </div>
      <ErrorBars
        title={T.brier}
        rows={[
          { label: T.model, value: score.brier, model: true },
          { label: T.baseline, value: score.brier_baseline },
          { label: T.persistence, value: score.brier_persistence },
        ]}
      />
      <ErrorBars
        title={T.logloss}
        rows={[
          { label: T.model, value: score.logloss, model: true },
          { label: T.baseline, value: score.logloss_baseline },
          ...(score.logloss_persistence != null ? [{ label: T.persistence, value: score.logloss_persistence }] : []),
        ]}
      />
      <div>
        <p className="mb-1 text-[13px] font-medium text-muted-foreground">{T.reliability}</p>
        <ReliabilityPlot bins={score.reliability} label={label} />
      </div>
    </section>
  );
}

function headsOf(docs: ScoreDoc[]): string[] {
  const seen = new Set<string>();
  for (const d of docs) for (const h of Object.keys(d.heads)) seen.add(h);
  return [...seen];
}

function PerMatch({ docs, names }: { docs: ScoreDoc[]; names: Map<string, string> }) {
  const heads = headsOf(docs);
  return (
    <div className="overflow-x-auto rounded-2xl border bg-card shadow-raised">
      <table className="w-full min-w-[34rem] text-left text-[15px]">
        <caption className="sr-only">{T.perMatch}</caption>
        <thead>
          <tr className="border-b text-[13px] text-subtle">
            <th scope="col" className="px-4 py-3 font-medium">{T.perMatch}</th>
            {heads.map((h) => (
              <th key={h} scope="col" className="px-4 py-3 font-medium">{T.heads[h] ?? h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {docs.map((d) => (
            <tr key={d.id}>
              <th scope="row" className="px-4 py-3 font-medium">
                <Link href={routes.next10Match(d.match_id)} className="underline-offset-4 hover:underline">{names.get(d.match_id) ?? d.match_id}</Link>
              </th>
              {heads.map((h) => {
                const s = d.heads[h];
                return (
                  <td key={h} className="px-4 py-3 align-top">
                    {s ? (
                      <span className="flex flex-col items-start gap-1">
                        <VerdictChip verdict={verdictOf(s)} />
                        <span className="text-[13px] text-subtle tnum">{T.brier} {f3(s.brier)} v {f3(s.brier_baseline)} · n {s.n}</span>
                      </span>
                    ) : (
                      <span className="text-subtle">–</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Per match and overall Brier and log loss against the base rate and persistence, with reliability plots. */
export function VerifyBoard() {
  const { data, isPending, isError, refetch } = useVerify();
  const matches = useMatches();
  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of matches.data ?? []) map.set(m.match_id, `${m.home.name} v ${m.away.name}`);
    return map;
  }, [matches.data]);
  const docs = useMemo(() => (data ?? []).filter((d) => Object.keys(d.heads).length > 0), [data]);
  const heads = headsOf(docs);
  const overall = useMemo(() => Object.fromEntries(heads.map((h) => [h, pool(docs.flatMap((d) => (d.heads[h] ? [d.heads[h]] : [])))])), [docs, heads]);

  return (
    <PageFrame title={T.heading} intro={T.intro} back={{ href: routes.next10, label: n10.nav.back }}>
      {isPending ? (
        <CardsSkeleton rows={3} />
      ) : isError ? (
        <ErrorState title={T.offlineTitle} onRetry={() => void refetch()}>
          {T.offlineBody}
        </ErrorState>
      ) : docs.length === 0 ? (
        <EmptyState title={T.emptyTitle}>{T.emptyBody}</EmptyState>
      ) : (
        <>
          <section aria-labelledby="v-overall" className="flex flex-col gap-3">
            <h2 id="v-overall" className="flex flex-wrap items-baseline gap-x-3 text-lg font-semibold">
              {T.overall}
              <span className="text-sm font-normal text-subtle">{T.matchCount(docs.length)}</span>
            </h2>
            <div className="grid gap-4 lg:grid-cols-3">
              {heads.map((h) => (
                <HeadCard key={h} head={h} score={overall[h]} />
              ))}
            </div>
            <p className="max-w-prose text-[13px] text-subtle">{T.verdictHelp}</p>
          </section>
          <section aria-labelledby="v-matches" className="flex flex-col gap-3">
            <h2 id="v-matches" className="text-lg font-semibold">{T.perMatch}</h2>
            <PerMatch docs={docs} names={names} />
          </section>
        </>
      )}
    </PageFrame>
  );
}


