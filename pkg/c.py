        z = 3
        ''')
    parsed = twl.parse_file_blocks(text)
    files = {f.path: f.content for f in parsed.files}
    assert files["pkg/a.py"] == "x = 1\n"                             # inner fence stripped
    assert files["docs/readme.md"].count("```") == 2                  # nested fences kept
    assert files["pkg/b.py"] == "y = 2\n"                             # fenced fallback
    assert "pkg/c.py" not in files                                    # truncated -> not written
    assert any("pkg/c.py" in m for m in parsed.malformed)
    assert parsed.prose.startswith("Summary first.")


def test_resolve_workspace_path_policy(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert twl.resolve_workspace_path(ws, "src\\mod.py") == (ws / "src" / "mod.py").resolve()
    assert twl.resolve_workspace_path(ws, str(ws / "a.py")) == (ws / "a.py").resolve()
    for bad in ("../escape.py", str(tmp_path / "outside.py"), ".git/config", ".env",
                "pkg/__pycache__/x.pyc", "", "."):
        with pytest.raises(ValueError):
            twl.resolve_workspace_path(ws, bad)


def test_apply_artifacts_writes_rejects_and_diffs(tmp_path):
    ws = tmp_path
    existing = ws / "mod.py"
    existing.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    same = ws / "same.py"
    same.write_text("keep\n", encoding="utf-8")
    protected = ws / "tests" / "test_mod.py"
    protected.parent.mkdir()
    protected.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    parsed = twl.parse_file_blocks(
        "<<<FILE: mod.py>>>\na = 1\nb = 20\nc = 3\nd = 4\n<<<END FILE>>>\n"
        "<<<FILE: same.py>>>\nkeep\n<<<END FILE>>>\n"
        "<<<FILE: new/pkg.py>>>\nz = 0\n<<<END FILE>>>\n"
        "<<<FILE: tests/test_mod.py>>>\nhacked\n<<<END FILE>>>\n"
        "<<<FILE: ../../evil.py>>>\nx\n<<<END FILE>>>\n")
    tracked = [existing, same, protected, ws / "new" / "pkg.py"]
    pre = twl.snapshot(tracked)
    res = twl.apply_artifacts(ws, parsed, protected=frozenset({protected}))
    post = twl.snapshot(tracked)
    modified, diff_lines, per_file = twl.compute_changes(pre, post)

    assert protected.read_text(encoding="utf-8").startswith("def test_x")
    assert {Path(p).name for p in res.written} == {"mod.py", "pkg.py"}
    reasons = dict(res.rejected)
    assert "protected" in reasons["tests/test_mod.py"]
    assert "escapes" in reasons["../../evil.py"]
    assert {Path(p).name for p in modified} == {"mod.py", "pkg.py"}
    assert per_file[str(existing)] == 3          # -b=2, +b=20, +d=4
    assert per_file[str(ws / "new" / "pkg.py")] == 1
    assert diff_lines == 4
    assert not list(ws.rglob("*.tmp"))           # atomic writes leave no temp files


def test_lf_reemission_of_crlf_file_is_not_a_change(tmp_path):
    f = tmp_path / "crlf.py"
    f.write_bytes(b"a = 1\r\nb = 2\r\n")
    pre = twl.snapshot([f])
    res = twl.apply_artifacts(tmp_path, twl.parse_file_blocks(
        "<<<FILE: crlf.py>>>\na = 1\nb = 2\n<<<END FILE>>>\n"))
    assert res.written == []
    assert twl.compute_changes(pre, twl.snapshot([f]))[1] == 0
    twl.apply_artifacts(tmp_path, twl.parse_file_blocks(
        "<<<FILE: crlf.py>>>\na = 1\nb = 3\n<<<END FILE>>>\n"))
    assert f.read_bytes() == b"a = 1\r\nb = 3\r\n"                   # convention preserved
    assert twl.compute_changes(pre, twl.snapshot([f]))[1] == 2


def test_atomic_write_preserves_exact_bytes(tmp_path):
    target = tmp_path / "deep" / "f.txt"
    twl.atomic_write_text(target, "line1\nline2 é\n")
    assert target.read_bytes() == "line1\nline2 é\n".encode("utf-8")


# ── P0.4 deterministic test runner ──────────────────────────────────────────
# _SAMPLE_TESTS is a string payload written to tmp_path and executed by a real
# pytest subprocess to verify junit parsing (including skip accounting). It is
# data, not a directive applied to this suite.

_SAMPLE_TESTS = '''
import pytest

def helper():
    return {}["missing"]

def test_pass():
    assert 1 + 1 == 2

def test_assert_fail():
    assert 1 == 2

def test_traceback():
    helper()

@pytest.mark.skip(reason="skip")
def test_skipped():
    pass
'''


def test_run_pytest_real_subprocess_counts_and_signature(tmp_path):
    tf = tmp_path / "test_sample.py"
    tf.write_text(_SAMPLE_TESTS, encoding="utf-8")
    rep = twl.run_pytest([tf], tmp_path, tmp_path / "junit1.xml", timeout=120)
    assert (rep.total, rep.passed, rep.failed, rep.skipped) == (4, 1, 2, 1)
    assert rep.pass_rate == pytest.approx(0.25)              # skips count against pass_rate
    assert rep.has_tracebacks is True                        # KeyError is not an assertion
    assert rep.returncode == 1 and rep.is_red
    tb_id = next(t for t in rep.failing_ids if t.endswith("test_traceback"))
    assert rep.top_frames[tb_id].endswith("KeyError")
    af_id = next(t for t in rep.failing_ids if t.endswith("test_assert_fail"))
    assert rep.top_frames[af_id].endswith("AssertionError")

    # Same failures after an unrelated line shift -> identical signature.
    tf.write_text("\n\n# shifted\n" + _SAMPLE_TESTS, encoding="utf-8")
    rep2 = twl.run_pytest([tf], tmp_path, tmp_path / "junit2.xml", timeout=120)
    assert rep2.signature_hash == rep.signature_hash and rep.signature_hash


def test_run_pytest_all_green_and_import_error_red(tmp_path):
    green = tmp_path / "test_green.py"
    green.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    rep = twl.run_pytest([green], tmp_path, tmp_path / "g.xml", timeout=120)
    assert rep.pass_rate == 1.0 and not rep.is_red and rep.signature_hash == ""

    red = tmp_path / "test_red.py"
    red.write_text("import module_that_does_not_exist_v2\n\ndef test_x():\n    assert True\n",
                   encoding="utf-8")
    rep = twl.run_pytest([red], tmp_path, tmp_path / "r.xml", timeout=120)
    assert rep.is_red and rep.errored >= 1 and rep.collection_error and rep.pass_rate == 0.0


def test_run_pytest_timeout(tmp_path):
    slow = tmp_path / "test_slow.py"
    slow.write_text("import time\n\ndef test_slow():\n    time.sleep(60)\n", encoding="utf-8")
    rep = twl.run_pytest([slow], tmp_path, tmp_path / "s.xml", timeout=8)
    assert rep.timed_out and rep.is_red and rep.pass_rate == 0.0


# ── Progress gate (pure Python) ─────────────────────────────────────────────

def rec(i, pr, s, *, verdict="FAIL", f=4, blk=0, d=10, sig="", ev=1, asym=True, tb=False):
    return twl.ProgressRecord(iteration=i, phase="P10" if i else "P6", pass_rate=pr, audit_score=s,
                              verdict=verdict, weighted_findings=f, blocking_findings=blk,
                              diff_lines=d, signature_hash=sig, evidence_files=ev,
                              asymmetry_ok=asym, has_tracebacks=tb)


def test_gate_accepts_early_at_stage_b():
    g = twl.progress_gate([rec(0, 1.0, 90, verdict="PASS", f=1)])
    assert g.decision == twl.DECISION_ACCEPT


def test_gate_accept_blocked_by_asymmetry_or_blockers_or_verdict():
    assert twl.progress_gate([rec(0, 1.0, 95, verdict="PASS", asym=False)]).decision == twl.DECISION_CONTINUE
    assert twl.progress_gate([rec(0, 1.0, 95, verdict="PASS", blk=1)]).decision == twl.DECISION_CONTINUE
    assert twl.progress_gate([rec(0, 1.0, 95, verdict="FAIL")]).decision == twl.DECISION_CONTINUE
    assert twl.progress_gate([rec(0, 0.9, 95, verdict="PASS")]).decision == twl.DECISION_CONTINUE


def test_gate_zero_diff_pivots_immediately():
    g = twl.progress_gate([rec(0, 0.5, 60), rec(1, 0.9, 80, d=0)])
    assert g.decision == twl.DECISION_PIVOT and "zero diff" in g.reason


def test_gate_two_consecutive_stalls_pivot():
    records = [rec(0, 0.5, 60, sig="s"), rec(1, 0.5, 61, sig="s"), rec(2, 0.5, 62, sig="s")]
    g = twl.progress_gate(records[:2])
    assert records[1].stalled and g.decision == twl.DECISION_CONTINUE
    g = twl.progress_gate(records)
    assert g.decision == twl.DECISION_PIVOT and "consecutive" in g.reason


def test_gate_improvement_resets_stall_and_hard_cap_pivots():
    records = [rec(0, 0.2, 40), rec(1, 0.2, 41), rec(2, 0.5, 50), rec(3, 0.8, 70)]
    for n in range(2, 4):
        twl.progress_gate(records[:n])
    assert records[1].stalled and not records[2].stalled
    g = twl.progress_gate(records)
    assert not records[3].stalled
    assert g.decision == twl.DECISION_PIVOT and "hard cap" in g.reason


def test_gate_same_signature_without_gain_is_a_stall_despite_score():
    r = [rec(0, 0.5, 60, sig="abc", f=6), rec(1, 0.5, 70, sig="abc", f=6)]
    twl.progress_gate(r)
    assert r[1].stalled


def test_gate_oscillation_is_a_stall():
    r = [rec(0, 0.4, 50), rec(1, 0.6, 50), rec(2, 0.5, 60, f=0)]
    twl.progress_gate(r[:2])
    twl.progress_gate(r)
    assert not r[1].stalled and r[2].stalled


def test_gate_escalation_band_once():
    assert twl.progress_gate([rec(0, 1.0, 78)]).decision == twl.DECISION_ESCALATE
    assert twl.progress_gate([rec(0, 1.0, 78)], escalation_used=True).decision == twl.DECISION_CONTINUE
    assert twl.progress_gate([rec(0, 1.0, 69)]).decision == twl.DECISION_CONTINUE


def test_gate_quarantine_candidate_and_adjudication_once():
    assert twl.progress_gate([rec(0, 0.0, 0, verdict="SPOOFING_DETECTED")]).decision == \
        twl.DECISION_QUARANTINE_CANDIDATE
    assert twl.progress_gate([rec(0, 0.0, 0, ev=0)]).decision == twl.DECISION_QUARANTINE_CANDIDATE
    assert twl.progress_gate([rec(0, 0.0, 0, ev=0)], adjudication_used=True).decision == \
        twl.DECISION_CONTINUE
    assert twl.progress_gate([rec(0, 1.0, 90, verdict="PASS", ev=0)], mutating=False).decision == \
        twl.DECISION_ACCEPT


def test_gate_tracebacks_request_debug():
    g = twl.progress_gate([rec(0, 0.3, 40, tb=True)])
    assert g.decision == twl.DECISION_CONTINUE and g.debug


def test_gate_requires_records():
    with pytest.raises(ValueError):
        twl.progress_gate([])


# ── P0.3 TaskContext persistence / resume / terminal short-circuit ──────────

def _write_task(tmp_path, prompt="Implement adder", task_id="9.9"):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    task_file = tmp_path / f"{task_id}_prompt.json"
    task_file.write_text(json.dumps({"task_id": task_id, "prompt": prompt, "workspace": str(ws),
                                     "agent_name": "cochem-coder"}), encoding="utf-8")
    return task_file, ws


def test_task_context_roundtrip_and_prompt_change_archives(tmp_path):
    original_runs_dir = twl.TDD_RUNS_DIR
    with _scoped_module_attr(twl, "TDD_RUNS_DIR", tmp_path / "runs"):
        task_file, ws = _write_task(tmp_path)
        raw = json.loads(task_file.read_text(encoding="utf-8"))
        ctx = twl.TaskContext.create_or_resume(str(task_file), raw)
        assert ctx.test_file == ws.resolve() / "tests" / "tdd" / "test_task_9_9.py"
        assert ctx.test_rel == "tests/tdd/test_task_9_9.py"
        ctx.plan = twl.Plan(goal="g", acceptance_criteria=[twl.AcceptanceCriterion(id="AC1", statement="s")],
                            test_cases=[twl.PlannedTestCase(id="T1", name="test_a")], file_targets=["adder.py"])
        ctx.dossier = twl.Dossier(text="d", sources=["x.py"])
        ctx.audit = twl.AuditResult(verdict="FAIL", score=55, findings=[twl.Finding(severity="LOW", issue="i")])
        ctx.test_report = twl.TestReport(total=2, passed=1, failed=1, failing_ids=["t::x"])
        ctx.progress.append(rec(0, 0.5, 55))
        ctx.producer_models.add(("claude-subscription", "claude-opus-5-5"))
        ctx.evidence_files.add(str(ws / "adder.py"))
        ctx.decision = twl.GateDecision(twl.DECISION_CONTINUE, "r", debug=True)
        ctx.save()

        again = twl.TaskContext.create_or_resume(str(task_file), raw)
        assert again.plan == ctx.plan and again.audit == ctx.audit
        assert again.test_report.pass_rate == 0.5 and again.progress[0].audit_score == 55
        assert again.producer_models == {("claude-subscription", "claude-opus-5-5")}
        assert again.decision.debug is True

        raw2 = dict(raw, prompt="Implement subtractor")
        fresh = twl.TaskContext.create_or_resume(str(task_file), raw2)
        assert fresh.plan is None
        assert any("superseded" in p.name for p in (tmp_path / "runs").iterdir())
    assert twl.TDD_RUNS_DIR == original_runs_dir


@pytest.mark.parametrize("outcome,expected", [
    (twl.OUTCOME_ACCEPTED, True), (twl.OUTCOME_PIVOTED, True),
    (twl.OUTCOME_QUARANTINED, True), (twl.OUTCOME_HARD_ABORT, True)])
def test_process_task_terminal_outcome_short_circuits(tmp_path, outcome, expected):
    """A finished task re-dispatched by main() must return without any LLM call."""
    original_runs_dir = twl.TDD_RUNS_DIR
    with _scoped_module_attr(twl, "TDD_RUNS_DIR", tmp_path / "runs"):
        task_file, _ = _write_task(tmp_path)
        raw = json.loads(task_file.read_text(encoding="utf-8"))
        ctx = twl.TaskContext.create_or_resume(str(task_file), raw)
        ctx.outcome, ctx.outcome_reason = outcome, "test"
        ctx.save()
        assert asyncio.run(twl.process_task(str(task_file), {})) is expected
    assert twl.TDD_RUNS_DIR == original_runs_dir
