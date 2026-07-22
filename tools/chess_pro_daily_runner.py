#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess_pro_daily_runner.py
# Projekt: ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:   ChessPro Daily Runner – professionelle 2-Seiten-Schachdomäne
# Version: v0.2.0
# Stand:   2026-06-27
# Autor:   ORÓMA · Jörg Werner · GPT-5.5 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# ─────
#   Führt den neuen professionellen ChessPro-Pfad headless aus. Dieser Runner ist
#   bewusst NICHT `chess2_daily_runner.py` und ersetzt die Chess2-Übergangsbremse
#   als Zielarchitektur:
#
#     • 1 Partie pro Aufruf
#     • Orchestrator startet täglich zwei Aufrufe: ORÓMA-Fokus Weiß und Schwarz
#     • professionelle Bewertungsregeln + Alpha-Beta-Suche
#     • vollständiger Positions-/Move-Trace in SnapChains
#     • Episode + episodic_metrics für Daily Summary
#     • optional UniversalPolicy-Lernen im Namespace `game:chess_pro`
#
# WARUM STELLUNGSORIENTIERT
# ─────────────────────────
#   Schachzüge haben oft erst viele Halbzüge später Wirkung. Deshalb speichert
#   ChessPro jede Entscheidung inklusive FEN, Feature-Vektor, Regelhits und
#   Suchtelemetrie. ORÓMA kann daraus später NMR-Ähnlichkeit, Replay und Dream-
#   Variationen bauen. Der terminale Reward wird nicht mehr nur auf einen letzten
#   Zug reduziert.
#
# PRODUKTIONSINVARIANTEN
# ──────────────────────
#   • Headless: keine GUI/pygame/OpenCV-Abhängigkeit.
#   • DBWriter-kompatibel: Writes nur über core.sql_manager und UniversalPolicy.
#   • Kein lokaler SQLite-Bypass, keine offenen Connections.
#   • Fehler sichtbar via stderr/JSON ok=false.
#
# v0.2.0 LONG SEARCH + LERNLOOP-KONTROLLE
# ───────────────────────────────────────
#   Der produktive Tageslauf bleibt bewusst auf zwei Partien begrenzt, darf aber
#   pro Partie deutlich länger rechnen. Zusätzlich wird der Lernloop kontrolliert:
#   Remis- oder Budget-Abbrüche erzeugen nicht nur neutrale Draw-Zähler, sondern
#   pro Zug ein schwellenbasiertes, stellungsbezogenes Lernsignal. Dadurch haben
#   lange Berechnungen unmittelbaren Nutzen für `policy_rules`, SnapChains, NMR
#   und spätere Dream-Replays.
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional

# Direkter Skriptstart aus systemd/SSH robust machen.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.chess_pro.rules import WHITE, BLACK, ChessPosition
from core.chess_pro import ChessProEncoder, ChessProSearch, ChessProTrace, ProfessionalEvaluator
from core import sql_manager


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.environ.get(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.environ.get(name, "") or "").strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "y"}


def _side(value: str) -> str:
    v = (value or "white").strip().lower()
    if v in {"b", "black", "schwarz"}:
        return BLACK
    return WHITE


def _outcome_from_status(status: str, max_plies_hit: bool = False) -> str:
    if status == "checkmate_black":
        return "W"
    if status == "checkmate_white":
        return "B"
    return "D"


def _terminal_reason(pos: ChessPosition, max_plies_hit: bool) -> str:
    if max_plies_hit:
        return "max_plies"
    try:
        return str(pos.status())
    except Exception:
        return "unknown"


def _write_episode(trace: ChessProTrace, summary: Dict[str, Any], no_db: bool = False) -> Optional[int]:
    if no_db:
        return None
    try:
        meta = {
            "namespace": trace.namespace,
            "focus_side": trace.focus_side,
            "outcome": trace.outcome,
            "terminal_reason": trace.terminal_reason,
            "plies": len(trace.decisions),
            "engine": "chess_pro",
            "version": "v0.2.0",
            "learning_mode": summary.get("learning_mode", "terminal_plus_shaped"),
            "game_budget_sec": summary.get("game_budget_sec", 0),
        }
        kind = f"game:chess_pro:{trace.focus_side}:explore_batch"
        eid = sql_manager.insert_episode(
            ts_start=int(trace.ts_start),
            ts_end=int(trace.ts_end or time.time()),
            kind=kind,
            source="chess_pro_daily_runner",
            label=f"ChessPro daily {trace.focus_side}",
            meta=meta,
        )
        if eid:
            ts = int(trace.ts_end or time.time())
            metric_keys = {
                "games": 1,
                "duration_ms": summary.get("duration_ms", 0),
                "plies": summary.get("plies", 0),
                "avg_score": summary.get("final_score_cp", 0),
                "score_cp": summary.get("final_score_cp", 0),
                "nodes": summary.get("nodes", 0),
                "qnodes": summary.get("qnodes", 0),
                "total_nodes": summary.get("total_nodes", 0),
                "search_ms": summary.get("search_ms", 0),
                "nps": summary.get("nps", 0),
                "depth_target": summary.get("depth", 0),
                "depth_reached_max": summary.get("depth_reached_max", 0),
                "depth_reached_avg": summary.get("depth_reached_avg", 0),
                "tt_hits": summary.get("tt_hits", 0),
                "cutoffs": summary.get("cutoffs", 0),
                "timed_out_moves": summary.get("timed_out_moves", 0),
                "game_budget_hit": 1 if summary.get("game_budget_hit", False) else 0,
                "game_budget_sec": summary.get("game_budget_sec", 0),
                "repetition_guard_moves": summary.get("repetition_guard_moves", 0),
                "repetition_penalty_abs_cp": summary.get("repetition_penalty_abs_cp", 0),
                "api_guard_ok": 1 if summary.get("api_guard_ok", True) else 0,
                "push_failures": summary.get("push_failures", 0),
                "pop_failures": summary.get("pop_failures", 0),
                "wins": 1 if trace.outcome in {"W", "B"} else 0,
                "wins_white": 1 if trace.outcome == "W" else 0,
                "wins_black": 1 if trace.outcome == "B" else 0,
                "draws": 1 if trace.outcome == "D" else 0,
                "focus_white": 1 if trace.focus_side == WHITE else 0,
                "focus_black": 1 if trace.focus_side == BLACK else 0,
                "rule_hits": summary.get("rule_hits", 0),
                "learn_items": summary.get("learn_items", 0),
                "learn_items_pos": summary.get("learn_items_pos", 0),
                "learn_items_neg": summary.get("learn_items_neg", 0),
                "learn_items_draw": summary.get("learn_items_draw", 0),
                "shaped_pos": summary.get("shaped_pos", 0),
                "shaped_neg": summary.get("shaped_neg", 0),
                "shaped_draw": summary.get("shaped_draw", 0),
                "snapchain_steps": len(trace.decisions),
            }
            for k, v in metric_keys.items():
                try:
                    sql_manager.insert_episodic_metric(int(eid), ts, str(k), float(v or 0))
                except Exception as e:
                    print(f"[chess_pro_daily_runner] episodic metric failed key={k}: {e!r}", file=sys.stderr)
            try:
                sql_manager.insert_snapchain({
                    "ts": ts,
                    "quality": 1.0,
                    "blob": trace.to_blob_bytes(),
                    "exported": 0,
                    "status": "active",
                    "origin": "game:chess_pro",
                    "gap_flag": 0,
                    "notes": json.dumps({"kind": "chess_pro_trace", "episode_id": int(eid), "focus_side": trace.focus_side}, ensure_ascii=False),
                    "namespace": trace.namespace,
                    "source_id": int(eid),
                    "version": "chess_pro_v0.2.0",
                    "weight": 1.0,
                })
            except Exception as e:
                print(f"[chess_pro_daily_runner] snapchain write failed: {e!r}", file=sys.stderr)
        return int(eid) if eid else None
    except Exception as e:
        print(f"[chess_pro_daily_runner] episode write failed: {e!r}", file=sys.stderr)
        return None


def _learn_items_summary(items: List[Dict[str, Any]]) -> Dict[str, int]:
    pos = neg = draw = 0
    for it in items:
        try:
            out = float(it.get("outcome", 0.0) or 0.0)
        except Exception:
            out = 0.0
        if out > 1e-9:
            pos += 1
        elif out < -1e-9:
            neg += 1
        else:
            draw += 1
    return {"learn_items_pos": int(pos), "learn_items_neg": int(neg), "learn_items_draw": int(draw)}


def _learn_policy(trace: ChessProTrace, no_db: bool = False) -> Dict[str, int]:
    items = trace.learn_items()
    summary = {"learn_items": int(len(items))}
    summary.update(_learn_items_summary(items))
    if no_db or not items:
        return summary
    try:
        from core.universal_policy import Policy  # type: ignore
        pol = Policy(namespace=trace.namespace)
        pol.learn_many(items)
        return summary
    except Exception as e:
        print(f"[chess_pro_daily_runner] policy learn failed: {e!r}", file=sys.stderr)
        return summary


def run_one_game(
    focus_side: str,
    seed: int,
    depth: int,
    max_plies: int,
    time_budget_ms: int,
    eps: float,
    namespace: str,
    game_budget_sec: int = 1680,
) -> Dict[str, Any]:
    rng = random.Random(int(seed))
    evaluator = ProfessionalEvaluator()
    encoder = ChessProEncoder(evaluator=evaluator)
    search = ChessProSearch(evaluator=evaluator)
    pos = ChessPosition()
    trace = ChessProTrace(namespace=namespace, focus_side=focus_side)
    nodes = 0
    qnodes = 0
    search_ms = 0
    depth_reached_values: List[int] = []
    tt_hits = 0
    cutoffs = 0
    timed_out_moves = 0
    repetition_guard_moves = 0
    repetition_penalty_abs_cp = 0
    push_failures = 0
    pop_failures = 0
    api_guard_ok = True
    t0 = time.time()
    max_plies_hit = False
    game_budget_hit = False
    budget_sec = max(1, int(game_budget_sec))

    for ply in range(max(1, int(max_plies))):
        elapsed_sec = time.time() - t0
        if elapsed_sec >= float(budget_sec):
            game_budget_hit = True
            break
        try:
            status = pos.status()
        except Exception:
            status = "ongoing"
        if status != "ongoing":
            break
        legal = pos.generate_legal_moves()
        if not legal:
            break
        enc = encoder.encode(pos, focus_side=pos.stm)
        remaining_ms = int(max(100.0, (float(budget_sec) - (time.time() - t0)) * 1000.0 - 50.0))
        move_budget_ms = min(max(100, int(time_budget_ms)), max(100, int(remaining_ms)))
        result = search.choose(pos, depth=max(1, int(depth)), time_budget_ms=move_budget_ms, eps=max(0.0, float(eps)), rng=rng)
        if result.move is None:
            break
        fen_before = pos.as_fen()
        ok = pos.apply(result.move)
        if not ok:
            # Dieser Pfad sollte nie eintreten, weil Search legale Moves nutzt.
            # Sichtbar abbrechen statt still falsche Traces zu schreiben.
            print(f"[chess_pro_daily_runner] invalid selected move={result.uci} fen={fen_before}", file=sys.stderr)
            break
        fen_after = pos.as_fen()
        ev_after = evaluator.evaluate(pos)
        trace.add_decision(ply=ply, enc=enc, result=result, fen_after=fen_after, legal_count=len(legal), eval_after_cp=int(ev_after.score_cp))
        nodes += int(result.nodes)
        qnodes += int(result.qnodes)
        search_ms += int(result.elapsed_ms)
        depth_reached_values.append(int(getattr(result, "depth_reached", 0) or 0))
        tt_hits += int(getattr(result, "tt_hits", 0) or 0)
        cutoffs += int(getattr(result, "cutoffs", 0) or 0)
        timed_out_moves += 1 if bool(getattr(result, "timed_out", False)) else 0
        repetition_guard_moves += int(getattr(result, "repetition_guard_moves", 0) or 0)
        repetition_penalty_abs_cp += int(getattr(result, "repetition_penalty_abs_cp", 0) or 0)
        push_failures += int(getattr(result, "push_failures", 0) or 0)
        pop_failures += int(getattr(result, "pop_failures", 0) or 0)
        api_guard_ok = bool(api_guard_ok and bool(getattr(result, "api_guard_ok", True)))
    else:
        max_plies_hit = True

    reason = "game_budget" if game_budget_hit else _terminal_reason(pos, max_plies_hit=max_plies_hit)
    try:
        status_final = pos.status()
    except Exception:
        status_final = reason
    outcome = _outcome_from_status(status_final, max_plies_hit=max_plies_hit)
    trace.finish(outcome=outcome, terminal_reason=reason)
    ev_final = evaluator.evaluate(pos)
    dt_ms = int((time.time() - t0) * 1000)
    rule_hits = sum(len(d.rule_hits) for d in trace.decisions)
    total_nodes = int(nodes) + int(qnodes)
    nps = float(total_nodes) / max(0.001, float(search_ms) / 1000.0) if search_ms else 0.0
    depth_reached_max = max(depth_reached_values) if depth_reached_values else 0
    depth_reached_avg = (sum(depth_reached_values) / float(len(depth_reached_values))) if depth_reached_values else 0.0
    shaped = trace.learning_summary()
    return {
        "ok": True,
        "trace": trace,
        "summary": {
            "ok": True,
            "namespace": namespace,
            "focus_side": focus_side,
            "seed": int(seed),
            "depth": int(depth),
            "max_plies": int(max_plies),
            "game_budget_sec": int(budget_sec),
            "game_budget_hit": bool(game_budget_hit),
            "learning_mode": "terminal_plus_shaped_eval_delta",
            "plies": len(trace.decisions),
            "outcome": outcome,
            "terminal_reason": reason,
            "duration_ms": dt_ms,
            "nodes": int(nodes),
            "qnodes": int(qnodes),
            "total_nodes": int(total_nodes),
            "search_ms": int(search_ms),
            "nps": round(float(nps), 2),
            "depth_reached_max": int(depth_reached_max),
            "depth_reached_avg": round(float(depth_reached_avg), 2),
            "tt_hits": int(tt_hits),
            "cutoffs": int(cutoffs),
            "timed_out_moves": int(timed_out_moves),
            "repetition_guard_moves": int(repetition_guard_moves),
            "repetition_penalty_abs_cp": int(repetition_penalty_abs_cp),
            "api_guard_ok": bool(api_guard_ok),
            "push_failures": int(push_failures),
            "pop_failures": int(pop_failures),
            "final_score_cp": int(ev_final.score_cp),
            "rule_hits": int(rule_hits),
            "shaped_pos": int(shaped.get("shaped_pos", 0)),
            "shaped_neg": int(shaped.get("shaped_neg", 0)),
            "shaped_draw": int(shaped.get("shaped_draw", 0)),
            "final_fen": pos.as_fen(),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ORÓMA ChessPro Daily Runner")
    ap.add_argument("--side", choices=["white", "black", "w", "b", "schwarz"], default=os.environ.get("OROMA_CHESS_PRO_SIDE", "white"), help="ORÓMA/Fokus-Seite für diesen Lauf")
    ap.add_argument("--games", type=int, default=_env_int("OROMA_CHESS_PRO_GAMES", 1), help="Produktiv default 1; Orchestrator startet zwei getrennte Läufe")
    ap.add_argument("--depth", type=int, default=_env_int("OROMA_CHESS_PRO_DEPTH", 4))
    ap.add_argument("--max-plies", type=int, default=_env_int("OROMA_CHESS_PRO_MAX_PLIES", 180))
    ap.add_argument("--time-budget-ms", type=int, default=_env_int("OROMA_CHESS_PRO_TIME_BUDGET_MS", 12000))
    ap.add_argument("--game-budget-sec", type=int, default=_env_int("OROMA_CHESS_PRO_GAME_BUDGET_SEC", 1680))
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_CHESS_PRO_EPS", 0.01))
    ap.add_argument("--seed", type=int, default=_env_int("OROMA_CHESS_PRO_SEED", int(time.time()) & 0xFFFFFFFF))
    ap.add_argument("--namespace", default=os.environ.get("OROMA_CHESS_PRO_NAMESPACE", "game:chess_pro"))
    ap.add_argument("--no-db", action="store_true", default=not _env_bool("OROMA_CHESS_PRO_DB", True))
    ap.add_argument("--no-learn", action="store_true", default=not _env_bool("OROMA_CHESS_PRO_LEARN", True))
    args = ap.parse_args(argv)

    focus_side = _side(str(args.side))
    games = max(1, int(args.games))
    outputs: List[Dict[str, Any]] = []
    total_learn = 0
    episode_ids: List[int] = []
    for i in range(games):
        seed = (int(args.seed) + i * 7919) & 0xFFFFFFFF
        result = run_one_game(
            focus_side=focus_side,
            seed=seed,
            depth=max(1, int(args.depth)),
            max_plies=max(1, int(args.max_plies)),
            time_budget_ms=max(100, int(args.time_budget_ms)),
            eps=max(0.0, float(args.eps)),
            namespace=str(args.namespace or "game:chess_pro"),
            game_budget_sec=max(1, int(args.game_budget_sec)),
        )
        trace: ChessProTrace = result["trace"]
        summary: Dict[str, Any] = result["summary"]
        if not args.no_learn:
            learn_summary = _learn_policy(trace, no_db=bool(args.no_db))
            summary.update(learn_summary)
            total_learn += int(learn_summary.get("learn_items", 0))
        else:
            items = trace.learn_items()
            learn_summary = {"learn_items": 0}
            learn_summary.update(_learn_items_summary(items))
            summary.update(learn_summary)
        eid = _write_episode(trace, summary, no_db=bool(args.no_db))
        if eid:
            episode_ids.append(int(eid))
            summary["episode_id"] = int(eid)
        outputs.append(summary)

    out = {
        "ok": True,
        "runner": "chess_pro_daily_runner",
        "version": "v0.2.0",
        "games": games,
        "focus_side": focus_side,
        "episode_ids": episode_ids,
        "learn_items": int(total_learn),
        "results": outputs,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
