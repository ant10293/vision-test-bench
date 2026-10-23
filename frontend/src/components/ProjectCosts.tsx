import { useQuery } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { Bone } from "./Skeleton";
import { api, formatUsd, formatWhen } from "../lib/api";
import { humanize } from "../lib/humanize";

export function ProjectCosts({ projectId, className = "" }: { projectId: string; className?: string }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const costs = useQuery({
    queryKey: ["project-costs", projectId],
    queryFn: () => api.projectCosts(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 4000,
  });
  if (!projectId) return null;
  const data = costs.data;

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={`cost-total${className ? ` ${className}` : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span className="cost-total-label">Provider API cost</span>
        <span className="mono text-xl">{data ? formatUsd(data.cost_usd) : costs.isError ? "—" : <Bone width="7ch" />}</span>
      </button>
      {open && (
        <CostBreakdown
          anchor={trigger}
          amount={data ? formatUsd(data.cost_usd) : undefined}
          error={costs.isError ? (costs.error as Error).message : ""}
          loading={costs.isLoading}
          data={data}
          onClose={close}
        />
      )}
    </>
  );
}

function CostBreakdown({
  anchor,
  amount,
  error,
  loading,
  data,
  onClose,
}: {
  anchor: RefObject<HTMLButtonElement | null>;
  amount?: string;
  error: string;
  loading: boolean;
  data: Awaited<ReturnType<typeof api.projectCosts>> | undefined;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useLayoutEffect(() => {
    function place() {
      const trigger = anchor.current;
      const panel = panelRef.current;
      if (!trigger || !panel) return;
      const rect = trigger.getBoundingClientRect();
      const margin = 12;
      const gap = 8;
      const width = Math.min(576, window.innerWidth - margin * 2);
      const alignEnd = rect.left + rect.width / 2 > window.innerWidth / 2;
      let left = alignEnd ? rect.right - width : rect.left;
      left = Math.max(margin, Math.min(left, window.innerWidth - margin - width));
      const top = rect.bottom + gap;
      panel.style.top = `${top}px`;
      panel.style.left = `${left}px`;
      panel.style.width = `${width}px`;
      panel.style.maxHeight = `${Math.max(160, window.innerHeight - top - margin)}px`;
    }
    place();
    panelRef.current?.focus();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = [...panelRef.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function onBackdrop(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) onClose();
  }

  return createPortal(
    <div className="cost-overlay" onClick={onBackdrop}>
      <div
        ref={panelRef}
        className="panel cost-overlay-panel outline-none"
        role="dialog"
        aria-modal="true"
        aria-label="Provider API cost"
        tabIndex={-1}
      >
        <div className="border-b border-[var(--line)] p-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium">Provider API cost</h2>
            {amount && <div className="mono text-2xl">{amount}</div>}
          </div>
          <p className="mt-1 text-xs text-[var(--muted)]">
            Each model call is priced from the catalog list rate. This is an estimate, not an invoice.
          </p>
        </div>
        <div className="space-y-3 p-4">
          {error && <p className="text-sm text-[var(--rose)]">{error}</p>}
          {loading && <p className="text-sm text-[var(--muted)]">Loading provider calls…</p>}
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
                <ul className="space-y-2 text-sm">
                  {data.calls.map((call) => {
                    const when = formatWhen(call.created_at);
                    const money = call.status === "completed" ? formatUsd(call.cost_usd, call.price_known) : "—";
                    return (
                      <li key={call.id}>
                        <Link to={`/runs/${call.run_id}/logs`} className="block rounded-md border border-transparent p-2 hover:border-[var(--line)]">
                          <span className="flex items-baseline justify-between gap-3">
                            <span className="min-w-0 truncate">{call.video_filename || call.run_name}</span>
                            <span className="mono shrink-0 text-xs">{money}</span>
                          </span>
                          <span className="mt-0.5 block text-xs text-[var(--muted)]">
                            {humanize(call.provider_id)}/{humanize(call.model_id)} · {humanize(call.stage)} · {humanize(call.status)}
                            {when ? ` · ${when}` : ""}
                          </span>
                          <span className="mono text-xs text-[var(--muted)]">
                            {call.input_tokens.toLocaleString()} in · {call.output_tokens.toLocaleString()} out
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
        </div>
      </div>
    </div>,
    document.body,
  );
}
