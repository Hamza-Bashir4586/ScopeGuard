"use client";

import { useState } from "react";
import { Link2, Plus, RotateCcw, Trash2 } from "lucide-react";
import { errorMessage } from "@/lib/api";
import type { CostCategory, CostItem, CostItemInput, Finding } from "@/lib/types";
import { formatMoney } from "@/lib/format";
import { Button, Card, SaveIndicator, type SaveState, cx } from "../ui";
import type { PatchFn } from "../FindingDetail";
import { evidenceAnchor } from "./Evidence";

const CATEGORIES: CostCategory[] = ["labor", "material", "equipment", "markup", "other"];
const DECIMAL_RE = /^-?\d+(\.\d+)?$/;

interface Row {
  key: string;
  item_id?: string;
  category: CostCategory;
  description: string;
  qty: string;
  unit: string;
  rate: string;
  source_evidence_id: string | null;
  // Server-computed values; only meaningful while the row is unchanged.
  amount: string | null;
  formula: string;
  dirty: boolean;
}

let keySeq = 0;
const nextKey = () => `row-${++keySeq}`;

function toRows(items: CostItem[]): Row[] {
  return items.map((i) => ({
    key: i.item_id || nextKey(),
    item_id: i.item_id,
    category: i.category,
    description: i.description,
    qty: i.qty ?? "",
    unit: i.unit,
    rate: i.rate ?? "",
    source_evidence_id: i.source_evidence_id,
    amount: i.amount,
    formula: i.formula,
    dirty: false,
  }));
}

function toInput(r: Row): CostItemInput {
  const qty = r.qty.trim();
  const rate = r.rate.trim();
  const out: CostItemInput = {
    category: r.category,
    description: r.description.trim(),
    qty: qty === "" ? null : qty,
    unit: r.unit.trim(),
    rate: rate === "" ? null : rate,
    source_evidence_id: r.source_evidence_id,
  };
  if (r.item_id) out.item_id = r.item_id;
  return out;
}

function cellInvalid(v: string): boolean {
  return v.trim() !== "" && !DECIMAL_RE.test(v.trim());
}

export function CostWorksheet({ finding, patch }: { finding: Finding; patch: PatchFn }) {
  const [rows, setRows] = useState<Row[]>(() => toRows(finding.cost_items));
  const [removed, setRemoved] = useState(false);
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  const dirty = removed || rows.some((r) => r.dirty);
  const invalid = rows.some((r) => cellInvalid(r.qty) || cellInvalid(r.rate));
  const overridden = finding.review.cost_items !== null;

  function update(key: string, changes: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...changes, dirty: true } : r)));
    if (state.kind !== "saving") setState({ kind: "idle" });
  }

  function addRow() {
    setRows((rs) => [
      ...rs,
      {
        key: nextKey(),
        category: "labor",
        description: "",
        qty: "",
        unit: "h",
        rate: "",
        source_evidence_id: null,
        amount: null,
        formula: "",
        dirty: true,
      },
    ]);
  }

  function removeRow(key: string) {
    setRows((rs) => rs.filter((r) => r.key !== key));
    setRemoved(true);
  }

  async function save(items: CostItemInput[] | null) {
    setState({ kind: "saving" });
    try {
      const updated = await patch({ cost_items: items });
      setRows(toRows(updated.cost_items));
      setRemoved(false);
      setState({ kind: "saved", at: Date.now() });
    } catch (err) {
      setState({ kind: "error", message: errorMessage(err) });
    }
  }

  function resetToAi() {
    if (!window.confirm("Replace the worksheet with the AI-estimated cost items? Your edits will be lost.")) return;
    void save(null);
  }

  const missingCount = rows.filter((r) => r.qty.trim() === "" || r.rate.trim() === "").length;

  return (
    <Card
      title="Cost worksheet"
      actions={
        <>
          <SaveIndicator state={state} dirty={dirty} />
          {overridden && (
            <Button variant="ghost" onClick={resetToAi} disabled={state.kind === "saving"}>
              <RotateCcw className="h-4 w-4" aria-hidden /> Reset to AI estimate
            </Button>
          )}
          <Button
            variant="primary"
            onClick={() => save(rows.map(toInput))}
            busy={state.kind === "saving"}
            disabled={!dirty || invalid}
          >
            Save costs
          </Button>
        </>
      }
    >
      <div className="-mx-4 overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
              <th className="px-4 pb-2 pr-2">Category</th>
              <th className="px-2 pb-2">Description</th>
              <th className="w-20 px-2 pb-2 text-right">Qty</th>
              <th className="w-16 px-2 pb-2">Unit</th>
              <th className="w-24 px-2 pb-2 text-right">Rate</th>
              <th className="w-28 px-2 pb-2 text-right">Amount</th>
              <th className="w-10 px-2 pb-2 pr-4" aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-slate-100 align-top last:border-b-0">
                <td className="px-4 py-2 pr-2">
                  <select
                    aria-label="Category"
                    value={r.category}
                    onChange={(e) => update(r.key, { category: e.target.value as CostCategory })}
                    className="input w-28"
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {c.charAt(0).toUpperCase() + c.slice(1)}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-2 py-2">
                  <input
                    aria-label="Description"
                    value={r.description}
                    onChange={(e) => update(r.key, { description: e.target.value })}
                    className="input w-full"
                    placeholder="Description"
                  />
                  <div className="mt-1 flex items-start gap-2 text-xs text-slate-500">
                    {r.dirty ? (
                      <span className="italic">Amount and formula recalculated on save</span>
                    ) : (
                      <span className="tabular-nums">{r.formula}</span>
                    )}
                    {r.source_evidence_id && (
                      <a
                        href={`#${evidenceAnchor(r.source_evidence_id)}`}
                        className="ml-auto inline-flex shrink-0 items-center gap-0.5 text-blue-700 hover:underline"
                      >
                        <Link2 className="h-3 w-3" aria-hidden /> Source
                      </a>
                    )}
                  </div>
                </td>
                <td className="px-2 py-2">
                  <NumCell value={r.qty} label="Quantity" onChange={(v) => update(r.key, { qty: v })} />
                </td>
                <td className="px-2 py-2">
                  <input
                    aria-label="Unit"
                    value={r.unit}
                    onChange={(e) => update(r.key, { unit: e.target.value })}
                    className="input w-full"
                  />
                </td>
                <td className="px-2 py-2">
                  <NumCell value={r.rate} label="Rate" onChange={(v) => update(r.key, { rate: v })} />
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  <div className="py-1.5">
                    {r.dirty ? <span className="text-slate-400">—</span> : formatMoney(r.amount)}
                  </div>
                </td>
                <td className="px-2 py-2 pr-4">
                  <button
                    type="button"
                    onClick={() => removeRow(r.key)}
                    className="rounded p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-700"
                    aria-label={`Remove ${r.description || "row"}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-4 text-sm text-slate-500">
                  No cost items.
                </td>
              </tr>
            )}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-200">
              <td className="px-4 pt-3" colSpan={5}>
                <Button variant="ghost" onClick={addRow}>
                  <Plus className="h-4 w-4" aria-hidden /> Add row
                </Button>
              </td>
              <td className="px-2 pt-3 text-right">
                <div className="text-xs text-slate-500">Total</div>
                <div className="font-semibold tabular-nums text-slate-900">{formatMoney(finding.cost_total)}</div>
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      <div className="mt-3 space-y-1 text-xs text-slate-500">
        {missingCount > 0 && (
          <p className="text-amber-800">
            {missingCount} {missingCount === 1 ? "row is" : "rows are"} missing a quantity or rate (highlighted) and
            {missingCount === 1 ? " is" : " are"} not included in the total.
          </p>
        )}
        {invalid && <p className="text-red-700">Quantity and rate must be plain numbers, e.g. 6.5 or 82.00.</p>}
        {dirty && <p>Total reflects the last saved worksheet; the server recalculates it when you save.</p>}
      </div>
    </Card>
  );
}

function NumCell({ value, label, onChange }: { value: string; label: string; onChange: (v: string) => void }) {
  const missing = value.trim() === "";
  const bad = cellInvalid(value);
  return (
    <input
      aria-label={label}
      inputMode="decimal"
      value={value}
      placeholder="?"
      onChange={(e) => onChange(e.target.value)}
      aria-invalid={bad || undefined}
      className={cx(
        "input w-full text-right tabular-nums",
        missing && "border-amber-400 bg-amber-50 placeholder:text-amber-700",
        bad && "border-red-500 bg-red-50",
      )}
    />
  );
}
