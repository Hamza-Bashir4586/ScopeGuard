"use client";

import Link from "next/link";
import { AlertTriangle, Check, ExternalLink, Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { FindingStatus, QuestionKind, ReviewStatus } from "@/lib/types";
import { humanize } from "@/lib/format";
import { USE_MOCK } from "@/lib/api";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// ---------- badges ----------

type Tone = "slate" | "blue" | "green" | "amber" | "red" | "violet";

const toneClasses: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  blue: "bg-blue-50 text-blue-800 ring-blue-200",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  amber: "bg-amber-50 text-amber-900 ring-amber-200",
  red: "bg-red-50 text-red-800 ring-red-200",
  violet: "bg-violet-50 text-violet-800 ring-violet-200",
};

export function Badge({ tone = "slate", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        toneClasses[tone],
      )}
    >
      {children}
    </span>
  );
}

export const findingStatusLabel: Record<FindingStatus, string> = {
  new: "New",
  already_recorded: "Already recorded",
  needs_info: "Needs info",
};

export function FindingStatusBadge({ status }: { status: FindingStatus }) {
  const tone: Tone = status === "new" ? "blue" : status === "already_recorded" ? "slate" : "amber";
  return <Badge tone={tone}>{findingStatusLabel[status]}</Badge>;
}

export function ReviewStatusBadge({ status }: { status: ReviewStatus }) {
  const tone: Tone = status === "confirmed" ? "green" : status === "dismissed" ? "red" : "slate";
  return <Badge tone={tone}>{humanize(status)}</Badge>;
}

export function DocTypeBadge({ docType }: { docType: string }) {
  return <Badge tone="violet">{humanize(docType)}</Badge>;
}

export function QuestionKindBadge({ kind }: { kind: QuestionKind }) {
  return <Badge tone="amber">{humanize(kind)}</Badge>;
}

// ---------- buttons & links ----------

type Variant = "primary" | "secondary" | "danger" | "ghost";

const variantClasses: Record<Variant, string> = {
  primary: "bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-300 disabled:text-slate-500",
  secondary:
    "bg-white text-slate-800 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:text-slate-400 disabled:hover:bg-white",
  danger:
    "bg-white text-red-700 ring-1 ring-inset ring-red-300 hover:bg-red-50 disabled:text-red-300 disabled:hover:bg-white",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:text-slate-300",
};

export function Button({
  variant = "secondary",
  className,
  busy,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed",
        variantClasses[variant],
        className,
      )}
    >
      {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function BoxLink({ href, label = "Open in Box" }: { href: string; label?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 whitespace-nowrap text-sm font-medium text-blue-700 hover:text-blue-900 hover:underline"
    >
      {label}
      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
    </a>
  );
}

// ---------- layout ----------

export function Card({
  title,
  actions,
  children,
  className,
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cx("rounded-lg border border-slate-200 bg-white", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
          {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function AppHeader({ right, scanId }: { right?: ReactNode; scanId?: string | null }) {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <Link
            href={scanId ? `/?scan=${encodeURIComponent(scanId)}` : "/"}
            className="flex items-center gap-2 text-slate-900"
          >
            <span className="grid h-7 w-7 place-items-center rounded bg-slate-900 text-xs font-bold text-white">
              SG
            </span>
            <span className="text-base font-semibold tracking-tight">ScopeGuard</span>
          </Link>
          <span className="hidden text-sm text-slate-500 sm:inline">Change-order review assistant</span>
          {USE_MOCK && <Badge tone="amber">Mock data</Badge>}
        </div>
        {right && <div className="flex items-center gap-2">{right}</div>}
      </div>
    </header>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label ?? "Loading…"}
    </div>
  );
}

export function ErrorBox({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span>{message}</span>
      </div>
      {action}
    </div>
  );
}

// ---------- mutation state ----------

export type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

export function SaveIndicator({ state, dirty }: { state: SaveState; dirty?: boolean }) {
  if (state.kind === "saving") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Saving…
      </span>
    );
  }
  if (state.kind === "error") {
    return (
      <span role="alert" className="inline-flex items-center gap-1 text-xs text-red-700">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> {state.message}
      </span>
    );
  }
  if (dirty) return <span className="text-xs text-amber-700">Unsaved changes</span>;
  if (state.kind === "saved") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-700">
        <Check className="h-3.5 w-3.5" aria-hidden /> Saved
      </span>
    );
  }
  return null;
}
