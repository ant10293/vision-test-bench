import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { AssetDropZone } from "../components/AssetDropZone";
import { api, formatTime, type ProjectSummary } from "../lib/api";
import type { VideoInfo } from "../lib/types";

function ProjectCard({ project, videos, onOpen }: { project: ProjectSummary; videos: VideoInfo[]; onOpen: () => void }) {
  const frames = videos.filter((video) => video.thumbnail_url);
  return (
    <button type="button" className="panel overflow-hidden text-left" onClick={onOpen}>
      {frames.length > 1 ? (
        <div className="grid grid-cols-2">
          {frames.map((video) => (
            <img key={video.id} src={video.thumbnail_url} alt="" className="aspect-video w-full bg-black object-contain" />
          ))}
        </div>
      ) : frames.length === 1 ? (
        <img src={frames[0].thumbnail_url} alt="" className="aspect-video w-full bg-black object-contain" />
      ) : (
        <div className="flex aspect-video items-center justify-center bg-black text-xs text-[var(--muted)]">No videos</div>
      )}
      <div className="space-y-1 p-3 text-sm">
        <div className="truncate">{project.name}</div>
        <div className="mono text-xs text-[var(--muted)]">
          {videos.length} {videos.length === 1 ? "video" : "videos"}
          {projectWhen(project) ? ` · ${projectWhen(project)}` : ""}
        </div>
      </div>
    </button>
  );
}

function projectWhen(project: ProjectSummary): string {
  const raw = project.created_at ?? "";
  const stamped = raw && !/(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? `${raw}Z` : raw;
  const date = new Date(stamped);
  if (!raw || Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function VideosPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const requestedProject = params.get("project") ?? "";
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: api.tasks });
  const [mode, setMode] = useState<"library" | "create" | "edit">(requestedProject ? "edit" : "library");
  const [editId, setEditId] = useState(requestedProject);
  const [name, setName] = useState("");
  const [staged, setStaged] = useState<File[]>([]);
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
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      if (!editId) throw new Error("Open a project before adding videos.");
      for (const file of files) await api.upload(editId, file);
    },
    onSuccess: async () => {
      setError("");
      await client.invalidateQueries({ queryKey: ["videos"] });
    },
    onError: (err: Error) => setError(err.message),
  });
  const save = useMutation({
    mutationFn: async () => {
      const trimmed = name.trim();
      if (!trimmed || !staged.length) throw new Error("Name the project and add at least one video.");
      const project = await api.createProject(trimmed, "");
      let stored = 0;
      try {
        for (const file of staged) {
          await api.upload(project.id, file);
          stored += 1;
        }
      } catch (err) {
        if (!stored) await api.deleteProject(project.id).catch(() => undefined);
        throw err;
      }
      return project;
    },
    onSuccess: async (project) => {
      setMode("edit");
      setEditId(project.id);
      setName("");
      setStaged([]);
      setError("");
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["videos"] });
    },
    onError: (err: Error) => setError(err.message),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteProject(editId),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["projects"] });
      await client.invalidateQueries({ queryKey: ["videos"] });
      backToLibrary();
    },
    onError: (err: Error) => setError(err.message),
  });
  const canSave = Boolean(name.trim()) && staged.length > 0 && !save.isPending;
  const shown = mode === "edit" ? projectVideos.data : libraryVideos.data?.filter((video) => !taskIds.has(video.project_id));

  function openCreate() {
    setMode("create");
    setEditId("");
    setName("");
    setStaged([]);
    setError("");
  }

  function openEdit(id: string) {
    setMode("edit");
    setEditId(id);
    setStaged([]);
    setError("");
  }

  function backToLibrary() {
    setMode("library");
    setEditId("");
    setName("");
    setStaged([]);
    setError("");
    if (params.get("project")) setParams({}, { replace: true });
  }

  const requested = (projects.data ?? []).find((project) => project.id === requestedProject);
  if (requested?.is_task) return <Navigate to={`/tasks/${requested.id}`} replace />;

  return (
    <div className="space-y-4">
      {mode !== "edit" && (
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-medium">Video library</h1>
            <p className="text-sm text-[var(--muted)]">Originals are stored unchanged. Playback uses a proxy only when the browser cannot decode the file.</p>
          </div>
          {mode === "library" && <button className="btn primary" onClick={openCreate}>New project</button>}
        </header>
      )}

      {mode === "library" && (
        studies.length ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
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

      {mode === "create" && (
        <section className="panel space-y-4 p-4">
          <div>
            <h2 className="text-lg">New project</h2>
            <p className="text-sm text-[var(--muted)]">Add a name and at least one video. Nothing is saved until both are present.</p>
          </div>
          <label className="field-label">
            <span>Project name</span>
            <input className="field" value={name} onChange={(event) => setName(event.target.value)} placeholder="Project name" aria-label="Project name" />
          </label>
          <AssetDropZone
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
            multiple
            defer
            prompt="Drag and drop videos here or"
            chooseLabel="Choose files"
            supported="Supported: MP4, MOV, WebM"
            onFiles={() => undefined}
            onStagedChange={setStaged}
          />
          {error && <p className="text-sm text-[var(--rose)]">{error}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn primary" disabled={!canSave} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : "Save project"}
            </button>
            <button className="btn" onClick={backToLibrary} disabled={save.isPending}>Cancel</button>
          </div>
        </section>
      )}

      {mode === "edit" && editing && (
        <section className="space-y-3">
          <div>
            <button className="text-sm text-[var(--muted)]" onClick={backToLibrary}>← Back to library</button>
            <h2 className="mt-2 text-lg">{editing.name}</h2>
            <p className="text-sm text-[var(--muted)]">Add videos to this project. They are stored as soon as you drop them.</p>
          </div>
          <AssetDropZone
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
            multiple
            prompt="Drag and drop videos here or"
            chooseLabel="Choose files"
            supported="Supported: MP4, MOV, WebM"
            pending={upload.isPending}
            pendingLabel="Reading and probing…"
            error={error}
            onFiles={(files) => {
              if (files.length) upload.mutate(files);
            }}
          />
        </section>
      )}

      {mode === "edit" && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {shown?.map((video) => (
            <Link key={video.id} to={`/videos/${video.id}`} className="panel overflow-hidden">
              {video.thumbnail_url && <img src={video.thumbnail_url} alt="" className="aspect-video w-full bg-black object-contain" />}
              <div className="space-y-1 p-3 text-sm">
                <div className="truncate">{video.filename}</div>
                <div className="mono text-xs text-[var(--muted)]">
                  {formatTime(video.duration_sec)} · {video.width}×{video.height} · {video.fps.toFixed(2)} fps
                  {video.variable_frame_rate ? " · VFR" : " · CFR"} · {video.codec}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
      {libraryVideos.isError && <p className="text-sm text-[var(--rose)]">{(libraryVideos.error as Error).message}</p>}
      {mode === "edit" && !projectVideos.isLoading && !projectVideos.data?.length && (
        <p className="text-sm text-[var(--muted)]" aria-live="polite">This project has no videos yet. Drop one above.</p>
      )}
      {mode === "edit" && editing && (
        <div className="border-t border-[var(--line)] pt-4">
          {error && <p className="mb-3 text-sm text-[var(--rose)]">{error}</p>}
          <button
            className="btn danger"
            disabled={remove.isPending || upload.isPending}
            onClick={() => {
              const label = editing.name || "this project";
              if (window.confirm(`Delete “${label}”? Its videos and files will be removed.`)) remove.mutate();
            }}
          >
            {remove.isPending ? "Deleting…" : "Delete project"}
          </button>
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
