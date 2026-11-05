"""Traffic: timed paths, reservations of nodes and corridors, and conflict checking (M4)."""

from amr_optimizer.traffic.conflicts import Conflict, find_conflicts, path_conflicts
from amr_optimizer.traffic.paths import (
    FOREVER,
    Corridor,
    InvalidTimedPathError,
    Occupancy,
    Resource,
    TimedPath,
    Visit,
    validate_timed_path,
)
from amr_optimizer.traffic.reservations import (
    Reservation,
    ReservationConflictError,
    ReservationTable,
)

__all__ = [
    "FOREVER",
    "Conflict",
    "Corridor",
    "InvalidTimedPathError",
    "Occupancy",
    "Reservation",
    "ReservationConflictError",
    "ReservationTable",
    "Resource",
    "TimedPath",
    "Visit",
    "find_conflicts",
    "path_conflicts",
    "validate_timed_path",
]
