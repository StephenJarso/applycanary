import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBox, ScoreBadge, TableSkeleton, relTime } from "../components";

export default function Applications() {
  const { data, isPending, error } = useQuery({
    queryKey: ["applications"],
    queryFn: api.applications,
  });

  if (isPending) return <TableSkeleton cols={5} />;
  if (error) return <ErrorBox error={error} />;

  return (
    <>
      {data && data.length === 0 && (
        <Empty
          title="No applications sent yet"
          hint={<>Prepared applications wait in the <Link to="/review">review queue</Link>.</>}
        />
      )}

      {data && data.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">{data.length} submitted applications</caption>
            <thead>
              <tr>
                <th scope="col" style={{ width: 52 }}>Score</th>
                <th scope="col">Role</th>
                <th scope="col" style={{ width: 160 }}>Company</th>
                <th scope="col" style={{ width: 110 }}>Source</th>
                <th scope="col" style={{ width: 90 }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((job) => (
                <tr key={job.id}>
                  <td><ScoreBadge score={job.score} /></td>
                  <td>
                    <Link to={`/job/${job.id}`} className="cell-title">{job.title}</Link>
                  </td>
                  <td>{job.company}</td>
                  <td className="cell-dim">{job.source}</td>
                  <td className="cell-dim">
                    <span className="chip">{job.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.length > 0 && (
        <p className="cell-dim" style={{ marginTop: 12, fontSize: 12 }}>
          Oldest application {relTime(data[data.length - 1]?.first_seen_at ?? null)} ago.
        </p>
      )}
    </>
  );
}
