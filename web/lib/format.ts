// Display formatting only. No money math happens in the browser except
// summing server-provided totals for the dashboard tile (done in integer cents).

const moneyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return moneyFmt.format(n);
}

export function formatQty(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

/** Parse a decimal string into integer cents (rounded half-up) without floats. */
function toCents(value: string): bigint | null {
  const m = /^\s*(-)?(\d*)(?:\.(\d*))?\s*$/.exec(value);
  if (!m || (!m[2] && !m[3])) return null;
  const neg = m[1] === "-";
  const whole = BigInt(m[2] || "0");
  const frac = (m[3] || "").padEnd(3, "0");
  let cents = whole * BigInt(100) + BigInt(frac.slice(0, 2));
  if (Number(frac[2]) >= 5) cents += BigInt(1);
  return neg ? -cents : cents;
}

/** Sum decimal strings for display; returns a decimal string with 2 places. */
export function sumMoney(values: Array<string | null | undefined>): string {
  let total = BigInt(0);
  for (const v of values) {
    if (!v) continue;
    const c = toCents(v);
    if (c !== null) total += c;
  }
  const neg = total < BigInt(0);
  const abs = neg ? -total : total;
  const s = abs.toString().padStart(3, "0");
  return `${neg ? "-" : ""}${s.slice(0, -2)}.${s.slice(-2)}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDate(value: string): string {
  // Timeline dates may be plain YYYY-MM-DD; avoid timezone shifts for those.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function humanize(value: string): string {
  const s = value.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
