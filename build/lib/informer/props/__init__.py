"""Prop firm profile configuration and helpers.

This package defines a small set of deterministic profiles used to
configure JARVIS for proprietary trading firm evaluations.  A profile
encapsulates account size and various rule parameters such as
profit/loss targets, per‑trade risk budgets and minimum execution
constraints.  When the environment variable ``PROP_PROFILE`` is set
to the name of a supported profile, the validator will enforce the
corresponding risk gates and produce a ``prop`` block in the final
decision.

The canonical way to access a profile is via :func:`get_profile` or
the convenience :func:`get_active_profile` which looks up the name in
``os.environ``.  New profiles may be added by defining additional
instances in :mod:`profiles`.
"""

from .profiles import PropFirmProfile, get_profile, get_active_profile  # noqa: F401

__all__ = [
    "PropFirmProfile",
    "get_profile",
    "get_active_profile",
]