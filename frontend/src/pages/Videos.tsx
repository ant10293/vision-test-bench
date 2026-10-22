import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AssetDropZone } from "../components/AssetDropZone";
import { api, formatTime } from "../lib/api";

export function VideosPage() {
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const videos = useQuery({ queryKey: ["videos"], queryFn: () => api.videos() });
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: () => api.createProject("Study", "Local research project"),
    onSuccess: async (project) => {
      setProjectId(project.id);
      await client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.upload(projectId || projects.data?.[0]?.id || "", file),
    onSuccess: async () => {
      setError("");
      await client.invalidateQueries({ queryKey: ["videos"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-medium">Video library</h1>
          <p className="text-sm text-[var(--muted)]">Originals are stored unchanged. Playback uses a proxy only when the browser cannot decode the file.</p>
        </div>
        <button className="btn" onClick={() => create.mutate()}>New project</button>
      </header>
      <AssetDropZone
        accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
        prompt="Drag and drop a video here or"
        chooseLabel="Choose file"
        supported="Supported: MP4, MOV, WebM"
        pending={upload.isPending}
        pendingLabel="Reading and probing…"
        error={error}
        onFiles={(files) => {
          const file = files[0];
          if (file) upload.mutate(file);
        }}
      />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {videos.data?.map((video) => (
          <Link key={video.id} to={`/videos/${video.id}`} className="panel overflow-hidden">
            {video.thumbnail_url && <img src={video.thumbnail_url} alt="" className="h-36 w-full object-cover" />}
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
  if (!video.data) return <p className="text-[var(--muted)]">Loading video…</p>;
  const item = video.data;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium">{item.filename}</h1>
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
            <img src={frame.url} alt="" className="h-28 w-full object-cover" />
            <figcaption className="mono px-2 py-1 text-xs text-[var(--muted)]">{frame.timestamp.toFixed(2)}s · {frame.reason}</figcaption>
          </figure>
        ))}
      </div>
      <Link className="btn primary inline-block" to={`/experiments/new?video=${item.id}`}>Analyze this video</Link>
    </div>
  );
}
