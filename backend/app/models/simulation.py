"""Simulation datasets and their render jobs. These are not analysis jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models.util import new_id, utcnow


class SimulationDataset(Base):
    __tablename__ = "simulation_datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(300))
    preset: Mapped[str] = mapped_column(String(80), default="assembly")
    variant: Mapped[str] = mapped_column(String(80))
    camera_mode: Mapped[str] = mapped_column(String(40))
    research_mode: Mapped[str] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column(Integer, default=1)
    quality: Mapped[str] = mapped_column(String(40), default="preview")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[int] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(Float)
    randomize: Mapped[bool] = mapped_column(Boolean, default=False)
    pair_key: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("video_assets.id", ondelete="SET NULL"), nullable=True)
    relative_dir: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    analysis_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analysis_run_id: Mapped[str] = mapped_column(String(36), default="")
    blender_version: Mapped[str] = mapped_column(String(80), default="")
    engine: Mapped[str] = mapped_column(String(80), default="")
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SimulationJob(Base):
    __tablename__ = "simulation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("simulation_datasets.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    frame_current: Mapped[int] = mapped_column(Integer, default=0)
    frame_total: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    worker_id: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SimulationEvaluation(Base):
    __tablename__ = "simulation_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("simulation_datasets.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    perception_preset: Mapped[str] = mapped_column(String(80), default="")
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
