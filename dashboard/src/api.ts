export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type RunStatus = "queued" | "running" | "completed" | "failed";
export type Severity = "high" | "medium" | "low";
export type Verdict = "confirmed" | "false_positive" | "unclear";

export interface Run {
  id: number;
  repo: string;
  pr: number;
  head_ref: string;
  base_sha: string;
  head_sha: string;
  merge_base: string | null;
  status: RunStatus;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  review_id: number | null;
}

export interface RunSummary extends Run {
  severity_counts: Record<Severity, number>;
  verdict_counts: Record<Verdict, number>;
}

export interface FindingRow {
  id: number;
  finding_id: string;
  tool: string;
  rule_id: string;
  severity: Severity;
  path: string;
  line: number;
  message: string;
  in_diff: boolean;
  verdict: Verdict | null;
  justification: string | null;
  published: boolean;
  human_label: Verdict | null;
  labelled_at: string | null;
}

export interface ToolReport {
  tool: string;
  status: string;
  error: string | null;
}

export interface RunDetail {
  run: Run;
  findings: FindingRow[];
  tool_reports: ToolReport[];
  has_artifacts: boolean;
}

export interface ToolStat {
  tool: string;
  total: number;
  confirmed: number;
  false_positive: number;
  unclear: number;
  confirmed_pct: number;
  false_positive_pct: number;
}

export interface EvalMetrics {
  labelled: number;
  agreement: number | null;
  agent_precision: number | null;
  fp_filter_accuracy: number | null;
}

export interface QueueItem extends FindingRow {
  run_id: number;
  repo: string;
  pr: number;
  head_sha: string;
}

export interface Evaluation {
  overall: EvalMetrics;
  by_tool: (EvalMetrics & { tool: string })[];
  queue: QueueItem[];
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  runs: () => get<RunSummary[]>("/api/runs"),
  run: (id: string) => get<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  toolStats: () => get<ToolStat[]>("/api/stats/tools"),
  evaluation: () => get<Evaluation>("/api/evaluation"),
  setLabel: async (findingRowId: number, label: Verdict | null): Promise<FindingRow> => {
    const res = await fetch(`${API_URL}/api/findings/${findingRowId}/label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json() as Promise<FindingRow>;
  },
};

export const artifactsUrl = (runId: number) => `${API_URL}/api/runs/${runId}/artifacts.zip`;
export const findingsCsvUrl = `${API_URL}/api/export/findings.csv`;

export const isActive = (s: RunStatus) => s === "queued" || s === "running";
