import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { RunStatus, Severity, Verdict } from "./api";

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge status-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge sev-${severity}`}>{severity.toUpperCase()}</span>;
}

const VERDICT_LABEL: Record<Verdict, string> = {
  confirmed: "Confirmed",
  false_positive: "False positive",
  unclear: "Unclear",
};

export function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return <span className="badge verdict-none">Not classified</span>;
  return <span className={`badge verdict-${verdict}`}>{VERDICT_LABEL[verdict]}</span>;
}

export function QueryState({ isLoading, error }: { isLoading: boolean; error: unknown }) {
  if (isLoading) return <p className="muted">Loading…</p>;
  if (error)
    return (
      <p className="error-box" role="alert">
        Could not reach the PR-Gate API ({String((error as Error).message ?? error)}). Is the backend running on
        localhost:8000?
      </p>
    );
  return null;
}

export const shortSha = (sha: string | null) => (sha ? sha.slice(0, 7) : "—");

export function formatTime(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function formatDate(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatClock(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
}

export const percent = (part: number, total: number) => (total > 0 ? Math.round((part / total) * 100) : 0);

export const location = (f: { path: string; line: number }) => (f.line > 0 ? `${f.path}:${f.line}` : f.path);

const TOOL_INFO: Record<string, { short: string; about: string }> = {
  gitleaks: { short: "GL", about: "Secrets detection" },
  "osv-scanner": { short: "OSV", about: "Dependency vulnerabilities" },
  semgrep: { short: "SG", about: "Static analysis" },
};

export function ToolName({ tool }: { tool: string }) {
  const info = TOOL_INFO[tool];
  return (
    <div className="tool">
      <span className="tool-icon" aria-hidden="true">{info?.short ?? tool.slice(0, 2).toUpperCase()}</span>
      <div>
        <div className="tool-name">{tool}</div>
        {info && <div className="cell-sub">{info.about}</div>}
      </div>
    </div>
  );
}

export function StatCard({ label, children, foot }: { label: string; children: ReactNode; foot?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{children}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  );
}

export function SplitBar({ parts, className = "" }: { parts: { tone: string; value: number }[]; className?: string }) {
  return (
    <div className={`split ${className}`} aria-hidden="true">
      {parts
        .filter((p) => p.value > 0)
        .map((p) => <span key={p.tone} className={`tone-${p.tone}`} style={{ flexGrow: p.value }} />)}
    </div>
  );
}

export function ShieldIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l7 3v5c0 4.6-2.9 8.1-7 10-4.1-1.9-7-5.4-7-10V6l7-3z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

export function DownloadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 4v11" />
      <path d="M7 11l5 5 5-5" />
      <path d="M5 20h14" />
    </svg>
  );
}

const LABELS: Verdict[] = ["confirmed", "false_positive", "unclear"];

export function LabelButtons({ findingRowId, value }: { findingRowId: number; value: Verdict | null }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (label: Verdict | null) => api.setLabel(findingRowId, label),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run"] });
      queryClient.invalidateQueries({ queryKey: ["evaluation"] });
    },
  });
  return (
    <div className="label-buttons" role="group" aria-label="Your label" onClick={(e) => e.stopPropagation()}>
      <span className="label-buttons-title">Your label</span>
      {LABELS.map((l) => (
        <button
          key={l}
          type="button"
          className="choice"
          aria-pressed={value === l}
          disabled={mutation.isPending}
          // clicking the current label again clears it
          onClick={() => mutation.mutate(value === l ? null : l)}
        >
          {VERDICT_LABEL[l]}
        </button>
      ))}
      {mutation.isPending && <span className="small muted"> saving…</span>}
      {mutation.isError && <span className="small err-text"> could not save</span>}
    </div>
  );
}
