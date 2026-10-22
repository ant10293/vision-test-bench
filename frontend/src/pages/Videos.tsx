import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { AssetDropZone } from "../components/AssetDropZone";
import { StorageSettings, VideoPathForm } from "../components/StorageSettings";
import { api, formatTime, formatWhen, type ProjectSummary } from "../lib/api";
import type { VideoInfo } from "../lib/types";
import { videoReady } from "../lib/uploadProgress";
import { useUploads } from "../stores/uploads";

function compareDisplayOrder(left: VideoInfo, right: VideoInfo): number {
  const leftRank = left.order_index;
  const rightRank = right.order_index;
  if (leftRank == null && rightRank == null) return 0;
  if (leftRank == null) return 1;
  if (rightRank == null) return -1;
  return leftRank - rightRank;
}

function inDisplayOrder(videos: VideoInfo[]): VideoInfo[] {
  if (!videos.some((video) => video.order_index != null)) return videos;
  return [...videos].sort(compareDisplayOrder);
}

/** Columns for the project mosaic. One row for 1–3 thumbnails; two rows after that, so four are 2×2. */
function mosaicColumns(count: number): number {
  if (count <= 3) return Math.max(1, count);
  return Math.min(4, Math.ceil(count / 2));
}

function ProjectCard({ project, videos, onOpen }: { project: ProjectSummary; videos: VideoInfo[]; onOpen: () => void }) {
  const listed = videos.filter((video) => video.ingest_status !== "failed");
  const frames = listed.filter((video) => video.thumbnail_url && videoReady(video));
  const columns = mosaicColumns(frames.length);
  const preview = frames.slice(0, columns * 2);
  const processing = listed.some((video) => video.ingest_status === "processing");
  return (
    <button type="button" className="panel overflow-hidden text-left" onClick={onOpen}>
      {preview.length ? (
        <div className={preview.length === 1 ? "thumb-grid is-single" : "thumb-grid"} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {preview.map((video) => (
            <img key={video.id} src={video.thumbnail_url} alt="" />
          ))}
        </div>
      ) : (
        <div className="thumb-empty">
          {processing ? "Processing…" : "No videos"}
        </div>
      )}
      <div className="space-y-1 p-3 text-sm">
        <div className="truncate">{project.name}</div>
        <div className="mono text-xs text-[var(--muted)]">
          {listed.length} {listed.length === 1 ? "video" : "videos"}
          {formatWhen(project.created_at) ? ` · ${formatWhen(project.created_at)}` : ""}
        </div>
      </div>
    </button>
  );
}

type SortCell = { key: string; video: VideoInfo | null; order: number; sortIndex: number };

function cellsWithSlot(videos: VideoInfo[], dragId: string, insertAt: number): SortCell[] {
  const resting = videos.filter((video) => video.id !== dragId);
  const slot = Math.max(0, Math.min(insertAt, resting.length));
  const cells: SortCell[] = [];
  let visual = 0;
  for (let index = 0; index <= resting.length; index += 1) {
    if (index === slot) {
      cells.push({ key: "slot", video: null, order: visual + 1, sortIndex: visual });
      visual += 1;
    }
    if (index < resting.length) {
      cells.push({ key: resting[index].id, video: resting[index], order: visual + 1, sortIndex: visual });
      visual += 1;
    }
  }
  return cells;
}

function slotIndexAtPoint(grid: HTMLElement, x: number, y: number): number | null {
  const cells = [...grid.querySelectorAll<HTMLElement>("[data-sort-index]")];
  if (!cells.length) return null;
  let best = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const cell of cells) {
    const rect = cell.getBoundingClientRect();
    const dx = x - (rect.left + rect.width / 2);
    const dy = y - (rect.top + rect.height / 2);
    const distance = dx * dx + dy * dy;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = Number(cell.dataset.sortIndex);
    }
  }
  return best;
}

function VideoFace({ video, order }: { video: VideoInfo; order: number }) {
  return (
    <>
      {video.thumbnail_url && <img src={video.thumbnail_url} alt="" className="aspect-video w-full bg-black object-contain" />}
      <div className="flex items-end gap-3 p-3 text-sm">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="truncate">{video.filename}</div>
          <div className="mono truncate text-xs text-[var(--muted)]">
            {formatTime(video.duration_sec)} · {video.width}×{video.height} · {video.fps.toFixed(2)} fps
            {video.variable_frame_rate ? " · VFR" : " · CFR"} · {video.codec}
          </div>
        </div>
        <div className="mono shrink-0 text-xs text-[var(--muted)]" aria-label={`Order ${order}`}>{order}</div>
      </div>
    </>
  );
}

function VideoCard({
  video,
  order,
  sortIndex,
  deleting,
  onDelete,
  onGrab,
}: {
  video: VideoInfo;
  order: number;
  sortIndex: number;
  deleting: boolean;
  onDelete: () => void;
  onGrab: (event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  return (
    <div className="video-card panel overflow-hidden" data-sort-index={sortIndex} onPointerDown={onGrab}>
      <button
        type="button"
        className="video-card-delete"
        aria-label={`Delete ${video.filename}`}
        title="Delete video"
        disabled={deleting}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onDelete();
        }}
      >
        <Trash2 size={15} aria-hidden="true" />
      </button>
      <Link to={`/videos/${video.id}`} className="block" draggable={false}>
        <VideoFace video={video} order={order} />
      </Link>
    </div>
  );
}

type DragState = {
  id: string;
  index: number;
  origin: number;
  x: number;
  y: number;
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
  active: boolean;
};

export function VideosPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const requestedProject = params.get("project") ?? "";
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const labSettings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: api.tasks });
  const view = useUploads((state) => state.view);
  const setView = useUploads((state) => state.setView);
  const items = useUploads((state) => state.items);
  const stageFiles = useUploads((state) => state.stageFiles);
  const stagePaths = useUploads((state) => state.stagePaths);
  const enqueue = useUploads((state) => state.enqueue);
  const enqueuePaths = useUploads((state) => state.enqueuePaths);
  const removeItem = useUploads((state) => state.removeItem);
  const beginCreate = useUploads((state) => state.beginCreate);
  const clearDraft = useUploads((state) => state.clearDraft);
  const forgetProject = useUploads((state) => state.forgetProject);
  const adoptServer = useUploads((state) => state.adoptServer);
  const mode = view.mode;
  const editId = view.editId;
  const name = view.name;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const taskIds = new Set((tasks.data ?? []).map((task) => task.id));
  const studies = (projects.data ?? []).filter((project) => !project.is_task && !taskIds.has(project.id));
  const editing = studies.find((project) => project.id === editId);
  const libraryVideos = useQuery({ queryKey: ["videos", "all"], queryFn: () => api.videos() });
  const projectVideos = useQuery({
    queryKey: ["videos", editId],
    queryFn: () => api.videos(editId),
    enabled: mode === "edit" && Boolean(editId),
  });
  const videosByProject = new Map<string, VideoInfo[]>();
  for (const video of libraryVideos.data ?? []) {
    const group = videosByProject.get(video.project_id) ?? [];
    group.push(video);
    videosByProject.set(video.project_id, group);
  }
  for (const [projectId, group] of videosByProject) {
    videosByProject.set(projectId, inDisplayOrder(group));
  }
  const remove = useMutation({
    mutationFn: () => {
      forgetProject(editId);
      return api.deleteProject(editId);
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["videos"] });
      backToLibrary();
    },
    onError: (err: Error) => setError(err.message),
  });
  const removeVideo = useMutation({
    mutationFn: (id: string) => api.deleteVideo(id),
    onMutate: async (id) => {
      setError("");
      await client.cancelQueries({ queryKey: ["videos"] });
      const snapshots = client.getQueriesData<VideoInfo[]>({ queryKey: ["videos"] });
      for (const [key, data] of snapshots) {
        if (Array.isArray(data)) client.setQueryData(key, data.filter((video) => video.id !== id));
      }
      return { snapshots };
    },
    onError: (err: Error, _id, context) => {
      for (const [key, data] of context?.snapshots ?? []) client.setQueryData(key, data);
      setError(err.message);
    },
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: ["videos"] });
    },
  });
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.orderVideos(editId, ids),
    onMutate: async (ids) => {
      setError("");
      await client.cancelQueries({ queryKey: ["videos", editId] });
      const previous = client.getQueryData<VideoInfo[]>(["videos", editId]);
      const library = client.getQueryData<VideoInfo[]>(["videos", "all"]);
      if (previous) {
        const byId = new Map(previous.map((video) => [video.id, video]));
        const ordered = ids.flatMap((id, index) => {
          const video = byId.get(id);
          return video ? [{ ...video, order_index: index }] : [];
        });
        const rest = previous.filter((video) => !ids.includes(video.id));
        client.setQueryData(["videos", editId], [...ordered, ...rest]);
      }
      if (library) {
        const rank = new Map(ids.map((id, index) => [id, index]));
        client.setQueryData(
          ["videos", "all"],
          library.map((video) => (rank.has(video.id) ? { ...video, order_index: rank.get(video.id) } : video)),
        );
      }
      return { previous, library };
    },
    onError: (err: Error, _ids, context) => {
      if (context?.previous) client.setQueryData(["videos", editId], context.previous);
      if (context?.library) client.setQueryData(["videos", "all"], context.library);
      setError(err.message);
    },
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: ["videos"] });
    },
  });
  const draftItems = items.filter((item) => item.projectId === "");
  const canSave = Boolean(name.trim()) && draftItems.some((item) => item.phase === "staged") && !saving;
  const creating = mode === "create";
  const [drag, setDrag] = useState<DragState | null>(null);
  const dragLive = useRef<DragState | null>(null);
  const suppressClick = useRef(false);
  const orderRef = useRef<string[]>([]);
  const gridRef = useRef<HTMLDivElement>(null);
  const stopGesture = useRef<(() => void) | null>(null);
  const shown = mode === "edit" ? inDisplayOrder(projectVideos.data ?? []) : libraryVideos.data?.filter((video) => !taskIds.has(video.project_id));
  useEffect(() => {
    if (reorder.isPending) return;
    orderRef.current = inDisplayOrder(projectVideos.data ?? []).filter(videoReady).map((video) => video.id);
  }, [projectVideos.data, reorder.isPending]);

  useEffect(() => () => stopGesture.current?.(), []);

  useEffect(() => {
    if (requestedProject) setView({ mode: "edit", editId: requestedProject });
  }, [requestedProject, setView]);

  useEffect(() => {
    if (mode === "edit" && editId && projectVideos.data) adoptServer(editId, projectVideos.data);
  }, [mode, editId, projectVideos.data, adoptServer]);

  function commit(index: number, sourceId: string) {
    const current = orderRef.current.length
      ? orderRef.current
      : inDisplayOrder(projectVideos.data ?? []).filter(videoReady).map((video) => video.id);
    if (!sourceId || reorder.isPending) return;
    const next = current.filter((id) => id !== sourceId);
    next.splice(Math.max(0, Math.min(index, next.length)), 0, sourceId);
    if (next.length === current.length && next.every((id, position) => id === current[position])) return;
    orderRef.current = next;
    reorder.mutate(next);
  }

  function grabCard(video: VideoInfo, index: number, event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || reorder.isPending) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const live: DragState = {
      id: video.id,
      index,
      origin: index,
      x: event.clientX,
      y: event.clientY,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      width: rect.width,
      height: rect.height,
      active: false,
    };
    dragLive.current = live;
    const move = (pointer: PointerEvent) => {
      const current = dragLive.current;
      if (!current) return;
      const distance = Math.hypot(pointer.clientX - current.x, pointer.clientY - current.y);
      if (!current.active && distance < 6) return;
      current.x = pointer.clientX;
      current.y = pointer.clientY;
      if (!current.active) {
        current.active = true;
        current.index = current.origin;
        document.body.classList.add("is-sorting");
        setDrag({ ...current });
        return;
      }
      const grid = gridRef.current;
      if (grid) {
        const next = slotIndexAtPoint(grid, pointer.clientX, pointer.clientY);
        if (next !== null) current.index = next;
      }
      setDrag({ ...current });
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      stopGesture.current = null;
      const finished = dragLive.current;
      dragLive.current = null;
      setDrag(null);
      document.body.classList.remove("is-sorting");
      if (finished?.active) {
        suppressClick.current = true;
        commit(finished.index, finished.id);
      }
    };
    stopGesture.current = end;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }

  function openCreate() {
    clearDraft();
    setView({ mode: "create", editId: "", name: "" });
    setError("");
    if (params.get("project")) setParams({}, { replace: true });
  }

  function openEdit(id: string) {
    setView({ mode: "edit", editId: id });
    setError("");
  }

  function backToLibrary() {
    setView({ mode: "library", editId: "", name: "" });
    setError("");
    if (params.get("project")) setParams({}, { replace: true });
  }

  function requestBackToLibrary() {
    if (creating && draftItems.length && !window.confirm("Go back without saving? This project will be lost if you leave before saving.")) return;
    if (creating) clearDraft();
    backToLibrary();
  }

  async function saveProject() {
    setSaving(true);
    setError("");
    try {
      const projectId = await beginCreate(name.trim());
      setView({ mode: "edit", editId: projectId, name: "" });
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["videos"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the project");
    } finally {
      setSaving(false);
    }
  }

  const requested = (projects.data ?? []).find((project) => project.id === requestedProject);
  if (requested?.is_task) return <Navigate to={`/tasks/${requested.id}`} replace />;
  const editingVideos = (shown ?? []).filter(videoReady);
  const assetRows = items.filter((item) => (creating ? item.projectId === "" : item.projectId === editId));
  const reference = labSettings.data?.video_storage === "reference";
  const sorting = drag?.active ? cellsWithSlot(editingVideos, drag.id, drag.index) : null;
  const draggedVideo = drag ? editingVideos.find((video) => video.id === drag.id) : undefined;

  return (
    <div className="space-y-4">
      {mode === "library" && (
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-medium">Video library</h1>
            <p className="text-sm text-[var(--muted)]">New videos can be copied into the lab or read from their current path. Playback uses a proxy only when the browser cannot decode the file.</p>
          </div>
          <button className="btn primary" onClick={openCreate}>New project</button>
        </header>
      )}

      {mode === "library" && <StorageSettings />}

      {mode === "library" && (
        studies.length ? (
          <div className="project-grid">
            {studies.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                videos={videosByProject.get(project.id) ?? []}
                onOpen={() => openEdit(project.id)}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--muted)]">No projects yet. Create one and add a video before it is saved.</p>
        )
      )}

      {(creating || (mode === "edit" && editing)) && (
        <section className="space-y-3">
          <div>
            <button className="text-sm text-[var(--muted)] disabled:opacity-45" onClick={requestBackToLibrary} disabled={saving}>← Back to library</button>
            {creating ? (
              <input
                className="field mt-2 mb-2 block"
                value={name}
                onChange={(event) => setView({ name: event.target.value })}
                placeholder="Project name"
                aria-label="Project name"
                autoFocus
              />
            ) : (
              <h2 className="mt-2 text-lg">{editing?.name}</h2>
            )}
            <p className="text-sm text-[var(--muted)]">
              {creating
                ? "Add a name and at least one video. After you save, upload and analysis keep going if you leave this page."
                : reference
                  ? "Add a path or drop a file, depending on the storage setting. Analysis keeps running if you switch pages. Drag a card to reorder it."
                  : "Drop videos to add them. Upload and analysis keep running if you switch pages. Drag a card to reorder it."}
            </p>
          </div>
          <StorageSettings />
          {reference ? (
            <VideoPathForm
              onAdd={(paths) => {
                if (creating) stagePaths(paths);
                else if (editId) enqueuePaths(editId, paths);
              }}
            />
          ) : null}
          {(!reference || assetRows.length > 0) && <AssetDropZone
            chooser={!reference}
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
            multiple
            prompt="Drag and drop videos here or"
            chooseLabel="Choose files"
            supported="Supported: MP4, MOV, WebM"
            rows={assetRows.map((item) => ({
              id: item.key,
              name: item.name,
              url: item.previewUrl,
              subtitle: item.subtitle,
              progress: item.progress,
              failed: item.phase === "error",
            }))}
            onRemove={removeItem}
            onFiles={(files) => {
              if (!files.length) return;
              if (creating) stageFiles(files);
              else if (editId) enqueue(editId, files);
            }}
          />}
        </section>
      )}

      {mode === "edit" && (
        <div
          ref={gridRef}
          className="video-grid"
          onClickCapture={(event) => {
            if (!suppressClick.current) return;
            event.preventDefault();
            event.stopPropagation();
            suppressClick.current = false;
          }}
        >
          {(sorting ?? editingVideos.map((video, index) => ({ key: video.id, video, order: index + 1, sortIndex: index }))).map((cell) =>
            cell.video ? (
              <VideoCard
                key={cell.key}
                video={cell.video}
                order={cell.order}
                sortIndex={cell.sortIndex}
                deleting={removeVideo.isPending && removeVideo.variables === cell.video.id}
                onGrab={(event) => grabCard(cell.video as VideoInfo, cell.sortIndex, event)}
                onDelete={() => {
                  const filename = cell.video?.filename ?? "this video";
                  const id = cell.video?.id ?? "";
                  if (window.confirm(`Delete “${filename}”? It will be removed from this project. A referenced original stays on disk.`)) removeVideo.mutate(id);
                }}
              />
            ) : (
              <div
                key={cell.key}
                className="video-card-slot"
                data-sort-index={cell.sortIndex}
                style={{ height: drag?.height }}
                aria-label={`Drop position ${cell.order}`}
              />
            ),
          )}
        </div>
      )}
      {drag?.active && draggedVideo && createPortal(
        <div
          className="video-card-ghost panel"
          style={{ left: drag.x - drag.offsetX, top: drag.y - drag.offsetY, width: drag.width, height: drag.height }}
        >
          <VideoFace video={draggedVideo} order={drag.index + 1} />
        </div>,
        document.body,
      )}
      {libraryVideos.isError && <p className="text-sm text-[var(--rose)]">{(libraryVideos.error as Error).message}</p>}
      {mode === "edit" && !projectVideos.isLoading && !editingVideos.length && !assetRows.length && (
        <p className="text-sm text-[var(--muted)]" aria-live="polite">
          {reference ? "This project has no videos yet. Add a path above." : "This project has no videos yet. Drop one above."}
        </p>
      )}
      {(creating || (mode === "edit" && editing)) && (
        <div className="border-t border-[var(--line)] pt-4">
          {error && <p className="mb-3 text-sm text-[var(--rose)]">{error}</p>}
          {creating ? (
            <button className="btn primary" disabled={!canSave} onClick={() => void saveProject()}>
              {saving ? "Saving…" : "Save project"}
            </button>
          ) : (
            <button
              className="btn danger"
              disabled={remove.isPending}
              onClick={() => {
                const label = editing?.name || "this project";
                if (window.confirm(`Delete “${label}”? Copied videos and previews will be removed. Referenced files stay on disk.`)) remove.mutate();
              }}
            >
              {remove.isPending ? "Deleting…" : "Delete project"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function VideoDetailPage() {
  const { videoId = "" } = useParams();
  const [params] = useSearchParams();
  const player = useRef<HTMLVideoElement>(null);
  const video = useQuery({ queryKey: ["video", videoId], queryFn: () => api.video(videoId), enabled: Boolean(videoId) });
  useEffect(() => {
    const node = player.current;
    const time = Number(params.get("t"));
    if (!node || !Number.isFinite(time)) return;
    const seek = () => {
      node.currentTime = time;
    };
    node.addEventListener("loadedmetadata", seek);
    if (node.readyState >= 1) seek();
    return () => node.removeEventListener("loadedmetadata", seek);
  }, [params, video.data?.id]);
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  if (!video.data) return <p className="text-[var(--muted)]">Loading video…</p>;
  const item = video.data;
  const project = projects.data?.find((row) => row.id === item.project_id);
  const backHref = project?.is_task ? `/tasks/${project.id}` : item.project_id ? `/videos?project=${item.project_id}` : "/videos";
  const backLabel = project?.is_task ? "← Back to task" : item.project_id ? "← Back to project" : "← Back to library";
  return (
    <div className="space-y-4">
      <div>
        <Link className="text-sm text-[var(--muted)]" to={backHref}>{backLabel}</Link>
        <h1 className="mt-2 text-2xl font-medium">{item.filename}</h1>
      </div>
      <p className="text-xs text-[var(--amber)]">Clip time starts at the beginning of this file. It is not the project capture clock.</p>
      <video ref={player} src={item.playback_url} controls className="max-h-[480px] w-full rounded-lg bg-black" />
      <div className="panel grid gap-2 p-3 text-sm md:grid-cols-3">
        <div>Checksum <span className="mono text-xs">{item.checksum}</span></div>
        <div>Duration {formatTime(item.duration_sec)}</div>
        <div>{item.width}×{item.height} · {item.codec} · {item.container}</div>
        <div>Capture {item.capture_time_utc || item.capture_time_raw || "not in the file"} ({item.timestamp_reliability || "absent"})</div>
        <div>File modified {item.file_mtime || "unknown"}</div>
        <div>Source {item.timestamp_source || "absent"}</div>
      </div>
      {item.contact_sheet_url && <img src={item.contact_sheet_url} alt="Contact sheet" className="panel w-full" />}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {item.frames?.map((frame) => (
          <figure key={frame.id} className="panel overflow-hidden">
            <img src={frame.url} alt="" className="aspect-video w-full bg-black object-contain" />
            <figcaption className="mono px-2 py-1 text-xs text-[var(--muted)]">{frame.timestamp.toFixed(2)}s · {frame.reason}</figcaption>
          </figure>
        ))}
      </div>
      <Link className="btn primary inline-block" to={`/experiments/new?video=${item.id}`}>Analyze this video</Link>
    </div>
  );
}
