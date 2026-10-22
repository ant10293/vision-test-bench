"""Video upload, probing, proxying, and preview frames."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import FrameAsset, Project, VideoAsset
from app.models.util import new_id
from app.services.storage import data_path, ensure_data_dirs, relative, safe_filename
from app.video.frames import contact_sheet, extract_frame, make_proxy, thumbnail
from app.video.probe import (
    VideoError,
    browser_compatible,
    presentation_timestamps,
    probe,
    sha256_file,
    snap_timestamps,
)
from app.video.sampling import plan_uniform

ALLOWED_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v"}


def ingest_file(session: Session, project_id: str, filename: str, source: Path) -> VideoAsset:
    ensure_data_dirs()
    project = session.get(Project, project_id)
    if project is None:
        raise VideoError("project not found")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise VideoError(f"unsupported type {suffix or 'unknown'}; use mp4, mov, or webm")
    checksum = sha256_file(source)
    existing = session.scalar(
        select(VideoAsset).where(
            VideoAsset.project_id == project_id,
            VideoAsset.checksum_sha256 == checksum,
        )
    )
    if existing is not None:
        return existing
    meta = probe(source)
    if meta.duration_sec <= 0 or meta.width <= 0:
        raise VideoError("the file has no usable video duration or dimensions")
    stored_name = f"{checksum[:12]}_{safe_filename(filename)}"
    if not stored_name.lower().endswith(suffix):
        stored_name += suffix
    original = data_path("originals", stored_name)
    if not original.exists():
        shutil.copy2(source, original)
    proxy_rel = ""
    if not browser_compatible(meta):
        proxy = data_path("proxies", f"{checksum[:12]}.mp4")
        if not proxy.exists():
            make_proxy(original, proxy)
        proxy_rel = relative(proxy)
    pts_cache = data_path("originals", f"{checksum[:12]}.pts.json")
    pts = presentation_timestamps(original, pts_cache)
    video = VideoAsset(
        id=new_id(),
        project_id=project_id,
        original_filename=safe_filename(filename),
        stored_path=relative(original),
        proxy_path=proxy_rel,
        checksum_sha256=checksum,
        mime_type=meta.mime_hint,
        container=meta.container,
        codec=meta.codec,
        duration_sec=meta.duration_sec,
        width=meta.width,
        height=meta.height,
        display_width=meta.display_width,
        display_height=meta.display_height,
        fps=meta.fps,
        avg_fps=meta.avg_fps,
        time_base=meta.time_base,
        rotation_deg=meta.rotation_deg,
        variable_frame_rate=meta.variable_frame_rate,
        frame_count=meta.frame_count or len(pts),
        size_bytes=original.stat().st_size,
    )
    session.add(video)
    session.flush()
    preview = plan_uniform(meta.duration_sec, 1.0, pts)[:40]
    frame_paths: list[Path] = []
    for ordinal, sample in enumerate(preview):
        dest = data_path("frames", video.id, f"{ordinal:04d}_{sample.timestamp:.3f}.jpg")
        extract_frame(original, sample.timestamp, dest, accurate=True)
        frame_paths.append(dest)
        session.add(
            FrameAsset(
                video_id=video.id,
                run_id=None,
                timestamp=sample.timestamp,
                path=relative(dest),
                width=meta.display_width,
                height=meta.display_height,
                reason="upload_preview",
                strategy="uniform",
                ordinal=ordinal,
            )
        )
    if preview:
        thumb = data_path("thumbs", f"{video.id}.jpg")
        thumbnail(original, preview[min(1, len(preview) - 1)].timestamp, thumb)
        video.thumbnail_path = relative(thumb)
        sheet = data_path("sheets", f"{video.id}.jpg")
        contact_sheet(frame_paths, sheet)
        video.contact_sheet_path = relative(sheet)
    session.commit()
    session.refresh(video)
    return video


def playback_path(video: VideoAsset) -> Path:
    if video.proxy_path:
        return data_path(*Path(video.proxy_path).parts)
    return data_path(*Path(video.stored_path).parts)


def sample_run_frames(
    session: Session,
    video: VideoAsset,
    run_id: str,
    samples,
) -> list[FrameAsset]:
    original = data_path(*Path(video.stored_path).parts)
    rows: list[FrameAsset] = []
    existing = {
        round(frame.timestamp, 3): frame
        for frame in session.scalars(
            select(FrameAsset).where(FrameAsset.video_id == video.id, FrameAsset.run_id == run_id)
        )
    }
    for ordinal, sample in enumerate(samples):
        key = round(sample.timestamp, 3)
        if key in existing:
            rows.append(existing[key])
            continue
        dest = data_path("frames", video.id, run_id, f"{ordinal:04d}_{sample.timestamp:.3f}.jpg")
        if not dest.exists():
            snapped = snap_timestamps([sample.timestamp], [])[0]
            extract_frame(original, snapped, dest, accurate=True)
        row = FrameAsset(
            video_id=video.id,
            run_id=run_id,
            timestamp=sample.timestamp,
            path=relative(dest),
            width=video.display_width,
            height=video.display_height,
            reason=sample.reason,
            strategy=sample.reason,
            motion_score=sample.motion_score,
            sharpness=sample.sharpness,
            scene_change=sample.scene_change,
            ordinal=ordinal,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows
