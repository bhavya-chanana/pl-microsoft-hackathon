import { cn } from "@/lib/utils";

/** A pitch outline over the page wash, one line of copy and an optional single action. */
export function EmptyState({
  title,
  children,
  action,
  className,
}: {
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-2xl border border-dashed px-6 py-8 text-center", className)}>
      <svg aria-hidden viewBox="0 0 120 80" className="h-16 w-24 text-subtle" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <rect x={2} y={2} width={116} height={76} rx={6} />
        <line x1={60} y1={2} x2={60} y2={78} />
        <circle cx={60} cy={40} r={11} />
        <rect x={2} y={22} width={16} height={36} />
        <rect x={102} y={22} width={16} height={36} />
      </svg>
      {title && <p className="text-lg font-semibold">{title}</p>}
      {children && <div className="max-w-prose text-[15px] text-muted-foreground">{children}</div>}
      {action}
    </div>
  );
}
