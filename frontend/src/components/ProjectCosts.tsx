import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, formatUsd, formatWhen } from "../lib/api";
import { humanize } from "../lib/humanize";

export function ProjectCosts({ projectId }: { projectId: string }) {
  const costs = useQuery({
    queryKey: ["project-costs", projectId],
    queryFn: () => api.projectCosts(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 4000,
  });
  if (!projectId) return null;
  const data = costs.data;
  return (
    <section className="panel space-y-3 p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Provider API cost</h2>
          <p className="text-xs text-[var(--muted)]">
            Each model call is priced from the catalog list rate. This is an estimate, not an invoice.
          </p>
        </div>
        {data && <div className="mono text-2xl">{formatUsd(data.cost_usd)}</div>}
      </div>
      {costs.isError && <p className="text-sm text-[var(--rose)]">{(costs.error as Error).message}</p>}
      {costs.isLoading && <p className="text-sm text-[var(--muted)]">Loading provider calls…</p>}
      {data && (
        <>
          <p className="text-xs text-[var(--muted)]">
            {data.input_tokens.toLocaleString()} input tokens · {data.output_tokens.toLocaleString()} output tokens
            {data.unpriced_calls > 0
              ? ` · ${data.unpriced_calls} ${data.unpriced_calls === 1 ? "call has" : "calls have"} no catalog price`
              : ""}
            {data.unitemized_usd > 0 ? ` · ${formatUsd(data.unitemized_usd)} stored on runs without a per-call record` : ""}
          </p>
          {data.calls.length ? (
            <ul className="max-h-80 space-y-2 overflow-auto text-sm">
              {data.calls.map((call) => {
                const when = formatWhen(call.created_at);
                const money = call.status === "completed" ? formatUsd(call.cost_usd, call.price_known) : "—";
                return (
                  <li key={call.id}>
                    <Link to={`/runs/${call.run_id}/logs`} className="grid gap-1 rounded-md border border-transparent p-2 hover:border-[var(--line)] sm:grid-cols-[1fr_auto]">
                      <span className="min-w-0">
                        <span className="block truncate">{call.video_filename || call.run_name}</span>
                        <span className="text-xs text-[var(--muted)]">
                          {humanize(call.provider_id)}/{humanize(call.model_id)} · {humanize(call.stage)} · {humanize(call.status)}
                          {when ? ` · ${when}` : ""}
                        </span>
                      </span>
                      <span className="mono text-xs text-[var(--muted)] sm:text-right">
                        {call.input_tokens.toLocaleString()} in · {call.output_tokens.toLocaleString()} out · {money}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-[var(--muted)]">No provider calls yet. Fixture and local analyses are not billed.</p>
          )}
        </>
      )}
    </section>
  );
}
