"use client";

import { ReactNode } from "react";
import { VERDICT_COLOR, humanise, pct, rupees, rupeesCompact } from "@/lib/format";

export function Card({
  title,
  hint,
  right,
  children,
  className = "",
}: {
  title?: string;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="flex items-baseline justify-between gap-4 border-b px-4 py-2.5">
          <div>
            {title && <h2 className="label">{title}</h2>}
            {hint && (
              <p className="mt-0.5 text-[11px]" style={{ color: "var(--ink-3)" }}>
                {hint}
              </p>
            )}
          </div>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/**
 * A stat tile is a chart form in its own right: one number, its unit, and the
 * one sentence that stops it being misread. No sparkline unless there is a
 * series worth drawing.
 */
export function Stat({
  label,
  value,
  sub,
  tone = "default",
  emphasis = false,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "default" | "good" | "warn" | "bad" | "accent";
  emphasis?: boolean;
}) {
  const colour =
    tone === "good"
      ? "var(--good)"
      : tone === "warn"
      ? "var(--warning)"
      : tone === "bad"
      ? "var(--critical)"
      : tone === "accent"
      ? "var(--accent)"
      : "var(--ink)";
  return (
    <div
      className="card px-4 py-3"
      style={emphasis ? { borderColor: "var(--accent)" } : undefined}
    >
      <div className="label">{label}</div>
      <div
        className="mt-1.5 tnum leading-none"
        style={{ color: colour, fontSize: emphasis ? 30 : 22, fontWeight: 500 }}
      >
        {value}
      </div>
      {sub && (
        <div className="mt-1.5 text-[11px] leading-snug" style={{ color: "var(--ink-3)" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export function Chip({
  children,
  colour = "var(--ink-3)",
  title,
}: {
  children: ReactNode;
  colour?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
      style={{ color: colour, background: "var(--surface-2)", border: `1px solid ${colour}33` }}
    >
      {children}
    </span>
  );
}

export function Verdict({ decision }: { decision: string }) {
  return (
    <Chip colour={VERDICT_COLOR[decision] ?? "var(--ink-3)"}>{humanise(decision)}</Chip>
  );
}

/** Horizontal bar. 8px, 4px rounded data-end, anchored to a shared baseline. */
export function Bar({
  value,
  max,
  colour = "var(--accent)",
  height = 8,
}: {
  value: number;
  max: number;
  colour?: string;
  height?: number;
}) {
  const w = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div
      className="w-full overflow-hidden rounded-sm"
      style={{ background: "var(--surface-3)", height }}
    >
      <div className="bar h-full" style={{ width: `${w}%`, background: colour, height }} />
    </div>
  );
}

/**
 * The utility identity, rendered so a reader can verify the arithmetic by eye:
 * incremental - cost - fatigue - risk = utility.
 */
export function UtilityBreakdown({
  incremental,
  cost,
  fatigue,
  risk,
  utility,
}: {
  incremental: number;
  cost: number;
  fatigue: number;
  risk: number;
  utility: number;
}) {
  const parts = [
    { label: "expected incremental", v: incremental, colour: "var(--s3)" },
    { label: "cost", v: -cost, colour: "var(--ink-3)" },
    { label: "customer fatigue", v: -fatigue, colour: "var(--s2)" },
    { label: "risk", v: -risk, colour: "var(--s4)" },
  ].filter((p) => p.v !== 0);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
      {parts.map((p, i) => (
        <span key={p.label} className="inline-flex items-center gap-1">
          {i > 0 && <span style={{ color: "var(--ink-3)" }}>{p.v < 0 ? "−" : "+"}</span>}
          <span
            className="inline-block h-2 w-2 rounded-sm"
            style={{ background: p.colour }}
            aria-hidden
          />
          <span className="tnum" style={{ color: "var(--ink-2)" }}>
            {rupees(Math.abs(p.v), { decimals: false })}
          </span>
          <span style={{ color: "var(--ink-3)" }}>{p.label}</span>
        </span>
      ))}
      <span style={{ color: "var(--ink-3)" }}>=</span>
      <span
        className="tnum font-medium"
        style={{ color: utility > 0 ? "var(--good)" : "var(--ink-3)" }}
      >
        {rupees(utility, { decimals: false })}
      </span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div
      className="card px-4 py-10 text-center text-[12px]"
      style={{ color: "var(--ink-3)" }}
    >
      {children}
    </div>
  );
}

export function Loading() {
  return <Empty>Loading…</Empty>;
}

export function ApiError({ message }: { message: string }) {
  return (
    <div
      className="card px-4 py-6 text-[12px]"
      style={{ borderColor: "var(--critical)", color: "var(--ink-2)" }}
    >
      <div style={{ color: "var(--critical)" }} className="label mb-2">
        Backend unreachable
      </div>
      <p className="mb-3">{message}</p>
      <p style={{ color: "var(--ink-3)" }}>
        Start it with{" "}
        <code className="tnum">
          uvicorn recoveryos.api.app:app --app-dir backend --reload
        </code>
        , then generate a world with{" "}
        <code className="tnum">python scripts/generate_synthetic_data.py</code>.
      </p>
    </div>
  );
}

export function SimulatedBadge() {
  return (
    <div
      className="flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-[11px]"
      style={{ background: "var(--surface-2)", color: "var(--ink-3)", border: "1px solid var(--line)" }}
    >
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: "var(--warning)" }}
        aria-hidden
      />
      Simulated environment — synthetic cases, simulated outcomes, no real customer contacted
    </div>
  );
}

export { rupees, rupeesCompact, pct, humanise };
