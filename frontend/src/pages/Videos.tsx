import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AssetDropZone } from "../components/AssetDropZone";
import { Bone, MediaPlaceholder } from "../components/Skeleton";
import { StorageSettings, VideoPathForm } from "../components/StorageSettings";
import { api, formatTime } from "../lib/api";
import type { VideoInfo } from "../lib/types";
import { LIBRARY_QUEUE, useUploads } from "../stores/uploads";

const PLACEHOLDERS = 3;

function LibraryFace({ video }: { video?: VideoInfo }) {
  if (!video) {
    return (
      <>
        <MediaPlaceholder />
        <div className="space-y-1 p-3" aria-hidden="true">
          <Bone width="72%" />
          <Bone width="92%" />
        </div>
      </>
    );
  }
  const waiting = !video.thumbnail_url && video.ingest_status !== "failed";
  return (
    <>
      {waiting ? <MediaPlaceholder /> : video.thumbnail_url && <img src={video.thumbnail_url} alt="" className="aspect-video w-full bg-black object-contain" />}
      <div className="space-y-1 p-3 text-sm">
        <div className="truncate">{video.filename}</div>
        <div className="mono truncate text-xs text-[var(--muted)]">
          {waiting ? <Bone width="92%" /> : (
            <>
              {formatTime(video.duration_sec)} · {video.width}×{video.height} · {video.fps.toFixed(2)} fps
              {video.variable_frame_rate ? " · VFR" : " · CFR"} · {video.codec}
            </>
          )}
        </div>
      </div>
    </>
  );
}

export function VideosPage() {
  const client = useQueryClient();
  const videos = useQuery({ queryKey: ["videos", "library"], queryFn: api.unassignedVideos });
  const labSettings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const items = useUploads((state) => state.items);
  const enqueueUnassigned = useUploads((state) => state.enqueueUnassigned);
  const enqueueUnassignedPaths = useUploads((state) => state.enqueueUnassignedPaths);
  const removeItem = useUploads((state) => state.removeItem);
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteVideo(id),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["videos"] });
    },
  });
  const reference = labSettings.data?.video_storage === "reference";
  const storagePending = labSettings.data === undefined && !labSettings.isError;
  const pending = videos.data === undefined && !videos.isError;
  const rows = items.filter((item) => item.projectId === LIBRARY_QUEUE);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-medium">Video library</h1>
        <p className="text-sm text-[var(--muted)]">Videos here are not in a project. Drop one, or add it to a project from that project.</p>
      </header>
      <StorageSettings />
      {videos.isError && <p className="text-sm text-[var(--rose)]">{(videos.error as Error).message}</p>}
      {remove.isError && <p className="text-sm text-[var(--rose)]">{(remove.error as Error).message}</p>}
      {storagePending ? (
        <div className="drop-zone" aria-hidden="true">
          <Bone width="16rem" />
          <Bone width="10rem" />
        </div>
      ) : reference ? (
        <VideoPathForm onAdd={(paths) => enqueueUnassignedPaths(paths)} />
      ) : null}
      {!storagePending && (
        <AssetDropZone
          chooser={!reference}
          accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
          multiple
          prompt={reference ? "Or" : "Drag and drop videos here or"}
          chooseLabel="Choose files"
          supported="Supported: MP4, MOV, WebM"
          rows={rows.map((item) => ({
            id: item.key,
            name: item.name,
            url: item.previewUrl,
            subtitle: item.subtitle,
            progress: item.progress,
            failed: item.phase === "error",
          }))}
          onRemove={removeItem}
          onFiles={(files) => {
            if (files.length) enqueueUnassigned(files);
          }}
        />
      )}
      {pending && (
        <div className="video-grid" aria-busy="true">
          <p className="sr-only">Loading videos</p>
          {Array.from({ length: PLACEHOLDERS }, (_, index) => (
            <div key={index} className="video-card panel overflow-hidden">
              <LibraryFace />
            </div>
          ))}
        </div>
      )}
      {videos.isSuccess && (
        videos.data.length ? (
          <div className="video-grid">
            {videos.data.map((video) => (
              <div key={video.id} className="video-card panel overflow-hidden">
                <button
                  type="button"
                  className="video-card-delete"
                  aria-label={`Delete ${video.filename}`}
                  title="Delete video"
                  disabled={remove.isPending && remove.variables === video.id}
                  onClick={() => {
                    if (window.confirm(`Delete “${video.filename}”? A referenced original stays on disk.`)) remove.mutate(video.id);
                  }}
                >
                  <Trash2 size={15} aria-hidden="true" />
                </button>
                <Link to={`/videos/${video.id}`} className="block">
                  <LibraryFace video={video} />
                </Link>
              </div>
            ))}
          </div>
        ) : (
          !rows.length && <p className="text-sm text-[var(--muted)]">The library is empty.</p>
        )
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
  if (!video.data) return <p className="text-[var(--muted)]">Loading video…</p>;
  const item = video.data;
  const backHref = item.project_id ? `/projects/${item.project_id}` : "/videos";
  const backLabel = item.project_id ? "← Back to project" : "← Back to library";
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
