import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, findingsCsvUrl } from "../api";
import {
  DownloadIcon,
  LabelButtons,
  QueryState,
  SeverityBadge,
  SplitBar,
  StatCard,
  VerdictBadge,
  location,
} from "../components";

const ALL = "all";
const PAGE = 5;

const fmt = (v: number | null) => (v === null ? "—" : `${v}%`);

export default function EvaluationPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["evaluation"], queryFn: api.evaluation });
  const [scope, setScope] = useState(ALL);
  const [limit, setLimit] = useState(PAGE);

  const metrics = (scope === ALL ? data?.overall : data?.by_tool.find((t) => t.tool === scope)) ?? data?.overall;
  const queue = (data?.queue ?? []).filter((f) => scope === ALL || f.tool === scope);
  const sample = (metrics?.labelled ?? 0) + queue.length;

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Evaluation</h1>
          <p className="lede">
            Label a sample of findings yourself to measure how accurate the AI agent's verdicts are. Metrics only
            count findings that have both an agent verdict and your label.
          </p>
        </div>
        <a className="button" href={findingsCsvUrl} download>
          <DownloadIcon /> Export all findings (.csv)
        </a>
      </div>
      <QueryState isLoading={isLoading} error={error} />
      {data && metrics && (
        <>
          <div className="section-head">
            <h2>
              Agent accuracy <span>· {scope === ALL ? "all tools" : scope}</span>
            </h2>
            {data.by_tool.length > 0 ? (
              <div className="tabs">
                {[ALL, ...data.by_tool.map((t) => t.tool)].map((t) => (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={scope === t}
                    onClick={() => {
                      setScope(t);
                      setLimit(PAGE);
                    }}
                  >
                    {t === ALL ? "All tools" : t}
                  </button>
                ))}
              </div>
            ) : (
              <span className="small muted">Updates as you label</span>
            )}
          </div>
          <div className="stats">
            <StatCard
              label="Labelled"
              foot={<SplitBar parts={[{ tone: "progress", value: metrics.labelled }, { tone: "rest", value: queue.length }]} />}
            >
              {metrics.labelled}
              <small>/ {sample}</small>
            </StatCard>
            <StatCard label="Agreement" foot="Your label matches the agent">
              {fmt(metrics.agreement)}
            </StatCard>
            <StatCard label="Agent precision" foot="Of “confirmed”, how many truly are">
              {fmt(metrics.agent_precision)}
            </StatCard>
            <StatCard label="FP-filter accuracy" foot="Of “false positive”, how many truly are">
              {fmt(metrics.fp_filter_accuracy)}
            </StatCard>
          </div>

          <h2>
            Labelling queue <span className="pill">{queue.length} left</span>
          </h2>
          <p className="section-note">
            A random sample of findings the agent classified that you haven't labelled yet.
          </p>
          {queue.length === 0 ? (
            <div className="card">
              <p className="empty">Nothing to label. Every classified finding here has a human label.</p>
            </div>
          ) : (
            <div className="queue">
              {queue.slice(0, limit).map((f) => (
                <article key={f.id} className="queue-item">
                  <header className="queue-head">
                    <SeverityBadge severity={f.severity} />
                    <span className="tool-name">{f.tool}</span>
                    <span className="mono small muted">{f.rule_id}</span>
                    <span className="queue-loc">
                      <span className="code-chip">{location(f)}</span>
                      <Link to={`/runs/${f.run_id}`}>
                        {f.repo.split("/").pop()} #{f.pr} · run {f.run_id}
                      </Link>
                    </span>
                  </header>
                  <div className="finding-body">
                    <div>
                      <div className="eyebrow">Tool message</div>
                      <p>{f.message}</p>
                    </div>
                    <div className="verdict-box">
                      <div className="eyebrow">
                        Agent verdict <VerdictBadge verdict={f.verdict} />
                      </div>
                      <p>{f.justification ?? "—"}</p>
                    </div>
                  </div>
                  <LabelButtons findingRowId={f.id} value={f.human_label} />
                </article>
              ))}
              {queue.length > limit && (
                <button type="button" className="more" onClick={() => setLimit(limit + PAGE)}>
                  {queue.length - limit} more {queue.length - limit === 1 ? "finding" : "findings"} in this sample
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
