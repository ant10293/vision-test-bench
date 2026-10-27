"""simulation datasets

Revision ID: c5e1a9b44d18
Revises: b8d4e2a61c30
Create Date: 2026-10-26 16:40:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e1a9b44d18"
down_revision: Union[str, Sequence[str], None] = "b8d4e2a61c30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "simulation_datasets" not in tables:
        op.create_table(
            "simulation_datasets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("preset", sa.String(length=80), nullable=False),
            sa.Column("variant", sa.String(length=80), nullable=False),
            sa.Column("camera_mode", sa.String(length=40), nullable=False),
            sa.Column("research_mode", sa.String(length=64), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("quality", sa.String(length=40), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("fps", sa.Integer(), nullable=False),
            sa.Column("duration", sa.Float(), nullable=False),
            sa.Column("randomize", sa.Boolean(), nullable=False),
            sa.Column("pair_key", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("video_id", sa.String(length=36), nullable=True),
            sa.Column("relative_dir", sa.String(length=300), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("analysis_config", sa.JSON(), nullable=True),
            sa.Column("analysis_run_id", sa.String(length=36), nullable=False, server_default=""),
            sa.Column("blender_version", sa.String(length=80), nullable=False),
            sa.Column("engine", sa.String(length=80), nullable=False),
            sa.Column("frame_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_simulation_datasets_status", "simulation_datasets", ["status"])
    if "simulation_jobs" not in tables:
        op.create_table(
            "simulation_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("stage", sa.String(length=64), nullable=False),
            sa.Column("frame_current", sa.Integer(), nullable=False),
            sa.Column("frame_total", sa.Integer(), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("worker_id", sa.String(length=64), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_simulation_jobs_dataset_id", "simulation_jobs", ["dataset_id"])
        op.create_index("ix_simulation_jobs_status", "simulation_jobs", ["status"])
    if "simulation_evaluations" not in tables:
        op.create_table(
            "simulation_evaluations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("perception_preset", sa.String(length=80), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_simulation_evaluations_dataset_id", "simulation_evaluations", ["dataset_id"])
        op.create_index("ix_simulation_evaluations_run_id", "simulation_evaluations", ["run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "simulation_evaluations" in tables:
        op.drop_table("simulation_evaluations")
    if "simulation_jobs" in tables:
        op.drop_table("simulation_jobs")
    if "simulation_datasets" in tables:
        op.drop_table("simulation_datasets")
