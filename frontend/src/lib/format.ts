/**
 * Money crosses the API as integer paise and is formatted exactly once, here.
 * Indian digit grouping (lakh/crore), because the audience reads it that way.
 */

export function rupees(paise: number, opts: { decimals?: boolean } = {}): string {
  const sign = paise < 0 ? "-" : "";
  const whole = Math.floor(Math.abs(paise) / 100);
  const frac = Math.abs(paise) % 100;
  const s = whole.toString();
  let grouped = s;
  if (s.length > 3) {
    const tail = s.slice(-3);
    let head = s.slice(0, -3);
    const parts: string[] = [];
    while (head.length > 2) {
      parts.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) parts.unshift(head);
    grouped = [...parts, tail].join(",");
  }
  return opts.decimals === false
    ? `${sign}₹${grouped}`
    : `${sign}₹${grouped}.${frac.toString().padStart(2, "0")}`;
}

/** Compact form for stat tiles: ₹12.3L, ₹4.1Cr. */
export function rupeesCompact(paise: number): string {
  const r = Math.abs(paise) / 100;
  const sign = paise < 0 ? "-" : "";
  if (r >= 1e7) return `${sign}₹${(r / 1e7).toFixed(2)}Cr`;
  if (r >= 1e5) return `${sign}₹${(r / 1e5).toFixed(2)}L`;
  if (r >= 1e3) return `${sign}₹${(r / 1e3).toFixed(1)}k`;
  return `${sign}₹${r.toFixed(0)}`;
}

export const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export const STATE_COLOR: Record<string, string> = {
  RECOVERED: "var(--good)",
  OPEN: "var(--accent)",
  WAITING: "var(--warning)",
  ESCALATED: "var(--serious)",
  STOPPED: "var(--ink-3)",
};

export const VERDICT_COLOR: Record<string, string> = {
  PASS: "var(--good)",
  BLOCK: "var(--critical)",
  DEFER: "var(--warning)",
  REQUIRE_APPROVAL: "var(--serious)",
};

/** Fixed slot order. Colour follows the policy, never its rank in a table. */
export const SERIES: Record<string, string> = {
  NAIVE: "var(--s2)",
  RULEBOOK: "var(--s4)",
  "RULEBOOK+RULES": "var(--s3)",
  RECOVERYOS: "var(--s1)",
};

export const humanise = (s: string) =>
  s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
