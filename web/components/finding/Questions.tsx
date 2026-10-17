"use client";

import { useState } from "react";
import { errorMessage } from "@/lib/api";
import type { Finding, Question } from "@/lib/types";
import { Button, Card, QuestionKindBadge, SaveIndicator, type SaveState } from "../ui";
import type { PatchFn } from "../FindingDetail";

export function QuestionsPanel({ finding, patch }: { finding: Finding; patch: PatchFn }) {
  const open = finding.questions.filter((q) => !q.resolution?.trim()).length;
  return (
    <Card
      title={`Outstanding questions (${finding.questions.length})`}
      actions={
        finding.questions.length > 0 ? (
          <span className={open > 0 ? "text-xs font-medium text-amber-800" : "text-xs text-emerald-700"}>
            {open > 0 ? `${open} unanswered` : "All answered"}
          </span>
        ) : undefined
      }
    >
      {finding.questions.length === 0 ? (
        <p className="text-sm text-slate-500">No open questions for this finding.</p>
      ) : (
        <ul className="space-y-4">
          {finding.questions.map((q) => (
            <QuestionItem key={q.question_id} q={q} finding={finding} patch={patch} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function QuestionItem({ q, finding, patch }: { q: Question; finding: Finding; patch: PatchFn }) {
  const [text, setText] = useState(q.resolution ?? "");
  const [saved, setSaved] = useState(q.resolution ?? "");
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const dirty = text !== saved;

  async function save() {
    setState({ kind: "saving" });
    try {
      // Send the full map so the result is the same whether the server merges or replaces.
      const updated = await patch({
        question_resolutions: { ...finding.review.question_resolutions, [q.question_id]: text },
      });
      const fresh = updated.questions.find((x) => x.question_id === q.question_id)?.resolution ?? text;
      setText(fresh);
      setSaved(fresh);
      setState({ kind: "saved", at: Date.now() });
    } catch (err) {
      setState({ kind: "error", message: errorMessage(err) });
    }
  }

  return (
    <li className="space-y-2">
      <QuestionKindBadge kind={q.kind} />
      <p className="text-sm leading-6 text-slate-800">{q.text}</p>
      <textarea
        aria-label="Your answer"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (state.kind === "saved") setState({ kind: "idle" });
        }}
        rows={3}
        placeholder="Your answer (e.g. reference to the email, RFI or measured quantity)"
        className="input block w-full resize-y px-2.5 py-2 text-sm leading-6"
      />
      <div className="flex items-center justify-end gap-2">
        <SaveIndicator state={state} dirty={dirty} />
        <Button onClick={save} busy={state.kind === "saving"} disabled={!dirty}>
          Save answer
        </Button>
      </div>
    </li>
  );
}
