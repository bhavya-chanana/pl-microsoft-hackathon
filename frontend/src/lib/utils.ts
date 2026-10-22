import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** tailwind-merge must know our custom font-size tokens, or it drops them next to text colours. */
const merge = extendTailwindMerge({
  extend: { theme: { text: ["score", "display"] } },
});

export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
