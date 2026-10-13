import { useQuery } from "@tanstack/react-query";
import { api, type ToolStat } from "../api";
import { QueryState, SplitBar, StatCard, ToolName, percent } from "../components";

const count = (n: number) => <span className={n === 0 ? "zero" : undefined}>{n}</span>;

function Summary({ stats }: { stats: ToolStat[] }) {
  const sum = (pick: (s: ToolStat) => number) => stats.reduce((n, s) => n + pick(s), 0);
  const all = sum((s) => s.total);
  const confirmed = sum((s) => s.confirmed);
  const falsePositive = sum((s) => s.false_positive);
  const unclear = sum((s) => s.unclear);
  const fpTools = stats.filter((s) => s.false_positive > 0);

  return (
    <div className="stats">
      <StatCard label="Findings reviewed" foot={`from ${stats.length} ${stats.length === 1 ? "tool" : "tools"}`}>
        {all}
      </StatCard>
      <StatCard label="Confirmed" foot={`${percent(confirmed, all)}% of findings`}>
        <span className="c-confirmed">{confirmed}</span>
      </StatCard>
      <StatCard
        label="False positives filtered"
        foot={
          <>
            {percent(falsePositive, all)}% of findings
            {fpTools.length === 1 && ` · all from ${fpTools[0].tool}`}
            {fpTools.length > 1 && ` · from ${fpTools.length} tools`}
          </>
        }
      >
        <span className="c-fp">{falsePositive}</span>
      </StatCard>
      <StatCard label="Unclear" foot={unclear === 0 ? "nothing needs a second look" : "need a second look"}>
        {unclear}
      </StatCard>
    </div>
  );
}

export default function ToolStatsPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["toolStats"], queryFn: api.toolStats });

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Tool stats</h1>
          <p className="lede">How often the agent confirmed or rejected each tool's findings, across all runs.</p>
        </div>
      </div>
      <QueryState isLoading={isLoading} error={error} />
      {data && (
        <>
          <Summary stats={data} />
          <div className="card">
            <div className="card-head">
              <h2>By tool</h2>
              <div className="legend">
                <span><i className="tone-confirmed" />Confirmed</span>
                <span><i className="tone-fp" />False positive</span>
                <span><i className="tone-unclear" />Unclear</span>
              </div>
            </div>
            {data.length === 0 ? (
              <p className="empty">No findings recorded yet.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Tool</th>
                      <th className="num">Findings</th>
                      <th className="num">Confirmed</th>
                      <th className="num">False pos.</th>
                      <th className="num">Unclear</th>
                      <th>Verdict split</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.map((s) => (
                      <tr key={s.tool}>
                        <td><ToolName tool={s.tool} /></td>
                        <td className="num big-num">{s.total}</td>
                        <td className="num big-num c-confirmed">{count(s.confirmed)}</td>
                        <td className="num big-num c-fp">{count(s.false_positive)}</td>
                        <td className="num big-num">{count(s.unclear)}</td>
                        <td className="split-cell">
                          <SplitBar
                            parts={[
                              { tone: "confirmed", value: s.confirmed },
                              { tone: "fp", value: s.false_positive },
                              { tone: "unclear", value: s.unclear },
                            ]}
                          />
                          <div className="small muted">
                            <strong className="c-confirmed">{s.confirmed_pct}%</strong> confirmed ·{" "}
                            <strong className="c-fp">{s.false_positive_pct}%</strong> false positive
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="card-foot">
              A high false-positive share means that tool's rules may need tuning before its findings block merges.
            </div>
          </div>
        </>
      )}
    </section>
  );
}
