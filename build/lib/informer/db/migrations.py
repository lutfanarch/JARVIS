"""Database migration helpers for JARVIS.

This module provides reusable functions to run Alembic migrations
programmatically from within Python code.  The primary function,
``upgrade_head``, runs all pending migrations up to the latest
revision.  It is designed to be independent of the current working
directory by computing absolute paths for the Alembic script
locations.

Usage example::

    from informer.db.migrations import upgrade_head
    upgrade_head()  # apply migrations using DATABASE_URL environment

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config


def upgrade_head(database_url: Optional[str] = None) -> None:
    """Upgrade the database schema to the latest revision.

    This function locates the project's ``alembic.ini`` file and
    runs ``alembic upgrade head`` programmatically.  It sets the
    ``script_location`` and ``version_locations`` options to
    absolute paths so that migrations work correctly regardless of
    the current working directory.  An optional ``database_url``
    argument can override the ``DATABASE_URL`` environment variable.

    Parameters
    ----------
    database_url : str or None, optional
        The SQLAlchemy database URL.  If not provided, the value
        of the ``DATABASE_URL`` environment variable is used.

    Raises
    ------
    Exception
        If the Alembic configuration or migration fails.
    """
    # Determine project root.  migrations.py lives at
    # <repo>/src/informer/db/migrations.py.  To locate the repository
    # root we need to ascend three parents: db -> informer -> src -> <repo>.
    # Using parents[3] yields the jarvis repository root irrespective
    # of the current working directory.  Previously parents[2] was used
    # which incorrectly pointed to the src directory and caused
    # ``alembic.ini`` lookups to fail when running db‑init from the
    # repository root or smoke tests.
    project_root = Path(__file__).resolve().parents[3]
    alembic_ini_path = project_root / "alembic.ini"
    if not alembic_ini_path.exists():
        raise FileNotFoundError(f"alembic.ini not found at {alembic_ini_path}")
    # Initialise Alembic configuration
    cfg = Config(str(alembic_ini_path))
    # Force script location to absolute path
    script_location = project_root / "alembic"
    cfg.set_main_option("script_location", str(script_location))
    # Explicitly set version_locations (optional but helps Alembic find versions)
    versions_path = script_location / "versions"
    cfg.set_main_option("version_locations", str(versions_path))
    # Determine database URL to use
    db_url = database_url or os.getenv("DATABASE_URL")
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    # Perform the upgrade
    command.upgrade(cfg, "head")