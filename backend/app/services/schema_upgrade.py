"""Add task-project columns on databases created before those fields existed."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

COLUMN_UPGRADES = {
    "projects": [("is_task", "BOOLEAN DEFAULT 0")],
    "video_assets": [
        ("session_id", "VARCHAR(36)"),
        ("research_mode", "VARCHAR(64) DEFAULT ''"),
        ("capture_time_raw", "VARCHAR(120) DEFAULT ''"),
        ("capture_time_utc", "VARCHAR(40) DEFAULT ''"),
        ("capture_timezone", "VARCHAR(40) DEFAULT ''"),
        ("timestamp_source", "VARCHAR(40) DEFAULT 'absent'"),
        ("timestamp_reliability", "VARCHAR(32) DEFAULT 'absent'"),
        ("timestamp_note", "TEXT DEFAULT ''"),
        ("file_mtime", "VARCHAR(40) DEFAULT ''"),
        ("order_index", "FLOAT"),
        ("order_locked", "BOOLEAN DEFAULT 0"),
        ("order_method", "VARCHAR(32) DEFAULT ''"),
        ("order_ambiguous", "BOOLEAN DEFAULT 0"),
        ("audio_probed", "BOOLEAN DEFAULT 0"),
        ("has_audio", "BOOLEAN DEFAULT 0"),
        ("audio_codec", "VARCHAR(80) DEFAULT ''"),
        ("audio_channels", "INTEGER DEFAULT 0"),
        ("ingest_status", "VARCHAR(32) DEFAULT 'ready'"),
        ("ingest_progress", "FLOAT DEFAULT 100"),
        ("ingest_detail", "VARCHAR(200) DEFAULT ''"),
        ("ingest_error", "TEXT DEFAULT ''"),
    ],
    "model_responses": [("price_known", "BOOLEAN NOT NULL DEFAULT 0")],
}


def ensure_task_columns(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        for table, columns in COLUMN_UPGRADES.items():
            if table not in tables:
                continue
            present = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        if "model_responses" in tables:
            present = {row[1] for row in conn.execute(text("PRAGMA table_info(model_responses)"))}
            if "price_known" in present:
                conn.execute(
                    text("UPDATE model_responses SET price_known = 1 WHERE cost_usd > 0 AND price_known = 0")
                )
