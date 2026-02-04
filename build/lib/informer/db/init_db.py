"""Database initialization script.

This module provides a command-line entry point for applying database
migrations using Alembic.  It reads the database URL from the
``DATABASE_URL`` environment variable.
"""

from __future__ import annotations

import os

from alembic import command
from alembic.config import Config


def main() -> None:
    """Apply all pending migrations to the database.

    The Alembic configuration file is located two directories above
    this module (in the project root).  The DATABASE_URL environment
    variable must be set for migrations to connect to the database.
    """
    here = os.path.dirname(__file__)
    # Construct path to alembic.ini relative to this file
    config_path = os.path.join(here, os.pardir, os.pardir, "alembic.ini")
    config = Config(os.path.abspath(config_path))
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()