from alembic import context
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers tables on the metadata)
from app.core.db import get_engine

if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported.")

with get_engine().connect() as connection:
    # Batch mode recreates tables; with foreign keys on, SQLite's DROP TABLE would cascade
    # or fail on dependent rows. The pragma only takes effect outside a transaction.
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()
    try:
        context.configure(
            connection=connection,
            target_metadata=SQLModel.metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign key violations after migration: {violations[:5]}")
    finally:
        connection.rollback()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
