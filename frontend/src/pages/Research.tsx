import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { humanize, humanizeText } from "../lib/humanize";
import type { ClaudeCodeSettings, CursorCliSettings, RunSummary } from "../lib/types";

function ClaudeCodePanel({ settings }: { settings: ClaudeCodeSettings }) {
  const client = useQueryClient();
  const [binary, setBinary] = useState(settings.binary);
  const [transport, setTransport] = useState(settings.transport);
  const [awaiting, setAwaiting] = useState(false);
  const [loginUrl, setLoginUrl] = useState("");
  const [loginCode, setLoginCode] = useState("");
  const [note, setNote] = useState("");
  const status = useQuery({
    queryKey: ["claude-code"],
    queryFn: api.claudeCode,
    refetchInterval: awaiting ? 3000 : false,
  });
  useEffect(() => {
    setBinary(settings.binary);
    setTransport(settings.transport);
  }, [settings.binary, settings.transport]);
  useEffect(() => {
    if (awaiting && status.data?.logged_in) {
      setAwaiting(false);
      setLoginUrl("");
      setLoginCode("");
      setNote("Claude Code is linked.");
    }
  }, [awaiting, status.data?.logged_in]);
  const linked = status.data?.logged_in ?? false;
  const dirty = binary !== settings.binary || transport !== settings.transport;
  return (
    <div className="space-y-2 rounded-lg border border-[var(--line)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">Claude Code</h3>
        <span className={`tag ${linked ? "observed" : "unknown"}`}>{linked ? "linked" : "not linked"}</span>
        <span className="tag">{settings.transport === "claude_code" ? "runs use Claude Code" : "runs use API key"}</span>
        {status.data?.version && <span className="tag">{status.data.version}</span>}
      </div>
      <p className="text-xs text-[var(--muted)]">
        Uses the Claude plan signed in through the Claude Code executable on this machine. The API key above still calls the Anthropic API when runs are set to API key.
      </p>
      {linked && (
        <p className="text-sm">
          {status.data?.subscription ? humanize(status.data.subscription) : "Signed in"}
          {status.data?.auth_method ? ` · ${status.data.auth_method}` : ""}
          {status.data?.account ? ` · ${status.data.account}` : ""}
        </p>
      )}
      {status.data?.error && !awaiting && <p className="text-sm text-[var(--rose)]">{status.data.error}</p>}
      <div className="grid gap-2 md:grid-cols-2">
        <label className="field-label">
          Executable
          <input className="field" value={binary} spellCheck={false} onChange={(event) => setBinary(event.target.value)} />
        </label>
        <label className="field-label">
          Anthropic runs
          <select className="field stretch" value={transport} onChange={(event) => setTransport(event.target.value as ClaudeCodeSettings["transport"])}>
            <option value="api">API key</option>
            <option value="claude_code">Claude Code subscription</option>
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={!dirty} onClick={async () => {
          try {
            await api.saveClaudeCode(binary, transport);
            setNote("Claude Code settings saved.");
            await client.invalidateQueries({ queryKey: ["providers"] });
            await client.invalidateQueries({ queryKey: ["claude-code"] });
          } catch (error) {
            setNote(error instanceof Error ? error.message : "save failed");
          }
        }}>Save</button>
        <button className="btn" onClick={async () => {
          try {
            const result = await api.linkClaudeCode(false);
            setLoginUrl(result.login_url || "");
            setAwaiting(Boolean(result.login_started) && !result.logged_in);
            setNote(result.logged_in ? "Claude Code is linked." : "Open the sign-in page and choose the Pro or Max account. Paste the code it shows here.");
            await client.invalidateQueries({ queryKey: ["claude-code"] });
          } catch (error) {
            setAwaiting(false);
            setNote(error instanceof Error ? error.message : "link failed");
          }
        }}>{linked ? "Refresh link" : "Link Claude Code"}</button>
        {linked && (
          <button className="btn" onClick={async () => {
            try {
              const result = await api.linkClaudeCode(true);
              setLoginUrl(result.login_url || "");
              setAwaiting(false);
              setNote("Finish the new sign-in in the browser to replace the linked account.");
            } catch (error) {
              setAwaiting(false);
              setNote(error instanceof Error ? error.message : "sign-in failed");
            }
          }}>Sign in again</button>
        )}
      </div>
      {loginUrl && <a className="text-sm text-[var(--blue)]" href={loginUrl} target="_blank" rel="noreferrer">Open the Claude sign-in page</a>}
      {awaiting && (
        <div className="flex flex-wrap items-center gap-2">
          <input className="field" spellCheck={false} placeholder="Paste the sign-in code" value={loginCode} onChange={(event) => setLoginCode(event.target.value)} />
          <button className="btn" disabled={!loginCode.trim()} onClick={async () => {
            try {
              const result = await api.submitClaudeCode(loginCode.trim());
              setLoginCode("");
              if (result.logged_in) {
                setAwaiting(false);
                setLoginUrl("");
                setNote("Claude Code is linked.");
              } else {
                setNote(result.error || "That code was not accepted. Paste the code from the Pro or Max account.");
              }
              await client.invalidateQueries({ queryKey: ["claude-code"] });
            } catch (error) {
              setNote(error instanceof Error ? error.message : "sign-in failed");
            }
          }}>Submit code</button>
        </div>
      )}
      {note && <p className="text-sm">{note}</p>}
    </div>
  );
}

function CursorCliPanel({ settings }: { settings: CursorCliSettings }) {
  const client = useQueryClient();
  const [binary, setBinary] = useState(settings.binary);
  const [transport, setTransport] = useState(settings.transport);
  const [awaiting, setAwaiting] = useState(false);
  const [loginUrl, setLoginUrl] = useState("");
  const [note, setNote] = useState("");
  const status = useQuery({
    queryKey: ["cursor-cli"],
    queryFn: api.cursorCli,
    refetchInterval: awaiting ? 3000 : false,
  });
  useEffect(() => {
    setBinary(settings.binary);
    setTransport(settings.transport);
  }, [settings.binary, settings.transport]);
  useEffect(() => {
    if (awaiting && status.data?.logged_in) {
      setAwaiting(false);
      setLoginUrl("");
      setNote("Cursor is linked.");
    }
  }, [awaiting, status.data?.logged_in]);
  const linked = status.data?.logged_in ?? false;
  const dirty = binary !== settings.binary || transport !== settings.transport;
  return (
    <div className="space-y-2 rounded-lg border border-[var(--line)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">Cursor CLI</h3>
        <span className={`tag ${linked ? "observed" : "unknown"}`}>{linked ? "linked" : "not linked"}</span>
        <span className="tag">{settings.transport === "login" ? "runs use the Cursor account" : "runs use the API key"}</span>
        {status.data?.version && <span className="tag">{status.data.version}</span>}
      </div>
      <p className="text-xs text-[var(--muted)]">
        Analysis runs call the agent executable in ask mode. The agent can read the frames and is not given permission to edit files. A linked account uses the Cursor plan. The API key above is used only when runs are set to API key.
      </p>
      {linked && (
        <p className="text-sm">
          {status.data?.account ? `Signed in · ${status.data.account}` : "Signed in"}
          {status.data?.auth_method ? ` · ${status.data.auth_method}` : ""}
        </p>
      )}
      {status.data?.error && !awaiting && <p className="text-sm text-[var(--rose)]">{status.data.error}</p>}
      <div className="grid gap-2 md:grid-cols-2">
        <label className="field-label">
          Executable
          <input className="field" value={binary} spellCheck={false} onChange={(event) => setBinary(event.target.value)} />
        </label>
        <label className="field-label">
          Cursor runs
          <select className="field stretch" value={transport} onChange={(event) => setTransport(event.target.value as CursorCliSettings["transport"])}>
            <option value="login">Cursor account</option>
            <option value="api_key">API key</option>
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={!dirty} onClick={async () => {
          try {
            await api.saveCursorCli(binary, transport);
            setNote("Cursor settings saved.");
            await client.invalidateQueries({ queryKey: ["providers"] });
            await client.invalidateQueries({ queryKey: ["cursor-cli"] });
          } catch (error) {
            setNote(error instanceof Error ? error.message : "save failed");
          }
        }}>Save</button>
        {transport === "login" && (
          <button className="btn" onClick={async () => {
            try {
              const result = await api.linkCursorCli(false);
              setLoginUrl(result.login_url || "");
              setAwaiting(Boolean(result.login_started) && !result.logged_in);
              setNote(result.logged_in ? "Cursor is linked." : "Open the sign-in page and finish with the Cursor account. This page checks until it is linked.");
              await client.invalidateQueries({ queryKey: ["cursor-cli"] });
            } catch (error) {
              setAwaiting(false);
              setNote(error instanceof Error ? error.message : "link failed");
            }
          }}>{linked ? "Refresh link" : "Link Cursor"}</button>
        )}
        {transport === "login" && linked && (
          <button className="btn" onClick={async () => {
            try {
              const result = await api.linkCursorCli(true);
              setLoginUrl(result.login_url || "");
              setAwaiting(Boolean(result.login_started));
              setNote("Finish the new sign-in in the browser to replace the linked account.");
            } catch (error) {
              setAwaiting(false);
              setNote(error instanceof Error ? error.message : "sign-in failed");
            }
          }}>Sign in again</button>
        )}
      </div>
      {loginUrl && <a className="text-sm text-[var(--blue)]" href={loginUrl} target="_blank" rel="noreferrer">Open the Cursor sign-in page</a>}
      {note && <p className="text-sm">{note}</p>}
    </div>
  );
}

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
              <h2 className="text-lg">{humanize(provider.id)}</h2>
              <span className={`tag ${provider.validated ? "observed" : "unknown"}`}>{provider.validated ? "validated" : "not validated"}</span>
              {provider.configured && <span className="tag">key {humanize(provider.secret_source)} {provider.hint}</span>}
            </div>
            {provider.last_error && <p className="text-sm text-[var(--rose)]">{provider.last_error}</p>}
            {provider.id === "anthropic" && (
              <p className="text-sm text-[var(--muted)]">API key. Analysis runs use it when Anthropic runs is set to API key.</p>
            )}
            {provider.id === "cursor" && (
              <p className="text-sm text-[var(--muted)]">API key. Analysis runs use it when Cursor runs is set to API key.</p>
            )}
            {!["deterministic", "fixture", "manual_json"].includes(provider.id) && (
              <div className="flex flex-wrap items-center gap-2">
                <input className="field" type="password" autoComplete="off" placeholder="API key" value={keys[provider.id] ?? ""} onChange={(event) => setKeys({ ...keys, [provider.id]: event.target.value })} />
                {provider.id !== "cursor" && (
                  <input className="field" placeholder={provider.id === "openai_compatible" ? "Base URL" : "Optional base URL"} value={bases[provider.id] ?? provider.base_url} onChange={(event) => setBases({ ...bases, [provider.id]: event.target.value })} />
                )}
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
            {provider.id === "anthropic" && provider.claude_code && <ClaudeCodePanel settings={provider.claude_code} />}
            {provider.id === "cursor" && provider.cursor_cli && <CursorCliPanel settings={provider.cursor_cli} />}
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
                <li key={model.model_id}>{humanize(model.model_id)} · video {model.native_video ? "yes" : "no"} · images {model.max_images} · {model.notes}</li>
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
        {[...new Set(prompts.data?.map((item) => item.key) ?? [])].map((item) => <option key={item} value={item}>{humanize(item)}</option>)}
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
        {runs.map((run) => <option key={run.id} value={run.id}>{humanizeText(run.name)}</option>)}
      </select>
      {selected ? (
        <dl className="mt-auto grid grid-cols-2 gap-x-4 gap-y-2 border-t border-[var(--line)] pt-3 text-xs">
          <div>
            <dt className="text-[var(--muted)]">Model</dt>
            <dd>{humanize(selected.model_id) || "unset"}</dd>
          </div>
          <div>
            <dt className="text-[var(--muted)]">Provider</dt>
            <dd>{humanize(selected.provider_id)}</dd>
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
