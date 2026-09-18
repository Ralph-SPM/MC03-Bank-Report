"""Process-lifetime storage for RCBC Initial Demo run results.

The demo deliberately keeps run state in memory.  This module does not create
files, databases, or directories; restarting the process therefore clears every
stored :class:`~mc03.domain.models.RunResult` as required by the demo boundary.
"""

from __future__ import annotations

from threading import RLock

from mc03.domain.models import RunResult


class RunStore:
    """Store completed runs by ``run_id`` for the lifetime of one process.

    ``RunResult`` remains mutable because review decisions and generated
    artifacts are applied to the result after the pipeline completes.  The
    store intentionally returns that same object rather than serializing or
    copying it, while protecting map operations with a small re-entrant lock.
    A missing lookup returns ``None`` and never inserts a placeholder.
    """

    def __init__(self) -> None:
        """Create an empty, non-durable run store."""
        self._runs: dict[str, RunResult] = {}
        self._lock = RLock()

    def store(self, result: RunResult) -> RunResult:
        """Store or overwrite a completed result and return it.

        Args:
            result: The completed in-memory result.  Its ``run_id`` must be a
                non-empty string so lookups cannot become ambiguous.

        Raises:
            TypeError: If *result* is not a :class:`RunResult`.
            ValueError: If ``run_id`` is blank.
        """
        if not isinstance(result, RunResult):
            raise TypeError("result must be a RunResult")
        if not isinstance(result.run_id, str) or not result.run_id.strip():
            raise ValueError("result.run_id must be a non-empty string")
        with self._lock:
            self._runs[result.run_id] = result
        return result

    def complete(self, result: RunResult) -> RunResult:
        """Alias expressing that only completed runs are stored."""
        return self.store(result)

    def save(self, result: RunResult) -> RunResult:
        """Compatibility alias for callers that use persistence terminology."""
        return self.store(result)

    def get(self, run_id: str) -> RunResult | None:
        """Return the result for *run_id*, or ``None`` without mutation."""
        if not isinstance(run_id, str):
            raise TypeError("run_id must be a string")
        with self._lock:
            return self._runs.get(run_id)

    def retrieve(self, run_id: str) -> RunResult | None:
        """Alias used by review, export, and download callers."""
        return self.get(run_id)

    def __contains__(self, run_id: object) -> bool:
        """Return whether a run id is currently stored."""
        if not isinstance(run_id, str):
            return False
        with self._lock:
            return run_id in self._runs

    def __len__(self) -> int:
        """Return the number of completed runs held in memory."""
        with self._lock:
            return len(self._runs)

    def clear(self) -> None:
        """Clear all process-lifetime state.

        This is useful for test isolation and does not imply durable deletion;
        the store has no durable state to delete.
        """
        with self._lock:
            self._runs.clear()


DEFAULT_RUN_STORE = RunStore()
"""The application-wide demo store used when no store is injected."""


__all__ = ["DEFAULT_RUN_STORE", "RunStore"]
