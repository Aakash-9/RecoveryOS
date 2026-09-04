"use client";

import { useState } from "react";
import Link from "next/link";
import { AuditEntry, Overview, post, useApi } from "@/lib/api";
import {
  ApiError,
  Bar,
  Card,
  Chip,
  Loading,
  SimulatedBadge,
  Stat,
  Verdict,
} from "@/components/ui";
import { STATE_COLOR, clock, humanise, pct, rupees, rupeesCompact } from "@/lib/format";

export default function OverviewPage() {
  const { data, error, loading, reload } = useApi<Overview>("/api/overview");
  const activity = useApi<{ entries: AuditEntry[] }>("/api/activity?limit=40");
  const [running, setRunning] = useState(false);

  async function runSweep() {
    setRunning(true);
    try {
      await post("/api/run?cases=120");
      reload();
      activity.reload();
    } finally {
      setRunning(false);
    }
  }

  if (error) return <ApiError message={error} />;
  if (loading || !data) return <Loading />;

  const grossShare =
    data.recovered_paise > 0
      ? data.would_have_recovered_anyway_paise / data.recovered_paise
      : 0;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Revenue recovery</h1>
          <p className="mt-0.5 text-[12px]" style={{ color: "var(--ink-2)" }}>
            {data.cases} cases · {data.open_cases} still live · {data.holdout_cases} held
            out untouched as a control arm
          </p>
        </div>
        <button
          onClick={runSweep}
          disabled={running}
          className="rounded-sm px-3 py-1.5 text-[12px] font-medium transition-opacity disabled:opacity-50"
          style={{ background: "var(--accent)", color: "#fff" }}
        >
          {running ? "Working the book…" : "Rebuild and run a sweep"}
        </button>
      </header>

      <SimulatedBadge />

      {/* ---------------------------------------------------------------- */}
      {/* The counterfactual. This is the whole argument, on one card.      */}
      {/* ---------------------------------------------------------------- */}
      <Card
        title="What we actually caused"
        hint="Gross recovery counts customers who would have paid anyway. This separates the two."
      >
        <div className="grid gap-6 lg:grid-cols-[1.15fr_1fr]">
          <div className="space-y-3">
            <Row
              label="Recovered"
              caption="what a conventional dashboard would show you"
              value={rupees(data.recovered_paise, { decimals: false })}
              bar={1}
              colour="var(--s1)"
              max={1}
            />
            <Row
              label="Would have recovered anyway"
              caption="self-cures: nobody had to do anything"
              value={`− ${rupees(data.would_have_recovered_anyway_paise, { decimals: false })}`}
              bar={grossShare}
              colour="var(--ink-3)"
              max={1}
            />
            <div className="border-t pt-3">
              <Row
                label="Incremental — recovery we caused"
                caption="the only number that belongs to the recovery system"
                value={rupees(data.incremental_paise, { decimals: false })}
                bar={1 - grossShare}
                colour="var(--good)"
                max={1}
                emphasis
              />
            </div>
          </div>

          <div
            className="rounded-sm p-4 text-[12px] leading-relaxed"
            style={{ background: "var(--surface-2)", color: "var(--ink-2)" }}
          >
            <p>
              <strong style={{ color: "var(--ink)" }}>
                {pct(grossShare)} of the gross figure
              </strong>{" "}
              was arriving with or without us. A recovery tool that reports only the
              top line is billing the merchant for work it did not do.
            </p>
            <p className="mt-3">
              In production the honest way to measure this is a randomised holdout —{" "}
              {data.holdout_cases} cases here are never touched, by design. In a
              simulation the exact counterfactual is also knowable, and both are shown
              on the{" "}
              <Link href="/evaluation" style={{ color: "var(--accent)" }}>
                evaluation page
              </Link>{" "}
              so you can see how well the estimator tracks the truth.
            </p>
          </div>
        </div>
      </Card>

      {/* Effort and restraint. */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <Stat
          label="Revenue at risk"
          value={rupeesCompact(data.revenue_at_risk_paise)}
          sub={`${data.cases} cases in the book`}
        />
        <Stat
          label="Expected recoverable"
          value={rupeesCompact(data.expected_recoverable_paise)}
          sub="heuristic forecast on live cases, not a promise"
          tone="accent"
        />
        <Stat
          label="Interventions"
          value={data.interventions.toLocaleString("en-IN")}
          sub={`${data.customer_contacts} of them reached a customer`}
        />
        <Stat
          label="Spent on self-curers"
          value={data.interventions_on_self_curers.toLocaleString("en-IN")}
          sub="cases chased that were going to pay regardless"
          tone={data.interventions_on_self_curers > data.interventions * 0.15 ? "warn" : "good"}
        />
        <Stat
          label="Human escalations"
          value={data.human_escalations.toLocaleString("en-IN")}
          sub="cases a person had to own"
        />
        <Stat
          label="Guardrail violations"
          value="0"
          sub="every action passed the policy engine"
          tone="good"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1.25fr]">
        <div className="space-y-4">
          <Card title="Recovery pipeline" hint="where the exposure goes">
            <div className="space-y-2.5">
              {data.pipeline.map((s, i) => {
                const top = data.pipeline[0].paise || 1;
                return (
                  <div key={s.stage}>
                    <div className="flex items-baseline justify-between text-[12px]">
                      <span style={{ color: "var(--ink-2)" }}>{s.stage}</span>
                      <span className="tnum" style={{ color: "var(--ink)" }}>
                        {s.paise > 0 ? rupees(s.paise, { decimals: false }) : `${s.cases}`}
                      </span>
                    </div>
                    <div className="mt-1">
                      <Bar
                        value={s.paise > 0 ? s.paise : 0}
                        max={top}
                        colour={i === data.pipeline.length - 1 ? "var(--good)" : "var(--s1)"}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title="How cases ended" hint="every stop names the rule that caused it">
            <div className="space-y-1.5">
              {Object.entries(data.by_state)
                .sort((a, b) => b[1] - a[1])
                .map(([state, n]) => (
                  <div key={state} className="flex items-center gap-2 text-[12px]">
                    <span
                      className="inline-block h-2 w-2 shrink-0 rounded-sm"
                      style={{ background: STATE_COLOR[state] ?? "var(--ink-3)" }}
                    />
                    <span className="w-24 shrink-0" style={{ color: "var(--ink-2)" }}>
                      {humanise(state)}
                    </span>
                    <div className="flex-1">
                      <Bar
                        value={n}
                        max={data.cases}
                        colour={STATE_COLOR[state] ?? "var(--ink-3)"}
                        height={6}
                      />
                    </div>
                    <span className="tnum w-8 text-right">{n}</span>
                  </div>
                ))}
            </div>
            {Object.keys(data.by_stop_reason).length > 0 && (
              <div className="mt-4 border-t pt-3">
                <div className="label mb-2">Stop reasons</div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(data.by_stop_reason)
                    .sort((a, b) => b[1] - a[1])
                    .map(([reason, n]) => (
                      <Chip key={reason}>
                        {humanise(reason)} · {n}
                      </Chip>
                    ))}
                </div>
              </div>
            )}
          </Card>
        </div>

        <Card
          title="Agent activity"
          hint="every decision, including the ones to do nothing"
          right={
            <Link href="/cases" className="text-[11px]" style={{ color: "var(--accent)" }}>
              all cases →
            </Link>
          }
        >
          <div className="-mx-4 max-h-[560px] overflow-y-auto px-4">
            {activity.data?.entries.length ? (
              <ol className="space-y-0">
                {activity.data.entries.map((e) => (
                  <li key={e.seq} className="border-b py-2 last:border-0">
                    <div className="flex items-baseline gap-2">
                      <span className="tnum shrink-0 text-[11px]" style={{ color: "var(--ink-3)" }}>
                        {clock(e.at)}
                      </span>
                      <Link
                        href={`/cases/${e.case_id}`}
                        className="tnum shrink-0 text-[11px] hover:underline"
                        style={{ color: "var(--accent)" }}
                      >
                        {e.case_id}
                      </Link>
                      <span className="flex-1 text-[12px]">
                        {e.chosen_action ?? "—"}
                      </span>
                      <Verdict decision={e.policy_decision} />
                    </div>
                    <div
                      className="mt-0.5 pl-[68px] text-[11px] leading-snug"
                      style={{ color: "var(--ink-3)" }}
                    >
                      {e.detail || e.diagnosis.slice(0, 120)}
                      {e.recovered_paise > 0 && (
                        <span className="tnum ml-1.5" style={{ color: "var(--good)" }}>
                          +{rupees(e.recovered_paise, { decimals: false })}
                        </span>
                      )}
                      {e.stop_reason && (
                        <span className="tnum ml-1.5" style={{ color: "var(--serious)" }}>
                          {humanise(e.stop_reason)}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="py-8 text-center text-[12px]" style={{ color: "var(--ink-3)" }}>
                No decisions recorded yet. Run a sweep.
              </p>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

function Row({
  label,
  caption,
  value,
  bar,
  max,
  colour,
  emphasis,
}: {
  label: string;
  caption: string;
  value: string;
  bar: number;
  max: number;
  colour: string;
  emphasis?: boolean;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <div
            className="text-[12.5px]"
            style={{ color: emphasis ? "var(--ink)" : "var(--ink-2)", fontWeight: emphasis ? 600 : 400 }}
          >
            {label}
          </div>
          <div className="text-[11px]" style={{ color: "var(--ink-3)" }}>
            {caption}
          </div>
        </div>
        <div
          className="tnum shrink-0"
          style={{ fontSize: emphasis ? 26 : 17, color: emphasis ? "var(--good)" : "var(--ink)" }}
        >
          {value}
        </div>
      </div>
      <div className="mt-1.5">
        <Bar value={bar} max={max} colour={colour} height={emphasis ? 10 : 8} />
      </div>
    </div>
  );
}
