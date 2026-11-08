import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getTrackedOpps,
  setOpportunityStatus,
  type Opportunity,
  type OpportunityStatus,
} from "./api";
import { useAuth } from "./context/AuthContext";

/**
 * Shared per-user application state for the internship/program/volunteer cards.
 *
 * The cards are presentational, but they are the place the decision is made, so
 * the control that records "I applied" lives on them rather than behind another
 * page. Every card mounts this hook; React Query dedupes the shared request.
 */
export function useOpportunityTracking() {
  const { user } = useAuth();
  const qc = useQueryClient();

  const { data } = useQuery({
    queryKey: ["tracked-opps"],
    queryFn: getTrackedOpps,
    enabled: !!user,
  });

  const stages = new Map((data ?? []).map((row) => [row.opp_url, row.stage]));

  const mark = useMutation({
    mutationFn: (args: { item: Opportunity; status: OpportunityStatus }) =>
      setOpportunityStatus({
        url: args.item.url,
        title: args.item.title,
        org: args.item.org,
        kind: args.item.kind,
        deadline: args.item.closes ?? args.item.date ?? null,
        status: args.status,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tracked-opps"] }),
  });

  return {
    signedIn: !!user,
    stageFor: (url: string) => stages.get(url) ?? "",
    mark: (item: Opportunity, status: OpportunityStatus) =>
      mark.mutate({ item, status }),
    busy: mark.isPending,
  };
}
