import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Empty, ErrorBox, TableSkeleton, relTime } from "../components";

export default function Sources() {
  const { data, isPending, error } = useQuery({ queryKey: ["sources"], queryFn: api.sources });

  if (isPending) return <TableSkeleton cols={6} />;
  if (error) return <ErrorBox error={error} />;

  const broken = data?.filter((s) => !s.ok) ?? [];

  return (
    <>
      <div className="banner" role="note">
        Connector health. A source returning zero for a long stretch usually means its
        API changed, not that hiring stopped.
      </div>

      {broken.length > 0 && (
        <div className="banner banner-bad" role="alert">
          {broken.length} source{broken.length > 1 ? "s" : ""} failing:{" "}
          {broken.map((s) => s.source).join(", ")}
        </div>
      )}

      {data && data.length === 0 && (
        <Empty
          title="No polls recorded yet"
          hint="Use “Poll sources” in the header to run one now."
        />
      )}

      {data && data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col" style={{ width: 24 }}><span className="sr-only">Status</span></th>
                <th scope="col">Source</th>
                <th scope="col" style={{ width: 70 }}>Runs</th>
                <th scope="col" style={{ width: 80 }}>Failures</th>
                <th scope="col" style={{ width: 80 }}>Found</th>
                <th scope="col" style={{ width: 70 }}>New</th>
                <th scope="col" style={{ width: 90 }}>Last run</th>
                <th scope="col" style={{ width: 80 }}>Duration</th>
              </tr>
            </thead>
            <tbody>
              {data.map((s) => (
                <tr key={s.source}>
                  <td><span className={`dot ${s.ok ? "dot-ok" : "dot-bad"}`} /></td>
                  <td>
                    <div className="cell-title">{s.source}</div>
                    {s.last_error && (
                      <div style={{ color: "var(--bad)", fontSize: 11.5 }}>{s.last_error}</div>
                    )}
                  </td>
                  <td className="cell-dim num">{s.runs}</td>
                  <td className="num" style={{ color: s.failures ? "var(--bad)" : "var(--text-dim)" }}>
                    {s.failures}
                  </td>
                  <td className="cell-dim num">{s.found}</td>
                  <td className="cell-dim num">{s.new_jobs}</td>
                  <td className="cell-dim num">{relTime(s.last_run_at)}</td>
                  <td className="cell-dim num">{s.last_duration_ms}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
