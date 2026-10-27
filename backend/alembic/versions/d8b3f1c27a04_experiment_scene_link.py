"""Link an experiment run to the scene it asked to render.

Revision ID: d8b3f1c27a04
Revises: c5e1a9b44d18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b3f1c27a04"
down_revision: Union[str, Sequence[str], None] = "c5e1a9b44d18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "simulation_datasets" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("simulation_datasets")}
    if "analysis_config" not in present:
        op.add_column("simulation_datasets", sa.Column("analysis_config", sa.JSON(), nullable=True))
    if "analysis_run_id" not in present:
        op.add_column(
            "simulation_datasets",
            sa.Column("analysis_run_id", sa.String(length=36), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "simulation_datasets" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("simulation_datasets")}
    if "analysis_run_id" in present:
        op.drop_column("simulation_datasets", "analysis_run_id")
    if "analysis_config" in present:
        op.drop_column("simulation_datasets", "analysis_config")
