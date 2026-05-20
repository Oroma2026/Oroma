#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Datei:    /opt/ai/oroma/core/decision_engine.py
# Projekt:  ORÓMA (Runtime Decision · Rules + Policy + Heuristik)
# Modul:    DecisionEngine – deterministische Aktionswahl aus Regelarchiv (rules) + PolicyEngine (policy_rules) + Adapter-Heuristik
# Version:  v3.7.3
# Stand:    2026-01-10
# Autor:    ORÓMA · KI-JWG-X1
# Lizenz:   MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul entscheidet zur Laufzeit eine Aktion aus einem State-Vektor (Adapter-Perspektive).
# Es kombiniert mehrere Wissensquellen in stabiler Priorität:
#
#   1) JSON-Prädikatregeln aus Regelarchiv (rules.content als JSON, type="predicate")
#   2) StateHash-Regeln aus Regelarchiv (klassisches Stringformat) + JSON-Policy-Exports (type="policy")
#   3) Gelernte Policy aus policy_rules via PolicyEngine(adapter)
#   4) Domänenspezifische Heuristik des Adapters (adapter.fallback_action)
#
# Ziel:
# - stabile, erklärbare Auswahl ohne „Heavy ML Runtime“ (tabellarisch/regelorientiert)
# - deterministisches Ranking (kein Zufall im Regel-Scoring)
# - symmetrie-stabil (Kanonraum + Rückmapping in Originalperspektive)
#
# KANONISIERUNG / SYMMETRIEN (KERNKONZEPT)
# ────────────────────────────────────────
# Viele Domänen besitzen Symmetrien (Rotation/Spiegelung). Der Adapter kann:
#   canonicalize(vec) -> (state_hash, perm, inv_perm)
#
# ORÓMA speichert Regeln/Policy typischerweise im Kanonraum.
# Daher:
#   - Matching passiert im Kanonraum (state_hash + action_can)
#   - Rückgabe wird vor dem Return in die Originalperspektive gemappt:
#       adapter.map_action_through_perm(action_can, inv_perm)
#     (nur wenn Adapter diese Methode anbietet; sonst action unverändert)
#
# DATENQUELLEN / DB-TABELLEN
# ──────────────────────────
# - rules        : Regelarchiv (classic strings + JSON rules + JSON policy exports)
# - policy_rules : gelernte Policy (PolicyEngine, schneller Lookup/Heuristik)
#
# Zugriff erfolgt über:
#   from core import sql_manager
#   with sql_manager.get_conn() as conn: ...
#
# RULES-LOADING: QUERY + CACHE (AKTUELLER CODE)
# ────────────────────────────────────────────
# Regeln werden pro Namespace geladen und kurz gecached, um DB-Load in schnellen Loops zu senken:
#   _RULE_CACHE_TTL = 3.0 Sekunden
#   _RULE_CACHE[namespace] = (ts, parsed_rule_items)
#
# DB Query (Namespace-Filter):
#   SELECT content, weight, active FROM rules
#   WHERE active=1 AND (content LIKE ? OR content LIKE ?)
#     params:
#       ( namespace + "%",  f'%"namespace": "{namespace}"%' )
#
# → Damit werden sowohl klassische String-Regeln (beginnen mit "ns::...") als auch JSON-Regeln,
#   die ein Feld "namespace" enthalten, gefunden.
#
# REGELFORMATE, DIE DIESE DATEI TATSÄCHLICH PARST
# ───────────────────────────────────────────────
# A) Klassisches Stringformat (Regex _RULE_RE):
#   "<namespace> :: IF state='<state_hash>' THEN action='<action>'  ... q=<float> ... n=<int>"
#   - state_hash und action werden als Kanonraum interpretiert (in_canonical=True)
#
# B) JSON-Prädikatregel (type="predicate"):
#   {
#     "type": "predicate",
#     "namespace": "game:tictactoe",
#     "if": {
#       "tags_all": [...],
#       "tags_any": [...],
#       "eq": { ... }
#     },
#     "then": {"action": "<action>"},
#     "in_canonical": true|false,
#     "score": {"q":..., "n":...}
#   }
#   - Matching basiert auf Adapter-Tags/StateDict:
#       adapter.state_tags(vec) -> List[str]   (optional)
#       adapter.state_dict(vec) -> Dict        (optional)
#   - Prädikate werden Score-sortiert; erste legale Regel gewinnt.
#
# C) JSON-Policy-Export (type="policy"):
#   { "type":"policy", "namespace":"...", "state_hash":"...", "action":"...", "q":..., "n":... }
#   - wird wie klassische Regel behandelt (state_hash + action_can)
#   - in_canonical=True (fest im Code)
#
# SCORING / RANKING (DETERMINISTISCH)
# ───────────────────────────────────
# Regeln werden in _RuleItem umgewandelt und nach score absteigend sortiert.
# Score-Funktion (aktuell im Code):
#   score = 0.6*weight + 0.35*map(q) + 0.05*bump(n)
#   map(q): [-1..+1] -> [0..1] (default 0.5)
#   bump(n): min(n, 2000) * 0.001
# → Keine Zufallsanteile, stabil reproduzierbar.
#
# TICTACTOE SOLVER (OPTIONALER SAFETY-NET, REALER CODEPFAD)
# ────────────────────────────────────────────────────────
# Für TicTacToe existiert eine spezielle Fassade:
#   class TTTDecision(DecisionEngine)
#   - nutzt TTTAdapter()
#   - aktiviert optional einen perfekten Minimax-Solver (core.ttt_solver),
#     um „falsche“ Self-Play-Dynamiken zu vermeiden (optimal play → Draw).
#
# Steuerung:
#   OROMA_TTT_SOLVER=0  → deaktiviert
#   Default: aktiv (wenn core.ttt_solver importierbar ist)
#
# EXECUTION FLOW (choose_action)
# ──────────────────────────────
# 0) legal actions im Originalraum: adapter.legal_actions(vec)
# 1) canonicalize(vec) → state_hash, perm, inv_perm
# 1b) optional TicTacToe solver (nur wenn use_ttt_solver=True und namespace game:tictactoe)
# 2a) predicate rules (tags/state_dict) → erste passende legale Regel (Score-sortiert)
# 2b) state_hash rules (classic/policy) → erste passende legale Regel (Score-sortiert)
# 3) PolicyEngine(adapter).choose_action(vec) → nur wenn legal
# 4) adapter.fallback_action(vec) → nur wenn legal
# → sonst None
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# class DecisionEngine:
#   - choose_action(state_vec_own_perspective: List[float]) -> Optional[str]
#   - choose_action_from_board(board_obj: Any) -> Optional[str]   (adapter.vectorize_board)
#
# class TTTDecision(DecisionEngine):
#   - Convenience für TicTacToe, inkl. ENV-basierter Solver-Aktivierung
#
# INVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ─────────────────────────────────────────
# - Determinismus: Scoring + Ranking ohne Zufall.
# - Kanonraum-Handling muss bleiben (sonst Regel/Policy Treffer falsch).
# - Rule-Cache TTL muss klein bleiben (DB-Last senken, aber schnell aktualisieren).
# - Best-effort: Adapter optional state_tags/state_dict, Fehler werden suppressed (log_guard).
# - Legal-Check muss strikt bleiben (keine illegalen Aktionen zurückgeben).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core import sql_manager
from core.policy_engine import PolicyEngine
from core.ttt_adapter import TTTAdapter  # Beispiel-Fassade unten

import logging
from core import log_guard
logger = logging.getLogger(__name__)
# Optional: Perfekter TicTacToe-Solver (Minimax).
#
# Hinweis: Der Solver wird NICHT global erzwungen, sondern nur genutzt,
# wenn eine Engine-Instanz `use_ttt_solver=True` setzt. Damit bleibt die
# Default-Logik (Regelarchiv → Policy → Heuristik) für alle anderen
# Namespaces unverändert.
try:
    from core import ttt_solver  # type: ignore
except Exception:
    ttt_solver = None  # type: ignore

# --------------------------- Klassisches Regel-Pattern ------------------------

_RULE_RE = re.compile(
    r"^(?P<ns>[^:]+:[^ ]+)\s*::\s*IF\s+state='(?P<state>[^']+)'\s*THEN\s*action='(?P<action>[^']+)'(?:.*?q\s*=\s*(?P<q>-?\d+(?:\.\d+)?))?(?:.*?n\s*=\s*(?P<n>\d+))?",
    re.IGNORECASE,
)

def _score(weight: float, q: Optional[float], n: Optional[int]) -> float:
    """
    Kombiniert Archiv-Gewicht (0..1), optional q∈[-1..+1] und n (Häufigkeit).
    Stabil, deterministisch, leicht konservativ.
    """
    w = max(0.0, min(1.0, float(weight)))
    qn = 0.5 * (float(q) + 1.0) if q is not None else 0.5  # map [-1..1]→[0..1], default 0.5
    nn = min(int(n or 0), 2000)
    bump = 0.001 * nn  # 0..2.0 capped
    return 0.6 * w + 0.35 * qn + 0.05 * bump

# --------------------------- Prädikatsevaluierung -----------------------------

def _adapter_tags(adapter: Any, vec: List[float]) -> List[str]:
    try:
        tags = adapter.state_tags(vec)  # type: ignore[attr-defined]
        if isinstance(tags, list):
            return [str(t) for t in tags]
    except Exception as e:
        log_guard.log_suppressed(logger, key="decision_engine.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
    return []

def _adapter_dict(adapter: Any, vec: List[float]) -> Dict[str, Any]:
    try:
        d = adapter.state_dict(vec)  # type: ignore[attr-defined]
        return dict(d) if isinstance(d, dict) else {}
    except Exception:
        return {}

def _pred_match(pred_if: Dict[str, Any], tags: List[str], state: Dict[str, Any]) -> bool:
    """
    Unterstützte Felder in pred_if:
      • tags_all: [..] – alle müssen in tags enthalten sein
      • tags_any: [..] – mindestens einer muss enthalten sein
      • eq: {k:v} – state[k] muss exakt v sein
    """
    try:
        t_all = pred_if.get("tags_all") or []
        t_any = pred_if.get("tags_any") or []
        eq    = pred_if.get("eq") or {}
        if t_all and not all(t in tags for t in t_all):
            return False
        if t_any and not any(t in tags for t in t_any):
            return False
        for k, v in dict(eq).items():
            if state.get(k) != v:
                return False
        return True
    except Exception:
        return False

# --------------------------- Regel-Fetch & Parsing ----------------------------

class _RuleItem:
    """
    Vereinheitlichte Repräsentation einer Regel:
     - kind: 'predicate' | 'classic'
     - ns: Namespace
     - state_hash (nur für classic; im Kanonraum)
     - pred_if (nur für predicate)
     - action_can: Aktion im Kanonraum (wird über inv_perm zurückgeführt)
     - in_canonical: bool (default True)
     - score: float
    """
    __slots__ = ("kind", "ns", "state_hash", "pred_if", "action_can", "in_canonical", "score")

    def __init__(self, kind: str, ns: str, *, state_hash: Optional[str] = None,
                 pred_if: Optional[Dict[str, Any]] = None, action_can: Optional[str] = None,
                 in_canonical: bool = True, score: float = 0.0):
        self.kind = kind
        self.ns = ns
        self.state_hash = state_hash
        self.pred_if = pred_if
        self.action_can = action_can
        self.in_canonical = in_canonical
        self.score = float(score)

def _parse_row_to_rules(row) -> List[_RuleItem]:
    """
    Nimmt eine Zeile aus 'rules' (content, weight, active) und gibt 0..1 _RuleItem(s) zurück.
    Versteht JSON-Prädikat, JSON-Policy (Export) und klassisches Stringformat.
    """
    content = row["content"] if hasattr(row, "keys") else row[0]
    weight  = float((row["weight"] if hasattr(row, "keys") else row[1]) or 0.0)
    items: List[_RuleItem] = []

    # (1) JSON?
    try:
        d = json.loads(content)
        if isinstance(d, dict) and d.get("type") == "predicate":
            ns = str(d.get("namespace", "")).strip()
            if not ns:
                return items
            then = d.get("then") or {}
            action_can = str(then.get("action")) if "action" in then else None
            if not action_can:
                return items
            sc = d.get("score") or {}
            q = sc.get("q"); n = sc.get("n")
            s = _score(weight, float(q) if q is not None else None,
                       int(n) if n is not None else None)
            items.append(_RuleItem(
                kind="predicate",
                ns=ns,
                pred_if=d.get("if") or {},
                action_can=action_can,
                in_canonical=bool(d.get("in_canonical", True)),
                score=s
            ))
            return items

        # (1b) JSON-Policy-Export (aus UniversalPolicy/Regelarchiv)
        #      Wird wie eine klassische Regel behandelt (state_hash + action).
        if isinstance(d, dict) and str(d.get("type") or "") == "policy":
            ns = str(d.get("namespace", "")).strip()
            st = str(d.get("state_hash", "")).strip()
            act = str(d.get("action", "")).strip()
            if ns and st and act:
                sc_q = d.get("q")
                sc_n = d.get("n")
                s = _score(weight, float(sc_q) if sc_q is not None else None,
                           int(sc_n) if sc_n is not None else None)
                items.append(_RuleItem(
                    kind="policy",
                    ns=ns,
                    state_hash=st,
                    action_can=act,
                    in_canonical=True,
                    score=s
                ))
                return items
    except Exception as e:
        log_guard.log_suppressed(logger, key="decision_engine.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

    # (2) Klassisch (String)
    m = _RULE_RE.search(str(content))
    if not m:
        return items
    ns = m.group("ns"); st = m.group("state"); act = m.group("action")
    q = float(m.group("q")) if m.group("q") is not None else None
    n = int(m.group("n")) if m.group("n") is not None else None
    s = _score(weight, q, n)
    items.append(_RuleItem(
        kind="classic",
        ns=ns,
        state_hash=st,
        action_can=act,
        in_canonical=True,
        score=s
    ))
    return items

# Kleiner Cache (ein paar Sekunden), um DB-Lookups in engen Taktungen zu vermeiden
_RULE_CACHE: Dict[str, Tuple[float, List[_RuleItem]]] = {}
_RULE_CACHE_TTL = 3.0  # Sekunden

def _load_rules_for_ns(namespace: str) -> List[_RuleItem]:
    now = time.time()
    cached = _RULE_CACHE.get(namespace)
    if cached and (now - cached[0] <= _RULE_CACHE_TTL):
        return cached[1]

    out: List[_RuleItem] = []
    try:
        with sql_manager.get_conn() as conn:
            rows = conn.execute(
                "SELECT content, weight, active FROM rules WHERE active=1 AND (content LIKE ? OR content LIKE ?)",
                (namespace + "%", f'%"namespace": "{namespace}"%')
            ).fetchall() or []
    except Exception:
        rows = []

    for r in rows:
        try:
            out.extend(_parse_row_to_rules(r))
        except Exception:
            continue

    # Score-absteigend
    out.sort(key=lambda x: float(x.score), reverse=True)
    _RULE_CACHE[namespace] = (now, out)
    return out

# --------------------------- Kern-Engine -------------------------------------

class DecisionEngine:
    """
    Regelarchiv-gestützte Auswahl (mit Prädikat-Track):
      1) JSON-Prädikate (rules.content als JSON, type='predicate') – Score-sortiert
      2) State-Hash-Regeln (klassisch + exportierte Policy-Regeln) – Score-sortiert
      3) Fallback: PolicyEngine(adapter)
      4) Fallback: adapter.fallback_action()
    Alle Aktionen aus dem KANONRAUM werden via inv_perm in die Originalperspektive zurückgeführt.
    """

    def __init__(self, adapter: Any):
        self.adapter = adapter
        self.namespace = getattr(adapter, "namespace", "default")

    # --- Hilfsfunktionen für Aktionsmapping ---
    def _map_can_to_orig(self, action_can: str, inv_perm: Optional[List[int]]) -> str:
        # Wenn der Adapter mapping kann, nutze inv_perm (Kanonraum → Original)
        try:
            if inv_perm is not None and hasattr(self.adapter, "map_action_through_perm"):
                return self.adapter.map_action_through_perm(action_can, inv_perm)  # type: ignore[attr-defined]
        except Exception as e:
            log_guard.log_suppressed(logger, key="decision_engine.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)
        return action_can

    def choose_action(self, state_vec_own_perspective: List[float]) -> Optional[str]:
        """
        Erwartet State-Vektor in ADAPTER-Perspektive (z. B. 9D TTT).
        """
        # 0) Legal im Original bestimmen
        legal = set(map(str, self.adapter.legal_actions(state_vec_own_perspective)))

        # 1) Kanonisieren (für Archiv/Mapping)
        try:
            state_hash, perm, inv_perm = self.adapter.canonicalize(state_vec_own_perspective)
        except Exception:
            state_hash, perm, inv_perm = ("", None, None)

        # 1b) Optional: TicTacToe-Minimax (Teacher/Safety-Net)
        #
        # Motivation:
        #  - In gelernten Policies kann TicTacToe oft "überoptimistisch" werden
        #    (Startspieler gewinnt zu häufig), was in tools/ttt_eval.py zu 0% Draws
        #    führt.
        #  - Der Solver erzwingt perfekte Defensive (optimal play → Remis).
        #
        # Aktivierung:
        #  - Diese Engine nutzt den Solver nur, wenn `self.use_ttt_solver=True`.
        #    Das setzt TTTDecision standardmäßig (abschaltbar via ENV).
        if (
            getattr(self, "use_ttt_solver", False)
            and self.namespace == "game:tictactoe"
            and ttt_solver is not None
            and state_hash
            and legal
        ):
            try:
                r = ttt_solver.best_action_from_state_hash(state_hash)  # type: ignore[attr-defined]
                if r:
                    a_can, _val = r
                    a_orig = self._map_can_to_orig(str(a_can), inv_perm)
                    if a_orig in legal:
                        return a_orig
            except Exception as e:
                log_guard.log_suppressed(logger, key="decision_engine.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        # 2) Regeln laden (Namespace)
        rules = _load_rules_for_ns(self.namespace)

        # 2a) PRÄDIKATE: Tags/StateDict aus ORIGINAL-Perspektive (abstrakt, domänenübergreifend)
        tags = _adapter_tags(self.adapter, state_vec_own_perspective)
        sdict = _adapter_dict(self.adapter, state_vec_own_perspective)

        for it in rules:
            if it.kind != "predicate" or it.ns != self.namespace:
                continue
            if not it.action_can:
                continue
            pred_if = it.pred_if or {}
            if not _pred_match(pred_if, tags, sdict):
                continue
            # Aktion ggf. aus Kanonraum zurückführen
            act_orig = self._map_can_to_orig(it.action_can, inv_perm) if it.in_canonical else it.action_can
            if act_orig in legal:
                return act_orig  # erste passende Prädikatsregel gewinnt (Score-sortiert)

        # 2b) STATE-HASH: Match im Kanonraum (klassisch + exportierte Policy-Regeln), dann Mapping zurück
        #     Hinweis: "policy" kommt als JSON-Export aus UniversalPolicy/Regelarchiv.
        for it in rules:
            if it.kind not in ("classic", "policy") or it.ns != self.namespace:
                continue
            if it.state_hash != state_hash or not it.action_can:
                continue
            act_orig = self._map_can_to_orig(it.action_can, inv_perm)
            if act_orig in legal:
                return act_orig  # erste legale Top-Regel

        # 3) PolicyEngine (falls Regeln nicht greifen)
        try:
            pe = PolicyEngine(self.adapter)
            act = pe.choose_action(state_vec_own_perspective)
            if act is not None and str(act) in legal:
                return str(act)
        except Exception as e:
            log_guard.log_suppressed(logger, key="decision_engine.pass.5", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        # 4) Heuristik
        try:
            act = self.adapter.fallback_action(state_vec_own_perspective)
            if act is not None and str(act) in legal:
                return str(act)
        except Exception as e:
            log_guard.log_suppressed(logger, key="decision_engine.pass.6", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING)

        return None

    # Komfort: direkt vom „Board“-Objekt (z. B. ["X","O","",...]) wählen
    def choose_action_from_board(self, board_obj: Any) -> Optional[str]:
        vec = self.adapter.vectorize_board(board_obj)
        return self.choose_action(vec)

# --------- Beispiel-Fassade für TicTacToe -----------------------------------

class TTTDecision(DecisionEngine):
    def __init__(self):
        super().__init__(TTTAdapter())

        # ------------------------------------------------------------------
        # TicTacToe: optionaler perfekter Solver
        # ------------------------------------------------------------------
        # Default: EIN (für stabile Defensive + reproduzierbare Evaluation).
        # Abschalten:
        #   OROMA_TTT_SOLVER=0
        #
        # Warum default ON?
        #   In Self-Play ohne perfekte Defensive können in policy_rules Werte
        #   entstehen, die eine "Startspieler gewinnt"-Welt modellieren.
        #   Das ist für TicTacToe falsch (optimal play → Draw). Der Solver
        #   verhindert, dass das Spiel als Trainingssignal "kaputt" lernt.
        v = (os.environ.get("OROMA_TTT_SOLVER") or "1").strip().lower()
        self.use_ttt_solver = v in ("1", "true", "yes", "on")