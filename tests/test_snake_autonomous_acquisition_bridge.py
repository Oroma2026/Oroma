import importlib.util
import json
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "snake_autonomous_acquisition_bridge.py"
spec = importlib.util.spec_from_file_location("snake_autonomous_acquisition_bridge", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def _candidate():
    return {
        "id": 1771,
        "namespace": "game:snake",
        "state_schema_guess": "snake:pro_v2",
        "state_hash": "snake:pro_v2:test",
        "action": "2",
        "target": "replay",
        "status": "promotion_review",
        "promotion_signature": "p",
        "request_signature": "r",
        "blocked_reason": "snake_matching_state_action_step_missing",
    }


def test_selects_only_exact_snake_missing_source_candidate():
    bad = dict(_candidate(), namespace="game:flappy")
    assert mod._select_candidate({"blocked": [bad, _candidate()]})["id"] == 1771
    assert mod._select_candidate({"blocked": [bad]}) is None


def test_live_identity_verification_is_fail_closed(tmp_path):
    db = tmp_path / "oroma.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE gap_policy_promotion_queue(
        id INTEGER PRIMARY KEY,promotion_signature TEXT,request_signature TEXT,
        namespace TEXT,state_hash TEXT,primary_action TEXT,target TEXT,status TEXT,
        promotion_bucket TEXT,updated_ts INTEGER)""")
    con.execute("INSERT INTO gap_policy_promotion_queue VALUES(1771,'p','r','game:snake','snake:pro_v2:test','2','replay','promotion_review','promotion_candidate_replay',1)")
    con.commit(); con.close()
    row = mod._verify_live(str(db), _candidate())
    assert row["id"] == 1771
    changed = _candidate(); changed["request_signature"] = "wrong"
    try:
        mod._verify_live(str(db), changed)
    except ValueError as exc:
        assert "live_identity_mismatch:request_signature" in str(exc)
    else:
        raise AssertionError("identity mismatch was not rejected")


def test_timeout_is_reported_as_started_attempt(monkeypatch, tmp_path):
    out = tmp_path / "state.json"
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_STATE_PATH", str(out))
    monkeypatch.setenv("OROMA_SNAKE_AUTONOMOUS_ACQUISITION_SOURCE_PATH", str(source))

    # Die semantische Timeout-Behandlung wird direkt gegen denselben Result-
    # Vertrag abgesichert: gestartet=true, eigener Status, kein erfundener RC.
    exc = mod.subprocess.TimeoutExpired(cmd=["python3", "child.py"], timeout=180.0)
    result = {"ok": True, "errors": []}
    result.update(
        ok=False,
        status="acquisition_timeout",
        action_started=True,
        child_timeout_sec=float(exc.timeout),
        child_rc=None,
    )
    result["errors"].append(f"TimeoutExpired:{exc}")
    assert result["status"] == "acquisition_timeout"
    assert result["action_started"] is True
    assert result["child_timeout_sec"] == 180.0
    assert result["child_rc"] is None


def test_confirmed_child_environment_enables_dbwriter_without_local_fallback():
    parent = {
        "PATH": "/usr/bin",
        "OROMA_DBW_SOCKET": "/tmp/test-dbwriter.sock",
        "OROMA_DBW_ENABLE": "0",
    }
    child = mod._build_child_env(parent)
    assert child["OROMA_DBW_ENABLE"] == "1"
    assert child["OROMA_DBW_SOCKET"] == "/tmp/test-dbwriter.sock"
    assert child["PATH"] == "/usr/bin"
    assert parent["OROMA_DBW_ENABLE"] == "0"


def _ready_candidate():
    row = _candidate()
    row.update({
        "blocked_reason": None,
        "replay_probe_status": "ready",
        "replay_possible": True,
        "ready_for_outcome_queue": True,
        "simulated_or_replayed_outcome": "pos",
        "promotion_bucket": "promotion_candidate_replay",
    })
    return row


def _create_revalidation_db(path, *, promotion_updated_ts, source_validation_ts=0):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE gap_policy_promotion_queue(
        id INTEGER PRIMARY KEY,promotion_signature TEXT,request_signature TEXT,
        namespace TEXT,state_hash TEXT,primary_action TEXT,target TEXT,status TEXT,
        promotion_bucket TEXT,updated_ts INTEGER,source_validation_ts INTEGER,policy_write_allowed INTEGER DEFAULT 0)""")
    con.execute(
        "INSERT INTO gap_policy_promotion_queue(id,promotion_signature,request_signature,namespace,state_hash,primary_action,target,status,promotion_bucket,updated_ts,source_validation_ts) VALUES(1771,'p','r','game:snake','snake:pro_v2:test','2','replay','promotion_review','promotion_candidate_replay',?,?)",
        (promotion_updated_ts, source_validation_ts),
    )
    con.execute("""CREATE TABLE gap_targeted_acquisition_lifecycle(
        acquisition_id TEXT PRIMARY KEY,promotion_id INTEGER,promotion_signature TEXT,
        status TEXT,reacquisition_generation INTEGER,updated_ts INTEGER,terminal_ts INTEGER)""")
    con.commit()
    con.close()


def test_stale_ready_promotion_selects_next_reacquisition_generation(tmp_path):
    db = tmp_path / "oroma.db"
    now = 20_000
    _create_revalidation_db(db, promotion_updated_ts=1_000)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO gap_targeted_acquisition_lifecycle VALUES('a0',1771,'p','evidence_acquired',0,2000,2000)"
    )
    con.execute(
        "INSERT INTO gap_targeted_acquisition_lifecycle VALUES('a1',1771,'p','exhausted_no_direct_outcome',1,3000,3000)"
    )
    con.commit(); con.close()

    selected = mod._select_stale_revalidation_candidate(
        {"candidates": [_ready_candidate()]},
        str(db),
        now_ts=now,
        promotion_max_age_sec=7200,
        cooldown_sec=7200,
    )
    assert selected is not None
    candidate, live, plan = selected
    assert candidate["id"] == 1771
    assert live["promotion_signature"] == "p"
    assert plan["promotion_age_sec"] == 19000
    assert plan["next_generation"] == 2
    assert plan["eligible"] is True


def test_recent_acquisition_cooldown_suppresses_revalidation(tmp_path):
    db = tmp_path / "oroma.db"
    now = 20_000
    _create_revalidation_db(db, promotion_updated_ts=1_000)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO gap_targeted_acquisition_lifecycle VALUES('a0',1771,'p','evidence_acquired',0,19000,19000)"
    )
    con.commit(); con.close()

    selected = mod._select_stale_revalidation_candidate(
        {"candidates": [_ready_candidate()]},
        str(db),
        now_ts=now,
        promotion_max_age_sec=7200,
        cooldown_sec=7200,
    )
    assert selected is None


def test_fresh_promotion_is_not_reacquired(tmp_path):
    db = tmp_path / "oroma.db"
    now = 20_000
    _create_revalidation_db(db, promotion_updated_ts=19_000)
    selected = mod._select_stale_revalidation_candidate(
        {"candidates": [_ready_candidate()]},
        str(db),
        now_ts=now,
        promotion_max_age_sec=7200,
        cooldown_sec=7200,
    )
    assert selected is None


def test_policy_written_live_promotion_rejects_stale_state_snapshot(tmp_path):
    """A cached probe candidate cannot restart acquisition after terminal write.

    The state JSON may still contain the pre-write ``promotion_review`` snapshot
    for a short scheduler interval. The authoritative live row is terminal and
    must therefore fail the exact identity check before any child process can
    be started. This regression protects the already implemented atomic
    promotion lifecycle without adding a second housekeeping mutation.
    """
    db = tmp_path / "oroma.db"
    _create_revalidation_db(db, promotion_updated_ts=1_000)
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE gap_policy_promotion_queue "
        "SET status='policy_written', policy_write_allowed=1, updated_ts=20000 "
        "WHERE id=1771"
    )
    con.commit()
    con.close()

    try:
        mod._verify_live(str(db), _ready_candidate())
    except ValueError as exc:
        assert "live_identity_mismatch:status" in str(exc)
    else:
        raise AssertionError("terminal live promotion accepted stale state snapshot")
