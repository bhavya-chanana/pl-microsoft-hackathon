"use client";

import { m, useReducedMotion } from "motion/react";
import { memo } from "react";

import { cn } from "@/lib/utils";

/** Status text with a light sweep across it. Static under reduced motion. */
function TextShimmerComponent({
  children,
  className,
  duration = 2,
  spread = 2,
}: {
  children: string;
  as?: React.ElementType;
  className?: string;
  duration?: number;
  spread?: number;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <p className={cn("text-muted-foreground", className)}>{children}</p>;
  return (
    <m.p
      className={cn("relative inline-block bg-[length:250%_100%,auto] bg-clip-text text-transparent [background-repeat:no-repeat,padding-box]", className)}
      initial={{ backgroundPosition: "100% center" }}
      animate={{ backgroundPosition: "0% center" }}
      transition={{ repeat: Infinity, duration, ease: "linear" }}
      style={
        {
          "--spread": `${children.length * spread}px`,
          backgroundImage:
            "linear-gradient(90deg, #0000 calc(50% - var(--spread)), var(--foreground), #0000 calc(50% + var(--spread))), linear-gradient(var(--muted-foreground), var(--muted-foreground))",
        } as React.CSSProperties
      }
    >
      {children}
    </m.p>
  );
}

export const TextShimmer = memo(TextShimmerComponent);
