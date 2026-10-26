"use client";

import { Clapperboard, FlaskConical, History, LayoutGrid } from "lucide-react";
import Link from "next/link";

import { useShell } from "@/components/shell/shell-store";
import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

const T = copy.explorePage;

const tile =
  "group flex min-h-40 flex-col justify-between gap-6 rounded-2xl border bg-card p-5 text-left shadow-raised transition hover:-translate-y-0.5 hover:border-foreground/20 hover:shadow-floating";

/** Things to try beyond following the match. */
export function ExplorePage() {
  const setDev = useShell((s) => s.setDev);
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 md:px-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-semibold tracking-tight">{T.heading}</h1>
        <p className="text-muted-foreground">{T.intro}</p>
      </header>
      <ul className="grid gap-4 md:grid-cols-2">
        <li className="md:col-span-2">
          <Link href={routes.multiverse} className={tile}>
            <LayoutGrid aria-hidden className="size-6 text-primary" />
            <span>
              <span className="block text-xl font-semibold">{T.multiverse.title}</span>
              <span className="block text-sm text-subtle">{T.multiverse.subtitle}</span>
              <span className="mt-1 block text-muted-foreground">{T.multiverse.body}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href={routes.broadcast} className={tile}>
            <Clapperboard aria-hidden className="size-6 text-primary" />
            <span>
              <span className="block text-xl font-semibold">{T.broadcast.title}</span>
              <span className="mt-1 block text-muted-foreground">{T.broadcast.body}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href={routes.lab} className={tile}>
            <FlaskConical aria-hidden className="size-6 text-primary" />
            <span>
              <span className="block text-xl font-semibold">{T.lab.title}</span>
              <span className="mt-1 block text-muted-foreground">{T.lab.body}</span>
            </span>
          </Link>
        </li>
        <li className="md:col-span-2">
          <button type="button" className={`${tile} w-full`} onClick={() => setDev(true)}>
            <History aria-hidden className="size-6 text-primary" />
            <span>
              <span className="block text-xl font-semibold">{T.replay.title}</span>
              <span className="mt-1 block text-muted-foreground">{T.replay.body}</span>
            </span>
          </button>
        </li>
      </ul>
    </div>
  );
}
