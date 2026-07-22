#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/universal_policy.py
# Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:   Universal Policy (UP) – tabellarische State→Action-Policy
# Version: v3.9-rc4 (Vertical-Proof Policy-Mutation Gate)
# Stand:   2026-04-18
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:  MIT
# =============================================================================
#
# ZWECK / SYSTEMROLLE
# ───────────────────
# UniversalPolicy ist die domänen-agnostische, leichtgewichtige Policy-Schicht für ORÓMA:
#   - Runtime-Entscheidung: choose(state_hash, legal, side) → Aktion
#   - Online-Lernen: learn(...) / learn_many(...) → UPSERT nach policy_rules
#   - Optional: Auto-Export “guter” Regeln in rules (Regelarchiv / Explainability)
#   - Optional: Vektor→State-Hash (Adapter) via mini_programs.universal_policy.adapter_universal
#
# Sie ist bewusst “klein und robust” (Edge/24-7), damit:
#   - auch ohne große Modelle (LLM/Vision) gelernt/entschieden werden kann,
#   - state_hash als String universell für Games, Sensorik, Tools, etc. funktioniert,
#   - die Policy-Infrastruktur als Transfer-Gerüst zwischen Domänen genutzt werden kann.
#
# DB-ERWARTUNG / SCHEMA
# ─────────────────────
# Erwartete Tabelle (bereitgestellt durch core.sql_manager.ensure_schema()):
#   policy_rules(namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)
#   UNIQUE(namespace, state_hash, action)
#
# INTEGRATION (MINIMAL)
# ────────────────────
#   from core import universal_policy as upol
#   pol = upol.Policy(namespace="game:snake")
#   a = pol.choose(state_hash, legal=[0,1,2], side="oroma")
#   pol.learn_many([{"state_hash": state_hash, "action_canon": a, "outcome": -1.0, "ts": int(time.time())}])
#
# OPTIONAL (ADAPTER)
# ─────────────────
#   - choose_vec(vec, spec=None, legal=None, side="oroma") → action
#   - hash_vec(vec, spec=None) → state_hash
#
# AUTO-EXPORT (ENV, analog policy_engine.py)
# ─────────────────────────────────────────
#   OROMA_UP_AUTO_EXPORT      (1)     0/1  – Auto-Export aktiv
#   OROMA_UP_MIN_N            (3)     int  – Mindest-Beobachtungen je (s,a)
#   OROMA_UP_MIN_ABS_Q        (0.15)  float– Mindest-|q|
#   OROMA_UP_MAJ_CONF         (0.00)  float– Mehrheitskonfidenz ∈ [0..1] (0=aus)
#   OROMA_UP_COOLDOWN_S       (600)   int  – Cooldown je (s,a)
#
# WAHL / TIEBREAK / SAMPLING
# ──────────────────────────
#   OROMA_UP_TEMP             (0.00)  float– Temperatur (0=greedy)
#   OROMA_UP_PRIOR_N          (0.00)  float– Pseudocount (Stabilisierung bei wenig Daten)
#
# PROAKTIVE KNOWLEDGE-GAPS (optional, choose)
# ──────────────────────────────────────────
#   OROMA_UP_GAPS                 (1)     0/1   – Gap-Logik aktiv
#   OROMA_UP_GAPS_COOLDOWN_S      (120)   int   – Gap-Cooldown je state_hash
#   OROMA_UP_GAPS_LOW_EVIDENCE_N  (2)     int   – “low evidence” Schwellwert
#   OROMA_UP_GAPS_UNCERTAINTY_EPS (0.05)  float – Unsicherheitsfenster für q
#   OROMA_UP_GAPS_DEBUG           (0)     0/1   – Debug-Logs
#
# DBWRITER / WRITE-DISZIPLIN
# ─────────────────────────
# Policy-Upserts können (je nach Systemmodus) über den DBWriter (Single-Writer) chunked
# ausgeführt werden, um SQLite-Locks zu vermeiden:
#   OROMA_DBW_ENABLE
#   OROMA_POLICY_DBW_CHUNK
#
# ROBUSTHEIT / PRODUKTIONSINVARIANTEN
# ──────────────────────────────────
# - “legal” kann partiell/leer sein: choose(...) muss stabil bleiben (kein Crash).
# - Unknown actions werden defensiv behandelt; side-aware Canon kann Adapter nutzen.
# - Keine stillen Fehler: DB/Adapter-Probleme werden sichtbar geloggt (aber nicht boot-kill).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os, json, time, math, logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import logging
from core import log_guard
logger = logging.getLogger(__name__)
LOG = logging.getLogger("oroma.universal_policy")
if not LOG.handlers:
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [UP] %(message)s"))
    LOG.addHandler(_sh)
LOG.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Core-DB + optional Regelarchiv + optional Universal-Adapter
# -----------------------------------------------------------------------------
from core import sql_manager
from core import execution_mode
try:
    from core import db_writer_client as db_writer_client
except Exception:
    db_writer_client = None  # type: ignore

try:
    from core import regelarchiv as _archiv
except Exception:
    _archiv = None  # type: ignore

# Adapter ist optional; nur für hash_vec/choose_vec genutzt
try:
    from mini_programs.universal_policy.adapter_universal import UniversalAdapter as _UNI
    _HAVE_ADAPTER = True
except Exception:
    _UNI = None  # type: ignore
    _HAVE_ADAPTER = False

# Schema idempotent sicherstellen
try:
    sql_manager.ensure_schema()
except Exception as e:
    LOG.debug("ensure_schema(): %s", e)


# =============================================================================
# ENV-Utilities
# =============================================================================
def _env_bool(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or "").lower() in ("1", "true", "yes", "on")

def _env_int(name: str, default: int) -> int:
    try: return int(os.environ.get(name, str(default)))
    except Exception: return default

def _env_float(name: str, default: float) -> float:
    try: return float(os.environ.get(name, str(default)))
    except Exception: return default


def _row_val(row: Any, key: str, default: Any = None) -> Any:
    """Robuster Row-Zugriff für sqlite3.Row und mapping-ähnliche Objekte.

    Hintergrund:
    Der neue Post-Commit-Export-Scan verarbeitet je nach Connection-Konfiguration
    echte sqlite3.Row-Objekte oder tuple-basierte Ergebnisse. Für Row-Objekte
    soll der Zugriff nie fatal werden, auch wenn ein Schlüssel fehlt oder die
    Zeile kein keys()-API wie erwartet anbietet.
    """
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        try:
            if hasattr(row, 'get'):
                return row.get(key, default)
        except Exception:
            pass
    return default


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default



# =============================================================================
# Proaktive Knowledge-Gaps (UP choose)
# =============================================================================
# Motivation:
#   In ORÓMA v3.7+ laufen Game-Runtimes (z.B. TicTacToe) zur Action-Wahl oft
#   direkt über core.universal_policy.Policy.choose(). Wenn niemand während
#   der Entscheidung Gaps emittiert, bleibt knowledge_gaps leer, obwohl
#   policy_rules wächst.
#
# Diese Logik erzeugt (throttled) zwei Gap-Typen:
#   - low_evidence:     gewählte Aktion hat zu wenige Beobachtungen (n < N)
#   - high_uncertainty: Top-2 Aktionen sind nahezu gleich gut (|q1-q2| < eps)
#
# ENV:
#   OROMA_UP_GAPS                 (1)     0/1
#   OROMA_UP_GAPS_LOW_EVIDENCE_N  (5)     n < N
#   OROMA_UP_GAPS_UNCERTAINTY_EPS (0.05)  |q1-q2| < eps
#   OROMA_UP_GAPS_COOLDOWN_S      (900)   cooldown pro (kind,namespace,state_hash)
# =============================================================================

_UP_GAPS_ON = _env_bool("OROMA_UP_GAPS", "1")
_UP_GAPS_LOW_EVIDENCE_N = _env_int("OROMA_UP_GAPS_LOW_EVIDENCE_N", 5)
_UP_GAPS_UNCERTAINTY_EPS = _env_float("OROMA_UP_GAPS_UNCERTAINTY_EPS", 0.05)
_UP_GAPS_COOLDOWN_S = _env_int("OROMA_UP_GAPS_COOLDOWN_S", 900)
_UP_GAPS_DEBUG = _env_bool("OROMA_UP_GAPS_DEBUG", "0")

_UP_GAP_LAST_TS: Dict[str, float] = {}


def _gap_emit(kind: str, namespace: str, state_hash: str,
              desc: str, confidence: float, meta: Dict[str, Any]) -> None:
    # Best-effort Gap-Emit mit Cooldown. Darf niemals fatal sein.
    if not _UP_GAPS_ON:
        return
    if not namespace or not state_hash:
        return
    key = f"{kind}::{namespace}::{state_hash}"
    now = time.time()
    last = _UP_GAP_LAST_TS.get(key, 0.0)
    if last and (now - last) < float(_UP_GAPS_COOLDOWN_S):
        return
    try:
        from core import gaps as _gaps  # local import: keine harte Start-Abhängigkeit
        _gaps.add_gap(kind=str(kind), desc=str(desc), confidence=float(confidence or 0.0), meta=dict(meta or {}))
        _UP_GAP_LAST_TS[key] = now
    except Exception as e:
        if _UP_GAPS_DEBUG:
            try:
                LOG.warning("gap_emit failed (%s): %s", kind, e)
            except Exception as e:
                log_guard.log_suppressed(logger, key="universal_policy.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        return


def _best_two(table: Dict[str, Dict[str, float]], legal_str: List[str]) -> Tuple[Optional[str], float, float, Optional[str], float, float]:
    # Liefert (a1,q1,n1,a2,q2,n2) fuer LEGAL. Robust bei Luecken.
    items: List[Tuple[str, float, float]] = []
    for a in legal_str:
        v = table.get(a)
        if not v:
            continue
        try:
            items.append((a, float(v.get("q", 0.0)), float(v.get("n", 0.0))))
        except Exception:
            continue
    if not items:
        return None, 0.0, 0.0, None, 0.0, 0.0
    items.sort(key=lambda t: (t[1], t[2]), reverse=True)
    a1, q1, n1 = items[0]
    if len(items) > 1:
        a2, q2, n2 = items[1]
    else:
        a2, q2, n2 = None, 0.0, 0.0
    return a1, q1, n1, a2, q2, n2

# =============================================================================
# Policy-Kern
# =============================================================================
class Policy:
    """
    DB-gestützte Universal-Policy (tabellarisch).
    """

    def __init__(self, namespace: str = "game:any", writer_id: str = "writer:core.universal_policy:legacy") -> None:
        self.namespace = namespace.strip() or "game:any"
        self.writer_id = str(writer_id or "writer:core.universal_policy:legacy").strip()
        self.last_mutation_decision: Dict[str, Any] = {}

        # Auswahl-/Prior-Parameter
        self.temp      = _env_float("OROMA_UP_TEMP", 0.0)
        self.prior_n   = max(0.0, _env_float("OROMA_UP_PRIOR_N", 0.0))

        # Auto-Export
        self.auto_export_on = _env_bool("OROMA_UP_AUTO_EXPORT", "1")
        self.exp_min_n      = _env_int("OROMA_UP_MIN_N", 3)
        self.exp_min_abs_q  = _env_float("OROMA_UP_MIN_ABS_Q", 0.15)
        self.exp_maj_conf   = _env_float("OROMA_UP_MAJ_CONF", 0.0)
        self.exp_cooldown_s = _env_int("OROMA_UP_COOLDOWN_S", 600)
        self._last_export_ts: Dict[Tuple[str, str, str], int] = {}
        self._last_export_cache_max = max(1000, int(_env_int("OROMA_UP_EXPORT_CACHE_MAX", 50000)))
        self._last_export_cache_gc_s = max(300, int(_env_int("OROMA_UP_EXPORT_CACHE_GC_S", 3600)))
        self._last_export_cache_last_gc = 0

        # Optionaler Adapter
        self.adapter = _UNI() if _HAVE_ADAPTER else None

        # ---------------------------------------------------------------------
        # DB-Read Cache (LRU) – optional, headless-friendly
        # ---------------------------------------------------------------------
        # Hintergrund:
        #   UI-/Game-Loops rufen choose() sehr häufig auf. Wenn parallel Writer-Jobs
        #   laufen (Dream/Export/Forgetting/Gaps), kann selbst im WAL-Modus kurzzeitig
        #   contention entstehen. Ein kleiner LRU-Cache reduziert DB-Reads massiv.
        #
        # Aktivierung:
        #   export OROMA_UP_CACHE_STATES=2000   # max States im Cache (0=aus)
        #
        # Hinweis:
        #   Cache ist pro-Process/Runtime. Bei Neustart leer (by design).
        self._cache_states_max = max(0, int(_env_int('OROMA_UP_CACHE_STATES', 0)))
        self._cache = {}  # type: ignore[var-annotated]  # state_hash -> {action_str:{q,n}}
        self._cache_order = []  # type: ignore[var-annotated]  # LRU order of state_hash

    # ------------------------------------------------------------------ #
    # WÄHLEN
    # ------------------------------------------------------------------ #
    def choose(self, state_hash: str, legal: List[int] | List[str], side: str = "oroma") -> Optional[int]:
        """
        Wählt eine Aktion aus LEGAL (Indices oder Strings).
        Heuristik: Sortiere nach q, dann n; optional Softmax über q (TEMP>0).

        Zusatz (optional): proaktive Knowledge-Gaps (throttled):
          - low_evidence:     n < OROMA_UP_GAPS_LOW_EVIDENCE_N
          - high_uncertainty: |q1-q2| < OROMA_UP_GAPS_UNCERTAINTY_EPS

        Robustheit:
          - PARTIAL TABLE FIX: Wenn DB nur einige LEGAL-Actions kennt, werden
            fehlende LEGAL-Actions mit Default (q=0,n=prior_n) aufgefüllt.
        """
        if not legal:
            return None

        # Text/Int robust normalisieren
        legal_str = [str(a) for a in legal]

        # Robust: cached_table muss IMMER definiert sein (auch im Erfolgsfall).
        # Hintergrund:
        #   In v3.7.x gibt es Betriebszustände, in denen die DB kurzfristig
        #   gelockt ist oder Fehler wirft. Dann nutzen wir best-effort den
        #   in-Process Cache. Der Cache ist optional (OROMA_UP_CACHE_STATES).
        #   UnboundLocalError darf hier nie passieren.
        cached_table = None
        try:
            cached_table = self._cache.get(state_hash) if hasattr(self, '_cache') else None
        except Exception:
            cached_table = None

        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT action, q, n, pos, neg, draw FROM policy_rules WHERE namespace=? AND state_hash=?",
                    (self.namespace, state_hash),
                )
                rows = cur.fetchall() or []
        except Exception as e:
            LOG.debug("choose: DB-Fehler: %s", e)
            rows = []

        had_rows = bool(rows) or bool(cached_table)

        # Policy für state_hash filtern auf LEGAL
        table: Dict[str, Dict[str, float]] = {}
        # Fallback: falls DB-Read gescheitert/gelockt ist, nutze Cache (best-effort).
        if (not rows) and cached_table:
            try:
                for _a, _v in cached_table.items():
                    if _a in legal_str:
                        table[_a] = {'q': float(_v.get('q', 0.0)), 'n': float(_v.get('n', self.prior_n))}
            except Exception as e:
                log_guard.log_suppressed(logger, key="universal_policy.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        for row in rows:
            a  = str(row["action"] if hasattr(row, "keys") else row[0])
            if a not in legal_str:
                continue
            try:
                q  = float(row["q"] if hasattr(row, "keys") else row[1])
                n  = float(row["n"] if hasattr(row, "keys") else row[2])
            except Exception:
                q, n = 0.0, 0.0
            table[a] = {"q": q, "n": n}

        # FIX: auch bei partieller DB-Abdeckung muessen ALLE legalen Actions existieren
        for a in legal_str:
            if a not in table:
                table[a] = {"q": 0.0, "n": self.prior_n}

        # Auswahl
        # Cache aktualisieren (LRU) – reduziert DB-Reads pro Zug erheblich.
        try:
            if getattr(self, '_cache_states_max', 0) > 0:
                self._cache[state_hash] = dict(table)
                if state_hash in self._cache_order:
                    self._cache_order.remove(state_hash)
                self._cache_order.append(state_hash)
                # LRU trim
                while len(self._cache_order) > int(self._cache_states_max):
                    _old = self._cache_order.pop(0)
                    if _old in self._cache:
                        del self._cache[_old]
        except Exception as e:
            log_guard.log_suppressed(logger, key="universal_policy.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        if self.temp and self.temp > 1e-9:
            # Softmax über q; leicht n gewichten
            vals = []
            for a in legal_str:
                q = table.get(a, {"q": 0.0})["q"]
                n = table.get(a, {"n": self.prior_n})["n"]
                vals.append((a, q + 1e-6 * n))
            m = max(v for _, v in vals)
            exps = [(a, math.exp((v - m) / float(self.temp))) for a, v in vals]
            Z = sum(x for _, x in exps) or 1.0
            import random
            r = random.random()
            acc = 0.0
            chosen = None
            for a, w in exps:
                acc += (w / Z)
                if r <= acc:
                    chosen = a
                    break
            if chosen is None:
                chosen = exps[-1][0]
        else:
            # Argmax (q, n)
            best = sorted(legal_str, key=lambda a: (table[a]["q"], table[a]["n"]), reverse=True)
            chosen = best[0]

        # Proaktive Gaps (defensiv, throttled)
        if had_rows and _UP_GAPS_ON and state_hash and chosen is not None:
            try:
                cn = float(table.get(chosen, {}).get("n", 0.0))
                cq = float(table.get(chosen, {}).get("q", 0.0))

                # low_evidence
                if cn >= 0.0 and cn < float(_UP_GAPS_LOW_EVIDENCE_N):
                    conf = max(0.0, min(1.0, cn / float(max(1, _UP_GAPS_LOW_EVIDENCE_N))))
                    _gap_emit(
                        kind="low_evidence",
                        namespace=self.namespace,
                        state_hash=str(state_hash),
                        desc=f"low_evidence: a={chosen} n={int(cn)} q={cq:.3f}",
                        confidence=conf,
                        meta={
                            "source": "universal_policy",
                            "namespace": self.namespace,
                            "state_hash": str(state_hash),
                            "action": str(chosen),
                            "n": float(cn),
                            "q": float(cq),
                            "legal": list(legal_str),
                        }
                    )

                # high_uncertainty
                a1, q1, n1, a2, q2, n2 = _best_two(table, legal_str)
                if a1 is not None and a2 is not None:
                    qgap = abs(float(q1) - float(q2))
                    if qgap < float(_UP_GAPS_UNCERTAINTY_EPS):
                        eps = float(max(1e-9, _UP_GAPS_UNCERTAINTY_EPS))
                        conf = max(0.0, min(1.0, 1.0 - (qgap / eps)))
                        _gap_emit(
                            kind="high_uncertainty",
                            namespace=self.namespace,
                            state_hash=str(state_hash),
                            desc=f"high_uncertainty: q-gap={qgap:.3f} (a1={a1} q1={q1:.3f} n1={int(n1)} | a2={a2} q2={q2:.3f} n2={int(n2)})",
                            confidence=conf,
                            meta={
                                "source": "universal_policy",
                                "namespace": self.namespace,
                                "state_hash": str(state_hash),
                                "a1": str(a1), "q1": float(q1), "n1": float(n1),
                                "a2": str(a2), "q2": float(q2), "n2": float(n2),
                                "chosen": str(chosen),
                                "legal": list(legal_str),
                            }
                        )
            except Exception as e:
                log_guard.log_suppressed(logger, key="universal_policy.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        return int(chosen) if str(chosen).isdigit() else chosen

    def choose_vec(self, vec: List[float], spec: Optional[Dict[str, Any]] = None,
                   legal: Optional[List[int] | List[str]] = None,
                   side: str = "oroma") -> Optional[int]:
        """
        Komfort-Funktion: Vektor → (hash via Adapter) → choose().
        """
        if not self.adapter:
            return None
        try:
            state_hash = self.hash_vec(vec, spec)
            if not state_hash:
                return None
            if legal is None:
                legal = [0, 1, 2, 3]  # typischer dir2-Raum; UI übergibt i.d.R. legal selbst
            return self.choose(state_hash, legal, side=side)
        except Exception:
            return None

    def hash_vec(self, vec: List[float], spec: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not self.adapter:
            return None
        try:
            state_hash, _perm, _inv = self.adapter.canonicalize(vec, spec or {})
            return state_hash
        except Exception:
            try:
                state_hash, _p, _ip = self.adapter.canonicalize(vec)  # tolerant
                return state_hash
            except Exception:
                return None

    # ------------------------------------------------------------------ #
    # LERNEN (BATCH)
    # ------------------------------------------------------------------ #
    def learn(self, item: Dict[str, Any]) -> None:
        self.learn_many([item])

    def learn_many(self, items: List[Dict[str, Any]]) -> None:
        """
        items: Liste von Dicts mit Feldern:
          - state_hash: str (erforderlich)
          - action_canon / action: int|str (erforderlich)
          - outcome: float|int → sign(+/-/0)
          - ts: int (optional; sonst now)
          - centroid: List[float] (optional)

        Ziel dieses Pfades:
          - Hot-Path ohne per-Item-SELECTs
          - atomarer UPSERT pro aggregiertem (state_hash,action)
          - Auto-Export erst nach Commit über kleine Kandidatenmenge
          - selektive Cache-Invalidierung nur für betroffene state_hashes
        """
        if not items:
            return

        decision = execution_mode.policy_mutation_decision(
            writer_id=self.writer_id,
            namespace=self.namespace,
            mutation_type="UPDATE_RULE_STATISTICS",
            boundary_authorized=False,
        )
        self.last_mutation_decision = decision.to_dict()
        if not decision.allowed:
            LOG.warning(
                "Policy-Mutation blockiert: mode=%s writer_id=%s namespace=%s reason=%s items=%d",
                decision.execution_mode, decision.writer_id, decision.namespace, decision.reason, len(items),
            )
            return

        now = int(time.time())
        try:
            aggregated: Dict[Tuple[str, str], Dict[str, Any]] = {}
            written_state_hashes: List[str] = []
            export_candidates: set[Tuple[str, str]] = set()
            centroid_map: Dict[Tuple[str, str], Optional[List[float]]] = {}
            for it in items:
                sh = str(it.get("state_hash", "")).strip()
                if not sh:
                    continue
                a_raw = it.get("action_canon", it.get("action", "0"))
                a = str(a_raw)
                out_f = float(it.get("outcome", 0.0))
                outcome = 1 if out_f > 1e-9 else -1 if out_f < -1e-9 else 0
                cen = it.get("centroid", None)
                cen_json = json.dumps(cen) if isinstance(cen, list) else None
                key = (sh, a)
                agg = aggregated.get(key)
                if agg is None:
                    agg = {
                        "n": 0,
                        "pos": 0,
                        "neg": 0,
                        "draw": 0,
                        "last_ts": 0,
                        "centroid": cen_json,
                    }
                    aggregated[key] = agg
                    written_state_hashes.append(sh)
                    centroid_map[key] = list(cen) if isinstance(cen, list) else None
                agg["n"] += 1
                if outcome > 0:
                    agg["pos"] += 1
                elif outcome < 0:
                    agg["neg"] += 1
                else:
                    agg["draw"] += 1
                try:
                    its = int(it.get("ts") or now)
                except Exception:
                    its = now
                if its > int(agg["last_ts"]):
                    agg["last_ts"] = its
                if cen_json and not agg.get("centroid"):
                    agg["centroid"] = cen_json
                # konservativer Prescan: nur potenzielle Export-Kandidaten vormerken
                n_inc = int(agg["n"])
                pos_inc = int(agg["pos"])
                neg_inc = int(agg["neg"])
                q_seed = 0.0
                if n_inc > 0:
                    q_seed = float(pos_inc - neg_inc) / float(n_inc)
                if n_inc >= int(self.exp_min_n) or abs(q_seed) >= float(self.exp_min_abs_q):
                    export_candidates.add(key)

            if not aggregated:
                return

            params_list: List[List[Any]] = []
            for (sh, a), agg in aggregated.items():
                n = int(agg["n"])
                pos_inc = int(agg["pos"])
                neg_inc = int(agg["neg"])
                draw_inc = int(agg["draw"])
                q_seed = 0.0
                if n > 0:
                    q_seed = float(pos_inc - neg_inc) / float(n)
                params_list.append([
                    self.namespace,
                    sh,
                    a,
                    n,
                    pos_inc,
                    neg_inc,
                    draw_inc,
                    q_seed,
                    int(agg["last_ts"] or now),
                    agg.get("centroid"),
                ])

            upsert_sql = """INSERT INTO policy_rules
                               (namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)
                               VALUES (?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(namespace, state_hash, action) DO UPDATE SET
                                   n = policy_rules.n + excluded.n,
                                   pos = policy_rules.pos + excluded.pos,
                                   neg = policy_rules.neg + excluded.neg,
                                   draw = policy_rules.draw + excluded.draw,
                                   q = CASE
                                           WHEN (policy_rules.n + excluded.n) > 0
                                           THEN CAST((policy_rules.pos + excluded.pos) - (policy_rules.neg + excluded.neg) AS REAL)
                                                / CAST(policy_rules.n + excluded.n AS REAL)
                                           ELSE 0.0
                                       END,
                                   last_ts = CASE
                                               WHEN excluded.last_ts > policy_rules.last_ts THEN excluded.last_ts
                                               ELSE policy_rules.last_ts
                                             END,
                                   centroid = COALESCE(excluded.centroid, policy_rules.centroid)
                            """

            use_dbw = bool(
                db_writer_client is not None
                and os.environ.get("OROMA_DBW_ENABLE", "0").strip().lower() not in ("0", "false", "no", "off")
            )
            if use_dbw:
                timeout_ms = int(getattr(sql_manager, "_dbw_timeout_ms", lambda kind='dream': 60000)("dream"))
                try:
                    dbw_chunk = max(1, int(os.environ.get("OROMA_POLICY_DBW_CHUNK", "25")))
                except Exception:
                    dbw_chunk = 25
                for i in range(0, len(params_list), dbw_chunk):
                    chunk = params_list[i:i + dbw_chunk]
                    db_writer_client.executemany(
                        upsert_sql,
                        chunk,
                        tag="universal_policy.learn_many",
                        priority="low",
                        timeout_ms=timeout_ms,
                        db="oroma",
                    )
            else:
                with sql_manager.get_conn() as conn:
                    cur = conn.cursor()
                    cur.executemany(upsert_sql, params_list)
                    conn.commit()

            # Nach erfolgreichem Commit/DBW-Flush nur betroffene Cache-Keys invalidieren.
            self._invalidate_cache_state_hashes(written_state_hashes)

            # Auto-Export entkoppelt vom Hot-Path: nur Kandidaten nach Commit scannen.
            try:
                self._post_commit_export_scan(export_candidates, centroid_map)
            except Exception as e:
                LOG.debug("post_commit_export_scan: %s", e)

            # Langläufer-Cache für Export-Cooldowns begrenzen.
            self._trim_last_export_cache()
        except Exception as e:
            LOG.debug("learn_many: DB-Fehler: %s", e)

    def _invalidate_cache_state_hashes(self, state_hashes: Iterable[str]) -> None:
        """Invalidiert nur die tatsächlich beschriebenen state_hashes im lokalen LRU-Cache."""
        try:
            if getattr(self, '_cache_states_max', 0) <= 0:
                return
            uniq = {str(sh) for sh in state_hashes if str(sh).strip()}
            if not uniq:
                return
            for sh in uniq:
                try:
                    self._cache.pop(sh, None)
                except Exception:
                    pass
            try:
                self._cache_order = [sh for sh in self._cache_order if sh not in uniq]
            except Exception:
                pass
        except Exception:
            return

    def _trim_last_export_cache(self) -> None:
        """Begrenzt den RAM-Cooldown-Cache für Auto-Export per TTL + Größenlimit."""
        try:
            now = int(time.time())
            last_gc = int(getattr(self, '_last_export_cache_last_gc', 0) or 0)
            if last_gc and (now - last_gc) < int(self._last_export_cache_gc_s):
                return
            self._last_export_cache_last_gc = now
            ttl_keep = max(int(self.exp_cooldown_s) * 4, int(self._last_export_cache_gc_s))
            min_keep_ts = now - ttl_keep
            pruned = {k: int(v) for k, v in self._last_export_ts.items() if int(v) >= min_keep_ts}
            if len(pruned) > int(self._last_export_cache_max):
                items_sorted = sorted(pruned.items(), key=lambda kv: int(kv[1]), reverse=True)
                pruned = dict(items_sorted[:int(self._last_export_cache_max)])
            self._last_export_ts = pruned
        except Exception:
            return

    def _post_commit_export_scan(self,
                                 candidate_pairs: Iterable[Tuple[str, str]],
                                 centroid_map: Dict[Tuple[str, str], Optional[List[float]]]) -> None:
        """Liest nach dem Commit nur potenzielle Export-Kandidaten aus der DB.

        Wichtig:
        - Kein Vorab-Read im Learn-Hot-Path.
        - Nur state_hashes aus diesem Batch.
        - SQL reduziert auf Rows, die Mindestschwellen bereits erfüllen.
        """
        if not self.auto_export_on:
            return
        pair_list = [(str(sh), str(a)) for (sh, a) in candidate_pairs if str(sh).strip()]
        if not pair_list:
            return
        pair_set = set(pair_list)
        state_hashes = sorted({sh for sh, _a in pair_list})
        if not state_hashes:
            return
        try:
            scan_chunk = max(10, int(_env_int('OROMA_UP_EXPORT_SCAN_CHUNK', 200)))
        except Exception:
            scan_chunk = 200
        exp_min_n = int(self.exp_min_n)
        exp_min_abs_q = float(self.exp_min_abs_q)
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            for i in range(0, len(state_hashes), scan_chunk):
                sh_chunk = state_hashes[i:i + scan_chunk]
                placeholders = ','.join(['?'] * len(sh_chunk))
                sql = f"""SELECT namespace, state_hash, action, n, pos, neg, draw, q, centroid
                            FROM policy_rules
                           WHERE namespace=?
                             AND state_hash IN ({placeholders})
                             AND n >= ?
                             AND ABS(q) >= ?"""
                params: List[Any] = [self.namespace] + sh_chunk + [exp_min_n, exp_min_abs_q]
                cur.execute(sql, params)
                rows = cur.fetchall() or []
                for row in rows:
                    sh = str(_row_val(row, 'state_hash', ''))
                    a = str(_row_val(row, 'action', ''))
                    if (sh, a) not in pair_set:
                        continue
                    n_new = _as_int(_row_val(row, 'n', 0), 0)
                    pos_new = _as_int(_row_val(row, 'pos', 0), 0)
                    neg_new = _as_int(_row_val(row, 'neg', 0), 0)
                    draw_new = _as_int(_row_val(row, 'draw', 0), 0)
                    q_new = _as_float(_row_val(row, 'q', 0.0), 0.0)
                    centroid = centroid_map.get((sh, a))
                    if centroid is None:
                        try:
                            cen_raw = _row_val(row, 'centroid', None)
                            if isinstance(cen_raw, str) and cen_raw.strip():
                                obj = json.loads(cen_raw)
                                centroid = obj if isinstance(obj, list) else None
                        except Exception:
                            centroid = None
                    try:
                        self._maybe_auto_export(sh, a, n_new, pos_new, neg_new, draw_new, q_new, 0, 0.0, centroid)
                    except Exception as e:
                        log_guard.log_suppressed(logger, key='universal_policy.pass.5', msg='Suppressed exception (was: pass)', exc=e, level=logging.WARNING)

    # ------------------------------------------------------------------ #
    # INTERN: Auto-Export → rules (Regelarchiv)
    # ------------------------------------------------------------------ #
    def _maybe_auto_export(self,
                           state_hash: str, action: str,
                           n_new: int, pos_new: int, neg_new: int, draw_new: int, q_new: float,
                           n_old: int, q_old: float,
                           centroid: Optional[List[float]]) -> None:
        if not self.auto_export_on:
            return
        if n_new < self.exp_min_n or abs(q_new) < self.exp_min_abs_q:
            return

        # Optional: Majority-Konfidenz
        if self.exp_maj_conf > 0.0:
            counts = sorted([pos_new, neg_new, draw_new], reverse=True)
            top = counts[0]; second = counts[1] if len(counts) > 1 else 0
            conf = (top - second) / float(max(1, n_new))
            if conf < self.exp_maj_conf:
                return

        key = (self.namespace, state_hash, action)
        now = int(time.time())
        last = self._last_export_ts.get(key, 0)
        if last and (now - last) < self.exp_cooldown_s:
            return

        try:
            if _archiv and hasattr(_archiv, "upsert_policy"):
                _archiv.upsert_policy(self.namespace, state_hash, action, float(q_new), int(n_new), centroid)  # type: ignore[misc]
            elif _archiv and hasattr(_archiv, "upsert"):
                _archiv.upsert(self.namespace, state_hash, action, float(q_new), int(n_new), centroid)  # type: ignore[misc]
            else:
                # Minimal-Fallback: direkt in rules upserten (stabiler Key)
                self._direct_archive_upsert(self.namespace, state_hash, action, float(q_new), int(n_new), centroid)
            self._last_export_ts[key] = now
        except Exception as e:
            LOG.debug("auto-export: %s", e)

    @staticmethod
    def _direct_archive_upsert(namespace: str, state_hash: str, action: str,
                               q: float, n: int, centroid: Optional[List[float]]) -> None:
        from core import sql_manager as _sql
        key = f'policy::{namespace}::{state_hash}::{action}'
        doc = {
            "type": "policy",
            "key": key,
            "namespace": namespace,
            "state_hash": state_hash,
            "action": action,
            "q": float(q),
            "n": int(n),
            "centroid": centroid if isinstance(centroid, list) else None,
            "updated_at": int(time.time()),
        }
        content_str = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        weight = (max(-1.0, min(1.0, float(q))) + 1.0) / 2.0
        now = time.time()
        with _sql.get_conn() as conn:  # type: ignore
            like_pat = f'%\"key\": \"{key}\"%'
            row = conn.execute("SELECT id FROM rules WHERE content LIKE ? LIMIT 1", (like_pat,)).fetchone()
            if row:
                rid = int(row["id"]) if hasattr(row, "keys") else int(row[0])
                conn.execute(
                    "UPDATE rules SET content=?, weight=?, active=1, updated_at=? WHERE id=?",
                    (content_str, float(weight), now, rid),
                )
            else:
                conn.execute(
                    """INSERT INTO rules (content, weight, active, exported, created_at, updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (content_str, float(weight), 1, 0, now, now),
                )

    # ------------------------------------------------------------------ #
    # STATS / HELPERS
    # ------------------------------------------------------------------ #
    def count_states(self) -> int:
        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(DISTINCT state_hash) FROM policy_rules WHERE namespace=?", (self.namespace,))
                row = cur.fetchone()
                return int(row[0] if row and not hasattr(row, "keys") else row["COUNT(DISTINCT state_hash)"])
        except Exception:
            return 0

    def count_rows(self) -> int:
        try:
            with sql_manager.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", (self.namespace,))
                row = cur.fetchone()
                return int(row[0] if row and not hasattr(row, "keys") else row["COUNT(*)"])
        except Exception:
            return 0


# =============================================================================
# Mini-CLI (optional): Stats / Einzellernen
# =============================================================================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ORÓMA Universal Policy (Runtime)")
    ap.add_argument("--namespace", "-n", type=str, default="game:any")
    ap.add_argument("--stats", action="store_true", help="Zeigt Policy-Stats an")
    ap.add_argument("--learn", type=str, default="", help="JSON-Item oder Pfad zu JSON-Datei mit Items[]")
    ap.add_argument("--choose", type=str, default="", help="state_hash für Probeauswahl")
    ap.add_argument("--legal", type=str, default="0,1,2,3", help="Kommagetrennte LEGAL-Aktionen")
    args = ap.parse_args()

    pol = Policy(namespace=args.namespace)

    if args.stats:
        print(json.dumps({
            "namespace": pol.namespace,
            "rows": pol.count_rows(),
            "states": pol.count_states(),
            "auto_export": pol.auto_export_on,
        }, ensure_ascii=False, indent=2))

    if args.learn:
        src = args.learn
        try:
            if os.path.isfile(src):
                with open(src, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                    pol.learn_many(data["items"])
                elif isinstance(data, list):
                    pol.learn_many(data)
                else:
                    pol.learn(data)  # einzelnes Item
            else:
                obj = json.loads(src)
                if isinstance(obj, list):
                    pol.learn_many(obj)
                else:
                    pol.learn(obj)
            print("learn: ok")
        except Exception as e:
            print("learn: fehler:", e)

    if args.choose:
        legal = [s.strip() for s in (args.legal or "0,1,2,3").split(",") if s.strip() != ""]
        a = pol.choose(args.choose, legal)
        print(json.dumps({"state_hash": args.choose, "legal": legal, "action": a}, ensure_ascii=False))
