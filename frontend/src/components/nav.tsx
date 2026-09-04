"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Overview", hint: "revenue at risk and what we caused" },
  { href: "/cases", label: "Cases", hint: "the book" },
  { href: "/scenarios", label: "Scenarios", hint: "seven questions, answered live" },
  { href: "/evaluation", label: "Evaluation", hint: "four policies, one book" },
  { href: "/policy", label: "Guardrails", hint: "what bounds the agent" },
];

export function Nav() {
  const pathname = usePathname();
  const { data: health } = useApi<{
    status: string;
    clock: string;
    llm_enabled: boolean;
    llm_model: string | null;
  }>("/api/health");
  const { data: chain } = useApi<{ intact: boolean; entries_checked: number }>(
    "/api/audit/verify"
  );

  return (
    <nav
      className="sticky top-0 flex h-screen w-[228px] shrink-0 flex-col border-r"
      style={{ background: "var(--surface)" }}
    >
      <div className="border-b px-5 py-4">
        <Link href="/" className="block">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[15px] font-semibold tracking-tight">RecoveryOS</span>
          </div>
          <p className="mt-1 text-[11px] leading-snug" style={{ color: "var(--ink-3)" }}>
            Recover the right revenue,
            <br />
            and prove it was yours.
          </p>
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {LINKS.map((l) => {
          const active = l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
          return (
            <Link
              key={l.href}
              href={l.href}
              className="block border-l-2 px-5 py-2 transition-colors"
              style={{
                borderLeftColor: active ? "var(--accent)" : "transparent",
                background: active ? "var(--surface-2)" : "transparent",
                color: active ? "var(--ink)" : "var(--ink-2)",
              }}
            >
              <div className="text-[12.5px] font-medium">{l.label}</div>
              <div className="text-[10.5px]" style={{ color: "var(--ink-3)" }}>
                {l.hint}
              </div>
            </Link>
          );
        })}
      </div>

      <div className="border-t px-5 py-3 text-[10.5px]" style={{ color: "var(--ink-3)" }}>
        <Row
          label="Audit chain"
          value={chain ? (chain.intact ? `intact · ${chain.entries_checked}` : "BROKEN") : "—"}
          colour={chain ? (chain.intact ? "var(--good)" : "var(--critical)") : undefined}
        />
        <Row
          label="Language model"
          value={health ? (health.llm_enabled ? "on" : "off") : "—"}
          title={health?.llm_model ?? undefined}
        />
        <Row label="Clock" value={health ? health.clock.slice(0, 10) : "—"} />
        <p className="mt-2.5 leading-snug">
          Synthetic data. Simulated outcomes. No real merchant, customer or payment.
        </p>
      </div>
    </nav>
  );
}

function Row({
  label,
  value,
  colour,
  title,
}: {
  label: string;
  value: string;
  colour?: string;
  title?: string;
}) {
  return (
    <div className="flex items-center justify-between py-0.5" title={title}>
      <span>{label}</span>
      <span className="tnum" style={{ color: colour ?? "var(--ink-2)" }}>
        {value}
      </span>
    </div>
  );
}
