"""Database session management utilities.

This module provides helper functions for constructing SQLAlchemy engines
and sessions based on environment configuration.  It centralizes
database connection handling and avoids repeating boilerplate across
modules.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session


def get_engine(url: Optional[str] = None, **kwargs) -> Engine:
    """Create a new SQLAlchemy engine.

    The database URL is taken from the ``DATABASE_URL`` environment
    variable if not provided explicitly.

    Args:
        url: A database URL.  If ``None``, the value of
            ``os.getenv('DATABASE_URL')`` is used.
        **kwargs: Additional keyword arguments passed to
            ``sqlalchemy.create_engine``.

    Returns:
        A SQLAlchemy :class:`Engine`.
    """
    url = url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return create_engine(url, **kwargs)


def get_sessionmaker(engine: Engine) -> sessionmaker:
    """Return a sessionmaker bound to the given engine."""
    return sessionmaker(bind=engine)


@contextmanager
def get_session(engine: Engine) -> Iterator[Session]:
    """Context manager that yields a SQLAlchemy ORM session.

    Ensures the session is closed after use.

    Args:
        engine: The SQLAlchemy engine to bind the session to.

    Yields:
        A SQLAlchemy :class:`Session` object.
    """
    SessionLocal = sessionmaker(bind=engine)
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()