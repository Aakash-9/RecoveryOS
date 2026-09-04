"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { CaseDetail, Scored, useApi } from "@/lib/api";
import {
  ApiError,
  Bar,
  Card,
  Chip,
  Loading,
  UtilityBreakdown,
  Verdict,
} from "@/components/ui";
import { STATE_COLOR, VERDICT_COLOR, humanise, pct, rupees, when } from "@/lib/format";

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useApi<CaseDetail>(`/api/cases/${id}`);

  if (error) return <ApiError message={error} />;
  if (loading || !data) return <Loading />;

  const c = data.case;
  const chosen = data.candidates.find((x) => x.policy.decision === "PASS" && x.utility_paise > 0);
  const bestBlocked = data.candidates.find(
    (x) => x.action !== "NO_ACTION" && x.policy.decision !== "PASS"
  );
  const maxUtility = Math.max(...data.candidates.map((x) => Math.abs(x.utility_paise)), 1);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/cases" className="text-[11px]" style={{ color: "var(--accent)" }}>
            ← cases
          </Link>
          <h1 className="tnum mt-1 text-[19px] font-semibold tracking-tight">{c.case_id}</h1>
          <p className="mt-0.5 text-[12px]" style={{ color: "var(--ink-2)" }}>
            {humanise(c.case_type)} · {String(data.customer.name)} ({c.customer_id}) ·{" "}
            {String(data.customer.segment).toLowerCase()}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {c.is_holdout && <Chip colour="var(--warning)">control arm — never touched</Chip>}
          <Chip colour={STATE_COLOR[c.state]}>{humanise(c.state)}</Chip>
          {c.stop_reason && <Chip colour="var(--serious)">{humanise(c.stop_reason)}</Chip>}
        </div>
      </header>

      {/* Exposure, diagnosis, baseline. */}
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <div className="space-y-4">
          <Card title="Exposure">
            <div className="tnum text-[26px] leading-none">
              {rupees(c.amount_paise, { decimals: false })}
            </div>
            <dl className="mt-3 space-y-1.5 text-[12px]">
              <Field k="Failure" v={`${c.raw_error_code ?? "—"} · ${c.failure_reason.replace(/_/g, " ")}`} />
              <Field k="Class" v={humanise(data.diagnosis.retryability)} />
              <Field k="Instrument" v={`${c.instrument_type}${c.is_recurring ? " · mandate" : ""}`} />
              <Field k="Failed at" v={when(c.created_at)} />
              <Field k="Attempts spent" v={String(c.attempts_made)} />
              <Field
                k="Recovered"
                v={c.recovered_paise ? rupees(c.recovered_paise, { decimals: false }) : "—"}
              />
              <Field k="Next decision" v={when(c.next_action_at)} />
            </dl>
          </Card>

          <Card title="Customer">
            <dl className="space-y-1.5 text-[12px]">
              <Field k="Tenure" v={`${data.customer.tenure_months} months`} />
              <Field
                k="Payment history"
                v={`${data.customer.prior_payments_ok} ok · ${data.customer.prior_payments_failed} failed`}
              />
              <Field
                k="Self-cured before"
                v={`${data.customer.prior_self_cures} of ${data.customer.prior_payments_failed}`}
              />
              <Field
                k="Pays after payday"
                v={data.customer.pays_after_payday ? "yes" : "no"}
              />
              <Field k="Channel" v={String(data.customer.preferred_channel)} />
              <Field
                k="Commercial consent"
                v={
                  data.customer.opted_out
                    ? "opted out"
                    : data.customer.dlt_consent
                    ? "on file"
                    : "not registered"
                }
              />
            </dl>
            <div className="mt-3 border-t pt-3">
              <div className="label mb-1.5">Attention already spent</div>
              <dl className="space-y-1.5 text-[12px]">
                <Field k="Contacts, 24h" v={String(data.context.contacts_24h)} />
                <Field k="Contacts, 7 days" v={String(data.context.contacts_7d)} />
                <Field k="Last contact" v={when(data.context.last_contact_at as string)} />
                <Field
                  k="Other live cases"
                  v={
                    Number(data.context.open_sibling_cases) > 0
                      ? `${data.context.open_sibling_cases} · largest ${rupees(
                          Number(data.context.sibling_max_amount_paise),
                          { decimals: false }
                        )}`
                      : "none"
                  }
                />
              </dl>
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Diagnosis" hint="deterministic, from the failure code — never model-decided">
            <p className="text-[13px] leading-relaxed" style={{ color: "var(--ink-2)" }}>
              {data.diagnosis.text}
            </p>

            <div className="mt-4 grid gap-4 border-t pt-4 sm:grid-cols-[200px_1fr]">
              <div>
                <div className="label">Recovers untouched</div>
                <div className="tnum mt-1 text-[26px] leading-none" style={{ color: "var(--s2)" }}>
                  {pct(data.p_self_cure)}
                </div>
                <div className="mt-2">
                  <Bar value={data.p_self_cure} max={1} colour="var(--s2)" />
                </div>
                <div className="mt-2 text-[11px]" style={{ color: "var(--ink-3)" }}>
                  Heuristic self-cure estimate. Every action below is scored against this,
                  not against zero.
                </div>
              </div>
              <ul className="space-y-1 text-[12px]" style={{ color: "var(--ink-2)" }}>
                {data.self_cure_reasoning.map((r, i) => (
                  <li key={i} className="flex gap-2">
                    <span style={{ color: "var(--ink-3)" }}>·</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>

            {data.promise.state !== "NONE" && (
              <div
                className="mt-4 rounded-sm p-3 text-[12px]"
                style={{ background: "var(--surface-2)" }}
              >
                <div className="label mb-1">Promise to pay · {humanise(data.promise.state)}</div>
                {data.promise.source_text && (
                  <p className="italic" style={{ color: "var(--ink-2)" }}>
                    “{data.promise.source_text}”
                  </p>
                )}
                <p className="mt-1.5 tnum" style={{ color: "var(--ink-3)" }}>
                  due {when(data.promise.promised_for)} ·{" "}
                  {rupees(data.promise.promised_amount_paise ?? 0, { decimals: false })} · confidence{" "}
                  {pct(data.promise.confidence)}
                </p>
              </div>
            )}
          </Card>

          <Card
            title="Action scoring"
            hint="utility = expected incremental − cost − customer fatigue − risk. NO_ACTION is exactly zero, so every option has to beat leaving them alone."
            right={
              <div className="text-[11px]" style={{ color: "var(--ink-3)" }}>
                recovery score{" "}
                <span className="tnum" style={{ color: "var(--ink)" }}>
                  {data.recovery_score}
                </span>
                /100
              </div>
            }
          >
            <ol className="space-y-0">
              {data.candidates.map((s) => (
                <ActionRow
                  key={s.label}
                  s={s}
                  max={maxUtility}
                  chosen={chosen?.label === s.label}
                />
              ))}
            </ol>

            <div
              className="mt-4 rounded-sm p-3 text-[12px] leading-relaxed"
              style={{ background: "var(--surface-2)", color: "var(--ink-2)" }}
            >
              {chosen ? (
                <>
                  <strong style={{ color: "var(--ink)" }}>{chosen.label}</strong> is chosen: it
                  adds {pct(chosen.uplift, 1)} on top of the {pct(data.p_self_cure)} that would
                  arrive anyway, worth{" "}
                  {rupees(chosen.expected_incremental_paise, { decimals: false })} against{" "}
                  {rupees(
                    chosen.cost_paise + chosen.fatigue_penalty_paise + chosen.risk_penalty_paise,
                    { decimals: false }
                  )}{" "}
                  of cost, fatigue and risk.
                </>
              ) : (
                <>
                  <strong style={{ color: "var(--ink)" }}>No action</strong> is chosen. Nothing
                  available beats leaving this customer alone
                  {bestBlocked && (
                    <>
                      {" "}
                      — the highest-scoring option, {bestBlocked.label}, was{" "}
                      {bestBlocked.policy.decision.toLowerCase().replace("_", " ")} by{" "}
                      <span className="tnum">{bestBlocked.policy.rules[0]?.rule_id}</span>
                    </>
                  )}
                  .
                </>
              )}
            </div>
          </Card>
        </div>
      </div>

      {/* Decision history. */}
      <Card
        title="Decision trail"
        hint="every pass of the loop, hash-chained. GET /api/audit/verify re-derives the whole chain."
      >
        {data.audit.length === 0 ? (
          <p className="py-6 text-center text-[12px]" style={{ color: "var(--ink-3)" }}>
            No decisions recorded for this case yet. Run a sweep from the overview.
          </p>
        ) : (
          <ol className="space-y-0">
            {data.audit.map((a) => (
              <li key={a.seq} className="border-b py-3 last:border-0">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="tnum text-[11px]" style={{ color: "var(--ink-3)" }}>
                    {when(a.at)}
                  </span>
                  <Chip>iteration {a.iteration}</Chip>
                  <span className="text-[13px] font-medium">{a.chosen_action}</span>
                  <Verdict decision={a.policy_decision} />
                  <span className="text-[11px]" style={{ color: "var(--ink-3)" }}>
                    {humanise(a.state_before)} → {humanise(a.state_after)}
                  </span>
                  {a.recovered_paise > 0 && (
                    <span className="tnum text-[12px]" style={{ color: "var(--good)" }}>
                      +{rupees(a.recovered_paise, { decimals: false })}
                    </span>
                  )}
                  {a.stop_reason && (
                    <Chip colour="var(--serious)">{humanise(a.stop_reason)}</Chip>
                  )}
                </div>
                {a.detail && (
                  <p className="mt-1 text-[12px]" style={{ color: "var(--ink-2)" }}>
                    {a.detail}
                  </p>
                )}
                {a.narrative && (
                  <p className="mt-1 text-[12px] italic" style={{ color: "var(--ink-3)" }}>
                    {a.narrative}
                  </p>
                )}
                {a.policy_rules.length > 0 && (
                  <ul className="mt-1.5 space-y-1">
                    {a.policy_rules.map((r, i) => (
                      <li key={i} className="text-[11px]">
                        <span
                          className="tnum"
                          style={{ color: VERDICT_COLOR[r.decision] ?? "var(--ink-3)" }}
                        >
                          {r.rule_id}
                        </span>{" "}
                        <span style={{ color: "var(--ink-2)" }}>{r.message}</span>{" "}
                        <span style={{ color: "var(--ink-3)" }}>[{r.citation}]</span>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="mt-1.5 flex gap-3 text-[10px]" style={{ color: "var(--ink-3)" }}>
                  <span className="tnum">hash {a.entry_hash?.slice(0, 16)}…</span>
                  <span className="tnum">prev {a.prev_hash?.slice(0, 16)}…</span>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>

      {c.archetype_lesson && (
        <p className="text-[11px]" style={{ color: "var(--ink-3)" }}>
          Scenario <span className="tnum">{c.archetype}</span> — {c.archetype_lesson}
        </p>
      )}
    </div>
  );
}

function ActionRow({ s, max, chosen }: { s: Scored; max: number; chosen: boolean }) {
  const blocked = s.policy.decision !== "PASS";
  return (
    <li
      className="border-b py-2.5 last:border-0"
      style={{ opacity: blocked && s.action !== "NO_ACTION" ? 0.72 : 1 }}
    >
      <div className="flex flex-wrap items-center gap-2">
        {chosen && (
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--good)" }}
            title="chosen"
          />
        )}
        <span className="text-[13px] font-medium">{s.label}</span>
        {s.policy.decision !== "PASS" && <Verdict decision={s.policy.decision} />}
        <span className="ml-auto flex items-center gap-3">
          <span className="w-28">
            <Bar
              value={Math.max(0, s.utility_paise)}
              max={max}
              colour={s.utility_paise > 0 ? (chosen ? "var(--good)" : "var(--s1)") : "var(--ink-3)"}
              height={6}
            />
          </span>
          <span
            className="tnum w-24 text-right text-[13px]"
            style={{ color: s.utility_paise > 0 ? "var(--ink)" : "var(--ink-3)" }}
          >
            {rupees(s.utility_paise, { decimals: false })}
          </span>
        </span>
      </div>

      <div className="mt-1.5">
        <UtilityBreakdown
          incremental={s.expected_incremental_paise}
          cost={s.cost_paise}
          fatigue={s.fatigue_penalty_paise}
          risk={s.risk_penalty_paise}
          utility={s.utility_paise}
        />
      </div>

      <ul className="mt-1 space-y-0.5">
        {s.explanation.slice(0, 4).map((e, i) => (
          <li key={i} className="text-[11px]" style={{ color: "var(--ink-3)" }}>
            · {e}
          </li>
        ))}
        {s.policy.rules.map((r, i) => (
          <li key={`r${i}`} className="text-[11px]">
            <span className="tnum" style={{ color: VERDICT_COLOR[r.decision] ?? "var(--ink-3)" }}>
              {r.rule_id}
            </span>{" "}
            <span style={{ color: "var(--ink-2)" }}>{r.message}</span>{" "}
            <span style={{ color: "var(--ink-3)" }}>[{r.citation}]</span>
          </li>
        ))}
      </ul>
    </li>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt style={{ color: "var(--ink-3)" }}>{k}</dt>
      <dd className="tnum text-right" style={{ color: "var(--ink-2)" }}>
        {v}
      </dd>
    </div>
  );
}
