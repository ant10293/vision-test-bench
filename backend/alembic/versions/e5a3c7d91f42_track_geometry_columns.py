"""image-plane scale and turn on spatial points

Revision ID: e5a3c7d91f42
Revises: d2f6a8c3e915
Create Date: 2026-10-29 14:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a3c7d91f42"
down_revision: Union[str, None] = "d2f6a8c3e915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "spatial_track_points" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("spatial_track_points")}
    if "relative_scale" not in present:
        op.add_column("spatial_track_points", sa.Column("relative_scale", sa.Float(), nullable=True))
    if "angle_deg" not in present:
        op.add_column("spatial_track_points", sa.Column("angle_deg", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "spatial_track_points" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("spatial_track_points")}
    if "angle_deg" in present:
        op.drop_column("spatial_track_points", "angle_deg")
    if "relative_scale" in present:
        op.drop_column("spatial_track_points", "relative_scale")
