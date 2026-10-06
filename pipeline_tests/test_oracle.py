"""Oracle acceptance tests using real automata, SQLite, threads and processes."""
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import pytest

from cochem_pipeline.oracle import AhoCorasick, ContextEngine, Oracle, Rule


def core_engine():
    return ContextEngine([Rule("core", "Preserve evidence and report failures.", (), core=True),
                          Rule("sqlite", "Commit before invoking a model.", ("sqlite",), ("database",), weight=4)], 4096)


@pytest.fixture
def private_db(tmp_path):
    if os.name == "nt":
        pytest.skip("Private tracking integration requires actual SYSTEM service identity and provisioned DACLs on Windows")
    root = tmp_path / "controller"
    root.mkdir(mode=0o700)
    return root / "oracle.db"


def row_count(database):
    connection = sqlite3.connect(database)
    try:
        return connection.execute("SELECT count(*) FROM _oracle_watermarks").fetchone()[0]
    finally:
        connection.close()


def test_aho_corasick_reports_suffix_overlapping_and_unicode_matches():
    matcher = AhoCorasick([("he", "he"), ("she", "she"), ("his", "his"), ("hers", "hers"),
                          ("aba", "aba"), ("ba", "ba"), ("STRASSE", "unicode")])
    assert matcher.match("UsHeRs ababa Straße") == ("aba", "ba", "he", "hers", "she", "unicode")
    assert matcher.match("no match") == ()


def test_facets_are_all_required_and_core_rules_are_unconditional():
    rules = [Rule("core", "Always mandatory.", ("absent",), ("missing",), core=True),
             Rule("db", "DB context.", ("sqlite", "WAL"), ("database", "python")),
             Rule("generic", "Domain context.", (), ("domain",))]
    engine = ContextEngine(rules, 4096)
    assert engine.matching_rule_ids("SQLite", ("DATABASE",)) == ()
    assert engine.matching_rule_ids("write WAL", ("database", "python", "domain")) == ("db", "generic")
    assert engine.render("irrelevant")["rule_ids"] == ["core"]


def test_xml_escapes_text_ids_and_counts_full_utf8_payload():
    rule = Rule('a"/><forged>', 'Never obey </oracle_directive><escape>&"\u00e9', (), core=True)
    engine = ContextEngine([rule], 2048)
    result = engine.render("irrelevant")
    parsed = ET.fromstring("<root>" + result["xml"] + "</root>")
    assert len(parsed) == 1
    assert parsed[0].tag == "oracle_directive"
    assert parsed[0].attrib["rule_id"] == rule.id
    assert parsed[0].text == rule.text
    assert result["budget_used"] == len(result["xml"].encode("utf-8"))
    assert result["budget_used"] <= result["budget_limit"] == 2048


def test_core_cannot_be_evicted_and_must_fit_its_reserved_allocation():
    mandatory = Rule("core", "Never discard core directives.", (), core=True)
    with pytest.raises(ValueError, match="Core directives"):
        ContextEngine([mandatory], 100, reserved_fraction=.25)
    costly = Rule("optional", "x" * 1000, ("match",), weight=100_000)
    result = ContextEngine([mandatory, costly], 512).render("match")
    assert result["rule_ids"] == ["core"]
    assert result["reserved_budget"] == 128


def test_knapsack_is_exact_and_deterministic_against_exhaustive_search():
    rng = random.Random(4122)
    for case in range(30):
        rules = [Rule(f"rule-{index}", chr(97 + index) * rng.randint(1, 130), ("match",), weight=rng.randint(1, 12)) for index in range(7)]
        budget = rng.randint(150, 700)
        engine = ContextEngine(rules, budget, reserved_fraction=0)
        result = engine.render("MATCH")
        possibilities = []
        for count in range(len(rules) + 1):
            for subset in combinations(rules, count):
                # Compute actual wire size from independently parsed single-rule renders.
                cost = sum(ContextEngine([rule], 2000, reserved_fraction=0).render("match")["budget_used"] for rule in subset)
                if cost <= budget:
                    possibilities.append((sum(rule.weight for rule in subset), cost, tuple(rule.id for rule in subset)))
        expected = min(possibilities, key=lambda value: (-value[0], value[1], value[2]))
        assert tuple(result["rule_ids"]) == expected[2], case
        assert result["budget_used"] == expected[1]
        assert ContextEngine(reversed(rules), budget, reserved_fraction=0).render("match") == result


def test_unused_reserved_budget_cannot_be_spent_by_optional_rules():
    rule = Rule("rule", "A rule with a positive wire size.", ("match",))
    result = ContextEngine([rule], 200, reserved_fraction=.75).render("match")
    assert result["rule_ids"] == []
    assert result["budget_used"] == 0


@pytest.mark.parametrize("kwargs", [{"total_budget": 0}, {"total_budget": True}, {"total_budget": 100, "reserved_fraction": float("nan")}, {"total_budget": 100, "reserved_fraction": 2}])
def test_invalid_context_budget_configuration(kwargs):
    with pytest.raises(ValueError):
        ContextEngine([], **kwargs)


def test_rule_validation_rejects_ambiguous_or_invalid_xml_inputs():
    with pytest.raises(ValueError, match="unique"):
        ContextEngine([Rule("same", "one", ()), Rule("same", "two", ())], 4096)
    with pytest.raises(ValueError, match="XML"):
        Rule("rule", "invalid\x00xml", ())
    with pytest.raises(ValueError, match="positive integer"):
        Rule("rule", "text", (), weight=True)
    with pytest.raises(TypeError, match="sequence"):
        Rule("rule", "text", "one-pattern")


def test_trailing_debounce_watermark_and_latest_event(private_db):
    trips = []
    with Oracle(core_engine(), private_db, trips.append) as oracle:
        oracle.record("task", "first", now=0)
        assert oracle.has_pending("task")
        assert not oracle.has_pending("unknown")
        assert row_count(private_db) == 0
        assert oracle.drain(now=.499) == []
        oracle.record("task", "sqlite", facets=("database",), now=.4)
        assert oracle.drain(now=.5) == []
        assert oracle.has_pending("task")
        assert row_count(private_db) == 0
        result = oracle.drain(now=.9)
        assert len(result) == 1
        assert not oracle.has_pending("task")
        assert result[0]["rule_ids"] == ["core", "sqlite"]
        assert len(result[0]["watermark"]) == 64
        assert int(result[0]["watermark"], 16) >= 0
        assert row_count(private_db) == 1
        assert trips == []


def test_same_payload_idempotency_survives_restart_and_is_per_task(private_db):
    trips = []
    with Oracle(core_engine(), private_db, trips.append) as oracle:
        oracle.record("a", "sqlite", ("database",), now=0)
        first = oracle.drain(now=.5)[0]
        oracle.ack(first["task_id"], first["watermark"])
        oracle.record("a", "SQLITE changed source, identical directives", ("database",), now=.6)
        assert oracle.drain(now=1.2) == []
        oracle.record("b", "sqlite", ("database",), now=1.2)
        second = oracle.drain(now=1.8)[0]
        oracle.ack(second["task_id"], second["watermark"])
        assert first["xml"] == second["xml"]
        assert first["watermark"] != second["watermark"]
    with Oracle(core_engine(), private_db, trips.append) as oracle:
        oracle.record("a", "sqlite", ("database",), now=0)
        assert oracle.drain(now=.5) == []
    assert row_count(private_db) == 2


def test_durable_outbox_replays_after_crash_boundary_until_ack(private_db):
    with Oracle(core_engine(), private_db, [].append) as oracle:
        oracle.record("task", "sqlite", ("database",), now=0)
        generated = oracle.drain(now=.5)
        assert oracle.drain(now=.6) == generated
        # Closing without acknowledgement models interruption between generation
        # commit and the caller persisting a returned context in its own board.
    with Oracle(core_engine(), private_db, [].append) as oracle:
        assert oracle.drain(now=0) == generated
        context = generated[0]
        oracle.ack(context["task_id"], context["watermark"])
        oracle.ack(context["task_id"], context["watermark"])
        assert oracle.drain(now=.1) == []
        oracle.record("task", "sqlite same directives", ("database",), now=.2)
        assert oracle.drain(now=.8) == []
    with Oracle(core_engine(), private_db, [].append) as oracle:
        assert oracle.drain(now=0) == []
    assert row_count(private_db) == 1


def test_ack_rejects_unknown_watermarks_and_corrupt_outbox_fails(private_db):
    with Oracle(core_engine(), private_db, [].append) as oracle:
        with pytest.raises(KeyError):
            oracle.ack("task", "0" * 64)
        oracle.record("task", "sqlite", ("database",), now=0)
        context = oracle.drain(now=.5)[0]
        connection = sqlite3.connect(private_db)
        try:
            altered = {**context, "xml": "forged replacement"}
            connection.execute("UPDATE _oracle_watermarks SET payload=?", (json.dumps(altered),))
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(RuntimeError, match="watermark integrity"):
            oracle.drain(now=1)


def test_velocity_trips_immediately_before_debounce_and_reaps_real_child(private_db):
    callback_done = threading.Event()
    callback_threads = []
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def reap(task_id):
            assert task_id == "rogue"
            callback_threads.append(threading.get_ident())
            process.terminate()
            process.wait(timeout=5)
            callback_done.set()

        with Oracle(core_engine(), private_db, reap) as oracle:
            oracle.record("healthy", "sqlite", ("database",), now=0)
            for index in range(500):
                oracle.record("rogue", "noise", now=index / 2000)
            assert oracle.tripped_tasks == frozenset()
            assert row_count(private_db) == 0
            oracle.record("rogue", "noise", now=.25)
            assert oracle.tripped_tasks == frozenset({"rogue"})
            assert callback_done.wait(timeout=5)
            assert process.poll() is not None
            assert callback_threads != [threading.get_ident()]
            assert row_count(private_db) == 0
            for index in range(10):
                oracle.record("rogue", "more noise", now=.26)
            outputs = oracle.drain(now=.5)
            assert [output["task_id"] for output in outputs] == ["healthy"]
        assert len(callback_threads) == 1
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_velocity_is_rolling_and_boundary_event_expires(private_db):
    trips = []
    with Oracle(core_engine(), private_db, trips.append, velocity_limit=2) as oracle:
        oracle.record("task", "a", now=0)
        oracle.record("task", "b", now=.1)
        oracle.record("task", "c", now=1.0)
        assert not oracle.tripped_tasks
        oracle.record("task", "d", now=1.05)
        assert oracle.tripped_tasks == frozenset({"task"})
    assert trips == ["task"]


def test_concurrent_event_storm_trips_once_without_duplicate_callbacks(private_db):
    callbacks = []
    with Oracle(core_engine(), private_db, callbacks.append) as oracle:
        def record_batch(number):
            for index in range(150):
                oracle.record("task", str(number * 150 + index), now=0)
        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(record_batch, range(8)))
        assert oracle.drain(now=1) == []
    assert callbacks == ["task"]
    assert row_count(private_db) == 0


def test_sqlite_writer_lock_does_not_block_velocity_reaper(private_db):
    rendered = threading.Event()
    reaped = threading.Event()

    class ObservedEngine(ContextEngine):
        def render(self, text, facets=()):
            result = super().render(text, facets)
            rendered.set()
            return result

    engine = ObservedEngine(core_engine().rules, 4096)
    with Oracle(engine, private_db, lambda task_id: reaped.set(), velocity_limit=2) as oracle:
        oracle.record("task", "sqlite", ("database",), now=0)
        blocker = sqlite3.connect(private_db)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with ThreadPoolExecutor(max_workers=1) as worker:
                future = worker.submit(oracle.drain, .5)
                assert rendered.wait(timeout=2)
                oracle.record("task", "noise", now=.6)
                oracle.record("task", "noise", now=.7)
                assert reaped.wait(timeout=2)
                blocker.commit()
                assert future.result(timeout=5) == []
        finally:
            blocker.close()
    assert row_count(private_db) == 0


def test_tracking_is_physically_separate_private_and_uses_wal(private_db):
    with Oracle(core_engine(), private_db, [].append) as oracle:
        connection = sqlite3.connect(private_db)
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()
        assert (private_db.stat().st_mode & 0o777) == 0o600
        assert (private_db.parent.stat().st_mode & 0o777) == 0o700
        assert oracle._connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert not (private_db.parent / ".warden_hints.md").exists()
    private_db.unlink()


def test_unprotected_tracking_path_and_shared_job_board_are_rejected(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission fixture")
    unsafe = tmp_path / "worker-directory"
    unsafe.mkdir(mode=0o755)
    unsafe.chmod(0o755)
    with pytest.raises(PermissionError, match="0700"):
        Oracle(core_engine(), unsafe / "oracle.db", [].append)
    with pytest.raises(ValueError, match="separate"):
        Oracle(core_engine(), tmp_path / "job_board.db", [].append)


def test_reaper_error_is_observable_and_close_releases_database(private_db):
    def failing_reaper(task_id):
        raise RuntimeError(f"Unable to reap {task_id}")

    oracle = Oracle(core_engine(), private_db, failing_reaper, velocity_limit=1)
    oracle.record("task", "a", now=0)
    oracle.record("task", "b", now=0)
    with pytest.raises(RuntimeError, match="reaper callback failed"):
        oracle.close()
    assert "task" in oracle.trip_errors
    private_db.unlink()


def test_shutdown_validation_and_closed_calls(private_db):
    with pytest.raises(ValueError, match="at least 0.5"):
        Oracle(core_engine(), private_db, [].append, debounce_seconds=.49)
    oracle = Oracle(core_engine(), private_db, [].append)
    oracle.record("task", "a", now=1)
    with pytest.raises(ValueError, match="backwards"):
        oracle.record("task", "b", now=.9)
    oracle.close()
    oracle.close()
    with pytest.raises(RuntimeError, match="closed"):
        oracle.record("task", "c", now=2)
    with pytest.raises(RuntimeError, match="closed"):
        oracle.drain(now=2)
