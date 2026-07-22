#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/snake3d_ui.py
# Projekt:   ORÓMA – Offline-Realtime-Organic-Memory-AI
# Modul:     Snake3D UI – read-only Policy-/Explore-Status
# Version:   v0.2.0-policy-status-readonly
# Stand:     2026-06-28
# Autor:     ORÓMA · Jörg Werner + GPT-5.5 Thinking
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# Diese UI macht Snake3D wie ein reguläres Spiel unter /snake3d/ sichtbar. Nach
# dem validierten Template-Transfer ist Snake3D ein read-only beobachtbarer
# Policy-/Explore-Runner: `snake3d:pro_v1` ist im Register eingetragen,
# Policy-Games nutzen Safety-/Q-Gates, Explore-Games liefern weiter Z-Achsen-
# Abdeckung.
#
# SICHERHEITS- UND BETRIEBSGRENZEN
# -------------------------------
# - Read-only UI: keine Runner-Starts, keine Shell-Kommandos, keine DB-Writes.
# - Kein Runner-Start aus der UI, kein Orchestrator-Kommando.
# - Keine policy_rules-Änderung, kein DBWriter-Call aus der UI.
# - Die Seite liest nur bestehende `episodes`/`episodic_metrics` und
#   `policy_rules`, damit der Schablonen-Transfer nachvollziehbar bleibt.
# - Das Episode-Lesen ist schema-kompatibel: ältere/neue DB-Stände können
#   `meta`, `meta_json` oder `value` für JSON-Metadaten verwenden.
# - SQLite-Verbindungen laufen über `core.sql_manager.get_conn()` und werden per
#   Context Manager geschlossen.
#
# ANGEZEIGTE KERNFRAGEN
# ---------------------
# - Läuft Snake3D im erwarteten Policy-/Explore-Modus?
# - Wird `snake3d:pro_v1` als konkrete Schablone bestätigt?
# - Wie relevant ist die Z-Achse praktisch (`danger_z_rate`,
#   `food_up_signal_rate`, `vertical_action_rate`)?
# - Schreibt der Runner eventbasierte Pos-/Neg-Regeln ohne Draw-Müll?
#
# ROUTEN
# ------
#   GET /snake3d/             HTML-Statusseite
#   GET /snake3d/api/status   JSON-Status
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Mapping, Optional

from flask import Blueprint, jsonify, render_template

try:
    from core import sql_manager
except Exception:  # pragma: no cover - sichtbar im JSON
    sql_manager = None  # type: ignore

try:
    from core.state_template import find_best_match
except Exception:  # pragma: no cover - Seite bleibt ohne Register importierbar
    find_best_match = None  # type: ignore

snake3d_bp = Blueprint(
    "snake3d",
    __name__,
    url_prefix="/snake3d",
    template_folder="templates",
)

# Kompatibilität mit register_games(...), das `snake3d_bp` oder `bp` akzeptiert.
bp = snake3d_bp

NAMESPACE = "game:snake3d"
STATE_SCHEMA_PREFIX = "snake3d:pro_v1%"
EPISODE_KIND = "game:snake3d:explore_batch"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return float(out)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _fmt_ts(ts: Any) -> str:
    n = _safe_int(ts, 0)
    if n <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(n))
    except Exception:
        return ""


def _parse_meta(raw: Any) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {k: row[k] for k in row.keys()}  # sqlite.Row
    except Exception:
        return {}


def _table_columns(conn: Any, table: str) -> List[str]:
    """Gibt die vorhandenen Spalten einer SQLite-Tabelle defensiv zurück.

    ORÓMA-ZIPs können über mehrere Entwicklungsstände hinweg leicht verschiedene
    Spaltennamen enthalten. Die Snake3D-UI darf daran nicht scheitern, weil sie
    nur eine read-only Statusseite ist. Deshalb wird das Schema gelesen und die
    Abfrage anschließend an die tatsächlich vorhandenen Spalten angepasst.
    """
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        out: List[str] = []
        for row in cur.fetchall() or []:
            d = _row_to_dict(row)
            name = str(d.get("name") or "").strip()
            if name:
                out.append(name)
        return out
    except Exception:
        return []


def _select_expr(columns: List[str], name: str, fallback: str = "NULL") -> str:
    return name if name in columns else f"{fallback} AS {name}"


def _first_existing(columns: List[str], names: List[str]) -> Optional[str]:
    for name in names:
        if name in columns:
            return name
    return None


def _template_match() -> Dict[str, Any]:
    if find_best_match is None:
        return {
            "registry_available": False,
            "best_template": "snake:pro_v2",
            "gaps": ["z_axis"],
            "recommendation": "snake:pro_v2 als Basis verwenden; Z-Achse ergänzen.",
        }
    try:
        out = find_best_match("navigation", ["danger", "food_direction", "z_axis"])
        if isinstance(out, dict):
            out = dict(out)
            out["registry_available"] = True
            return out
    except Exception as exc:
        return {
            "registry_available": False,
            "error": repr(exc),
            "best_template": "snake:pro_v2",
            "gaps": ["z_axis"],
        }
    return {"registry_available": False, "best_template": "snake:pro_v2", "gaps": ["z_axis"]}


def _latest_episode() -> Dict[str, Any]:
    if sql_manager is None:
        return {"ok": False, "error": "sql_manager_import_failed"}
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            episode_cols = _table_columns(conn, "episodes")
            if not episode_cols:
                return {"ok": False, "error": "episodes_schema_unavailable"}

            id_col = _first_existing(episode_cols, ["id", "episode_id", "rowid"]) or "rowid"
            kind_col = _first_existing(episode_cols, ["kind", "type", "name"])
            meta_col = _first_existing(episode_cols, ["meta", "meta_json", "value", "json", "payload"])
            ts_start_col = _first_existing(episode_cols, ["ts_start", "start_ts", "ts"])
            ts_end_col = _first_existing(episode_cols, ["ts_end", "end_ts", "ts"])

            where_parts: List[str] = []
            params: List[Any] = []
            for col in ["kind", "source", "label"]:
                if col in episode_cols:
                    where_parts.append(f"{col} = ?")
                    params.append(EPISODE_KIND)
            if "label" in episode_cols:
                where_parts.append("label LIKE ?")
                params.append("%snake3d%")
            if "source" in episode_cols:
                where_parts.append("source LIKE ?")
                params.append("%snake3d%")
            if not where_parts:
                return {"ok": False, "error": "episodes_lookup_columns_missing"}

            select_cols = [
                f"{id_col} AS id",
                _select_expr(episode_cols, "kind"),
                _select_expr(episode_cols, "source"),
                _select_expr(episode_cols, "label"),
                f"{ts_start_col or '0'} AS ts_start",
                f"{ts_end_col or ts_start_col or '0'} AS ts_end",
                f"{meta_col or 'NULL'} AS meta_raw",
            ]
            order_col = id_col if id_col != "rowid" else "rowid"
            cur.execute(
                f"""
                SELECT {', '.join(select_cols)}
                FROM episodes
                WHERE {' OR '.join(where_parts)}
                ORDER BY {order_col} DESC
                LIMIT 1
                """,
                tuple(params),
            )
            ep = _row_to_dict(cur.fetchone())
            if not ep:
                return {"ok": True, "found": False, "schema_columns": episode_cols}

            metrics: Dict[str, float] = {}
            metric_cols = _table_columns(conn, "episodic_metrics")
            if "episode_id" in metric_cols and "key" in metric_cols and "value" in metric_cols:
                cur.execute(
                    """
                    SELECT key, value
                    FROM episodic_metrics
                    WHERE episode_id = ?
                    """,
                    (_safe_int(ep.get("id"), 0),),
                )
                for r in cur.fetchall() or []:
                    d = _row_to_dict(r)
                    k = str(d.get("key") or "").strip()
                    if not k:
                        continue
                    metrics[k] = _safe_float(d.get("value"), 0.0)

            meta = _parse_meta(ep.get("meta_raw"))
            # Einige ältere Episoden enthalten alle Runner-Werte ausschließlich in
            # meta_json. Für die UI sind diese Werte gleichwertige Statusmetriken.
            for key in [
                "games", "avg_steps", "avg_food", "high_food", "learn_items",
                "learned_items", "pos_items", "neg_items", "draw_items",
                "danger_z_rate", "food_up_signal_rate", "vertical_action_rate",
                "template_fit_score", "policy_games", "explore_games",
                "policy_enabled", "policy_seen", "policy_accepted", "policy_fallback",
                "policy_guarded", "policy_rejected_n", "policy_rejected_q",
                "policy_rejected_unsafe", "policy_miss", "policy_epsilon",
                "chains_count", "snapchains_written",
            ]:
                if key not in metrics and key in meta:
                    metrics[key] = _safe_float(meta.get(key), 0.0)

            return {
                "ok": True,
                "found": True,
                "id": _safe_int(ep.get("id"), 0),
                "kind": ep.get("kind") or EPISODE_KIND,
                "source": ep.get("source") or "",
                "label": ep.get("label") or "",
                "ts_start": _safe_int(ep.get("ts_start"), 0),
                "ts_end": _safe_int(ep.get("ts_end"), 0),
                "time_start": _fmt_ts(ep.get("ts_start")),
                "time_end": _fmt_ts(ep.get("ts_end")),
                "duration_s": max(0, _safe_int(ep.get("ts_end"), 0) - _safe_int(ep.get("ts_start"), 0)),
                "schema_meta_column": meta_col or "",
                "metrics": metrics,
                "meta_core": {
                    "explore_only": bool(meta.get("explore_only", True)),
                    "mode": str(meta.get("mode") or ("explore" if bool(meta.get("explore_only", True)) else "policy")),
                    "state_schema": str(meta.get("state_schema") or "snake3d:pro_v1"),
                    "action_schema": str(meta.get("action_schema") or "relative3d_5"),
                    "base_template": str(meta.get("base_template") or "snake:pro_v2"),
                    "policy_enabled": bool(float(meta.get("policy_enabled", 0.0) or 0.0) > 0.0),
                    "policy_learn_ok": bool(meta.get("policy_learn_ok", False)),
                    "policy_learn_status": str(meta.get("policy_learn_status") or ""),
                    "template_fit_score": _safe_float(meta.get("template_fit_score"), 0.0),
                    "template_validation": meta.get("template_validation") if isinstance(meta.get("template_validation"), dict) else {},
                    "template_adjustment_suggestions": meta.get("template_adjustment_suggestions") if isinstance(meta.get("template_adjustment_suggestions"), list) else [],
                },
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _policy_summary() -> Dict[str, Any]:
    if sql_manager is None:
        return {"ok": False, "error": "sql_manager_import_failed"}
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) AS rules,
                       COALESCE(SUM(pos),0) AS pos,
                       COALESCE(SUM(neg),0) AS neg,
                       COALESCE(SUM(draw),0) AS draw,
                       COALESCE(SUM(n),0) AS n,
                       ROUND(AVG(q),4) AS q_avg,
                       MIN(q) AS q_min,
                       MAX(q) AS q_max
                FROM policy_rules
                WHERE namespace = ? AND state_hash LIKE ?
                """,
                (NAMESPACE, STATE_SCHEMA_PREFIX),
            )
            row = _row_to_dict(cur.fetchone())
            return {
                "ok": True,
                "namespace": NAMESPACE,
                "state_schema_prefix": "snake3d:pro_v1",
                "rules": _safe_int(row.get("rules"), 0),
                "pos": _safe_int(row.get("pos"), 0),
                "neg": _safe_int(row.get("neg"), 0),
                "draw": _safe_int(row.get("draw"), 0),
                "n": _safe_int(row.get("n"), 0),
                "q_avg": _safe_float(row.get("q_avg"), 0.0),
                "q_min": _safe_float(row.get("q_min"), 0.0),
                "q_max": _safe_float(row.get("q_max"), 0.0),
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}



def _snapchain_summary() -> Dict[str, Any]:
    """Read-only Sicht auf Snake3D-SnapChains für den Vertical-Learning-Anschluss."""
    if sql_manager is None:
        return {"ok": False, "error": "sql_manager_import_failed"}
    try:
        with sql_manager.get_conn(None) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) AS chains,
                       COALESCE(SUM(CASE WHEN notes LIKE 'snake3d_daily:policy:%' THEN 1 ELSE 0 END),0) AS policy_chains,
                       COALESCE(SUM(CASE WHEN notes LIKE 'snake3d_daily:explore:%' THEN 1 ELSE 0 END),0) AS explore_chains,
                       MAX(id) AS last_id,
                       MAX(ts) AS last_ts
                FROM snapchains
                WHERE origin = ? OR namespace = ?
                """,
                (NAMESPACE, NAMESPACE),
            )
            row = _row_to_dict(cur.fetchone())
            return {
                "ok": True,
                "namespace": NAMESPACE,
                "chains": _safe_int(row.get("chains"), 0),
                "policy_chains": _safe_int(row.get("policy_chains"), 0),
                "explore_chains": _safe_int(row.get("explore_chains"), 0),
                "last_id": _safe_int(row.get("last_id"), 0),
                "last_ts": _safe_int(row.get("last_ts"), 0),
                "last_time": _fmt_ts(row.get("last_ts")),
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def build_status() -> Dict[str, Any]:
    episode = _latest_episode()
    policy = _policy_summary()
    snapchains = _snapchain_summary()
    template = _template_match()
    metrics: Mapping[str, Any] = episode.get("metrics") if isinstance(episode.get("metrics"), dict) else {}
    meta_core: Mapping[str, Any] = episode.get("meta_core") if isinstance(episode.get("meta_core"), dict) else {}

    return {
        "ok": bool(episode.get("ok", False)) and bool(policy.get("ok", False)),
        "read_only": True,
        "namespace": NAMESPACE,
        "state_schema": "snake3d:pro_v1",
        "action_schema": "relative3d_5",
        "mode": str(meta_core.get("mode") or "explore"),
        "policy_enabled": bool(meta_core.get("policy_enabled", False)),
        "orchestrator_enabled": False,
        "template": template,
        "episode": episode,
        "policy": policy,
        "snapchains": snapchains,
        "summary": {
            "latest_found": bool(episode.get("found", False)),
            "explore_only": bool(meta_core.get("explore_only", True)),
            "policy_learn_ok": bool(meta_core.get("policy_learn_ok", False)),
            "policy_learn_status": str(meta_core.get("policy_learn_status") or ""),
            "games": _safe_int(metrics.get("games"), 0),
            "avg_steps": round(_safe_float(metrics.get("avg_steps"), 0.0), 3),
            "avg_food": round(_safe_float(metrics.get("avg_food"), 0.0), 3),
            "high_food": round(_safe_float(metrics.get("high_food"), 0.0), 3),
            "learned_items": _safe_int(metrics.get("learned_items"), 0),
            "learn_items": _safe_int(metrics.get("learn_items"), 0),
            "pos_items": _safe_int(metrics.get("pos_items"), 0),
            "neg_items": _safe_int(metrics.get("neg_items"), 0),
            "draw_items": _safe_int(metrics.get("draw_items"), 0),
            "danger_z_rate": round(_safe_float(metrics.get("danger_z_rate"), 0.0), 6),
            "food_up_signal_rate": round(_safe_float(metrics.get("food_up_signal_rate"), 0.0), 6),
            "vertical_action_rate": round(_safe_float(metrics.get("vertical_action_rate"), 0.0), 6),
            "template_fit_score": round(_safe_float(metrics.get("template_fit_score"), _safe_float(meta_core.get("template_fit_score"), 0.0)), 3),
            "policy_games": _safe_int(metrics.get("policy_games"), 0),
            "explore_games": _safe_int(metrics.get("explore_games"), 0),
            "policy_enabled": bool(_safe_float(metrics.get("policy_enabled"), 0.0) > 0.0),
            "policy_seen": _safe_int(metrics.get("policy_seen"), 0),
            "policy_accepted": _safe_int(metrics.get("policy_accepted"), 0),
            "policy_fallback": _safe_int(metrics.get("policy_fallback"), 0),
            "policy_guarded": _safe_int(metrics.get("policy_guarded"), 0),
            "policy_rejected_n": _safe_int(metrics.get("policy_rejected_n"), 0),
            "policy_rejected_q": _safe_int(metrics.get("policy_rejected_q"), 0),
            "policy_rejected_unsafe": _safe_int(metrics.get("policy_rejected_unsafe"), 0),
            "chains_count": _safe_int(metrics.get("chains_count"), 0),
            "snapchains_written": _safe_int(metrics.get("snapchains_written"), 0),
        },
    }


@snake3d_bp.route("/", methods=["GET"])
def page() -> str:
    return render_template("snake3d.html", status=build_status())


@snake3d_bp.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(build_status())
