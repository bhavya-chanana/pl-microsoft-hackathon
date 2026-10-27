import { Compass, Radio, Sparkles, ChartColumn, ClipboardList } from "lucide-react";

import { copy } from "@/lib/copy";
import { routes } from "@/lib/routes";

export const NAV = [
  { href: routes.live, label: copy.nav.live, icon: Radio },
  { href: routes.moments, label: copy.nav.moments, icon: Sparkles },
  { href: routes.stats, label: copy.nav.stats, icon: ChartColumn },
  { href: routes.coach, label: copy.nav.coach, icon: ClipboardList },
  { href: routes.explore, label: copy.nav.explore, icon: Compass },
] as const;

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
