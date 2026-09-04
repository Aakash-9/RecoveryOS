"use client";

import { Evaluation, PolicyMetrics, useApi } from "@/lib/api";
import { ApiError, Bar, Card, Chip, Loading, Stat } from "@/components/ui";
import { SERIES, pct, rupees, rupeesCompact } from "@/lib/format";

type Row = {
  label: string;
  hint?: string;
  get: (m: PolicyMetrics) => string;
  num?: (m: PolicyMetrics) => number;
  lowerIsBetter?: boolean;
  emphasis?: boolean;
};

const ROWS: Row[] = [
  {
    label: "Gross recovered",
    hint: "what a conventional dashboard reports",
    get: (m) => rupees(m.recovered_paise, { decimals: false }),
    num: (m) => m.recovered_paise,
  },
  {
    label: "Would have arrived untouched",
    hint: "self-cures — identical across policies, because it is a property of the book",
    get: (m) => rupees(m.true_counterfactual_paise, { decimals: false }),
  },
  {
    label: "Incremental recovered",
    hint: "the exact counterfactual, knowable only inside a simulation",
    get: (m) => rupees(m.true_incremental_paise, { decimals: false }),
    num: (m) => m.true_incremental_paise,
    emphasis: true,
  },
  {
    label: "…estimated from the control arm",
    hint: "what a real deployment could measure, using only the untouched holdout",
    get: (m) => rupees(m.incremental_paise, { decimals: false }),
  },
  {
    label: "Capture rate vs lawful oracle",
    hint: "share of what was actually there to win",
    get: (m) => pct(m.capture_rate, 1),
    num: (m) => m.capture_rate,
  },
  {
    label: "Cases recovered",
    get: (m) => `${m.cases_recovered} / ${m.cases}`,
    num: (m) => m.cases_recovered,
  },
  {
    label: "Interventions executed",
    get: (m) => String(m.interventions),
    num: (m) => m.interventions,
    lowerIsBetter: true,
  },
  {
    label: "Customer contacts sent",
    hint: "messages and links that reached a person",
    get: (m) => String(m.customer_contacts),
    num: (m) => m.customer_contacts,
    lowerIsBetter: true,
  },
  {
    label: "Chased customers who would have paid",
    get: (m) => String(m.interventions_on_self_curers),
    num: (m) => m.interventions_on_self_curers,
    lowerIsBetter: true,
  },
  {
    label: "Human escalations",
    get: (m) => String(m.human_escalations),
    num: (m) => m.human_escalations,
    lowerIsBetter: true,
  },
  {
    label: "Guardrail violations",
    hint: "actions executed after the policy engine refused them",
    get: (m) => String(m.guardrail_violations),
    num: (m) => m.guardrail_violations,
    lowerIsBetter: true,
    emphasis: true,
  },
  {
    label: "Customers driven to opt out",
    get: (m) => String(m.customers_opted_out),
    num: (m) => m.customers_opted_out,
    lowerIsBetter: true,
  },
  {
    label: "Incremental per contact",
    hint: "the efficiency measure that matters",
    get: (m) => rupees(m.true_incremental_per_contact_paise, { decimals: false }),
    num: (m) => m.true_incremental_per_contact_paise,
  },
  { label: "Actions per treated case", get: (m) => m.actions_per_case.toFixed(2) },
];

export default function EvaluationPage() {
  const { data, error, loading } = useApi<Evaluation>("/api/evaluation");

  if (error) return <ApiError message={error} />;
  if (loading || !data) return <Loading />;

  const names = Object.keys(data.policies);
  const ros = data.policies["RECOVERYOS"];
  const compliant = data.policies["RULEBOOK+RULES"];
  const rulebook = data.policies["RULEBOOK"];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[19px] font-semibold tracking-tight">
          Four policies, one book of cases
        </h1>
        <p className="mt-0.5 max-w-4xl text-[12px]" style={{ color: "var(--ink-2)" }}>
          {data.n_cases} synthetic cases, seed {data.seed}. Every policy faces byte-identical
          cases and identical customer behaviour — the simulator uses common random numbers, so
          a difference in the result can only come from the decision.
        </p>
      </header>

      {ros && compliant && rulebook && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat
            label="Incremental recovered"
            value={rupeesCompact(ros.true_incremental_paise)}
            sub={`${(ros.true_incremental_paise / Math.max(1, compliant.true_incremental_paise)).toFixed(2)}× the fully compliant dunning ladder`}
            tone="good"
            emphasis
          />
          <Stat
            label="Customer contacts"
            value={String(ros.customer_contacts)}
            sub={`against ${rulebook.customer_contacts} for the standard ladder — ${pct(
              1 - ros.customer_contacts / Math.max(1, rulebook.customer_contacts)
            )} fewer`}
            tone="accent"
          />
          <Stat
            label="Guardrail violations"
            value={String(ros.guardrail_violations)}
            sub={`the standard ladder committed ${rulebook.guardrail_violations}`}
            tone="good"
          />
          <Stat
            label="Customers lost"
            value={String(ros.customers_opted_out)}
            sub={`over-contacting cost the standard ladder ${rulebook.customers_opted_out}`}
            tone="good"
          />
        </div>
      )}

      <Card title="Policy comparison">
        <div className="-mx-4 -my-4 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="hairline">
                <th className="px-4 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider" style={{ color: "var(--ink-3)" }}>
                  Measure
                </th>
                {names.map((n) => (
                  <th key={n} className="px-4 py-2.5 text-right">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="inline-block h-2 w-2 rounded-sm"
                        style={{ background: SERIES[n] ?? "var(--ink-3)" }}
                      />
                      <span className="text-[11px] font-semibold">{n}</span>
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => {
                const values = row.num ? names.map((n) => row.num!(data.policies[n])) : null;
                const best = values
                  ? row.lowerIsBetter
                    ? Math.min(...values)
                    : Math.max(...values)
                  : null;
                return (
                  <tr
                    key={row.label}
                    className="border-b last:border-0"
                    style={row.emphasis ? { background: "var(--surface-2)" } : undefined}
                  >
                    <td className="px-4 py-2">
                      <div style={{ color: row.emphasis ? "var(--ink)" : "var(--ink-2)", fontWeight: row.emphasis ? 600 : 400 }}>
                        {row.label}
                      </div>
                      {row.hint && (
                        <div className="text-[10.5px]" style={{ color: "var(--ink-3)" }}>
                          {row.hint}
                        </div>
                      )}
                    </td>
                    {names.map((n, i) => {
                      const m = data.policies[n];
                      const isBest = values !== null && values[i] === best;
                      return (
                        <td
                          key={n}
                          className="tnum px-4 py-2 text-right"
                          style={{
                            color: isBest ? "var(--good)" : "var(--ink)",
                            fontWeight: isBest ? 600 : 400,
                          }}
                        >
                          {row.get(m)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="Incremental recovery"
          hint="bars share one baseline and one scale — no second axis"
        >
          <div className="space-y-3">
            {names.map((n) => {
              const m = data.policies[n];
              const max = Math.max(...names.map((x) => data.policies[x].true_incremental_paise));
              return (
                <div key={n}>
                  <div className="flex items-baseline justify-between text-[12px]">
                    <span style={{ color: "var(--ink-2)" }}>{n}</span>
                    <span className="tnum">{rupees(m.true_incremental_paise, { decimals: false })}</span>
                  </div>
                  <div className="mt-1">
                    <Bar value={m.true_incremental_paise} max={max} colour={SERIES[n]} height={10} />
                  </div>
                  <div className="mt-1 text-[10.5px]" style={{ color: "var(--ink-3)" }}>
                    {m.customer_contacts} contacts · {m.guardrail_violations} violations ·{" "}
                    {m.customers_opted_out} customers lost
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card
          title="The ceiling"
          hint="a lawful oracle with perfect foresight — a denominator, not a target"
        >
          <div className="space-y-3">
            <Line
              label="Total revenue at risk"
              value={data.oracle.total_at_risk_paise}
              max={data.oracle.total_at_risk_paise}
              colour="var(--surface-3)"
            />
            <Line
              label="Recoverable by any lawful action"
              value={data.oracle.recoverable_paise}
              max={data.oracle.total_at_risk_paise}
              colour="var(--s1)"
              note={`${data.oracle.recoverable_cases} of ${data.n_cases} cases`}
            />
            <Line
              label="…of which arrives untouched"
              value={data.oracle.self_cure_paise}
              max={data.oracle.total_at_risk_paise}
              colour="var(--ink-3)"
              note={`${data.oracle.self_cure_cases} cases self-cure`}
            />
            <Line
              label="Genuinely winnable by intervening"
              value={data.oracle.winnable_paise}
              max={data.oracle.total_at_risk_paise}
              colour="var(--good)"
            />
          </div>
          <p className="mt-3 text-[11px] leading-relaxed" style={{ color: "var(--ink-3)" }}>
            {data.oracle.note}
          </p>
        </Card>
      </div>

      {data.sensitivity && (
        <Card
          title="Sensitivity across seeds"
          hint="one seed is a screenshot, not a result — this is the median and range across independent worlds"
        >
          <div className="-mx-4 -my-4 overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="hairline" style={{ color: "var(--ink-3)" }}>
                  <th className="px-4 py-2 text-left text-[10px] font-medium uppercase tracking-wider">Policy</th>
                  <th className="px-4 py-2 text-right text-[10px] font-medium uppercase tracking-wider">Median incremental</th>
                  <th className="px-4 py-2 text-right text-[10px] font-medium uppercase tracking-wider">Range</th>
                  <th className="px-4 py-2 text-right text-[10px] font-medium uppercase tracking-wider">Contacts</th>
                  <th className="px-4 py-2 text-right text-[10px] font-medium uppercase tracking-wider">Violations</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.sensitivity).map(([name, runs]) => {
                  const inc = runs.map((r) => r.true_incremental_paise).sort((a, b) => a - b);
                  const median = inc[Math.floor(inc.length / 2)];
                  const contacts = runs.map((r) => r.customer_contacts).sort((a, b) => a - b);
                  const viol = runs.map((r) => r.guardrail_violations).sort((a, b) => a - b);
                  return (
                    <tr key={name} className="border-b last:border-0">
                      <td className="px-4 py-2">
                        <span className="inline-flex items-center gap-1.5">
                          <span
                            className="inline-block h-2 w-2 rounded-sm"
                            style={{ background: SERIES[name] ?? "var(--ink-3)" }}
                          />
                          {name}
                        </span>
                      </td>
                      <td className="tnum px-4 py-2 text-right">{rupees(median, { decimals: false })}</td>
                      <td className="tnum px-4 py-2 text-right text-[11px]" style={{ color: "var(--ink-3)" }}>
                        {rupees(inc[0], { decimals: false })} … {rupees(inc[inc.length - 1], { decimals: false })}
                      </td>
                      <td className="tnum px-4 py-2 text-right">{contacts[Math.floor(contacts.length / 2)]}</td>
                      <td className="tnum px-4 py-2 text-right">{viol[Math.floor(viol.length / 2)]}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-4 text-[11px]" style={{ color: "var(--ink-3)" }}>
            Regenerate with{" "}
            <code className="tnum">
              python scripts/run_evaluation.py --cases 150 --sweep 42,43,44,45,46,47,48
            </code>
          </p>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {names.map((n) => (
          <div key={n} className="card p-3">
            <div className="mb-1 flex items-center gap-1.5">
              <span
                className="inline-block h-2 w-2 rounded-sm"
                style={{ background: SERIES[n] ?? "var(--ink-3)" }}
              />
              <span className="text-[12px] font-semibold">{n}</span>
            </div>
            <p className="text-[11px] leading-snug" style={{ color: "var(--ink-3)" }}>
              {data.policy_descriptions[n]}
            </p>
            {Object.keys(data.policies[n].violation_rules).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {Object.entries(data.policies[n].violation_rules)
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 4)
                  .map(([rule, count]) => (
                    <Chip key={rule} colour="var(--critical)">
                      {rule} ×{count}
                    </Chip>
                  ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div
        className="card p-4 text-[11.5px] leading-relaxed"
        style={{ color: "var(--ink-2)", borderColor: "var(--warning)" }}
      >
        <div className="label mb-1.5" style={{ color: "var(--warning)" }}>
          Read this before quoting any number above
        </div>
        <p>{data.disclaimer}</p>
        <p className="mt-2">
          Two counterfactuals are shown deliberately. The exact one is only knowable inside a
          simulation, where the latent self-cure draw is on record. The control-arm estimate is
          what a real deployment could measure, and it is noisy at this holdout size — comparing
          them is how you tell whether the estimator is working, and it is the honest way to
          present a number this easy to inflate.
        </p>
      </div>
    </div>
  );
}

function Line({
  label,
  value,
  max,
  colour,
  note,
}: {
  label: string;
  value: number;
  max: number;
  colour: string;
  note?: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between text-[12px]">
        <span style={{ color: "var(--ink-2)" }}>{label}</span>
        <span className="tnum">{rupees(value, { decimals: false })}</span>
      </div>
      <div className="mt-1">
        <Bar value={value} max={max} colour={colour} />
      </div>
      {note && (
        <div className="mt-0.5 text-[10.5px]" style={{ color: "var(--ink-3)" }}>
          {note}
        </div>
      )}
    </div>
  );
}
