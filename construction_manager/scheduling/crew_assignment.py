"""Crew schedule projections.

The public location is intentionally separate from the calculation entry point;
the implementation remains shared during the incremental engine migration.
"""

from .engine import crew_actual_work_intervals

__all__ = ["crew_actual_work_intervals"]
