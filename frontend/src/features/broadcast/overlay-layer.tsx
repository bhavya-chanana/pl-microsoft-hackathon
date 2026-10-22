"use client";

import { BadgeCheck } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { useLive } from "@/features/live/live-store";
import { useProfile } from "@/features/profile/profile-store";
import type { Language, Overlay } from "@/lib/contract";
import { copy } from "@/lib/copy";
import { overlayText, sideOf, sideTone } from "@/lib/match";
import { cn } from "@/lib/utils";

import { useOverlayDirector } from "./use-overlay-director";

const enter = { duration: 0.3, ease: [0.16, 1, 0.3, 1] as const };

function Stats({ overlay }: { overlay: Overlay }) {
  if (!overlay.stats.length) return null;
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1">
      {overlay.stats.slice(0, 3).map((stat) => (
        <div key={stat.label} className="flex items-baseline gap-1">
          <dt className="text-xs text-graphic-foreground/70">
            {stat.label}
          </dt>
          <dd className="font-display text-base font-semibold tabular-nums @lg:text-lg">
            {stat.value}
            {stat.unit && <span className="ml-0.5 text-xs font-normal">{stat.unit}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Verified({ overlay }: { overlay: Overlay }) {
  const status = useLive((s) => s.insights[overlay.insight_id]?.fact_check.status);
  if (status !== "verified" && status !== "rewritten") return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-graphic-foreground/70">
      <BadgeCheck aria-hidden className="size-3.5" />
      {copy.factCheck[status]}
    </span>
  );
}

function LowerThird({ overlay, language }: { overlay: Overlay; language: Language }) {
  const meta = useLive((s) => s.meta);
  const side = sideOf(meta, overlay.team_id);
  const text = overlayText(overlay, language);
  return (
    <div className="graphic-cut flex max-w-[min(36rem,80%)] items-stretch bg-graphic text-graphic-foreground shadow-floating">
      <span aria-hidden className={cn("w-1.5 shrink-0", side ? sideTone[side].bg : "bg-graphic-foreground/40")} />
      <div className="flex min-w-0 flex-col gap-0.5 px-3 py-1.5 pr-6 @lg:gap-1 @lg:px-4 @lg:py-2 @lg:pr-8">
        <p className="line-clamp-2 font-display text-base leading-tight font-semibold @lg:text-xl @3xl:text-2xl">{text.headline}</p>
        {text.body && (
          <p className="line-clamp-2 max-w-prose text-xs text-graphic-foreground/85 @lg:text-sm">{text.body}</p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Stats overlay={overlay} />
          <Verified overlay={overlay} />
        </div>
      </div>
    </div>
  );
}

function StatCard({ overlay, language }: { overlay: Overlay; language: Language }) {
  const meta = useLive((s) => s.meta);
  const side = sideOf(meta, overlay.team_id);
  const text = overlayText(overlay, language);
  return (
    <div className="graphic-cut-left w-[min(18rem,46vw)] bg-graphic py-2 pr-3 pl-6 text-graphic-foreground shadow-floating">
      <p className={cn("font-display text-lg leading-tight font-semibold", side && sideTone[side].text)}>
        {text.headline}
      </p>
      <Stats overlay={overlay} />
    </div>
  );
}

function Banner({ overlay, language }: { overlay: Overlay; language: Language }) {
  const text = overlayText(overlay, language);
  const correction = overlay.kind === "correction_banner";
  return (
    <div
      className={cn(
        "flex w-full flex-col items-center gap-0.5 bg-graphic px-6 py-2 text-center text-graphic-foreground shadow-floating",
        correction ? "border-t-4 border-graphic-warn" : "border-t-4 border-graphic-foreground/60",
      )}
    >
      {correction && <p className="text-xs font-medium text-graphic-warn">{copy.overlays.correction}</p>}
      <p className="font-display text-base font-bold @lg:text-display">{text.headline}</p>
      {text.body && <p className="hidden max-w-prose text-sm opacity-90 @lg:block">{text.body}</p>}
    </div>
  );
}

/** Timed broadcast graphics over the pitch, one per slot. */
export function OverlayLayer() {
  const onAir = useOverlayDirector();
  const overlays = useLive((s) => s.overlays);
  const language = useProfile((s) => s.language);
  const pick = (id: string | null) => (id ? overlays[id] : undefined);
  const lower = pick(onAir.lower);
  const upper = pick(onAir.upper_right);
  const banner = pick(onAir.banner);

  return (
    <div className="pointer-events-none absolute inset-0" aria-live="polite" aria-atomic="false">
      <div className="absolute inset-x-0 top-0">
        <AnimatePresence>
          {banner && (
            <motion.div
              key={banner.overlay_id}
              initial={{ opacity: 0, y: -24 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -24 }}
              transition={enter}
            >
              <Banner overlay={banner} language={language} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div className="absolute top-[14%] right-0">
        <AnimatePresence>
          {upper && (
            <motion.div
              key={upper.overlay_id}
              initial={{ opacity: 0, x: 48 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 48 }}
              transition={enter}
            >
              <StatCard overlay={upper} language={language} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div className="absolute inset-x-0 bottom-[6%] left-0">
        <AnimatePresence>
          {lower && (
            <motion.div
              key={lower.overlay_id}
              initial={{ opacity: 0, x: -64 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -64 }}
              transition={enter}
            >
              <LowerThird overlay={lower} language={language} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
