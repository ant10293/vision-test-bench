import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, formatTime } from "../lib/api";
import { RunAnalysis } from "./RunPages";

export function DashboardPage() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 3000 });
  const data = dash.data;
  if (dash.isError) return <p className="text-[var(--rose)]">{(dash.error as Error).message}</p>;
  if (!data) return <p className="text-[var(--muted)]">Loading the laboratory…</p>;
  const cards = [
    ["Videos", data.videos],
    ["Completed runs", data.runs_completed],
    ["Active runs", data.runs_active],
    ["Objects", data.objects],
    ["Actions", data.actions],
    ["Workflows", data.workflows],
    ["Ambiguities", data.ambiguities],
    ["Connected providers", data.providers_connected.length],
  ];
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-medium">Research dashboard</h1>
          <p className="text-sm text-[var(--muted)]">
            What models claim about an unfamiliar procedure, and which claims the pictures support.
          </p>
        </div>
        <Link className="btn primary" to="/experiments/new">New experiment</Link>
      </header>
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {cards.map(([label, value]) => (
          <div key={String(label)} className="panel p-3">
            <div className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</div>
            <div className="mono text-2xl">{value}</div>
          </div>
        ))}
      </section>
      <section className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="panel p-4">
          <h2 className="mb-3 text-sm font-medium">Recent experiments</h2>
          <div className="space-y-2">
            {data.recent_runs.map((run) => (
              <Link key={run.id} to={`/runs/${run.id}`} className="grid grid-cols-[72px_1fr] gap-3 rounded-md border border-transparent p-2 hover:border-[var(--line)]">
                {run.thumbnail_url ? <img src={run.thumbnail_url} alt="" className="aspect-video w-full rounded bg-black object-contain" /> : <div className="aspect-video rounded bg-[#0d1016]" />}
                <div className="min-w-0">
                  <div className="truncate">{run.name}</div>
                  <div className="text-xs text-[var(--muted)]">
                    {run.provider_id} · {run.model_id} · {run.modality} · {run.status}
                    {run.fixture ? " · fixture" : ""} · {run.action_count} actions · {run.object_count} objects
                    · {formatTime(run.duration_sec)} · ${run.cost_usd.toFixed(4)}
                  </div>
                </div>
              </Link>
            ))}
            {!data.recent_runs.length && <p className="text-sm text-[var(--muted)]">No experiments yet. Start one, or let the fixture laboratory finish seeding.</p>}
          </div>
        </div>
        <div className="panel p-4">
          <h2 className="mb-2 text-sm font-medium">Actions per recent run</h2>
          <div className="h-52">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.recent_runs.map((run) => ({ name: run.model_id.slice(0, 12), actions: run.action_count, objects: run.object_count }))}>
                <XAxis dataKey="name" stroke="#8b95a8" fontSize={11} />
                <YAxis stroke="#8b95a8" fontSize={11} allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="actions" fill="#3ddc97" />
                <Bar dataKey="objects" fill="#7eb6ff" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-3 text-xs text-[var(--muted)]">
            Tokens {data.input_tokens + data.output_tokens} · estimated API cost ${data.cost_usd.toFixed(4)}.
            Connected means a validation request succeeded: {data.providers_connected.join(", ") || "none yet"}.
          </p>
        </div>
      </section>
      <LatestAnalysis />
    </div>
  );
}

function LatestAnalysis() {
  const runs = useQuery({
    queryKey: ["runs", "completed"],
    queryFn: () => api.runs({ status: "completed" }),
    refetchInterval: 3000,
  });
  const first = runs.data?.[0];
  return (
    <section className="panel space-y-3 p-4">
      <h2 className="text-sm font-medium">Latest analysis</h2>
      {runs.isError && <p className="text-sm text-[var(--rose)]">{(runs.error as Error).message}</p>}
      {runs.isLoading && <p className="text-sm text-[var(--muted)]">Loading analysis…</p>}
      {runs.isSuccess && !first && <p className="text-sm text-[var(--muted)]">No completed analysis yet.</p>}
      {first && <RunAnalysis key={first.id} runId={first.id} embedded />}
    </section>
  );
}
