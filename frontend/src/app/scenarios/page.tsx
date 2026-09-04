"use client";

import { useState } from "react";
import { ScenarioResult, post, useApi } from "@/lib/api";
import { ApiError, Card, Loading, SimulatedBadge } from "@/components/ui";

interface ScenarioMeta {
  key: string;
  title: string;
  question: string;
  archetype: string;
}

const KIND: Record<string, { colour: string; mark: string }> = {
  info: { colour: "var(--ink-3)", mark: "·" },
  decision: { colour: "var(--s1)", mark: "→" },
  block: { colour: "var(--critical)", mark: "✕" },
  money: { colour: "var(--good)", mark: "✓" },
  stop: { colour: "var(--serious)", mark: "■" },
};

export default function ScenariosPage() {
  const { data, error, loading } = useApi<{ scenarios: ScenarioMeta[] }>("/api/scenarios");
  const [active, setActive] = useState<string | null>(null);
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [running, setRunning] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(key: string) {
    setActive(key);
    setRunning(true);
    setResult(null);
    setFailure(null);
    try {
      setResult(await post<ScenarioResult>(`/api/demo/${key}`));
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  if (error) return <ApiError message={error} />;
  if (loading || !data) return <Loading />;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[19px] font-semibold tracking-tight">Scenarios</h1>
        <p className="mt-0.5 max-w-3xl text-[12px]" style={{ color: "var(--ink-2)" }}>
          Seven questions a reviewer would actually ask. Nothing here is scripted — each one
          runs the same decision engine, guardrail layer and simulator the evaluation uses, and
          prints what actually happened.
        </p>
      </header>

      <SimulatedBadge />

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-2">
          {data.scenarios.map((s) => (
            <button
              key={s.key}
              onClick={() => run(s.key)}
              disabled={running}
              className="card w-full px-3.5 py-3 text-left transition-colors disabled:opacity-60"
              style={{
                borderColor: active === s.key ? "var(--accent)" : "var(--line)",
                background: active === s.key ? "var(--surface-2)" : "var(--surface)",
              }}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13px] font-medium">{s.title}</span>
                <span className="tnum text-[10px]" style={{ color: "var(--ink-3)" }}>
                  {s.key}
                </span>
              </div>
              <p className="mt-1 text-[11.5px] leading-snug" style={{ color: "var(--ink-3)" }}>
                {s.question}
              </p>
            </button>
          ))}
        </div>

        <div>
          {!active && (
            <Card>
              <p className="py-16 text-center text-[12.5px]" style={{ color: "var(--ink-3)" }}>
                Pick a scenario. It runs live against the engine — expect a second or two.
              </p>
            </Card>
          )}

          {running && (
            <Card>
              <p className="py-16 text-center text-[12.5px]" style={{ color: "var(--ink-3)" }}>
                Working the case…
              </p>
            </Card>
          )}

          {failure && <ApiError message={failure} />}

          {result && !running && (
            <Card title={result.title} hint={result.question}>
              <ol className="space-y-0">
                {result.steps.map((step, i) => {
                  const k = KIND[step.kind] ?? KIND.info;
                  return (
                    <li key={i} className="flex gap-3 border-b py-2 last:border-0">
                      <span
                        className="tnum mt-0.5 w-4 shrink-0 text-center text-[12px]"
                        style={{ color: k.colour }}
                        aria-hidden
                      >
                        {k.mark}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline gap-2">
                          {step.at && (
                            <span className="tnum text-[11px]" style={{ color: "var(--ink-3)" }}>
                              {step.at}
                            </span>
                          )}
                          <span className="text-[12.5px] font-medium" style={{ color: k.colour }}>
                            {step.label}
                          </span>
                        </div>
                        {step.detail && (
                          <p
                            className="mt-0.5 text-[11.5px] leading-snug"
                            style={{ color: "var(--ink-2)" }}
                          >
                            {step.detail}
                          </p>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>

              <div
                className="mt-4 rounded-sm p-3.5 text-[12.5px] leading-relaxed"
                style={{ background: "var(--surface-2)", color: "var(--ink-2)" }}
              >
                {result.verdict.split("\n").map((para, i) => (
                  <p key={i} className={i > 0 ? "mt-2.5" : ""}>
                    {para}
                  </p>
                ))}
              </div>

              <p className="mt-3 text-[11px]" style={{ color: "var(--ink-3)" }}>
                {result.disclaimer} Reproduce in a terminal with{" "}
                <code className="tnum">python scripts/demo.py {result.key}</code>
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
