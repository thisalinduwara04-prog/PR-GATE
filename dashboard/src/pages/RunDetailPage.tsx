import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, artifactsUrl, isActive, type FindingRow } from "../api";
import {
  DownloadIcon,
  LabelButtons,
  QueryState,
  SeverityBadge,
  StatusBadge,
  VerdictBadge,
  formatTime,
  location,
  shortSha,
} from "../components";

const ALL = "all";

export default function RunDetailPage() {
  const { id = "" } = useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.run(id),
    refetchInterval: (q) => (q.state.data && isActive(q.state.data.run.status) ? 3000 : false),
  });

  const [tool, setTool] = useState(ALL);
  const [severity, setSeverity] = useState(ALL);
  const [verdict, setVerdict] = useState(ALL);
  const [publishedOnly, setPublishedOnly] = useState(false);
  const [open, setOpen] = useState<Set<number>>(new Set());

  const findings = data?.findings ?? [];
  const tools = useMemo(() => [...new Set(findings.map((f) => f.tool))].sort(), [findings]);
  const shown = findings.filter(
    (f) =>
      (tool === ALL || f.tool === tool) &&
      (severity === ALL || f.severity === severity) &&
      (verdict === ALL || (f.verdict ?? "none") === verdict) &&
      (!publishedOnly || f.published),
  );

  const toggle = (f: FindingRow) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(f.id)) next.delete(f.id);
      else next.add(f.id);
      return next;
    });

  return (
    <section>
      <Link className="back" to="/">← All runs</Link>
      <QueryState isLoading={isLoading} error={error} />
      {data && (
        <>
          <div className="page-head">
            <div>
              <h1>
                Run #{data.run.id} <StatusBadge status={data.run.status} />
              </h1>
              <p className="lede">
                {data.run.repo} · PR #{data.run.pr} · <span className="code-chip">{shortSha(data.run.head_sha)}</span>
              </p>
            </div>
            {data.has_artifacts && (
              <a
                className="button"
                href={artifactsUrl(data.run.id)}
                download
                title="Raw SARIF from each scanner, plus exactly what the AI agent received and returned."
              >
                <DownloadIcon /> Download tool outputs (.zip)
              </a>
            )}
          </div>
          {isActive(data.run.status) && <p className="muted">This run is in progress; refreshing every 3 seconds.</p>}
          {data.run.status === "failed" && (
            <p className="error-box" role="alert">
              <strong>Run failed:</strong> {data.run.error ?? "unknown error"}. Nothing was published to the PR.
            </p>
          )}

          <div className="card">
            <div className="card-body">
              <dl className="meta">
                <div>
                  <dt>Pull request</dt>
                  <dd>
                    <a href={`https://github.com/${data.run.repo}/pull/${data.run.pr}`} target="_blank" rel="noreferrer">
                      {data.run.repo} #{data.run.pr}
                    </a>{" "}
                    <span className="muted">({data.run.head_ref})</span>
                  </dd>
                </div>
                <div>
                  <dt>Head / merge-base</dt>
                  <dd>
                    <span className="code-chip">{shortSha(data.run.head_sha)}</span> /{" "}
                    <span className="code-chip">{shortSha(data.run.merge_base)}</span>
                  </dd>
                </div>
                <div>
                  <dt>Timing</dt>
                  <dd className="small">
                    created {formatTime(data.run.created_at)}
                    <br />
                    started {formatTime(data.run.started_at)}
                    <br />
                    finished {formatTime(data.run.finished_at)}
                  </dd>
                </div>
                <div>
                  <dt>Review</dt>
                  <dd>
                    {data.run.review_id ? (
                      <a
                        href={`https://github.com/${data.run.repo}/pull/${data.run.pr}#pullrequestreview-${data.run.review_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        review {data.run.review_id}
                      </a>
                    ) : (
                      "not published"
                    )}
                  </dd>
                </div>
              </dl>
              {data.tool_reports.length > 0 && (
                <ul className="tool-reports" aria-label="Tools">
                  {data.tool_reports.map((t, i) => (
                    <li key={i}>
                      <strong className="mono">{t.tool}</strong>{" "}
                      <span className={t.status === "ok" ? "ok-text" : "err-text"}>{t.status}</span>
                      {t.error && <span className="muted"> · {t.error}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="section-head">
            <h2>
              Findings <span>· {shown.length} of {findings.length}</span>
            </h2>
          </div>
          <div className="card">
            <div className="card-head">
              <div className="filters">
                <label>
                  Tool
                  <select value={tool} onChange={(e) => setTool(e.target.value)}>
                    <option value={ALL}>All</option>
                    {tools.map((t) => <option key={t}>{t}</option>)}
                  </select>
                </label>
                <label>
                  Severity
                  <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
                    <option value={ALL}>All</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
                </label>
                <label>
                  Verdict
                  <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
                    <option value={ALL}>All</option>
                    <option value="confirmed">Confirmed</option>
                    <option value="false_positive">False positive</option>
                    <option value="unclear">Unclear</option>
                    <option value="none">Not classified</option>
                  </select>
                </label>
                <label className="check">
                  <input type="checkbox" checked={publishedOnly} onChange={(e) => setPublishedOnly(e.target.checked)} />
                  Published only
                </label>
              </div>
            </div>
            {shown.length === 0 ? (
              <p className="empty">
                {findings.length === 0
                  ? `No findings${isActive(data.run.status) ? " yet" : ""}.`
                  : "No findings match these filters."}
              </p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th aria-label="Expand" />
                      <th>ID</th>
                      <th>Severity</th>
                      <th>Tool / rule</th>
                      <th>Location</th>
                      <th>Verdict</th>
                      <th>Your label</th>
                      <th>Published</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((f) => (
                      <Fragment key={f.id}>
                        <tr className="clickable" onClick={() => toggle(f)}>
                          <td>
                            <button className="expander" aria-expanded={open.has(f.id)} aria-label="Show details">
                              {open.has(f.id) ? "▾" : "▸"}
                            </button>
                          </td>
                          <td className="mono">{f.finding_id}</td>
                          <td><SeverityBadge severity={f.severity} /></td>
                          <td>
                            <div className="tool-name">{f.tool}</div>
                            <div className="cell-sub mono">{f.rule_id}</div>
                          </td>
                          <td>
                            <span className="code-chip">{location(f)}</span>
                            {f.in_diff && <span className="tag">changed line</span>}
                          </td>
                          <td><VerdictBadge verdict={f.verdict} /></td>
                          <td>{f.human_label ? <VerdictBadge verdict={f.human_label} /> : <span className="small muted">—</span>}</td>
                          <td>{f.published ? "Yes" : "No"}</td>
                        </tr>
                        {open.has(f.id) && (
                          <tr className="details">
                            <td />
                            <td colSpan={7}>
                              <div className="finding-body">
                                <div>
                                  <div className="eyebrow">Tool message</div>
                                  <p>{f.message}</p>
                                </div>
                                <div>
                                  <div className="eyebrow">Agent justification</div>
                                  <p>{f.justification ?? "—"}</p>
                                </div>
                              </div>
                              {f.verdict && <LabelButtons findingRowId={f.id} value={f.human_label} />}
                            </td>
                          </tr>
                        )}
                      </Fragment>
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
