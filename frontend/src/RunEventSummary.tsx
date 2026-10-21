import { runSummary } from "./runSummary";

export function RunEventSummary({
  kind,
  data,
}: {
  kind: string;
  data: Record<string, unknown>;
}) {
  const summary = runSummary(kind, data);
  const result = (data.result || {}) as Record<string, unknown>;
  const items = Array.isArray(result.items)
    ? (result.items as Record<string, unknown>[])
    : [];
  return (
    <>
      <strong>{summary.title}</strong>
      <p className="run-summary">{summary.detail}</p>
      {summary.interface && (
        <small className="cell-help">{summary.interface}</small>
      )}
      {data.tool === "search_policies" &&
        items.map((p, i) => (
          <blockquote key={i} className="source-quote">
            <b>
              {String(p.title)} · V{String(p.version)}
            </b>
            <p>{String(p.body)}</p>
          </blockquote>
        ))}
      <details className="run-debug">
        <summary>技术诊断</summary>
        <pre>
          {kind}
          {"\n"}
          {JSON.stringify(data, null, 2)}
        </pre>
      </details>
    </>
  );
}
