"""Entry point for running Informer as a module.

This allows the CLI to be invoked with ``python -m informer``.
"""

from .cli import cli

if __name__ == "__main__":  # pragma: no cover
    cli()