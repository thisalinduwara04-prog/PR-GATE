import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, isActive, type RunSummary } from "../api";
import { QueryState, SplitBar, StatCard, StatusBadge, formatClock, formatDate, percent, shortSha } from "../components";

type Filter = "all" | "active" | "completed" | "failed";

const FILTER_LABEL: Record<Filter, string> = {
  all: "All",
  active: "In progress",
  completed: "Completed",
  failed: "Failed",
};

const matches = (r: RunSummary, f: Filter) => f === "all" || (f === "active" ? isActive(r.status) : r.status === f);

const total = (runs: RunSummary[], pick: (r: RunSummary) => number) => runs.reduce((n, r) => n + pick(r), 0);

function Summary({ runs }: { runs: RunSummary[] }) {
  const repos = new Set(runs.map((r) => r.repo)).size;
  const completed = runs.filter((r) => r.status === "completed").length;
  const failed = runs.filter((r) => r.status === "failed").length;
  const active = runs.filter((r) => isActive(r.status)).length;
  const high = total(runs, (r) => r.severity_counts.high);
  const medium = total(runs, (r) => r.severity_counts.medium);
  const low = total(runs, (r) => r.severity_counts.low);
  const confirmed = total(runs, (r) => r.verdict_counts.confirmed);
  const falsePositive = total(runs, (r) => r.verdict_counts.false_positive);
  const unclear = total(runs, (r) => r.verdict_counts.unclear);

  return (
    <div className="stats">
      <StatCard label="Total runs" foot={`${repos} ${repos === 1 ? "repository" : "repositories"}`}>
        {runs.length}
      </StatCard>
      <StatCard
        label="Run health"
        foot={
          <>
            {completed + failed > 0 ? `${percent(completed, completed + failed)}% success rate` : "No finished runs yet"}
            {active > 0 && ` · ${active} in progress`}
          </>
        }
      >
        <span className="c-ok">{completed}</span>
        <small>completed</small>
        <span className="c-high">{failed}</span>
        <small>failed</small>
      </StatCard>
      <StatCard
        label="Findings by severity"
        foot={
          <>
            <SplitBar
              parts={[
                { tone: "high", value: high },
                { tone: "medium", value: medium },
                { tone: "low", value: low },
              ]}
            />
            <strong className="c-high">{high}</strong> <span className="gap">high</span>
            <strong className="c-medium">{medium}</strong> <span className="gap">medium</span>
            <strong className="c-low">{low}</strong> low
          </>
        }
      >
        {high + medium + low}
      </StatCard>
      <StatCard
        label="Agent verdicts"
        foot={
          <>
            <SplitBar
              parts={[
                { tone: "confirmed", value: confirmed },
                { tone: "fp", value: falsePositive },
                { tone: "unclear", value: unclear },
              ]}
            />
            <strong className="c-confirmed">{confirmed}</strong> <span className="gap">confirmed</span>
            <strong className="c-fp">{falsePositive}</strong> <span className="gap">false positive</span>
            <strong>{unclear}</strong> unclear
          </>
        }
      >
        {confirmed}
        <small>confirmed</small>
      </StatCard>
    </div>
  );
}

export default function RunsPage() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const { data, isLoading, error } = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: (q) => (q.state.data?.some((r) => isActive(r.status)) ? 3000 : 10000),
  });

  const runs = data ?? [];
  const shown = runs.filter((r) => matches(r, filter));
  // "In progress" only appears while something is queued or running
  const filters = (Object.keys(FILTER_LABEL) as Filter[]).filter(
    (f) => f !== "active" || filter === "active" || runs.some((r) => isActive(r.status)),
  );

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Runs</h1>
          <p className="lede">Every pull-request scan, with what the tools found and what the agent decided.</p>
        </div>
      </div>
      <QueryState isLoading={isLoading} error={error} />
      {data && (
        <>
          <Summary runs={runs} />
          <div className="card">
            <div className="card-head">
              <div className="tabs">
                {filters.map((f) => (
                  <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                    {FILTER_LABEL[f]}
                    <span>{runs.filter((r) => matches(r, f)).length}</span>
                  </button>
                ))}
              </div>
              <span className="small muted">
                Showing {shown.length} of {runs.length}
              </span>
            </div>
            {shown.length === 0 ? (
              <p className="empty">
                {runs.length === 0
                  ? "No runs yet. Open or update a pull request on a watched repository."
                  : "No runs match this filter."}
              </p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Repository &amp; PR</th>
                      <th>Status</th>
                      <th>Findings</th>
                      <th>Agent verdicts</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) => (
                      <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                        <td>
                          <Link className="run-id" to={`/runs/${r.id}`} onClick={(e) => e.stopPropagation()}>
                            #{r.id}
                          </Link>
                        </td>
                        <td>
                          <div className="cell-title">{r.repo}</div>
                          <div className="cell-sub">
                            PR #{r.pr} · <span className="code-chip">{shortSha(r.head_sha)}</span>
                          </div>
                        </td>
                        <td>
                          <StatusBadge status={r.status} />
                          {r.status === "failed" && r.error && <div className="run-error">{r.error}</div>}
                        </td>
                        <td>
                          <div className="chips">
                            {(["high", "medium", "low"] as const).map((s) => (
                              <span
                                key={s}
                                className={`chip ${r.severity_counts[s] > 0 ? `sev-${s}` : "chip-zero"}`}
                                title={`${r.severity_counts[s]} ${s}`}
                              >
                                {s[0].toUpperCase()}
                                {r.severity_counts[s]}
                              </span>
                            ))}
                          </div>
                          <SplitBar
                            className="split-thin"
                            parts={[
                              { tone: "high", value: r.severity_counts.high },
                              { tone: "medium", value: r.severity_counts.medium },
                              { tone: "low", value: r.severity_counts.low },
                            ]}
                          />
                        </td>
                        <td>
                          <div>
                            <strong className="big-num c-confirmed">{r.verdict_counts.confirmed}</strong> confirmed
                          </div>
                          <div className="cell-sub">
                            <strong className="c-fp">{r.verdict_counts.false_positive}</strong> false positive ·{" "}
                            <strong>{r.verdict_counts.unclear}</strong> unclear
                          </div>
                        </td>
                        <td>
                          <div className="strong">{formatDate(r.created_at)}</div>
                          <div className="cell-sub">{formatClock(r.created_at)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
