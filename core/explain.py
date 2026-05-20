#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/explain.py
# Projekt: ORÓMA
# Version: v3.5 (Explainability 2.0 + MetaSnaps + Hypothesen)
# Stand:   2025-09-21
#
# Zweck:
#   Explainability-Modul für ORÓMA:
#     - WhyTrace: Evidenzen, Entscheidung, Outcome, Scores
#     - NarrativeEngine → menschenlesbare Erklärungstexte
#     - Kausale Kanten (Edges) zwischen Evidenzen und Entscheidung
#     - Episoden-Verknüpfung (Recall), Knowledge-Gap-Heuristik
#     - (NEU v3.5) Meta-Snaps Einbindung (optional via ENV)
#     - (NEU v3.5) Hypothesen-Registry (Erzeugen/Listen/Ergebnis)
#
# Öffentliche Kernfunktionen:
#   - quick_explain(context, evidences, decision, decision_vector=None, ...)
#   - why_decision(context_centroid=None, topk=5)
#   - get_recent_decisions(limit=50)
#   - why_last()
#   - (NEU) hypotheses_add(desc, meta=None) -> int
#   - (NEU) hypotheses_list(status=None, limit=50) -> list[dict]
#   - (NEU) hypotheses_update_result(h_id, status, result=None, meta=None) -> bool
#
# Hinweise:
#   - Voll kompatibel mit v3.0-Traces; neue Spalten sind optional.
#   - Keine externen Abhängigkeiten; nur stdlib + core.sql_manager/episodic.
# =============================================================================

from __future__ import annotations

import json
import os
import sys
import time
import zlib
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from core.log_guard import log_suppressed
import logging

# ---------------- Projektbasis / optionale Imports ---------------------------
BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

_SQL_OK = True
try:
    from core import sql_manager  # type: ignore
    sql_manager.ensure_schema()
except Exception:
    _SQL_OK = False

_EP_OK = True
try:
    from core import episodic  # type: ignore
except Exception:
    _EP_OK = False

# MetaSnaps Flag (optional)
_ENABLE_METASNAP = os.environ.get("OROMA_ENABLE_METASNAP", "false").lower() not in ("0","false","no","off")

# ---------------- Kompaktes Blob-Format für große Felder ---------------------
_MAGIC = b"WTRC"
_VER = 1
_HDR = struct.Struct(">4sBI")  # magic(4), ver(1), zlen(uint32)

def _pack_json(d: Dict[str, Any]) -> bytes:
    raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    z = zlib.compress(raw, level=6)
    return _HDR.pack(_MAGIC, _VER, len(z)) + z

def _unpack_json(blob: bytes) -> Dict[str, Any]:
    if not blob or len(blob) < _HDR.size:
        return {}
    try:
        magic, ver, zlen = _HDR.unpack_from(blob, 0)
        if magic != _MAGIC or ver != _VER:
            return {}
        raw = zlib.decompress(blob[_HDR.size:_HDR.size + zlen])
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}

# ---------------- Schema / Persistenz ----------------------------------------
def ensure_schema() -> None:
    """Erweitert/legt explain_traces & hypotheses idempotent an."""
    if not _SQL_OK:
        return

    # Wichtig: in ORÓMA koennen unterschiedliche DB-Snapshots im Umlauf sein.
    # Ensure/Repair muss daher Alt-Schemata tolerieren (keine harten Exceptions).
    with sql_manager.get_conn() as conn:
        cur = conn.cursor()

        # Explain-Traces (wie v3.0, erweitert)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS explain_traces (
                id INTEGER PRIMARY KEY,
                created_at INTEGER,
                closed INTEGER DEFAULT 0,
                context TEXT,
                decision TEXT,
                outcome  TEXT,
                evidence BLOB,
                scores   BLOB,
                episodes TEXT,
                narrative TEXT,
                causal_edges BLOB
            )
            """
        )
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_explain_created ON explain_traces(created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_explain_closed  ON explain_traces(closed)")
        except Exception:
            pass

        # Hypothesen (NEU v3.5)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hypotheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|succeeded|failed
                result REAL DEFAULT NULL,
                meta   TEXT DEFAULT NULL
            )
            """
        )

        # ─────────────────────────────────────────────────────────────────
        # Schema-Repair: hypotheses.created_at (Alt-DB-Kompatibilitaet)
        #
        # Aeltere DB-Snapshots koennen eine hypotheses-Tabelle ohne created_at
        # besitzen. Dann scheitert der Index-Aufbau. Wir reparieren best-effort:
        #   - ALTER TABLE ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0
        #   - Backfill: setze 0->now
        # ─────────────────────────────────────────────────────────────────
        try:
            cur.execute("PRAGMA table_info(hypotheses)")
            cols = [str(r[1]) for r in (cur.fetchall() or [])]
            if "created_at" not in cols:
                cur.execute("ALTER TABLE hypotheses ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
                cur.execute(
                    "UPDATE hypotheses SET created_at = CAST(strftime('%s','now') AS INTEGER) WHERE created_at = 0"
                )
        except Exception:
            pass

        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_hyp_created ON hypotheses(created_at)")
        except Exception:
            pass
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_hyp_status  ON hypotheses(status)")
        except Exception:
            pass

        conn.commit()

@dataclass
class Evidence:
    kind: str
    score: float
    weight: float = 1.0
    note: str = ""
    vector: Optional[List[float]] = None
    rule_id: Optional[int] = None
    snap_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"kind": self.kind, "score": float(self.score), "weight": float(self.weight)}
        if self.note: d["note"] = self.note
        if self.vector is not None: d["vector"] = [float(x) for x in self.vector]
        if self.rule_id is not None: d["rule_id"] = int(self.rule_id)
        if self.snap_id is not None: d["snap_id"] = int(self.snap_id)
        return d

# ---------------- Narrative-Engine (Explainability 2.0) ----------------------
class NarrativeEngine:
    """Erzeugt kurze, prägnante, menschenlesbare Erklärungen aus einem WhyTrace."""

    @staticmethod
    def _fmt_pct(x: float) -> str:
        try:
            return f"{max(0.0, min(1.0, x))*100:.0f}%"
        except Exception:
            return "-"

    @staticmethod
    def build_causal_edges(evidence: List[Evidence], decision: Dict[str, Any], episodes: List[int],
                           metas: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Heuristische Kausalkanten: Evidenz/MetaSnap -> Entscheidung."""
        action = str((decision or {}).get("action", "unknown"))
        weights = [max(0.0, float(e.score) * float(e.weight)) for e in (evidence or [])]
        wmax = max(weights) if weights else 1.0
        edges = []
        for e in (evidence or []):
            raw_w = max(0.0, float(e.score) * float(e.weight))
            norm_w = (raw_w / wmax) if wmax > 0 else 0.0
            edges.append({
                "source_kind": e.kind,
                "target_action": action,
                "weight": norm_w,
                "raw_weight": raw_w,
                "note": e.note or "",
                "rule_id": e.rule_id,
                "snap_id": e.snap_id,
            })
        if episodes:
            edges.append({
                "source_kind": "episodic_support",
                "target_action": action,
                "weight": 0.5,
                "raw_weight": 0.5,
                "episodes": [int(x) for x in episodes],
            })
        # MetaSnaps als zusätzliche Quellen (wenn vorhanden)
        for m in (metas or []):
            edges.append({
                "source_kind": f"meta:{m.get('label','?')}",
                "target_action": action,
                "weight": float(m.get("norm_score", 0.4)),
                "raw_weight": float(m.get("score", 0.0)),
                "note": "MetaSnap-Bezug",
                "meta_sources": m.get("sources", []),
            })
        return {"nodes": list({e["source_kind"] for e in edges} | {action}),
                "edges": edges}

    @staticmethod
    def _metas_txt(metas: Optional[List[Dict[str, Any]]]) -> str:
        if not metas:
            return "keine Meta-Snaps"
        parts = []
        for m in metas[:3]:
            lab = str(m.get("label","?"))
            sc  = float(m.get("score",0.0))
            parts.append(f"{lab} ({sc:.2f})")
        return "; ".join(parts)

    @staticmethod
    def generate(trace: "WhyTrace", metas: Optional[List[Dict[str, Any]]] = None) -> str:
        """Narrativer Kurztext aus Evidenzen, Scores, Entscheidung & Outcome (+ MetaSnaps)."""
        try:
            action = str((trace.decision or {}).get("action", "unknown"))
            policy = str((trace.decision or {}).get("policy", "auto"))
            score_w = float((trace.scores or {}).get("weighted", (trace.decision or {}).get("score", 0.0)))
            evs = trace.evidence or []
            epi = trace.episodes or []

            ranked = sorted(((e.kind, float(e.score), float(e.weight), e.note or "") for e in evs),
                            key=lambda t: t[1]*t[2], reverse=True)[:3]
            ev_parts = []
            for kind, s, w, note in ranked:
                frag = f"{kind} ({s:.2f}×{w:.2f})"
                if note:
                    frag += f": {note}"
                ev_parts.append(frag)
            ev_txt = "; ".join(ev_parts) if ev_parts else "keine starke Evidenz"

            epi_txt = "episodische Stütze vorhanden" if epi else "keine episodische Stütze"

            out = (trace.outcome or {})
            out_bits = [f"{k}={out[k]}" for k in ("reward", "result", "status") if k in out]
            out_txt = ", ".join(out_bits) if out_bits else "kein Ergebnis"

            conf_txt = NarrativeEngine._fmt_pct(score_w)

            meta_txt = ""
            if metas is not None:
                meta_txt = f" Meta-Snaps: {NarrativeEngine._metas_txt(metas)}."

            return (
                f"Aktion „{action}“ (Policy: {policy}, Vertrauen: {conf_txt}). "
                f"Evidenzbasis: {ev_txt}. "
                f"Episoden: {epi_txt}. "
                f"Outcome: {out_txt}.{meta_txt}"
            )
        except Exception:
            return "Erklärung nicht verfügbar."

# ---------------- Meta-Snaps Lookup (optional) -------------------------------
def _load_related_metas(context: Dict[str, Any], episodes: List[int]) -> List[Dict[str, Any]]:
    """
    Sucht MetaSnaps, die zu einer Chain/Episode passen.
    Heuristiken:
      - context.chain_id ∈ sources
      - irgendeine episode_id ∈ sources
    Fällt still zurück, wenn Tabelle fehlt.
    """
    if not (_SQL_OK and _ENABLE_METASNAP):
        return []
    try:
        conn = sql_manager.get_conn()
        cur = conn.cursor()
        metas: List[Dict[str, Any]] = []
        # Roh laden (kleiner Umfang, z. B. letzte 500)
        cur.execute("SELECT id, label, sources, score, created_at FROM meta_snaps ORDER BY created_at DESC LIMIT 500")
        rows = cur.fetchall() or []
        chain_id = None
        try:
            chain_id = int((context or {}).get("chain_id"))
        except Exception:
            chain_id = None

        for r in rows:
            lab = r.get("label") if hasattr(r, "get") else r[1]
            sc  = float(r.get("score", 0.0) if hasattr(r, "get") else r[3])
            src = r.get("sources") if hasattr(r, "get") else r[2]
            try:
                src_list = json.loads(src) if isinstance(src, str) else (src or [])
            except Exception:
                src_list = []
            hit = False
            if chain_id is not None and chain_id in src_list:
                hit = True
            if not hit and episodes:
                for e in episodes:
                    if int(e) in src_list:
                        hit = True
                        break
            if hit:
                metas.append({"label": lab, "score": sc, "sources": src_list})
        # norm_score fürs Causal-Graph
        if metas:
            mx = max([m["score"] for m in metas] or [1.0])
            for m in metas:
                m["norm_score"] = (m["score"] / mx) if mx > 0 else 0.4
        return metas
    except Exception:
        return []

# ---------------- WhyTrace ---------------------------------------------------
@dataclass
class WhyTrace:
    context: Dict[str, Any]
    created_at: int = field(default_factory=lambda: int(time.time()))
    closed: bool = False
    decision: Optional[Dict[str, Any]] = field(default_factory=dict)
    outcome: Optional[Dict[str, Any]] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)
    scores: Dict[str, Any] = field(default_factory=dict)
    episodes: List[int] = field(default_factory=list)
    narrative: str = ""
    causal_edges: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    @classmethod
    def new(cls, context: Dict[str, Any]) -> "WhyTrace":
        return cls(context=dict(context or {}))

    def add_evidence(self, kind: str, score: float, weight: float = 1.0, note: str = "",
                     vector: Optional[Sequence[float]] = None, rule_id: Optional[int] = None,
                     snap_id: Optional[int] = None) -> None:
        if self.closed:
            raise RuntimeError("Trace ist geschlossen.")
        v = [float(x) for x in vector] if vector is not None else None
        self.evidence.append(Evidence(kind=kind, score=float(score), weight=float(weight),
                                      note=note or "", vector=v, rule_id=rule_id, snap_id=snap_id))

    def bind_decision(self, action: str, score: float, policy: str = "auto",
                      params: Optional[Dict[str, Any]] = None) -> None:
        self.decision = {"action": action, "score": float(score), "policy": policy, "params": params or {}}

    def attach_episodes(self, ids: List[int]) -> None:
        self.episodes = [int(x) for x in (ids or [])]

    def _compute_scores(self) -> Dict[str, Any]:
        if not self.evidence:
            return {"raw": 0.0, "weighted": 0.0, "count": 0, "per_kind": {}}
        total_w = 0.0
        s_raw = 0.0
        s_wgt = 0.0
        per_kind: Dict[str, Dict[str, float]] = {}
        for ev in self.evidence:
            s_raw += ev.score
            s_wgt += ev.score * ev.weight
            total_w += ev.weight
            k = per_kind.setdefault(ev.kind, {"sum": 0.0, "wsum": 0.0, "n": 0})
            k["sum"] += ev.score
            k["wsum"] += ev.score * ev.weight
            k["n"] += 1
        weighted = s_wgt / (total_w or 1.0)
        return {
            "raw": s_raw / len(self.evidence),
            "weighted": weighted,
            "count": len(self.evidence),
            "per_kind": {k: {"avg": v["sum"]/max(1,v["n"]), "wavg": v["wsum"]/max(1,v["n"]), "n": v["n"]}
                         for k, v in per_kind.items()}
        }

    def _finalize_narrative_and_causal(self, metas: Optional[List[Dict[str, Any]]] = None) -> None:
        self.narrative = NarrativeEngine.generate(self, metas=metas)
        self.causal_edges = NarrativeEngine.build_causal_edges(self.evidence, self.decision or {},
                                                               self.episodes or [], metas=metas)

    def close(self, outcome: Optional[Dict[str, Any]] = None, metas: Optional[List[Dict[str, Any]]] = None) -> None:
        self.closed = True
        if outcome:
            self.outcome = dict(outcome)
        self.scores = self._compute_scores()
        self._finalize_narrative_and_causal(metas=metas)

    def save(self) -> int:
        if not _SQL_OK:
            raise RuntimeError("sql_manager nicht verfügbar")
        ensure_schema()

        evidence_blob = _pack_json({"evidence": [e.to_dict() for e in self.evidence]})
        scores_blob = _pack_json(self.scores or {})
        causal_blob = _pack_json(self.causal_edges or {})

        conn = sql_manager.get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO explain_traces
            (created_at, closed, context, decision, outcome, evidence, scores, episodes, narrative, causal_edges)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(self.created_at),
                int(self.closed),
                json.dumps(self.context or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(self.decision or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(self.outcome or {}, ensure_ascii=False, separators=(",", ":")),
                evidence_blob,
                scores_blob,
                json.dumps(self.episodes or [], ensure_ascii=False, separators=(",", ":")),
                self.narrative or "",
                causal_blob,
            )
        )
        conn.commit()
        self.id = int(cur.lastrowid)
        try:
            sql_manager.insert_metric("whytrace_saved", 1.0)
        except Exception as e:
            log_suppressed(
                logging.getLogger(__name__),
                key="core.explain.pass.1",
                exc=e,
                msg="Suppressed exception (was: pass)",
            )
        return self.id

# ---------------- Episoden-Ähnlichkeit ---------------------------------------
def explain_decision(decision_vector: Optional[Sequence[float]], topk: int = 5) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"episodes": [], "text": "Keine episodische Referenz verfügbar."}
    if _EP_OK and decision_vector:
        try:
            hits = episodic.recall_similar(list(decision_vector), topk=topk)
            summary["episodes"] = [
                {"episode_id": int(h.get("episode_id", h.get("id", 0))),
                 "score": float(h.get("dist", h.get("score", 0.0)))}
                for h in hits
            ]
            if hits:
                summary["text"] = (
                    f"Entscheidung ähnelt {len(hits)} früheren Episoden; "
                    f"Top-Treffer Episode={summary['episodes'][0]['episode_id']} "
                    f"(Distanz={summary['episodes'][0]['score']:.3f})."
                )
        except Exception as e:
            summary["text"] = f"Episoden-Suche fehlgeschlagen: {type(e).__name__}"
    return summary

# ---------------- Gap-Heuristik ---------------------------------------------
def _maybe_record_gap(*, context: Dict[str, Any], decision: Dict[str, Any], scores: Dict[str, Any],
                      evidence_count: int, episodes: List[int]) -> None:
    """Schreibt Knowledge-Gaps heuristisch (nur wenn gaps-Modul existiert)."""
    try:
        from core import gaps as gaps_core  # lazy import
    except Exception:
        return
    try:
        weighted = float(scores.get("weighted", 0.0) if isinstance(scores, dict) else 0.0)
        meta_base = {
            "context": context, "decision": decision, "scores": scores,
            "evidence_count": int(evidence_count), "episodes": list(episodes or []),
        }
        if weighted < 0.40:
            gaps_core.add_gap(kind="uncertain_decision",
                              desc=f"Niedriger Entscheidungs-Score (weighted={weighted:.2f})",
                              confidence=max(0.0, min(1.0, weighted)), meta={**meta_base})
        if evidence_count < 1:
            gaps_core.add_gap(kind="missing_evidence", desc="Keine Evidenz im Trace",
                              confidence=weighted, meta={**meta_base})
        elif evidence_count < 2 and weighted < 0.6:
            gaps_core.add_gap(kind="weak_evidence",
                              desc="Geringe Anzahl an Evidenzen bei moderatem Score",
                              confidence=weighted, meta={**meta_base})
        if not episodes and weighted < 0.7:
            gaps_core.add_gap(kind="no_episode_support",
                              desc="Keine ähnlichen Episoden zur Entscheidung gefunden",
                              confidence=weighted, meta={**meta_base})
    except Exception as e:
        log_suppressed(
            logging.getLogger(__name__),
            key="core.explain.pass.2",
            exc=e,
            msg="Suppressed exception (was: pass)",
        )

# ---------------- Haupt-API --------------------------------------------------
def quick_explain(
    context: Dict[str, Any],
    evidences: List[Dict[str, Any]],
    decision: Dict[str, Any],
    decision_vector: Optional[Sequence[float]] = None,
    episodes_topk: int = 5,
    outcome: Optional[Dict[str, Any]] = None
) -> int:
    """
    Erzeugt & speichert einen vollständigen WhyTrace inkl.:
      - Evidenzen, Entscheidung, Outcome
      - Scores, episodische Stütze
      - Narrativ (menschenlesbar)
      - Kausale Kanten
      - (NEU) Meta-Snaps Anreicherung (optional)
    """
    ensure_schema()
    tr = WhyTrace.new(context=context)

    for ev in evidences or []:
        tr.add_evidence(
            kind=str(ev.get("kind","generic")),
            score=float(ev.get("score", 0.0)),
            weight=float(ev.get("weight", 1.0)),
            note=str(ev.get("note","")),
            vector=ev.get("vector"),
            rule_id=ev.get("rule_id"),
            snap_id=ev.get("snap_id"),
        )

    tr.bind_decision(
        action=str(decision.get("action","unknown")),
        score=float(decision.get("score", 0.0)),
        policy=str(decision.get("policy","auto")),
        params=decision.get("params") or {}
    )

    # episodische Stütze
    epi_ids: List[int] = []
    if decision_vector:
        ex = explain_decision(decision_vector, topk=episodes_topk)
        epi_ids = [e["episode_id"] for e in ex.get("episodes", [])]
        tr.attach_episodes(epi_ids)
        tr.add_evidence(kind="episodic",
                        score=1.0 if ex.get("episodes") else 0.0,
                        weight=0.5,
                        note=ex.get("text",""))

    # (NEU) MetaSnaps-Anreicherung
    metas: List[Dict[str, Any]] = []
    try:
        metas = _load_related_metas(context or {}, epi_ids)
    except Exception:
        metas = []

    # abschließen → erzeugt narrative & causal_edges + Scores
    tr.close(outcome=outcome or {}, metas=metas)

    # Gap-Heuristik
    _maybe_record_gap(context=context, decision=tr.decision or {}, scores=tr.scores or {},
                      evidence_count=len(tr.evidence), episodes=epi_ids)

    return tr.save()

# ---------------- Public API für UI (why_ui.py) ------------------------------
def why_decision(context_centroid: Optional[Sequence[float]] = None, topk: int = 5) -> Dict[str, Any]:
    try:
        if context_centroid:
            vec = [float(x) for x in context_centroid]
            ex = explain_decision(vec, topk=topk)
            return {"ok": True, "reason": ex.get("text", ""), "episodes": ex.get("episodes", [])}
        else:
            return {"ok": True, "reason": "Kein Kontext-Centroid übergeben.", "episodes": []}
    except Exception as e:
        return {"ok": False, "error": f"why_decision failed: {type(e).__name__}: {e}"}

def _row_get(row, key: str, idx_fallback: Optional[int] = None):
    try:
        return row[key]
    except Exception:
        if idx_fallback is not None:
            try:
                return row[idx_fallback]
            except Exception:
                return None
        return None

def _json_load_safe(obj: Any) -> Dict[str, Any]:
    try:
        if obj is None:
            return {}
        if isinstance(obj, (bytes, bytearray)):
            try:
                return json.loads(obj.decode("utf-8"))
            except Exception:
                return {}
        if isinstance(obj, str):
            return json.loads(obj) if obj else {}
        return {}
    except Exception:
        return {}

def get_recent_decisions(limit: int = 50) -> List[Dict[str, Any]]:
    if not _SQL_OK:
        return []
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, created_at, decision, outcome, scores, evidence, narrative, causal_edges
        FROM explain_traces
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (int(limit),)
    )
    out: List[Dict[str, Any]] = []
    for r in (cur.fetchall() or []):
        rid = int(_row_get(r, "id", 0) or 0)
        ts = int(_row_get(r, "created_at", 1) or int(time.time()))
        decision = _json_load_safe(_row_get(r, "decision", 2))
        outcome  = _json_load_safe(_row_get(r, "outcome", 3))
        scores   = _unpack_json(_row_get(r, "scores", 4)) if _row_get(r, "scores", 4) else {}
        evidence = _unpack_json(_row_get(r, "evidence", 5)) if _row_get(r, "evidence", 5) else {}
        narrative = _row_get(r, "narrative", 6) or ""
        causal_edges = _unpack_json(_row_get(r, "causal_edges", 7)) if _row_get(r, "causal_edges", 7) else {}

        reason = ""
        try:
            per = (scores or {}).get("per_kind") or {}
            if per:
                best_k = max(per.items(), key=lambda kv: (kv[1] or {}).get("wavg", 0.0))
                bk_name = str(best_k[0]); bk_val = float((best_k[1] or {}).get("wavg", 0.0))
                reason = f"stärkste Evidenz: {bk_name} (wØ={bk_val:.2f})"
        except Exception:
            reason = ""

        if not reason:
            evs = (evidence or {}).get("evidence") or []
            if evs:
                kinds = {}
                for ev in evs:
                    k = str(ev.get("kind", "k"))
                    kinds[k] = kinds.get(k, 0) + 1
                topk2 = sorted(kinds.items(), key=lambda kv: kv[1], reverse=True)[:2]
                reason = "Evidenzen: " + ", ".join(f"{k}×{n}" for k, n in topk2)

        out.append({
            "id": rid, "ts": ts,
            "action": (decision or {}).get("action"),
            "score": (scores or {}).get("weighted", (decision or {}).get("score")),
            "reason": reason, "narrative": narrative,
            "decision": decision, "outcome": outcome, "scores": scores,
            "count_evidence": len((evidence or {}).get("evidence") or []),
            "causal": causal_edges,
        })
    return out

def why_last() -> Dict[str, Any]:
    if not _SQL_OK:
        return {"ok": False, "error": "sql_manager nicht verfügbar"}
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, created_at, decision, outcome, scores, narrative, causal_edges "
        "FROM explain_traces ORDER BY created_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "msg": "Keine Entscheidung gespeichert"}

    rid = int(_row_get(row, "id", 0) or 0)
    ts = int(_row_get(row, "created_at", 1) or int(time.time()))
    decision = _json_load_safe(_row_get(row, "decision", 2))
    outcome  = _json_load_safe(_row_get(row, "outcome", 3))
    scores   = _unpack_json(_row_get(row, "scores", 4)) if _row_get(row, "scores", 4) else {}
    narrative = _row_get(row, "narrative", 5) or ""
    causal_edges = _unpack_json(_row_get(row, "causal_edges", 6)) if _row_get(row, "causal_edges", 6) else {}
    return {"ok": True, "id": rid, "ts": ts, "decision": decision, "outcome": outcome,
            "scores": scores, "narrative": narrative, "causal": causal_edges}

# ---------------- Hypothesen-Registry (NEU) ----------------------------------
def hypotheses_add(description: str, meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Erzeugt eine neue Hypothese (status=pending)."""
    if not _SQL_OK:
        return None
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO hypotheses (description, created_at, status, meta) VALUES (?, ?, ?, ?)",
        (str(description), int(time.time()), "pending",
         json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":")))
    )
    conn.commit()
    return int(cur.lastrowid)

def hypotheses_update_result(h_id: int, status: str, result: Optional[float] = None,
                             meta: Optional[Dict[str, Any]] = None) -> bool:
    """Aktualisiert Status/Ergebnis einer Hypothese."""
    if not _SQL_OK:
        return False
    ensure_schema()
    status = str(status).lower()
    if status not in ("pending","running","succeeded","failed"):
        status = "failed"
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE hypotheses SET status=?, result=?, meta=? WHERE id=?",
        (status, None if result is None else float(result),
         json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":")),
         int(h_id))
    )
    conn.commit()
    return cur.rowcount > 0

def hypotheses_list(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Listet Hypothesen (optional gefiltert nach Status)."""
    if not _SQL_OK:
        return []
    ensure_schema()
    conn = sql_manager.get_conn()
    cur = conn.cursor()
    if status:
        cur.execute(
            "SELECT id, description, created_at, status, result, meta "
            "FROM hypotheses WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (str(status), int(limit))
        )
    else:
        cur.execute(
            "SELECT id, description, created_at, status, result, meta "
            "FROM hypotheses ORDER BY created_at DESC LIMIT ?",
            (int(limit),)
        )
    rows = cur.fetchall() or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            meta = _json_load_safe(_row_get(r, "meta", 5))
        except Exception:
            meta = {}
        out.append({
            "id": int(_row_get(r, "id", 0) or 0),
            "description": _row_get(r, "description", 1) or "",
            "created_at": int(_row_get(r, "created_at", 2) or 0),
            "status": _row_get(r, "status", 3) or "pending",
            "result": _row_get(r, "result", 4),
            "meta": meta,
        })
    return out

# ---------------- Selftest ----------------------------------------------------
def _selftest() -> None:
    ctx = {"source": "pong", "state": {"tick": 42}, "chain_id": 123}
    evid = [
        {"kind": "vision", "score": 0.35, "weight": 1.2, "note": "Ball links nah"},
        {"kind": "heuristic", "score": 0.30, "weight": 1.0, "note": "Paddle drift"},
    ]
    dec = {"action": "move_left", "score": 0.33, "policy": "rule", "params": {"speed": 0.4}}
    vec = [0.2, 0.0, -0.1, 0.33, 0.05, -0.2]

    # Hypothese anlegen
    h_id = hypotheses_add("Bewegung+Ton+Farbe verbessert Pong-Defense", meta={"source":"selftest"}) or -1
    if h_id > 0:
        hypotheses_update_result(h_id, "running")

    tid = quick_explain(ctx, evid, dec, decision_vector=vec, episodes_topk=3, outcome={"reward": 0.1, "status": "ok"})
    last = why_last()

    if h_id > 0:
        hypotheses_update_result(h_id, "succeeded", result=+0.1, meta={"trace_id": tid})

    print("[explain] trace id:", tid)
    print("[explain] last.narrative:", (last.get("narrative","") or "")[:200])

if __name__ == "__main__":
    _selftest()