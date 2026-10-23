import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type VideoStorage } from "../lib/api";
import { SettingsFieldset } from "./SettingsFieldset";

export function StorageSettings() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const save = useMutation({
    mutationFn: (videoStorage: VideoStorage) => api.saveSettings({ video_storage: videoStorage }),
    onSuccess: (data) => client.setQueryData(["settings"], data),
  });
  const mode = settings.data?.video_storage;
  const pending = save.isPending || settings.isLoading;

  return (
    <SettingsFieldset legend="Video storage" disabled={pending}>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="radio"
          name="video-storage"
          className="mt-1"
          checked={mode === "copy"}
          onChange={() => save.mutate("copy")}
        />
        <span>
          <span className="block">Copy into the lab</span>
          <span className="block text-xs text-[var(--muted)]">The file is copied into the lab. The original can be moved or deleted.</span>
        </span>
      </label>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="radio"
          name="video-storage"
          className="mt-1"
          checked={mode === "reference"}
          onChange={() => save.mutate("reference")}
        />
        <span>
          <span className="block">Reference the original path</span>
          <span className="block text-xs text-[var(--muted)]">The lab reads the file where it is. Moving or deleting that file means it will not load.</span>
        </span>
      </label>
      {save.isError && <p className="text-sm text-[var(--rose)]">{(save.error as Error).message}</p>}
      <p className="text-xs text-[var(--muted)]">Choose how a new video is added. This applies to the next file you add.</p>
    </SettingsFieldset>
  );
}

export function VideoPathForm({
  onAdd,
  pending = false,
  error = "",
  hint = "One full path per line. The lab reads each file where it is and does not copy it.",
}: {
  onAdd: (paths: string[]) => void | Promise<void>;
  pending?: boolean;
  error?: string;
  hint?: string;
}) {
  const [value, setValue] = useState("");
  const [localError, setLocalError] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="panel space-y-2 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        const paths = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
        if (!paths.length) {
          setLocalError("Enter the full path to a video.");
          return;
        }
        setLocalError("");
        setBusy(true);
        void Promise.resolve(onAdd(paths))
          .then(() => setValue(""))
          .catch((err: unknown) => setLocalError(err instanceof Error ? err.message : "Could not add the videos"))
          .finally(() => setBusy(false));
      }}
    >
      <label className="block text-sm">
        Paths to the videos
        <textarea
          className="field mt-1 block min-h-24 w-full resize-y"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={"/Users/you/Videos/clip.mov"}
          aria-label="Video paths"
          spellCheck={false}
          disabled={pending || busy}
        />
      </label>
      <p className="text-xs text-[var(--muted)]">{hint}</p>
      {(localError || error) && <p className="text-sm text-[var(--rose)]">{localError || error}</p>}
      <button className="btn primary" type="submit" disabled={pending || busy}>{pending || busy ? "Adding…" : "Add videos"}</button>
    </form>
  );
}
