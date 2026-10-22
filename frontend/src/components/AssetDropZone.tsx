import { useEffect, useRef, useState, type ReactNode } from "react";

type Item = { name: string; url: string; file: File };

type Props = {
  accept: string;
  multiple?: boolean;
  prompt: string;
  chooseLabel: string;
  supported: string;
  hint?: string;
  pending?: boolean;
  pendingLabel?: string;
  error?: string;
  onFiles: (files: File[]) => void;
  defer?: boolean;
  onStagedChange?: (files: File[]) => void;
  settings?: ReactNode;
};

export function AssetDropZone({
  accept,
  multiple = false,
  prompt,
  chooseLabel,
  supported,
  hint,
  pending = false,
  pendingLabel,
  error,
  onFiles,
  defer = false,
  onStagedChange,
  settings,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const wasPending = useRef(false);
  const itemsRef = useRef<Item[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  itemsRef.current = items;

  useEffect(() => {
    if (wasPending.current && !pending && !error) {
      setItems((current) => {
        for (const item of current) URL.revokeObjectURL(item.url);
        return [];
      });
    }
    wasPending.current = pending;
  }, [pending, error]);

  useEffect(() => {
    return () => {
      for (const item of itemsRef.current) URL.revokeObjectURL(item.url);
    };
  }, []);

  function publish(next: Item[]) {
    onStagedChange?.(next.map((item) => item.file));
  }

  function isFileDrag(event: { dataTransfer: DataTransfer | null }) {
    return Array.from(event.dataTransfer?.types ?? []).includes("Files");
  }

  function take(list: FileList | null) {
    const files = [...(list ?? [])];
    const chosen = multiple ? files : files.slice(0, 1);
    if (!chosen.length) return;
    const added = chosen.map((file) => ({ name: file.name, url: URL.createObjectURL(file), file }));
    setItems((current) => {
      const next = defer && multiple ? [...current, ...added] : added;
      if (!(defer && multiple)) {
        for (const item of current) URL.revokeObjectURL(item.url);
      }
      publish(next);
      return next;
    });
    if (!defer) onFiles(chosen);
  }

  function remove(url: string) {
    setItems((current) => {
      const next = current.filter((item) => item.url !== url);
      const gone = current.find((item) => item.url === url);
      if (gone) URL.revokeObjectURL(gone.url);
      publish(next);
      return next;
    });
  }

  return (
    <div>
      <div
        className={`drop-zone${dragOver ? " is-dragover" : ""}`}
        tabIndex={0}
        onDragEnter={(event) => {
          if (!isFileDrag(event)) return;
          event.preventDefault();
          setDragOver(true);
        }}
        onDragOver={(event) => {
          if (!isFileDrag(event)) return;
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOver(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          if (!isFileDrag(event)) return;
          take(event.dataTransfer.files);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            input.current?.click();
          }
        }}
      >
        <div className="drop-zone-icon" aria-hidden="true" />
        <p className="drop-zone-copy">
          {prompt}{" "}
          <button type="button" className="linkish" onClick={() => input.current?.click()}>
            {chooseLabel}
          </button>
        </p>
        <input
          ref={input}
          type="file"
          hidden
          multiple={multiple}
          accept={accept}
          onChange={(event) => {
            take(event.target.files);
            event.currentTarget.value = "";
          }}
        />
      </div>
      <div className="drop-zone-foot">
        <span>{supported}</span>
        {hint ? <span>{hint}</span> : <span />}
      </div>
      {items.map((item) => (
        <div key={item.url} className="drop-item">
          <div className="drop-item-main">
            <video className="drop-item-thumb" src={item.url} muted playsInline preload="metadata" />
            <div className="drop-item-meta">
              <strong>{item.name}</strong>
              <span>{pending ? pendingLabel : error || "Ready"}</span>
            </div>
            {defer && (
              <button type="button" className="btn" onClick={() => remove(item.url)}>Remove</button>
            )}
          </div>
        </div>
      ))}
      {!items.length && pending && pendingLabel && <p className="drop-zone-status">{pendingLabel}</p>}
      {!items.length && error && <p className="drop-zone-status is-error">{error}</p>}
      {settings && <div className="drop-zone-settings">{settings}</div>}
    </div>
  );
}
