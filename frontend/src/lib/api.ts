"use client";

import { useCallback, useEffect, useState } from "react";

export const API =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8001";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${path}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetch on mount, and again whenever `reload()` is called.
 *
 * The nonce is what makes `reload` work without calling setState synchronously
 * inside the effect body — React 19 flags that as a cascading render, and
 * bumping a counter from an event handler is the pattern it wants instead.
 */
export function useApi<T>(path: string) {
  const [state, setState] = useState<ApiState<T>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    request<T>(path)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((e: Error) => {
        if (!cancelled) setState({ data: null, error: e.message, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, [path, nonce]);

  const reload = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    setNonce((n) => n + 1);
  }, []);

  return { ...state, reload };
}

export const post = <T,>(path: string) => request<T>(path, { method: "POST" });

// --------------------------------------------------------------------------
// Shapes returned by the backend. Money is always integer paise.
// --------------------------------------------------------------------------

export interface Overview {
  clock: string;
  cases: number;
  revenue_at_risk_paise: number;
  expected_recoverable_paise: number;
  recovered_paise: number;
  would_have_recovered_anyway_paise: number;
  incremental_paise: number;
  interventions: number;
  customer_contacts: number;
  interventions_on_self_curers: number;
  human_escalations: number;
  holdout_cases: number;
  open_cases: number;
  by_state: Record<string, number>;
  by_stop_reason: Record<string, number>;
  pipeline: { stage: string; cases: number; paise: number }[];
  disclaimer: string;
}

export interface CaseRow {
  case_id: string;
  customer_id: string;
  case_type: string;
  amount_paise: number;
  failure_reason: string;
  raw_error_code: string | null;
  state: string;
  stop_reason: string | null;
  recovered_paise: number;
  attempts_made: number;
  is_holdout: boolean;
  is_recurring: boolean;
  instrument_type: string;
  created_at: string;
  next_action_at: string | null;
  archetype: string;
  archetype_lesson: string;
}

export interface RuleHit {
  rule_id: string;
  decision: string;
  citation: string;
  message: string;
  defer_hours: number;
}

export interface Scored {
  action: string;
  label: string;
  variant: string | null;
  delay_hours: number;
  rationale: string;
  p_self_cure: number;
  p_treated: number;
  uplift: number;
  expected_incremental_paise: number;
  cost_paise: number;
  fatigue_penalty_paise: number;
  risk_penalty_paise: number;
  utility_paise: number;
  explanation: string[];
  policy: { decision: string; rules: RuleHit[] };
}

export interface CaseDetail {
  case: CaseRow;
  customer: Record<string, string | number | boolean>;
  context: Record<string, number | string | null>;
  promise: {
    state: string;
    promised_for: string | null;
    promised_amount_paise: number | null;
    confidence: number;
    source_text: string | null;
  };
  diagnosis: { text: string; retryability: string };
  recovery_score: number;
  p_self_cure: number;
  self_cure_reasoning: string[];
  candidates: Scored[];
  audit: AuditEntry[];
}

export interface AuditEntry {
  seq: number;
  case_id?: string;
  at: string;
  iteration: number;
  state_before: string;
  state_after: string;
  diagnosis: string;
  retryability: string;
  p_self_cure: number;
  chosen_action: string | null;
  candidates: {
    action: string;
    uplift: number;
    expected_incremental_paise: number;
    cost_paise: number;
    fatigue_paise: number;
    risk_paise: number;
    utility_paise: number;
  }[];
  policy_decision: string;
  policy_rules: { rule_id: string; decision: string; citation: string; message: string }[];
  outcome: string | null;
  recovered_paise: number;
  detail: string;
  stop_reason: string | null;
  narrative: string | null;
  entry_hash?: string;
  prev_hash?: string;
}

export interface PolicyView {
  policy: Record<string, unknown>;
  rules: { rule_id: string; citation: string; description: string }[];
  afa_ceiling_paise: number;
  afa_ceiling_exempt_paise: number;
}

export interface PolicyMetrics {
  policy: string;
  description: string;
  cases: number;
  revenue_at_risk_paise: number;
  recovered_paise: number;
  cases_recovered: number;
  true_counterfactual_paise: number;
  true_incremental_paise: number;
  incremental_paise: number;
  incremental_interval_paise: [number, number];
  counterfactual_is_precise: boolean;
  true_incremental_per_contact_paise: number;
  interventions: number;
  customer_contacts: number;
  retries: number;
  human_escalations: number;
  interventions_on_self_curers: number;
  guardrail_violations: number;
  violation_rules: Record<string, number>;
  customers_opted_out: number;
  spend_paise: number;
  actions_per_case: number;
  capture_rate: number;
  holdout_cases: number;
  holdout_at_risk_paise: number;
  stop_reasons: Record<string, number>;
}

export interface Evaluation {
  seed: number;
  n_cases: number;
  clock: string;
  disclaimer: string;
  oracle: {
    recoverable_paise: number;
    recoverable_cases: number;
    self_cure_paise: number;
    self_cure_cases: number;
    winnable_paise: number;
    total_at_risk_paise: number;
    note: string;
  };
  policies: Record<string, PolicyMetrics>;
  policy_descriptions: Record<string, string>;
  sensitivity?: Record<
    string,
    { seed: number; true_incremental_paise: number; customer_contacts: number; guardrail_violations: number; customers_opted_out: number }[]
  >;
}

export interface ScenarioResult {
  key: string;
  title: string;
  question: string;
  steps: { label: string; detail: string; kind: string; at: string | null }[];
  verdict: string;
  disclaimer: string;
}
