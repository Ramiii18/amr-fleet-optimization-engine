"""Stored replay traces of simulation jobs, and how they are sent (D33).

A trace arrives from a worker process as gzip bytes and is kept as it is: the server never
parses it. The store is bounded twice, by the number of traces and by their total size, and
drops the oldest first. It is used from the event loop only, so it needs no lock.
"""

from collections import OrderedDict

MAX_KEPT_TRACES = 20
MAX_TRACE_BYTES = 32 * 1024 * 1024  # compressed bytes of all kept traces together


class TraceStore:
    """Compressed traces by job id, oldest first, within both limits at all times."""

    __slots__ = ("_max_bytes", "_max_traces", "_total", "_traces")

    def __init__(self, max_traces: int = MAX_KEPT_TRACES, max_bytes: int = MAX_TRACE_BYTES) -> None:
        if max_traces < 0 or max_bytes < 0:
            raise ValueError("the limits of a trace store must be non-negative")
        self._max_traces = max_traces
        self._max_bytes = max_bytes
        self._traces: OrderedDict[str, bytes] = OrderedDict()
        self._total = 0

    def put(self, job_id: str, data: bytes) -> bool:
        """Keep ``data`` for ``job_id``, dropping the oldest traces to make room.

        Returns False, and keeps nothing, if this one trace is larger than the store allows.
        """
        self.discard(job_id)
        if self._max_traces == 0 or len(data) > self._max_bytes:
            return False
        while len(self._traces) >= self._max_traces or self._total + len(data) > self._max_bytes:
            _, dropped = self._traces.popitem(last=False)
            self._total -= len(dropped)
        self._traces[job_id] = data
        self._total += len(data)
        return True

    def get(self, job_id: str) -> bytes | None:
        return self._traces.get(job_id)

    def discard(self, job_id: str) -> None:
        dropped = self._traces.pop(job_id, None)
        if dropped is not None:
            self._total -= len(dropped)

    def __contains__(self, job_id: object) -> bool:
        return job_id in self._traces

    def __len__(self) -> int:
        return len(self._traces)

    @property
    def total_bytes(self) -> int:
        return self._total


def accepts_gzip(accept_encoding: str | None) -> bool:
    """True if a request with this ``Accept-Encoding`` header may be answered in gzip.

    The header lists codings with optional weights, such as ``gzip, br;q=0.8, *;q=0.1``; a
    weight of 0 refuses a coding, and ``*`` stands for every coding not named. Without the
    header the answer is not compressed, which every client can read.
    """
    if accept_encoding is None:
        return False
    weights: dict[str, float] = {}
    for part in accept_encoding.split(","):
        coding, *parameters = (piece.strip().lower() for piece in part.split(";"))
        if not coding:
            continue
        weight = 1.0
        for parameter in parameters:
            name, _, value = parameter.partition("=")
            if name.strip() == "q":
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
        weights[coding] = weight
    for coding in ("gzip", "x-gzip", "*"):
        if coding in weights:
            return weights[coding] > 0
    return False
