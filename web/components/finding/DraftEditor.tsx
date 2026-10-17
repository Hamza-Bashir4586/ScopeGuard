"use client";

import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { errorMessage } from "@/lib/api";
import type { Finding } from "@/lib/types";
import { Badge, Button, Card, SaveIndicator, type SaveState } from "../ui";
import type { PatchFn } from "../FindingDetail";

export function DraftEditor({ finding, patch }: { finding: Finding; patch: PatchFn }) {
  const [text, setText] = useState(finding.draft);
  const [saved, setSaved] = useState(finding.draft);
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  const dirty = text !== saved;
  const edited = saved !== finding.ai_draft;

  async function save(value: string | null) {
    setState({ kind: "saving" });
    try {
      const updated = await patch({ draft: value });
      setText(updated.draft);
      setSaved(updated.draft);
      setState({ kind: "saved", at: Date.now() });
    } catch (err) {
      setState({ kind: "error", message: errorMessage(err) });
    }
  }

  function reset() {
    if (!window.confirm("Replace the draft with the original AI draft? Your edits will be lost.")) return;
    setText(finding.ai_draft);
    void save(null);
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Draft change request {edited && <Badge tone="blue">Edited</Badge>}
        </span>
      }
      actions={
        <>
          <SaveIndicator state={state} dirty={dirty} />
          {(edited || dirty) && (
            <Button variant="ghost" onClick={reset} disabled={state.kind === "saving"}>
              <RotateCcw className="h-4 w-4" aria-hidden /> Reset to AI draft
            </Button>
          )}
          <Button variant="primary" onClick={() => save(text)} busy={state.kind === "saving"} disabled={!dirty}>
            Save draft
          </Button>
        </>
      }
    >
      <textarea
        aria-label="Draft change request"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (state.kind === "saved") setState({ kind: "idle" });
        }}
        rows={18}
        className="input block w-full resize-y px-3 py-2.5 font-sans text-[15px] leading-7"
      />
      <p className="mt-2 text-xs text-slate-500">
        This text becomes the narrative of the exported packet. Unverified evidence is never included.
      </p>
    </Card>
  );
}
