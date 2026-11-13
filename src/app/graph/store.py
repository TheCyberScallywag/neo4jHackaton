"""Relate adapter: the embedded `relate-db` engine on a single-file `.rl` store.

Relate takes an exclusive lock on the store for any opener, read-only included, so this
adapter opens the store per unit of work and closes it straight after (about 8 ms on the demo
graph). Within a process a lock serialises access, making the app a single writer; across
processes (the Streamlit admin, the CLI, or `relate --database ... --read-only`) a locked store
is retried a bounded number of times before giving up with `GraphBusy`.

Every statement is parameterised; reads are bounded by `max_rows` and never silently
truncated.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import relate

ENGINE_VERSION = "1.0.1"  # keep in step with pyproject's relate-db pin and the documented CLI


class GraphError(RuntimeError):
    """A Relate statement failed."""


class GraphBusy(GraphError):
    """The store stayed locked by another process for the whole retry window."""


class GraphTruncated(GraphError):
    """A read produced more rows than the configured bound."""


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.RLock())


class Tx:
    """Statements run inside one open store (and, for writes, one transaction)."""

    def __init__(self, conn: Any, max_rows: int) -> None:
        self._conn = conn
        self._max_rows = max_rows

    def run(self, statement: str, params: dict[str, Any] | None = None) -> None:
        try:
            self._conn.cursor().execute(statement, params or {})
        except relate.Error as error:
            raise GraphError(str(error)) from error

    def read(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            cursor = self._conn.cursor()
            cursor.execute(statement, params or {})
            rows = cursor.fetchmany(self._max_rows + 1)
        except relate.Error as error:
            raise GraphError(str(error)) from error
        if len(rows) > self._max_rows:
            raise GraphTruncated(f"More than {self._max_rows} rows; narrow the query.")
        return [row.data() for row in rows]


class GraphStore:
    def __init__(
        self,
        path: Path,
        *,
        max_rows: int = 500,
        attempts: int = 40,
        backoff_seconds: float = 0.05,
    ) -> None:
        self.path = Path(path).resolve()
        self.max_rows = max_rows
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds

    def _open(self, *, read_only: bool) -> Any:
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                return relate.connect(str(self.path), read_only=read_only)
            except relate.OperationalError as error:
                if "locked" not in str(error).lower():
                    raise GraphError(str(error)) from error
                last = error
                time.sleep(min(self.backoff_seconds * (attempt + 1), 0.5))
        raise GraphBusy(f"{self.path} stayed locked: {last}")

    @contextmanager
    def write(self) -> Iterator[Tx]:
        """One atomic write: committed on success, rolled back on any exception."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock_for(self.path):
            conn = self._open(read_only=False)
            try:
                conn.autocommit = False
                tx = Tx(conn, self.max_rows)
                try:
                    yield tx
                except BaseException:
                    conn.rollback()
                    raise
                try:
                    conn.commit()
                except relate.Error as error:
                    raise GraphError(str(error)) from error
            finally:
                conn.close()

    @contextmanager
    def reader(self) -> Iterator[Tx]:
        if not self.path.exists():
            raise GraphError(f"Graph store {self.path} is missing; run the seed first.")
        with _lock_for(self.path):
            conn = self._open(read_only=True)
            try:
                yield Tx(conn, self.max_rows)
            finally:
                conn.close()

    def read(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.reader() as tx:
            return tx.read(statement, params)
