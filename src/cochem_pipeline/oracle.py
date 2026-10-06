"""Deterministic rule retrieval and an out-of-band, debounced Oracle.

Context budgets are **UTF-8 bytes**, including XML tags, attributes and escaping.
They are conservative relative to normal text-token budgets and make the hard
limit independent of a provider tokenizer. ``xml`` is a sequence of escaped
``oracle_directive`` elements, intended to be appended to a prompt. Core rules
must fit floor(total_budget * reserved_fraction); optional rules cannot spend
unused reserved capacity. The optional selection is an exact 0/1 knapsack.

The tracking database must be in a separate protected controller directory,
never the worker's job board or workspace. POSIX mode checks protect against
other users, not workers sharing the controller UID. Windows construction
requires the real SYSTEM identity and a SYSTEM/Administrators-only directory
DACL, validated by the platform module. A table name or hidden file is never a
security boundary. No transient hints files are created.

``record`` only coalesces events and checks velocity; rendering and SHA-256
watermarking occur in ``drain`` after a trailing debounce of at least 500 ms.
A trip schedules the supplied reaper on a separate executor without holding an
Oracle lock. This module does not claim to elevate or terminate OS processes.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Callable, Iterable
import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
import time


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    patterns: tuple[str, ...]
    facets: tuple[str, ...] = ()
    core: bool = False
    weight: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Rule id must be a nonempty string")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Rule text must be a nonempty string")
        if not isinstance(self.core, bool):
            raise TypeError("Rule core must be a boolean")
        if isinstance(self.weight, bool) or not isinstance(self.weight, int) or self.weight < 1:
            raise ValueError("Rule weight must be a positive integer")
        for name in ("patterns", "facets"):
            values = getattr(self, name)
            if isinstance(values, (str, bytes)):
                raise TypeError(f"Rule {name} must be a sequence of strings")
            values = tuple(values)
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"Rule {name} cannot contain empty or non-string values")
            object.__setattr__(self, name, values)
        for value in (self.id, self.text):
            if any(not (char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF or
                        0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF)
                   for char in value):
                raise ValueError("Rule id/text contains an invalid XML 1.0 character")


class AhoCorasick:
    """A real multi-pattern trie with BFS failure links and inherited outputs.

    Matching is Unicode case-insensitive (``str.casefold``), including overlap
    and suffix matches. Returned identifiers are distinct and sorted.
    """

    def __init__(self, patterns: Iterable[tuple[str, str]]) -> None:
        self._next: list[dict[str, int]] = [{}]
        self._fail = [0]
        self._outputs: list[set[str]] = [set()]
        for pattern, identifier in sorted(patterns):
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("Patterns must be nonempty strings")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("Pattern identifiers must be nonempty strings")
            state = 0
            for char in pattern.casefold():
                child = self._next[state].get(char)
                if child is None:
                    child = len(self._next)
                    self._next[state][char] = child
                    self._next.append({})
                    self._fail.append(0)
                    self._outputs.append(set())
                state = child
            self._outputs[state].add(identifier)
        queue = deque(self._next[0].values())
        while queue:
            state = queue.popleft()
            for char, child in self._next[state].items():
                queue.append(child)
                fallback = self._fail[state]
                while fallback and char not in self._next[fallback]:
                    fallback = self._fail[fallback]
                self._fail[child] = self._next[fallback].get(char, 0)
                self._outputs[child].update(self._outputs[self._fail[child]])

    def match(self, text: str) -> tuple[str, ...]:
        if not isinstance(text, str):
            raise TypeError("Retrieval text must be a string")
        state = 0
        matches: set[str] = set()
        for char in text.casefold():
            while state and char not in self._next[state]:
                state = self._fail[state]
            state = self._next[state].get(char, 0)
            matches.update(self._outputs[state])
        return tuple(sorted(matches))


def _facets(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("Facets must be a sequence of strings")
    values = tuple(values)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("Facets must contain nonempty strings")
    return tuple(sorted({value.casefold() for value in values}))


class ContextEngine:
    """Retrieve matching facets and select optional rules with exact knapsack.

    Each rule with patterns needs at least one text match; each declared facet
    must be present in the request. An optional rule without patterns matches
    any text, subject to its facets. Core rules are unconditional. Selection
    maximizes the sum of integer weights, then minimizes consumed bytes, then
    chooses lexicographically smallest sorted IDs. Input ordering has no effect.
    """

    def __init__(self, rules: Iterable[Rule], total_budget: int,
                 reserved_fraction: float = 0.25) -> None:
        if isinstance(total_budget, bool) or not isinstance(total_budget, int) or total_budget < 1:
            raise ValueError("total_budget must be a positive integer number of UTF-8 bytes")
        if isinstance(reserved_fraction, bool) or not isinstance(reserved_fraction, (float, int)) or not math.isfinite(reserved_fraction) or not 0 <= reserved_fraction <= 1:
            raise ValueError("reserved_fraction must be finite and between 0 and 1")
        supplied = tuple(rules)
        if any(not isinstance(rule, Rule) for rule in supplied):
            raise TypeError("rules must contain Rule objects")
        ordered = tuple(sorted(supplied, key=lambda rule: rule.id))
        if len({rule.id for rule in ordered}) != len(ordered):
            raise ValueError("Rule IDs must be unique")
        self.rules = ordered
        self.total_budget = total_budget
        self.reserved_budget = math.floor(total_budget * reserved_fraction)
        self._core = tuple(rule for rule in ordered if rule.core)
        self._optional = tuple(rule for rule in ordered if not rule.core)
        self._xml = {rule.id: self._directive(rule) for rule in ordered}
        self._cost = {identifier: len(xml.encode("utf-8")) for identifier, xml in self._xml.items()}
        core_cost = sum(self._cost[rule.id] for rule in self._core)
        if core_cost > self.reserved_budget:
            raise ValueError(f"Core directives require {core_cost} UTF-8 bytes, exceeding reserved budget {self.reserved_budget}")
        self._automaton = AhoCorasick((pattern, rule.id) for rule in self._optional for pattern in rule.patterns)
        self._rule_facets = {rule.id: frozenset(_facets(rule.facets)) for rule in ordered}

    @staticmethod
    def _directive(rule: Rule) -> str:
        return (f'<oracle_directive rule_id="{escape(rule.id, quote=True)}" '
                f'core="{str(rule.core).lower()}">{escape(rule.text, quote=True)}</oracle_directive>')

    def matching_rule_ids(self, text: str, facets: Iterable[str] = ()) -> tuple[str, ...]:
        available_facets = frozenset(_facets(facets))
        matched = frozenset(self._automaton.match(text))
        return tuple(rule.id for rule in self._optional
                     if (not rule.patterns or rule.id in matched)
                     and self._rule_facets[rule.id].issubset(available_facets))

    def render(self, text: str, facets: Iterable[str] = ()) -> dict:
        matches = frozenset(self.matching_rule_ids(text, facets))
        candidates = tuple(rule for rule in self._optional if rule.id in matches)
        capacity = self.total_budget - self.reserved_budget
        # used bytes -> (score, sorted selected IDs). Pareto pruning removes
        # only states dominated in both score and cost, preserving exactness.
        states: dict[int, tuple[int, tuple[str, ...]]] = {0: (0, ())}
        for rule in candidates:
            updated = dict(states)
            cost = self._cost[rule.id]
            for used, (score, identifiers) in states.items():
                next_used = used + cost
                if next_used > capacity:
                    continue
                candidate = (score + rule.weight, identifiers + (rule.id,))
                current = updated.get(next_used)
                if current is None or candidate[0] > current[0] or (candidate[0] == current[0] and candidate[1] < current[1]):
                    updated[next_used] = candidate
            states = {}
            best_score = -1
            for used in sorted(updated):
                value = updated[used]
                if value[0] > best_score:
                    states[used] = value
                    best_score = value[0]
        used, (_, selected) = min(states.items(), key=lambda item: (-item[1][0], item[0], item[1][1]))
        identifiers = tuple(rule.id for rule in self._core) + selected
        xml = "".join(self._xml[identifier] for identifier in identifiers)
        actual = len(xml.encode("utf-8"))
        if actual > self.total_budget:
            raise RuntimeError("Context budget accounting failed")
        return {"xml": xml, "rule_ids": list(identifiers), "budget_used": actual,
                "budget_limit": self.total_budget, "reserved_budget": self.reserved_budget}


@dataclass(frozen=True)
class _Event:
    text: str
    facets: tuple[str, ...]
    observed_at: float
    revision: int


def _protect_tracking_path(path: Path) -> Path:
    if path.name.casefold() == "job_board.db":
        raise ValueError("Oracle tracking must use a separate protected database, not job_board.db")
    if str(path) == ":memory:":
        raise ValueError("Oracle tracking requires a persistent protected database path")
    path = path.expanduser().absolute()
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Oracle tracking directory/database must not be symlinks")
    if os.name == "nt":
        from .windows import require_system, validate_private_directory, validate_private_path
        require_system()
        path.parent.mkdir(parents=True, exist_ok=True)
        validate_private_directory(path.parent)
        if path.exists():
            if not path.is_file() or path.stat().st_nlink != 1:
                raise PermissionError("Oracle tracking must be a private regular file without hard links")
            validate_private_path(path)
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists():
                validate_private_path(sidecar)
    else:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_stat = path.parent.stat()
        if parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
            raise PermissionError("Oracle tracking parent must be controller-owned and mode 0700, separate from worker directories")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            os.close(descriptor)
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1 or file_stat.st_uid != os.geteuid() or stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise PermissionError("Oracle tracking database must be controller-owned, regular, and mode 0600")
    return path


class Oracle:
    """Coalesce task events, render after debounce, and trip runaway tasks.

    ``on_trip(task_id)`` executes once per task on a dedicated executor and must
    perform the actual platform reaper operation. It must not call ``close``.
    Pending events for that task are discarded immediately on the trip; other
    tasks continue. The rolling rate interval is (now - 1 second, now].

    ``now`` supports deterministic monotonic test clocks; leave it unset in
    production. Explicit record times for each task must not go backwards.
    Contexts and their watermarks are persisted atomically to a durable outbox.
    ``drain`` replays unacknowledged contexts, including after a restart. The
    controller must persist each context idempotently in its protected job board
    in delivery order and then call ``ack(task_id, watermark, delivery_id)``.
    A return to an earlier context creates a new delivery, while repetitions of
    the current context do not. This is at-least-once delivery;
    acknowledgment is never inferred from returning a Python value.
    """

    def __init__(self, engine: ContextEngine, tracking_db: str | Path,
                 on_trip: Callable[[str], None], debounce_seconds: float = 0.5,
                 velocity_limit: int = 500) -> None:
        if not isinstance(engine, ContextEngine):
            raise TypeError("engine must be a ContextEngine")
        if not callable(on_trip):
            raise TypeError("on_trip must be callable")
        if isinstance(debounce_seconds, bool) or not isinstance(debounce_seconds, (int, float)) or not math.isfinite(debounce_seconds) or debounce_seconds < 0.5:
            raise ValueError("debounce_seconds must be at least 0.5 seconds")
        if isinstance(velocity_limit, bool) or not isinstance(velocity_limit, int) or velocity_limit < 1:
            raise ValueError("velocity_limit must be a positive integer")
        self.engine = engine
        self.tracking_db = _protect_tracking_path(Path(tracking_db))
        self.debounce_seconds = float(debounce_seconds)
        self.velocity_limit = velocity_limit
        self._on_trip = on_trip
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._drain_lock = threading.Lock()
        self._pending: dict[str, _Event] = {}
        self._rates: dict[str, deque[float]] = {}
        self._last_seen: dict[str, float] = {}
        self._tripped: set[str] = set()
        self._revision = 0
        self._submissions = 0
        self._closing = False
        self._closed = False
        self._futures: dict[str, Future] = {}
        self._connection = sqlite3.connect(self.tracking_db, timeout=5, check_same_thread=False)
        try:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute('''CREATE TABLE IF NOT EXISTS _oracle_watermarks (
                task_id TEXT NOT NULL,
                watermark TEXT NOT NULL,
                rule_ids TEXT NOT NULL,
                created_at REAL NOT NULL,
                payload TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (acknowledged IN (0,1)),
                PRIMARY KEY (task_id, watermark)
            )''')
            columns = {row[1] for row in self._connection.execute("PRAGMA table_info(_oracle_watermarks)")}
            if not {"payload", "acknowledged"}.issubset(columns):
                raise RuntimeError("Legacy Oracle tracking lacks durable payloads; configure a new private Oracle database and replay pending job events")
            self._connection.execute('''CREATE TABLE IF NOT EXISTS _oracle_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                watermark TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (acknowledged IN (0,1))
            )''')
            self._connection.execute('''CREATE INDEX IF NOT EXISTS _oracle_delivery_task
                ON _oracle_deliveries(task_id, id)''')
            # Preserve existing acknowledged history and pending payloads when
            # upgrading. Watermark identity and delivery identity are separate:
            # A -> B -> A needs another A delivery, but no duplicate rule body.
            self._connection.execute('''INSERT INTO _oracle_deliveries(task_id,watermark,acknowledged)
                SELECT w.task_id,w.watermark,w.acknowledged FROM _oracle_watermarks w
                WHERE NOT EXISTS (SELECT 1 FROM _oracle_deliveries d
                                  WHERE d.task_id=w.task_id AND d.watermark=w.watermark)
                ORDER BY w.rowid''')
            self._connection.commit()
        except BaseException:
            self._connection.close()
            raise
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="oracle-reaper")

    @staticmethod
    def _clock(now: float | None) -> float:
        value = time.monotonic() if now is None else now
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("now must be a finite monotonic timestamp")
        return float(value)

    def _require_open(self) -> None:
        if self._closing or self._closed:
            raise RuntimeError("Oracle is closed")

    @property
    def tripped_tasks(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._tripped)

    def has_pending(self, task_id: str) -> bool:
        """Whether this task still has coalesced events awaiting a stable drain."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a nonempty string")
        with self._lock:
            self._require_open()
            return task_id in self._pending

    @property
    def trip_errors(self) -> dict[str, str]:
        with self._lock:
            futures = tuple(self._futures.items())
        errors = {}
        for task_id, future in futures:
            if future.done() and not future.cancelled():
                error = future.exception()
                if error is not None:
                    errors[task_id] = f"{type(error).__name__}: {error}"
        return errors

    def record(self, task_id: str, text: str, facets: Iterable[str] = (),
               now: float | None = None) -> None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a nonempty string")
        if not isinstance(text, str):
            raise TypeError("Event text must be a string")
        normalized_facets = _facets(facets)
        trip = False
        with self._condition:
            self._require_open()
            if task_id in self._tripped:
                return
            observed = self._clock(now)
            previous = self._last_seen.get(task_id)
            if previous is not None and observed < previous:
                raise ValueError("Event time cannot move backwards for a task")
            self._last_seen[task_id] = observed
            rate = self._rates.setdefault(task_id, deque())
            while rate and rate[0] <= observed - 1.0:
                rate.popleft()
            rate.append(observed)
            if len(rate) > self.velocity_limit:
                self._tripped.add(task_id)
                self._pending.pop(task_id, None)
                self._rates.pop(task_id, None)
                self._submissions += 1
                trip = True
            else:
                self._revision += 1
                self._pending[task_id] = _Event(text, normalized_facets, observed, self._revision)
        if trip:
            try:
                future = self._executor.submit(self._on_trip, task_id)
                with self._lock:
                    self._futures[task_id] = future
            finally:
                with self._condition:
                    self._submissions -= 1
                    self._condition.notify_all()

    def drain(self, now: float | None = None) -> list[dict]:
        observed = self._clock(now)
        with self._drain_lock:
            with self._lock:
                self._require_open()
                due = tuple((task_id, event) for task_id, event in sorted(self._pending.items())
                            if observed - event.observed_at >= self.debounce_seconds)
            for task_id, event in due:
                with self._lock:
                    if self._closing or task_id in self._tripped or self._pending.get(task_id) is not event:
                        continue
                rendered = self.engine.render(event.text, event.facets)
                material = json.dumps({"task_id": task_id, "xml": rendered["xml"], "rule_ids": rendered["rule_ids"]},
                                      sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                watermark = hashlib.sha256(material).hexdigest()
                output = {"task_id": task_id, "watermark": watermark, **rendered}
                with self._lock:
                    if self._closing or task_id in self._tripped or self._pending.get(task_id) is not event:
                        continue
                # No state lock is held across SQLite I/O: velocity detection
                # and the asynchronous reaper remain live even during DB locks.
                delivery_id = None
                with self._connection:
                    cursor = self._connection.execute(
                        "INSERT OR IGNORE INTO _oracle_watermarks (task_id,watermark,rule_ids,created_at,payload) VALUES (?,?,?,?,?)",
                        (task_id, watermark, json.dumps(rendered["rule_ids"]), time.time(),
                         json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
                    )
                    inserted = cursor.rowcount == 1
                    current_delivery = self._connection.execute(
                        "SELECT watermark FROM _oracle_deliveries WHERE task_id=? ORDER BY id DESC LIMIT 1",
                        (task_id,),
                    ).fetchone()
                    if current_delivery is None or current_delivery[0] != watermark:
                        cursor = self._connection.execute(
                            "INSERT INTO _oracle_deliveries(task_id,watermark) VALUES (?,?)", (task_id, watermark)
                        )
                        delivery_id = cursor.lastrowid
                        self._connection.execute(
                            "UPDATE _oracle_watermarks SET acknowledged=0 WHERE task_id=? AND watermark=?",
                            (task_id, watermark),
                        )
                with self._lock:
                    current = not self._closing and task_id not in self._tripped and self._pending.get(task_id) is event
                    if current:
                        self._pending.pop(task_id, None)
                if not current and (inserted or delivery_id is not None):
                    with self._connection:
                        if delivery_id is not None:
                            self._connection.execute("DELETE FROM _oracle_deliveries WHERE id=?", (delivery_id,))
                        if inserted:
                            self._connection.execute("DELETE FROM _oracle_watermarks WHERE task_id=? AND watermark=?", (task_id, watermark))
                        else:
                            self._connection.execute('''UPDATE _oracle_watermarks SET acknowledged=NOT EXISTS (
                                SELECT 1 FROM _oracle_deliveries WHERE task_id=? AND watermark=? AND acknowledged=0)
                                WHERE task_id=? AND watermark=?''', (task_id, watermark, task_id, watermark))
            rows = self._connection.execute(
                """SELECT d.id,d.task_id,d.watermark,w.payload FROM _oracle_deliveries d
                   JOIN _oracle_watermarks w ON w.task_id=d.task_id AND w.watermark=d.watermark
                   WHERE d.acknowledged=0 ORDER BY d.id"""
            ).fetchall()
            outputs = []
            for delivery_id, task_id, watermark, payload in rows:
                with self._lock:
                    # Replayed payloads have already passed their original
                    # debounce, but a fresh event starts another quiet window.
                    if self._closing or task_id in self._tripped or task_id in self._pending:
                        continue
                output = json.loads(payload)
                if not isinstance(output, dict) or output.get("task_id") != task_id or output.get("watermark") != watermark:
                    raise RuntimeError("Oracle outbox identity integrity check failed")
                material = json.dumps({"task_id": task_id, "xml": output.get("xml"), "rule_ids": output.get("rule_ids")},
                                      sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if hashlib.sha256(material).hexdigest() != watermark:
                    raise RuntimeError("Oracle outbox watermark integrity check failed")
                outputs.append({**output, "delivery_id": delivery_id})
            with self._lock:
                return [output for output in outputs if not self._closing
                        and output["task_id"] not in self._tripped
                        and output["task_id"] not in self._pending]

    def ack(self, task_id: str, watermark: str, delivery_id: int | None = None) -> None:
        """Acknowledge only after the controller durably stores this context.

        Repeated acknowledgments are idempotent. A delivery ID prevents an old
        acknowledgment from consuming a later reactivation of the same rules.
        The legacy two-argument form is accepted only for unambiguous history.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a nonempty string")
        if not isinstance(watermark, str) or len(watermark) != 64 or any(char not in "0123456789abcdef" for char in watermark):
            raise ValueError("watermark must be a lowercase SHA-256 hex digest")
        if delivery_id is not None and (type(delivery_id) is not int or delivery_id <= 0):
            raise ValueError("delivery_id must be a positive integer")
        with self._drain_lock:
            with self._lock:
                self._require_open()
            with self._connection:
                if delivery_id is None:
                    identifiers = self._connection.execute(
                        "SELECT id FROM _oracle_deliveries WHERE task_id=? AND watermark=?", (task_id, watermark)
                    ).fetchall()
                    if len(identifiers) > 1:
                        raise ValueError("Reactivated context acknowledgment requires its delivery_id")
                    delivery_id = identifiers[0][0] if identifiers else None
                cursor = self._connection.execute(
                    "UPDATE _oracle_deliveries SET acknowledged=1 WHERE id=? AND task_id=? AND watermark=?",
                    (delivery_id, task_id, watermark),
                )
                if cursor.rowcount != 1:
                    raise KeyError("Oracle context watermark was not generated for this task")
                self._connection.execute('''UPDATE _oracle_watermarks SET acknowledged=1
                    WHERE task_id=? AND watermark=? AND NOT EXISTS (
                        SELECT 1 FROM _oracle_deliveries WHERE task_id=? AND watermark=? AND acknowledged=0)''',
                    (task_id, watermark, task_id, watermark))

    def close(self) -> None:
        if threading.current_thread().name.startswith("oracle-reaper"):
            raise RuntimeError("Oracle.close cannot be called from its reaper callback")
        with self._condition:
            if self._closed:
                return
            if self._closing:
                self._condition.wait_for(lambda: self._closed)
                return
            self._closing = True
            self._condition.wait_for(lambda: self._submissions == 0)
        try:
            self._executor.shutdown(wait=True)
            with self._drain_lock:
                self._connection.close()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()
        errors = self.trip_errors
        if errors:
            raise RuntimeError(f"Oracle reaper callback failed: {errors}")

    def __enter__(self) -> Oracle:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
