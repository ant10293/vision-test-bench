import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import type { RunSummary } from "../lib/types";

export function ProvidersPage() {
  const client = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [bases, setBases] = useState<Record<string, string>>({});
  const [models, setModels] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: (id: string) => api.saveKey(id, keys[id] ?? "", bases[id] ?? ""),
    onSuccess: async () => {
      setKeys({});
      setMessage("Key stored on the server. It is not returned to the browser.");
      await client.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error: Error) => setMessage(error.message),
  });
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium">Providers</h1>
      <p className="max-w-3xl text-sm text-[var(--muted)]">
        Keys stay on the backend. A provider is connected only after Validate succeeds against the real API.
        Deterministic and fixture providers need no key. Live inference has not been claimed from this screen until that call returns ok.
      </p>
      {message && <p className="text-sm">{message}</p>}
      <div className="space-y-3">
        {providers.data?.map((provider) => (
          <section key={provider.id} className="panel space-y-2 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg">{provider.id}</h2>
              <span className={`tag ${provider.validated ? "observed" : "unknown"}`}>{provider.validated ? "validated" : "not validated"}</span>
              {provider.configured && <span className="tag">key {provider.secret_source} {provider.hint}</span>}
            </div>
            {provider.last_error && <p className="text-sm text-[var(--rose)]">{provider.last_error}</p>}
            {!["deterministic", "fixture", "manual_json"].includes(provider.id) && (
              <div className="flex flex-wrap items-center gap-2">
                <input className="field" type="password" autoComplete="off" placeholder="API key" value={keys[provider.id] ?? ""} onChange={(event) => setKeys({ ...keys, [provider.id]: event.target.value })} />
                <input className="field" placeholder={provider.id === "openai_compatible" ? "Base URL" : "Optional base URL"} value={bases[provider.id] ?? provider.base_url} onChange={(event) => setBases({ ...bases, [provider.id]: event.target.value })} />
                <button className="btn" onClick={() => save.mutate(provider.id)}>Save key</button>
                <button className="btn" onClick={async () => {
                  try {
                    await api.validateProvider(provider.id);
                    setMessage(`${provider.id} validation request succeeded.`);
                  } catch (error) {
                    setMessage(error instanceof Error ? error.message : "validation failed");
                  }
                  await client.invalidateQueries({ queryKey: ["providers"] });
                }}>Validate</button>
              </div>
            )}
            {["deterministic", "fixture", "manual_json"].includes(provider.id) && (
              <button className="btn" onClick={async () => { await api.validateProvider(provider.id); await client.invalidateQueries({ queryKey: ["providers"] }); }}>Mark local provider ready</button>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <input className="field" placeholder="Add model id" value={models[provider.id] ?? ""} onChange={(event) => setModels({ ...models, [provider.id]: event.target.value })} />
              <button className="btn shrink-0" onClick={async () => { await api.addModel(provider.id, models[provider.id] ?? ""); await client.invalidateQueries({ queryKey: ["providers"] }); }}>Add model</button>
              <button className="btn shrink-0" onClick={async () => {
                const found = await api.discoverModels(provider.id);
                setMessage(found.error ? found.error : `Discovered ${found.discovered.length} model ids.`);
              }}>Discover</button>
            </div>
            <ul className="grid gap-1 text-xs text-[var(--muted)] md:grid-cols-2">
              {provider.models.map((model) => (
                <li key={model.model_id}>{model.model_id} · video {model.native_video ? "yes" : "no"} · images {model.max_images} · {model.notes}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

/** Ignore surrounding and trailing whitespace so spacing-only edits are not a new version. */
function comparablePrompt(value: string): string {
  return value
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trimEnd())
    .join("\n")
    .trim();
}

export function PromptsPage() {
  const client = useQueryClient();
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: api.prompts });
  const [key, setKey] = useState("scene");
  const current = prompts.data?.find((item) => item.key === key);
  const [body, setBody] = useState<string | null>(null);
  const saved = current?.body ?? "";
  const draft = body ?? saved;
  const unchanged = !current || comparablePrompt(draft) === comparablePrompt(saved);
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-medium">Prompt versions</h1>
      <p className="text-sm text-[var(--muted)]">Saving creates a new version. Past experiments keep the snapshot they launched with.</p>
      <select className="field max-w-sm" value={key} onChange={(event) => { setKey(event.target.value); setBody(null); }}>
        {[...new Set(prompts.data?.map((item) => item.key) ?? [])].map((item) => <option key={item}>{item}</option>)}
      </select>
      <textarea className="field min-h-80 font-mono text-xs" value={draft} onChange={(event) => setBody(event.target.value)} />
      <button className="btn primary" disabled={unchanged} onClick={async () => {
        if (unchanged) return;
        await api.savePrompt(key, current?.name || key, draft);
        setBody(null);
        await client.invalidateQueries({ queryKey: ["prompts"] });
      }}>Save new version</button>
      {current && <p className="text-xs text-[var(--muted)]">v{current.version}</p>}
    </div>
  );
}

export function ComparePage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.runs() });
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [comparisonId, setComparisonId] = useState("");
  const [matchLeft, setMatchLeft] = useState("");
  const [matchRight, setMatchRight] = useState("");
  const completed = runs.data?.filter((run) => run.status === "completed") ?? [];
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium">Model comparison laboratory</h1>
      <p className="max-w-3xl text-sm text-[var(--muted)]">
        Object ids from different runs are not the same object. Assisted matches use labels and are marked as assisted. Disagreement is not a verdict.
      </p>
      <div className="grid items-stretch gap-4 lg:grid-cols-2">
        <RunColumn title="Left run" value={left} onChange={setLeft} runs={completed} />
        <RunColumn title="Right run" value={right} onChange={setRight} runs={completed} />
      </div>
      <button className="btn primary" disabled={!left || !right} onClick={async () => {
        const created = await api.compare(left, right);
        setComparisonId(created.id);
        setReport(created.report);
      }}>Compare</button>
      {report && (
        <div className="space-y-3">
          <pre className="panel mono max-h-[480px] overflow-auto p-3 text-xs">{JSON.stringify(report, null, 2)}</pre>
          <div className="flex flex-wrap gap-2">
            <input className="field max-w-[160px]" placeholder="Left object id" value={matchLeft} onChange={(event) => setMatchLeft(event.target.value)} />
            <input className="field max-w-[160px]" placeholder="Right object id" value={matchRight} onChange={(event) => setMatchRight(event.target.value)} />
            <button className="btn" onClick={async () => {
              const updated = await api.setMatches(comparisonId, [{ left: matchLeft, right: matchRight }]);
              setReport(updated.report);
            }}>Set manual match</button>
          </div>
        </div>
      )}
    </div>
  );
}

function RunColumn({
  title,
  value,
  onChange,
  runs,
}: {
  title: string;
  value: string;
  onChange: (id: string) => void;
  runs: RunSummary[];
}) {
  const selected = runs.find((run) => run.id === value);
  return (
    <section className="panel flex h-full min-h-48 flex-col gap-3 p-4">
      <h2 className="text-sm font-medium">{title}</h2>
      <select className="field stretch" value={value} onChange={(event) => onChange(event.target.value)} aria-label={title}>
        <option value="">{title}</option>
        {runs.map((run) => <option key={run.id} value={run.id}>{run.name}</option>)}
      </select>
      {selected ? (
        <dl className="mt-auto grid grid-cols-2 gap-x-4 gap-y-2 border-t border-[var(--line)] pt-3 text-xs">
          <div>
            <dt className="text-[var(--muted)]">Model</dt>
            <dd>{selected.model_id || "unset"}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted)]">Provider</dt>
            <dd>{selected.provider_id}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-[var(--muted)]">Video</dt>
            <dd className="truncate">{selected.video_filename}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted)]">Objects / actions</dt>
            <dd>{selected.object_count} / {selected.action_count}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-auto text-xs text-[var(--muted)]">Select a completed run.</p>
      )}
    </section>
  );
}
