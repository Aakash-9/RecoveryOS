"use client";

import { PolicyView, useApi } from "@/lib/api";
import { ApiError, Card, Chip, Loading } from "@/components/ui";
import { rupees } from "@/lib/format";

const GROUPS: { title: string; hint: string; match: (id: string) => boolean; tone: string }[] = [
  {
    title: "Regulatory",
    hint: "not house style — these come from the RBI e-mandate framework and TRAI's commercial-communication rules",
    match: (id) => id.startsWith("RBI-") || id.startsWith("TRAI-") || id.startsWith("CONSUMER-"),
    tone: "var(--critical)",
  },
  {
    title: "Merchant-configured",
    hint: "the bounds this merchant set on autonomous recovery, from merchant_policy.json",
    match: (id) => id.startsWith("MERCHANT-"),
    tone: "var(--warning)",
  },
  {
    title: "Domain safety",
    hint: "actions that cannot succeed, or that break faith with a customer",
    match: (id) => id.startsWith("POLICY-"),
    tone: "var(--serious)",
  },
  {
    title: "Batch allocation",
    hint: "what a single sweep can afford: money, people, and a customer's attention",
    match: (id) => id.startsWith("ALLOCATOR-"),
    tone: "var(--s1)",
  },
];

export default function PolicyPage() {
  const { data, error, loading } = useApi<PolicyView>("/api/policy");
  if (error) return <ApiError message={error} />;
  if (loading || !data) return <Loading />;

  const p = data.policy as Record<string, never> & {
    policy_id: string;
    merchant_name: string;
    max_retry_attempts: number;
    min_retry_gap_hours: number;
    max_contacts_per_24h: number;
    max_contacts_per_7d: number;
    human_approval_threshold_paise: number;
    max_discount_percent: number;
    allowed_actions: string[];
    quiet_hours_start_hour: number;
    quiet_hours_end_hour: number;
    min_utility_paise: number;
    intervention_budget_paise: number;
    human_review_capacity: number;
    max_iterations_per_case: number;
    holdout_fraction: number;
    action_costs_paise: Record<string, number>;
    cost_basis_note: string;
    goodwill_cost_per_contact_paise: number;
    goodwill_cost_note: string;
    min_utility_note: string;
    holdout_note: string;
  };

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[19px] font-semibold tracking-tight">Guardrails</h1>
        <p className="mt-0.5 max-w-3xl text-[12px]" style={{ color: "var(--ink-2)" }}>
          The agent proposes; this layer disposes. It is deterministic, it never calls a
          language model, and it is the only thing that can authorise a money action. Every
          refusal names its rule and cites where that rule comes from.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Merchant policy" hint={`${p.merchant_name} · ${p.policy_id}`}>
          <dl className="space-y-2 text-[12px]">
            <Limit k="Automated retries per case" v={String(p.max_retry_attempts)} />
            <Limit k="Minimum gap between debits" v={`${p.min_retry_gap_hours}h`} />
            <Limit k="Contacts per customer, 24h" v={String(p.max_contacts_per_24h)} />
            <Limit
              k="Contacts per customer, 7 days"
              v={String(p.max_contacts_per_7d)}
              note="counted across all of that customer's open cases, not per case"
            />
            <Limit
              k="Human approval threshold"
              v={rupees(p.human_approval_threshold_paise, { decimals: false })}
              note="above this, a person approves before anything is done"
            />
            <Limit k="Maximum discount" v={`${p.max_discount_percent}%`} />
            <Limit
              k="Commercial contact window"
              v={`${String(p.quiet_hours_end_hour).padStart(2, "0")}:00 – ${String(
                p.quiet_hours_start_hour
              ).padStart(2, "0")}:00 IST`}
            />
            <Limit
              k="Human review capacity per sweep"
              v={String(p.human_review_capacity)}
              note="a collections team is the one resource more code cannot scale"
            />
            <Limit
              k="Intervention budget per sweep"
              v={rupees(p.intervention_budget_paise, { decimals: false })}
            />
            <Limit k="Maximum loop iterations per case" v={String(p.max_iterations_per_case)} />
            <Limit
              k="Control arm"
              v={`${Math.round(p.holdout_fraction * 100)}% held out`}
              note={p.holdout_note}
            />
            <Limit
              k="Floor before spending human time"
              v={rupees(p.min_utility_paise, { decimals: false })}
              note={p.min_utility_note}
            />
          </dl>

          <div className="mt-4 border-t pt-3">
            <div className="label mb-2">Actions this merchant permits</div>
            <div className="flex flex-wrap gap-1.5">
              {p.allowed_actions.map((a) => (
                <Chip key={a} colour="var(--good)">
                  {a}
                </Chip>
              ))}
            </div>
            <p className="mt-2 text-[11px]" style={{ color: "var(--ink-3)" }}>
              Anything not on this list is refused, whatever proposes it — including the
              language model.
            </p>
          </div>
        </Card>

        <Card title="Unit economics" hint="heuristic, documented, and the same for every policy under test">
          <table className="w-full text-[12px]">
            <tbody>
              {Object.entries(p.action_costs_paise).map(([action, cost]) => (
                <tr key={action} className="border-b last:border-0">
                  <td className="py-1.5" style={{ color: "var(--ink-2)" }}>
                    {action}
                  </td>
                  <td className="tnum py-1.5 text-right">
                    {rupees(cost, { decimals: false })}
                  </td>
                </tr>
              ))}
              <tr>
                <td className="py-1.5" style={{ color: "var(--ink-2)" }}>
                  Customer attention, first contact
                </td>
                <td className="tnum py-1.5 text-right">
                  {rupees(p.goodwill_cost_per_contact_paise, { decimals: false })}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="mt-3 text-[11px] leading-relaxed" style={{ color: "var(--ink-3)" }}>
            {p.cost_basis_note}
          </p>
          <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--ink-3)" }}>
            {p.goodwill_cost_note} It rises with the square of recent contacts: the second
            message in a week costs four times the first, the third nine times.
          </p>
          <div className="mt-3 border-t pt-3 text-[11px]" style={{ color: "var(--ink-3)" }}>
            RBI additional-factor ceilings —{" "}
            <span className="tnum" style={{ color: "var(--ink-2)" }}>
              {rupees(data.afa_ceiling_paise, { decimals: false })}
            </span>{" "}
            generally,{" "}
            <span className="tnum" style={{ color: "var(--ink-2)" }}>
              {rupees(data.afa_ceiling_exempt_paise, { decimals: false })}
            </span>{" "}
            for insurance premiums, mutual-fund subscriptions and credit-card bills.
          </div>
        </Card>
      </div>

      {GROUPS.map((g) => {
        const rules = data.rules.filter((r) => g.match(r.rule_id));
        if (!rules.length) return null;
        return (
          <Card key={g.title} title={g.title} hint={g.hint}>
            <ul className="space-y-3">
              {rules.map((r) => (
                <li key={r.rule_id} className="border-b pb-3 last:border-0 last:pb-0">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="tnum text-[12px] font-semibold" style={{ color: g.tone }}>
                      {r.rule_id}
                    </span>
                    <span className="text-[10.5px]" style={{ color: "var(--ink-3)" }}>
                      {r.citation}
                    </span>
                  </div>
                  <p className="mt-1 text-[12px]" style={{ color: "var(--ink-2)" }}>
                    {r.description}
                  </p>
                </li>
              ))}
            </ul>
          </Card>
        );
      })}
    </div>
  );
}

function Limit({ k, v, note }: { k: string; v: string; note?: string }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <dt style={{ color: "var(--ink-2)" }}>{k}</dt>
        <dd className="tnum shrink-0">{v}</dd>
      </div>
      {note && (
        <div className="text-[10.5px] leading-snug" style={{ color: "var(--ink-3)" }}>
          {note}
        </div>
      )}
    </div>
  );
}
