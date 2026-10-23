import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { VideoInfo } from "../lib/types";

export function LibraryPicker({
  videos,
  loading = false,
  error = "",
  initialIds = [],
  onClose,
  onOpen,
}: {
  videos: VideoInfo[];
  loading?: boolean;
  error?: string;
  initialIds?: string[];
  onClose: () => void;
  onOpen: (videos: VideoInfo[]) => void;
}) {
  const [selected, setSelected] = useState(() => new Set(initialIds));

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const chosen = videos.filter((video) => selected.has(video.id));

  return createPortal(
    <div className="library-picker-backdrop" onMouseDown={onClose}>
      <div
        className="library-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Video library"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="library-picker-grid">
          {loading && <p className="library-picker-empty">Loading videos…</p>}
          {!loading && error && <p className="library-picker-empty">{error}</p>}
          {!loading && !error && !videos.length && <p className="library-picker-empty">No videos in the library.</p>}
          {!loading && !error && videos.map((video) => {
            const on = selected.has(video.id);
            return (
              <button
                key={video.id}
                type="button"
                className={`library-picker-cell${on ? " is-selected" : ""}`}
                aria-pressed={on}
                aria-label={video.filename}
                onClick={() => toggle(video.id)}
              >
                <span className="library-picker-thumb">
                  {video.thumbnail_url ? <img src={video.thumbnail_url} alt="" /> : null}
                </span>
                <span className="library-picker-name" title={video.filename}>{video.filename}</span>
              </button>
            );
          })}
        </div>
        <div className="library-picker-foot">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={!chosen.length} onClick={() => onOpen(chosen)}>
            Open
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
