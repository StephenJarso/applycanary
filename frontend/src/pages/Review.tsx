import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Chips, Empty, ErrorBox, ScoreBadge, TableSkeleton, relTime } from "../components";

export default function Review() {
  const qc = useQueryClient();
  const { data, isPending, error } = useQuery({ queryKey: ["review"], queryFn: api.review });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["review"] });
    void qc.invalidateQueries({ queryKey: ["status"] });
  };
  const submit = useMutation({ mutationFn: (id: number) => api.submit(id), onSuccess: refresh });
  const skip = useMutation({ mutationFn: (id: number) => api.skip(id), onSuccess: refresh });

  if (isPending) return <TableSkeleton cols={5} />;
  if (error) return <ErrorBox error={error} />;

  return (
    <>
      <div className="banner" role="note">
        Applications prepared and waiting for you. Nothing here has been sent.
      </div>

      {submit.isError && <ErrorBox error={submit.error} />}
      {submit.isSuccess && (
        <div className={`banner banner-${submit.data.ok ? "ok" : "warn"}`} role="status">
          {submit.data.message}
        </div>
      )}

      {data && data.length === 0 && (
        <Empty
          title="Nothing awaiting review"
          hint={<>Tailor a CV from a <Link to="/">job</Link> to queue an application.</>}
        />
      )}

      {data && data.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col" style={{ width: 52 }}>Score</th>
                <th scope="col">Role</th>
                <th scope="col" style={{ width: 150 }}>Company</th>
                <th scope="col" style={{ width: 60 }}>Age</th>
                <th scope="col" style={{ width: 190 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((job) => (
                <tr key={job.id}>
                  <td><ScoreBadge score={job.score} /></td>
                  <td>
                    <Link to={`/job/${job.id}`} className="cell-title">{job.title}</Link>
                    {job.score?.missing_keywords.length ? (
                      <Chips items={job.score.missing_keywords} variant="miss" max={4} />
                    ) : null}
                  </td>
                  <td>{job.company}</td>
                  <td className="cell-dim num">{relTime(job.posted_at ?? job.first_seen_at)}</td>
                  <td>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        className="btn-primary btn-sm"
                        onClick={() => submit.mutate(job.id)}
                        disabled={submit.isPending}
                      >
                        Submit
                      </button>
                      <Link to={`/job/${job.id}`} className="btn btn-sm">Review</Link>
                      <button
                        className="btn-danger btn-sm"
                        onClick={() => skip.mutate(job.id)}
                        disabled={skip.isPending}
                      >
                        Skip
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
