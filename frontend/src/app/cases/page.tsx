"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { CaseRow, useApi } from "@/lib/api";
import { ApiError, Card, Chip, Loading } from "@/components/ui";
import { STATE_COLOR, humanise, rupees, when } from "@/lib/format";

const STATES = ["ALL", "OPEN", "WAITING", "RECOVERED", "ESCALATED", "STOPPED"];

export default function CasesPage() {
  const { data, error, loading } = useApi<{ cases: CaseRow[]; count: number }>("/api/cases?limit=600");
  const [state, setState] = useState("ALL");
  const [archetype, setArchetype] = useState("ALL");
  const [query, setQuery] = useState("");

  const archetypes = useMemo(
    () => ["ALL", ...Array.from(new Set(data?.cases.map((c) => c.archetype) ?? [])).sort()],
    [data]
  );

  const rows = useMemo(() => {
    let r = data?.cases ?? [];
    if (state !== "ALL") r = r.filter((c) => c.state === state);
    if (archetype !== "ALL") r = r.filter((c) => c.archetype === archetype);
    if (query.trim()) {
      const q = query.toLowerCase();
      r = r.filter(
        (c) =>
          c.case_id.toLowerCase().includes(q) ||
          c.customer_id.toLowerCase().includes(q) ||
          c.failure_reason.toLowerCase().includes(q)
      );
    }
    return r;
  }, [data, state, archetype, query]);

  if (error) return <ApiError message={error} />;
  if (loading || !data) return <Loading />;

  const atRisk = rows.reduce((a, c) => a + c.amount_paise, 0);
  const recovered = rows.reduce((a, c) => a + c.recovered_paise, 0);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[19px] font-semibold tracking-tight">Cases</h1>
        <p className="mt-0.5 text-[12px]" style={{ color: "var(--ink-2)" }}>
          {rows.length} of {data.count} · {rupees(atRisk, { decimals: false })} at risk ·{" "}
          {rupees(recovered, { decimals: false })} recovered
        </p>
      </header>

      {/* Filters sit in one row above the table, per the interaction spec. */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={state} onChange={setState} options={STATES} label="State" />
        <Select value={archetype} onChange={setArchetype} options={archetypes} label="Scenario" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="case, customer or failure reason"
          className="rounded-sm px-2.5 py-1.5 text-[12px] outline-none"
          style={{ background: "var(--surface)", border: "1px solid var(--line)", minWidth: 240 }}
        />
      </div>

      <Card>
        <div className="-mx-4 -my-4 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="hairline" style={{ color: "var(--ink-3)" }}>
                <Th>Case</Th>
                <Th>Customer</Th>
                <Th>Type</Th>
                <Th right>Amount</Th>
                <Th>Failure</Th>
                <Th right>Attempts</Th>
                <Th>State</Th>
                <Th>Ended because</Th>
                <Th right>Recovered</Th>
                <Th>Next</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr
                  key={c.case_id}
                  className="border-b transition-colors last:border-0 hover:bg-[var(--surface-2)]"
                >
                  <td className="px-4 py-2">
                    <Link
                      href={`/cases/${c.case_id}`}
                      className="tnum hover:underline"
                      style={{ color: "var(--accent)" }}
                    >
                      {c.case_id}
                    </Link>
                    {c.is_holdout && (
                      <span className="ml-1.5">
                        <Chip title="Never touched: randomised control arm">control</Chip>
                      </span>
                    )}
                  </td>
                  <td className="tnum px-4 py-2" style={{ color: "var(--ink-3)" }}>
                    {c.customer_id}
                  </td>
                  <td className="px-4 py-2" style={{ color: "var(--ink-2)" }}>
                    {humanise(c.case_type)}
                  </td>
                  <td className="tnum px-4 py-2 text-right">
                    {rupees(c.amount_paise, { decimals: false })}
                  </td>
                  <td className="px-4 py-2" style={{ color: "var(--ink-2)" }}>
                    <span className="tnum text-[11px]">{c.raw_error_code ?? "—"}</span>{" "}
                    {c.failure_reason.replace(/_/g, " ")}
                  </td>
                  <td className="tnum px-4 py-2 text-right" style={{ color: "var(--ink-3)" }}>
                    {c.attempts_made}
                  </td>
                  <td className="px-4 py-2">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="inline-block h-1.5 w-1.5 rounded-full"
                        style={{ background: STATE_COLOR[c.state] }}
                      />
                      {humanise(c.state)}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-[11px]" style={{ color: "var(--ink-3)" }}>
                    {c.stop_reason ? humanise(c.stop_reason) : "—"}
                  </td>
                  <td
                    className="tnum px-4 py-2 text-right"
                    style={{ color: c.recovered_paise ? "var(--good)" : "var(--ink-3)" }}
                  >
                    {c.recovered_paise ? rupees(c.recovered_paise, { decimals: false }) : "—"}
                  </td>
                  <td className="tnum px-4 py-2 text-[11px]" style={{ color: "var(--ink-3)" }}>
                    {when(c.next_action_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="py-10 text-center text-[12px]" style={{ color: "var(--ink-3)" }}>
              Nothing matches those filters.
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={`px-4 py-2 text-[10px] font-medium uppercase tracking-wider ${
        right ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  label: string;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--ink-3)" }}>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-sm px-2 py-1.5 text-[12px] outline-none"
        style={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" }}
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o === "ALL" ? "All" : humanise(o)}
          </option>
        ))}
      </select>
    </label>
  );
}
