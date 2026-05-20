#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/ui/tictactoe_ui.py
# Projekt:   ORÓMA – Headless UI (Flask)
# Modul:     TicTacToe Arena (Universal Policy + Exploration)
# Version:   v3.7.3-r1 (UniversalPolicy, D4-Canon, DB-UPSERT Feedback)
# Stand:     2025-11-10
# Autor:     ORÓMA · KI-JWG-X1
# =============================================================================
#
# ZWECK
# ─────
# Zwei präzise ORÓMA-vs-ORÓMA Modi mit Universal Policy (UP):
#   • oroma_vs_oroma_explore  → Policy + begrenzte Exploration/STM (Lernmodus)
#   • oroma_vs_oroma_policy   → Policy-only (Benchmark; ≈100% Remis bei guter Policy)
#
# NEU in r1:
#   • UniversalPolicyShim: nutzt core.universal_policy.* falls vorhanden,
#     sonst Fallback auf bestehende Policy-Engine (TTTAdapter), sonst Heuristik.
#   • Side-aware State + D4-Kanonisierung → stabiler state_hash (perspektivisch)
#   • Action-Mapping zwischen Original- und Kanon-Raum (M und M_inv)
#   • Direkter DB-UPSERT in policy_rules (n/pos/neg/draw/q/last_ts)
#   • Zähleranzeige policy_rules + Archiv-Regeln wie gehabt
#
# ENV
# ───
#   OROMA_TTT_MODE_DEFAULT=oroma_vs_oroma_explore|oroma_vs_oroma_policy|...
#   OROMA_TTT_USE_POLICY=1
#   OROMA_TTT_EPS=0.08
#   OROMA_TTT_STM_SIZE=500
#   OROMA_TTT_STM_FORCE_AT=3
#   OROMA_TTT_EPS_MOVES_PER_GAME=1
#   OROMA_TTT_EPS_DECAY=1.0
#   OROMA_TTT_EPS_MIN=0.00
#   OROMA_TTT_CANON_STM=true|false       (Default true)
#   OROMA_TTT_LOSS_PRESSURE=2.0
#
# SICHERHEIT & ROBUSTHEIT
# ───────────────────────
#   • Alle DB/Policy-Aufrufe in try/except, keine Crashes im Loop
#   • Thread-sicherer Game-Loop via Lock
#   • Alias-Normalisierung: oroma_solo/oroma_vs_oroma → oroma_vs_oroma_explore
# =============================================================================

from __future__ import annotations
import os, json, time, random, logging, threading
from collections import deque, Counter
from typing import Optional, List, Dict, Any, Tuple
from flask import Blueprint, jsonify, render_template, request, current_app
import logging
from core.log_guard import log_suppressed

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG = logging.getLogger("oroma.tictactoe")
if not LOG.handlers or os.environ.get("OROMA_TTT_LOG") == "1":
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [TicTacToe] %(message)s"))
    LOG.addHandler(sh)
lvl = os.environ.get("OROMA_TTT_LOGLEVEL", "INFO").upper()
LOG.setLevel(getattr(logging, lvl, logging.INFO))
LOG.debug("=== TicTacToe v3.7.3-r1 initialisiert (Level=%s) ===", logging.getLevelName(LOG.level))

# -----------------------------------------------------------------------------
# Core-Module (best effort)
# -----------------------------------------------------------------------------
try:
    from core.snappattern import SnapPattern
    from core import sql_manager, reward
    try:
        from core import db_writer_client as db_writer_client
    except Exception:
        db_writer_client = None  # type: ignore
    try:
        from core import roter_faden  # optional
    except Exception:
        roter_faden = None  # type: ignore
    LOG.debug("✅ Core-Module geladen (snappattern/sql_manager/reward).")
except Exception as e:
    LOG.warning("⚠️ Core-Importfehler: %s", e)
    SnapPattern = None  # type: ignore
    sql_manager = reward = roter_faden = None  # type: ignore

# Legacy Policy-Engine (Fallback)
try:
    from core.policy_engine import PolicyEngine
    from core.ttt_adapter import TTTAdapter
    _HAVE_LEGACY_POLICY = True
except Exception:
    PolicyEngine = None  # type: ignore
    TTTAdapter = None    # type: ignore
    _HAVE_LEGACY_POLICY = False

# Universal Policy (bevorzugt)
try:
    import core.universal_policy as upol  # erwartet .Policy o.ä.
    _HAVE_UP = True
except Exception:
    upol = None  # type: ignore
    _HAVE_UP = False

# -----------------------------------------------------------------------------
# Defaults / Profile
# -----------------------------------------------------------------------------
DEFAULT_SPEED = os.environ.get("OROMA_TTT_SPEED", "normal").lower()
DEF_TICK = 0.15 if DEFAULT_SPEED == "normal" else 0.0
DEF_END  = 0.50 if DEFAULT_SPEED == "normal" else 0.0

USE_POLICY_DEFAULT = os.environ.get("OROMA_TTT_USE_POLICY", "1").strip().lower() in ("1","true","on","yes")
EPS_EXPLORATION    = max(0.0, min(1.0, float(os.environ.get("OROMA_TTT_EPS", "0.08") or "0.08")))
STM_SIZE           = max(0, int(os.environ.get("OROMA_TTT_STM_SIZE", "500") or "500"))
STM_FORCE          = max(0, int(os.environ.get("OROMA_TTT_STM_FORCE_AT", "3") or "3"))

EPS_DECAY = float(os.environ.get("OROMA_TTT_EPS_DECAY", "1.0") or "1.0")
EPS_MIN   = float(os.environ.get("OROMA_TTT_EPS_MIN",   "0.00") or "0.00")
EPS_MOVES_PER_GAME = int(os.environ.get("OROMA_TTT_EPS_MOVES_PER_GAME", "1") or "1")

_MODE_DEFAULT_RAW = (os.environ.get("OROMA_TTT_MODE_DEFAULT", "oroma_vs_oroma_explore") or "oroma_vs_oroma_explore").lower()

def _normalize_default_mode(m: str) -> str:
    return "oroma_vs_oroma_explore" if m in ("oroma_vs_oroma", "oroma_solo") else m

def _normalize_mode(m: str) -> str:
    m = (m or "").lower()
    if m in ("oroma_vs_oroma", "oroma_solo"):
        return "oroma_vs_oroma_explore"
    return m

tictactoe_bp = Blueprint("tictactoe_ui", __name__, url_prefix="/tictactoe")

# =============================================================================
# D4-Kanonisierung (8 Symmetrien)
# =============================================================================
_CANON_STM = os.environ.get("OROMA_TTT_CANON_STM", "true").strip().lower() not in ("0","false","no","off")

# Index-Layout:
#  0 1 2
#  3 4 5
#  6 7 8
_D4 = [
    [0,1,2,3,4,5,6,7,8],          # ident
    [2,5,8,1,4,7,0,3,6],          # rot90
    [8,7,6,5,4,3,2,1,0],          # rot180
    [6,3,0,7,4,1,8,5,2],          # rot270
    [2,1,0,5,4,3,8,7,6],          # flipX
    [6,7,8,3,4,5,0,1,2],          # flipY
    [0,3,6,1,4,7,2,5,8],          # flipDiag
    [8,5,2,7,4,1,6,3,0],          # flipAntiDiag
]

def _inv_perm(M: List[int]) -> List[int]:
    inv = [0]*9
    for i, j in enumerate(M): inv[j] = i
    return inv

def _board_to_vec(board: List[str], side: str) -> List[int]:
    """X=+1, O=-1, leer=0 — aus Sicht von 'side' (O → Vorzeichen invertiert)."""
    raw = [1 if v == "X" else -1 if v == "O" else 0 for v in board]
    return raw if side == "X" else [-x for x in raw]

def _canon_board_with_map(board: List[str], side: str) -> Tuple[List[int], List[int], List[int]]:
    """
    Liefert kanonisierten Board-Vektor (aus Sicht 'side') und die verwendete Permutation M
    (orig→canon) sowie deren Inverse M_inv (canon→orig).
    """
    base = _board_to_vec(board, side)
    best_vec: Optional[List[int]] = None
    best_M: Optional[List[int]] = None

    for M in _D4:
        bt = [0]*9
        for i in range(9):  # lege base[i] nach Position M[i]
            bt[M[i]] = base[i]
        if (best_vec is None) or (tuple(bt) < tuple(best_vec)):
            best_vec = bt; best_M = M
    M = best_M or _D4[0]
    return best_vec or base, M, _inv_perm(M)

def _state_hash(board: List[str], side: str) -> Tuple[str, List[int], List[int]]:
    """Erzeugt stabilen, kanonischen state_hash + liefert M/M_inv."""
    vec, M, M_inv = _canon_board_with_map(board, side)
    # kompakter Hash: String der 9 Zahlen (-1,0,1), z.B. "1,0,0,0,-1,..."
    sh = "v1|" + ",".join(str(x) for x in vec)
    return sh, M, M_inv

# =============================================================================
# Kurzzeitgedächtnis (STM) – wie zuvor (leicht gekürzt)
# =============================================================================
class ShortTermMemory:
    def __init__(self, capacity: int = 500):
        self.capacity = int(capacity)
        self.games: deque[Tuple[Tuple[int, ...], str]] = deque(maxlen=self.capacity)
        self.prefix_counter: Counter[Tuple[Tuple[int, ...], str]] = Counter()
        self.loss_prefix_counter: Counter[Tuple[Tuple[int, ...], str]] = Counter()

    def _canon_prefix(self, prefix: List[int], side: str) -> Tuple[int, ...]:
        if not (_CANON_STM and prefix): return tuple(prefix or [])
        best: Optional[Tuple[int, ...]] = None
        # Für STM genügt identisches D4 wie oben; side hat keinen Einfluss auf Indexfolge
        for M in _D4:
            t = tuple(M[i] for i in prefix)
            if (best is None) or (t < best): best = t
        return best or tuple(prefix or [])

    def _inc_prefixes(self, seq: List[int], side: str, delta: int, *, into: str = "seen"):
        keyseq = self._canon_prefix(seq, side)
        ctr = self.prefix_counter if into == "seen" else self.loss_prefix_counter
        ctr[(keyseq, side)] += delta
        if ctr[(keyseq, side)] <= 0:
            del ctr[(keyseq, side)]

    def note_game(self, seq: List[int], result: Optional[str], start_side: str):
        if not seq: return
        if self.capacity > 0 and len(self.games) == self.capacity:
            old_seq, _ = self.games[0]
            self._inc_prefixes(list(old_seq), start_side, -1, into="seen")
        if self.capacity > 0:
            self.games.append((tuple(seq), result or ""))
            self._inc_prefixes(list(seq), start_side, +1, into="seen")

    def count(self, prefix: List[int], side: str) -> int:
        return int(self.prefix_counter.get((self._canon_prefix(prefix, side), side), 0))

    def count_loss(self, prefix: List[int], side: str) -> int:
        return int(self.loss_prefix_counter.get((self._canon_prefix(prefix, side), side), 0))

    def clear(self):
        self.games.clear(); self.prefix_counter.clear(); self.loss_prefix_counter.clear()

    def stats(self) -> Dict[str, Any]:
        return {"size": self.capacity, "stored": len(self.games), "force_at": STM_FORCE, "top_prefix_hits": []}

# =============================================================================
# Universal Policy Shim
# =============================================================================
class UniversalPolicyShim:
    """
    Bevorzugt core.universal_policy.Policy; fällt zurück auf Legacy PolicyEngine(TTTAdapter);
    sonst liefert .choose() immer None (Heuristik übernimmt), .learn() no-op.
    """
    def __init__(self, namespace: str = "game:tictactoe"):
        self.namespace = namespace
        self.enabled = USE_POLICY_DEFAULT and (_HAVE_UP or _HAVE_LEGACY_POLICY)
        self._impl = None
        if _HAVE_UP:
            try:
                # erwartete API: Policy(namespace=...) mit .choose(state_hash, legal_actions, side) und .learn(...)
                if hasattr(upol, "Policy"):
                    self._impl = upol.Policy(namespace=self.namespace)  # type: ignore[attr-defined]
            except Exception as e:
                LOG.debug("[UP] Init-Fehler: %s", e)
        if self._impl is None and _HAVE_LEGACY_POLICY:
            try:
                self._impl = PolicyEngine(TTTAdapter())  # type: ignore[assignment]
                setattr(self._impl, "namespace", self.namespace)
            except Exception as e:
                LOG.debug("[LegacyPolicy] Init-Fehler: %s", e)

    def choose(self, board: List[str], side: str, legal_moves: List[int]) -> Optional[int]:
        if not (self.enabled and self._impl and legal_moves):
            return None
        try:
            # Kanonisieren
            sh, M, M_inv = _state_hash(board, side)
            legal_canon = [M[i] for i in legal_moves]
            a_canon: Optional[int] = None

            # Universal-API
            if _HAVE_UP and hasattr(self._impl, "choose"):
                a_canon = self._impl.choose(sh, legal_canon, side=side)  # type: ignore[attr-defined]
            # Legacy-API
            elif hasattr(self._impl, "choose_action_from_board"):
                a_orig = self._impl.choose_action_from_board(  # type: ignore[attr-defined]
                    board if side == "X" else ["X" if v=="O" else "O" if v=="X" else "" for v in board]
                )
                if a_orig is None: return None
                # map in Canon-Raum (falls möglich), sonst direkt
                a_canon = M[a_orig] if 0 <= a_orig < 9 else None

            if a_canon is None: return None
            # zurück nach Original
            a = M_inv[a_canon]
            return a if a in legal_moves else None
        except Exception as e:
            LOG.debug("[UP] choose fail: %s", e)
            return None

    def learn_many(self, items: List[Dict[str, Any]]):
        """
        items: [{state_hash, action_canon, side, outcome (+1/-1/0), ts}]
        → policy_rules UPSERT + optional impl.learn(...)
        """
        if not items: return
        # 1) DB-UPSERT
        if sql_manager and hasattr(sql_manager, "get_conn"):
            try:
                use_dbw = bool(
                    db_writer_client is not None
                    and os.environ.get("OROMA_DBW_ENABLE", "0").strip().lower() not in ("0", "false", "no", "off")
                )
                now = int(time.time())
                sql_stmt = """INSERT INTO policy_rules
                               (namespace, state_hash, action, n, pos, neg, draw, q, last_ts)
                               VALUES (?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(namespace, state_hash, action) DO UPDATE SET
                                   n    = policy_rules.n + excluded.n,
                                   pos  = policy_rules.pos + excluded.pos,
                                   neg  = policy_rules.neg + excluded.neg,
                                   draw = policy_rules.draw + excluded.draw,
                                   q    = (policy_rules.q + excluded.q)/2.0,
                                   last_ts = excluded.last_ts
                            """
                if use_dbw:
                    timeout_ms = int(getattr(sql_manager, "_dbw_timeout_ms", lambda kind='dream': 60000)("dream"))
                    for it in items:
                        ns = self.namespace
                        st = it["state_hash"]; ac = int(it["action_canon"])
                        outcome = float(it["outcome"])
                        pos = 1 if outcome > 0 else 0
                        neg = 1 if outcome < 0 else 0
                        drw = 1 if outcome == 0 else 0
                        q = outcome
                        db_writer_client.exec_write(
                            sql_stmt,
                            [ns, st, ac, 1, pos, neg, drw, q, now],
                            tag="tictactoe.policy_rules.upsert",
                            priority="low",
                            timeout_ms=timeout_ms,
                            db="oroma",
                        )
                else:
                    with sql_manager.get_conn() as c:
                        for it in items:
                            ns = self.namespace
                            st = it["state_hash"]; ac = int(it["action_canon"])
                            outcome = float(it["outcome"])
                            pos = 1 if outcome > 0 else 0
                            neg = 1 if outcome < 0 else 0
                            drw = 1 if outcome == 0 else 0
                            q = outcome
                            c.execute(sql_stmt, (ns, st, ac, 1, pos, neg, drw, q, now))
                        c.commit()
            except Exception as e:
                LOG.debug("[UP] DB-UPSERT fehlgeschlagen: %s", e)

        # 2) impl.learn(...) (optional)
        try:
            if self._impl and hasattr(self._impl, "learn"):
                self._impl.learn(items)  # type: ignore[attr-defined]
        except Exception as e:
            log_suppressed('ui/tictactoe_ui.py:325', exc=e, level=logging.WARNING)
            pass

# =============================================================================
# Runtime-Engine
# =============================================================================
class TicTacToeRuntime:
    MODES = ("oroma_vs_ki","oroma_vs_human","ki_vs_ki","oroma_vs_oroma_explore","oroma_vs_oroma_policy")
    def __init__(self):
        norm = _normalize_default_mode(_MODE_DEFAULT_RAW)
        self.mode = norm if norm in self.MODES else "oroma_vs_oroma_explore"
        self.auto = False

        self.lock = threading.Lock()
        self._lock_held_since: float = 0.0

        self.snapchain: Optional[Any] = None
        self._ensure_snapchain("init")

        # Universal Policy
        self.policy = UniversalPolicyShim(namespace="game:tictactoe")
        self.policy_enabled = bool(self.policy.enabled)

        self.speed_mode = DEFAULT_SPEED
        self.tick_delay = DEF_TICK
        self.end_delay  = DEF_END

        # STM + Explorationzähler
        self.stm = ShortTermMemory(STM_SIZE)
        self._line: List[int] = []
        self._eps_current = EPS_EXPLORATION
        self._explore_moves_this_game = 0
        self._start_side = "X"
        self._traj: List[Dict[str, Any]] = []  # Lern-Feedback: [{state_hash, action_canon, side}...]

        # Stats
        self.stats = {"games": 0, "wins": {"oroma": 0, "ki": 0, "human": 0, "oroma_x": 0, "oroma_o": 0, "draw": 0}, "snaps_total": 0}

        # Board/Züge
        self.board: List[str] = [""] * 9
        self.turn: str = "X"
        self.winner: Optional[str] = None
        self.last_move: Optional[int] = None
        self.winner_line: List[int] = []
        self._next_start: str = random.choice(["X", "O"])

        # ---------------------------------------------------------------------
        # UI/DB Stabilität (2026-01)
        #  - DB-Counts im UI werden gecached (TTL), um /api/state nicht zu blockieren.
        #  - SnapChain-DB-Writes können optional in RAM gebatched werden (flush alle N Games).
        # ---------------------------------------------------------------------
        try:
            self._db_stats_ttl_s = int(os.environ.get("OROMA_TTT_DB_STATS_TTL_S", "15"))
        except Exception:
            self._db_stats_ttl_s = 15
        if self._db_stats_ttl_s < 0:
            self._db_stats_ttl_s = 0
        self._db_stats_cache = None
        self._db_stats_cache_ts = 0.0

        try:
            self._save_batch_games = int(os.environ.get("OROMA_TTT_SAVE_BATCH_GAMES", "1"))
        except Exception:
            self._save_batch_games = 1
        if self._save_batch_games < 1:
            self._save_batch_games = 1
        self._pending_snapchains = []
        self._pending_games = 0


        self.reset()
        threading.Thread(target=self._loop, daemon=True, name="TTT-Loop").start()

    # SnapChain Helpers (unverändert kompakt)
    def _ensure_snapchain(self, ctx: str = "") -> bool:
        if self.snapchain is not None: return True
        try:
            from core.snapchain import SnapChain as _SC
            self.snapchain = _SC(patterns=[], metadata={"game": "tictactoe"})
            return True
        except Exception as e:
            LOG.error("[_ensure_snapchain] SnapChain-Setup fehlgeschlagen: %s", e)
            self.snapchain = None
            return False

    def _snap_len(self) -> int:
        sc = self.snapchain
        if not sc: return 0
        try:
            pats = getattr(sc, "patterns", None)
            if isinstance(pats, list): return len(pats)
        except Exception: pass
        return 0

    def _append_pattern_from_vector(self, vec: List[float], md: Dict[str, Any]) -> bool:
        if not self._ensure_snapchain("append") or not SnapPattern:
            return False
        try:
            pat = SnapPattern.from_snaps([list(map(float, vec))], metadata=dict(md or {}))
            if hasattr(self.snapchain, "append"): self.snapchain.append(pat)  # type: ignore[attr-defined]
            else: raise RuntimeError("SnapChain hat keine append-API")
            return True
        except Exception as e:
            LOG.error("[_append_pattern_from_vector] Append fehlgeschlagen: %s", e)
            return False

    # Spiel-API
    def reset(self):
        self.turn = self._next_start
        self._start_side = self.turn
        self._next_start = "O" if self._next_start == "X" else "X"
        self.board = [""] * 9
        self.winner = None
        self.last_move = None
        self.winner_line = []
        self._line = []
        self._traj.clear()
        self._explore_moves_this_game = 0
        self.stats["games"] += 1
        LOG.info("🎮 Neues Spiel #%d (Start=%s, Modus=%s) – Policy:%s",
                 self.stats["games"], self.turn, self.mode, "ON" if self.policy_enabled else "OFF")

    # Auto-Loop
    def _loop(self):
        while True:
            time.sleep(self.tick_delay or 0.05)
            if not self.auto or self.winner:
                continue
            got = self.lock.acquire(timeout=0.01)
            if not got: continue
            self._lock_held_since = time.time()
            try:
                self._auto_turn_locked()
            except Exception as e:
                LOG.error("[_loop] Fehler: %s", e)
            finally:
                self._lock_held_since = 0.0
                self.lock.release()

    def _auto_turn_locked(self):
        if self.winner: return
        if self.mode == "oroma_vs_ki":
            self._ai_turn_oroma_locked() if self.turn == "X" else self._ai_turn_random_locked("ki")
        elif self.mode == "ki_vs_ki":
            self._ai_turn_random_locked("ki")
        elif self.mode == "oroma_vs_human" and self.turn == "X":
            self._ai_turn_oroma_locked()
        elif self.mode in ("oroma_vs_oroma_explore", "oroma_vs_oroma_policy"):
            self._ai_turn_oroma_locked()

    def kick(self):
        got = self.lock.acquire(timeout=1.0)
        if not got:
            LOG.error("[kick] ❌ Lock-Timeout"); return
        self._lock_held_since = time.time()
        try:
            if self.winner: return
            self._auto_turn_locked()
        finally:
            self._lock_held_since = 0.0
            self.lock.release()

    # Heuristiken
    def _safe_random_move(self, moves: List[int], sym_me: str) -> Optional[int]:
        if not moves: return None
        sym_you = "O" if sym_me == "X" else "X"
        # Sofortgewinn nehmen
        for i in moves:
            tmp = self.board.copy(); tmp[i] = sym_me
            if self._check_winner(tmp) == sym_me: return i
        # sichere Kandidaten (keine 1-Zug-Niederlage)
        safe = []
        for i in moves:
            tmp = self.board.copy(); tmp[i] = sym_me
            bad = False
            for j,v in enumerate(tmp):
                if not v:
                    tmp2 = tmp.copy(); tmp2[j] = sym_you
                    if self._check_winner(tmp2) == sym_you: bad = True; break
            if not bad: safe.append(i)
        return random.choice(safe) if safe else random.choice(moves)

    # Policy-Zug (Universal)
    def _policy_pick_for_side(self, side: str) -> Optional[int]:
        if not (self.policy_enabled and self.policy):
            return None
        moves = [i for i,v in enumerate(self.board) if not v]
        return self.policy.choose(self.board, side, moves)

    # KI-Züge – Policy + Exploration
    def _ai_turn_oroma_locked(self):
        moves = [i for i, v in enumerate(self.board) if not v]
        policy_only = (self.mode == "oroma_vs_oroma_policy")
        me = self.turn; you = "O" if me == "X" else "X"

        # Exploration-Entscheid
        if policy_only:
            use_explore = False
        else:
            # Loss-aware Präfixdruck (seitensensitiv)
            base = self.stm.count(self._line, me)
            try:
                w = float(os.environ.get("OROMA_TTT_LOSS_PRESSURE", "2.0"))
                loss = self.stm.count_loss(self._line, me)
                prefix_hits = int(round(base + w * loss))
            except Exception:
                prefix_hits = base
            force_explore = prefix_hits >= STM_FORCE
            can_explore   = self._explore_moves_this_game < EPS_MOVES_PER_GAME
            use_explore   = can_explore and (force_explore or (random.random() < self._eps_current))

        # 1) Policy
        pol = None if use_explore else self._policy_pick_for_side(self.turn)
        if pol is not None:
            self._apply_move_locked(pol, "oroma", record_traj=True); return

        # 2) Policy-only fallback: Block → Center → Corner → Side (mit Safety)
        if policy_only:
            for i in moves:
                tmp = self.board.copy(); tmp[i] = you
                if self._check_winner(tmp) == you:
                    self._apply_move_locked(i, "oroma", record_traj=True); return
            if 4 in moves:
                self._apply_move_locked(4, "oroma", record_traj=True); return
            corners = [i for i in (0,2,6,8) if i in moves]
            sides   = [i for i in (1,3,5,7) if i in moves]
            pick = self._safe_random_move(corners or sides or moves, sym_me=me)
            if pick is not None:
                self._apply_move_locked(pick, "oroma", record_traj=True)
            return

        # 3) Lernmodus: Win → Block → „sichere“ Neuheit → safe random
        for i in moves:
            tmp = self.board.copy(); tmp[i] = me
            if self._check_winner(tmp) == me:
                self._apply_move_locked(i, "oroma", record_traj=True); return
        for i in moves:
            tmp = self.board.copy(); tmp[i] = you
            if self._check_winner(tmp) == you:
                self._apply_move_locked(i, "oroma", record_traj=True); return

        pick = self._safe_random_move(moves, sym_me=me)
        if pick is not None:
            if use_explore: self._explore_moves_this_game += 1
            self._apply_move_locked(pick, "oroma", record_traj=True)

    def _ai_turn_random_locked(self, who: str):
        moves = [i for i, v in enumerate(self.board) if not v]
        if moves:
            pick = self._safe_random_move(moves, sym_me=self.turn) or random.choice(moves)
            self._apply_move_locked(pick, who, record_traj=False)

    # Zug-Logik
    def _apply_move_locked(self, idx: int, who: str, record_traj: bool):
        if self.winner or self.board[idx]: return
        symbol = self.turn
        # Trajektorie: vor dem Setzen aktuellen state_hash berechnen
        if record_traj:
            try:
                sh, M, _M_inv = _state_hash(self.board, symbol)
                a_canon = M[idx]
                self._traj.append({"state_hash": sh, "action_canon": int(a_canon), "side": symbol})
            except Exception as e:
                log_suppressed('ui/tictactoe_ui.py:588', exc=e, level=logging.WARNING)
                pass

        self.board[idx] = symbol
        self.last_move = idx
        self._line.append(idx)
        self.turn = "O" if symbol == "X" else "X"
        self.winner = self._check_winner(self.board)

        feats = [1.0 if v == "X" else -1.0 if v == "O" else 0.0 for v in self.board]
        md = {"game": "tictactoe", "player": who, "side": symbol, "idx": idx}
        if self._append_pattern_from_vector(feats, md):
            self.stats["snaps_total"] += 1

        if self.winner:
            LOG.info("🏁 Gewinner: %s (letzter Zug von %s)", self.winner, who)
            threading.Thread(target=self._finish_async, args=(who, self.winner), daemon=True).start()

    def move(self, idx: int, who: str = "human"):
        got = self.lock.acquire(timeout=1.0)
        if not got:
            LOG.error("[move] ❌ Lock-Timeout"); return
        self._lock_held_since = time.time()
        try:
            if self.winner or self.board[idx]: return
            self._apply_move_locked(idx, who, record_traj=False)
        finally:
            self._lock_held_since = 0.0
            self.lock.release()

    # Abschluss
    def _finish_async(self, who: str, symbol: str):
        try:
            time.sleep(self.end_delay)
            self._finish_game(who, symbol)
        except Exception as e:
            LOG.error("[_finish_async] Fehler: %s", e)

    def _finish_game(self, who: str, symbol: str):
        # Statistik
        if self.mode in ("oroma_vs_oroma_explore", "oroma_vs_oroma_policy"):
            if symbol == "X":
                self.stats["wins"]["oroma_x"] += 1; self.stats["wins"]["oroma"] += 1
            elif symbol == "O":
                self.stats["wins"]["oroma_o"] += 1; self.stats["wins"]["oroma"] += 1
            else:
                self.stats["wins"]["draw"] += 1
        else:
            if symbol == "X" and who == "oroma": self.stats["wins"]["oroma"] += 1
            elif symbol == "O" and who == "ki":  self.stats["wins"]["ki"] += 1
            elif who == "human":                 self.stats["wins"]["human"] += 1
            elif symbol == "draw":               self.stats["wins"]["draw"] += 1

        # Reward-Log
        if reward and hasattr(reward, "log"):
            try:
                r = {"X": 1.0, "O": 1.0, "draw": 0.2}.get(symbol or "draw", 0.0)
                reward.log("tictactoe", r)
            except Exception: pass

        # STM-Logging (seitensensitiv)
        try:
            self.stm.note_game(self._line, self.winner or "draw", self._start_side)
            if (self.winner or "") in ("X","O"):
                loser = "O" if self.winner == "X" else "X"
                idxs = range(0, len(self._line), 2) if loser == "X" else range(1, len(self._line), 2)
                loss_seq = [self._line[i] for i in idxs]
                if loss_seq:
                    for k in range(1, len(loss_seq)+1):
                        self.stm._inc_prefixes(loss_seq[:k], loser, +1, into="loss")
        except Exception as e:
            log_suppressed('ui/tictactoe_ui.py:659', exc=e, level=logging.WARNING)
            pass

        # Policy-Feedback (DB + impl.learn)
        try:
            if self.policy and self.policy_enabled and self._traj:
                outcome_items = []
                for step, tr in enumerate(self._traj):
                    side = tr["side"]
                    # Ergebnis aus Sicht des 'side' zum Zeitpunkt des Zuges
                    if self.winner == side:
                        out = +1.0
                    elif self.winner in ("X","O"):
                        out = -1.0
                    else:
                        out = 0.0
                    outcome_items.append({
                        "state_hash": tr["state_hash"],
                        "action_canon": tr["action_canon"],
                        "side": side,
                        "outcome": out,
                        "ts": int(time.time())
                    })
                self.policy.learn_many(outcome_items)
        except Exception as e:
            LOG.debug("[finish] Policy-Feedback Fehler: %s", e)

        # SnapChain-Export (kompakt)
        try:
            export = {
                "schema_version": "ui-export-1",
                "patterns": [{"centroid":[1.0 if v=="X" else -1.0 if v=="O" else 0.0 for v in self.board], "num_snaps": len(self._line)}],
                "metadata": {"game":"tictactoe", "winner": self.winner or "draw", "mode": self.mode},
                "valid": True, "ts_created": time.time()
            }
            blob_b = json.dumps(export, ensure_ascii=False).encode("utf-8")
            self._enqueue_ttt_snapchain_blob(blob_b)
            LOG.info("💾 SnapChain queued (batch=%s, pending=%s)", getattr(self,"_save_batch_games",1), len(getattr(self,"_pending_snapchains",[])))
        except Exception as e:
            LOG.error("[_finish_game] DB-Insert-Fehler: %s", e)

        # ε-Decay
        try: self._eps_current = max(EPS_MIN, self._eps_current * EPS_DECAY)
        except Exception: pass

        time.sleep(self.end_delay + 0.2)
        self.snapchain = None
        self._ensure_snapchain("restart")
        self.reset()
        if self.auto: self.kick()

    # Utilities
    def _check_winner(self, b: List[str]) -> Optional[str]:
        wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
        for a, bb, c in wins:
            if b[a] and b[a] == b[bb] == b[c]:
                self.winner_line = [a, bb, c]; return b[a]
        self.winner_line = []; return "draw" if all(b) else None

    def set_speed(self, mode: str):
        m = (mode or "").lower()
        if m == "turbo":
            self.speed_mode = "turbo"; self.tick_delay = 0.0; self.end_delay = 0.0
        else:
            self.speed_mode = "normal"; self.tick_delay = 0.15; self.end_delay = 0.5
        LOG.info("⚙️ Geschwindigkeit: %s", self.speed_mode)

    def _policy_counts(self) -> Dict[str, int]:
        """Policy/Archiv-Zähler für UI.

        Stabilitäts-FIX (2026-01):
          In der Baseline wurden diese COUNT(*) Abfragen bei jedem /api/state Poll
          (typisch alle ~300ms im Browser) ausgeführt. Das kann bei großer DB und
          parallelen Writer-Jobs (Dream/Forgetting/Exporter) die UI spürbar blockieren.

          Daher: wir holen die Zähler nur noch über _get_db_stats_cached() (TTL-Cache).
        """
        st = self._get_db_stats_cached()
        return {
            "policy_rules_count": int(st.get("policy_rules_count", 0) or 0),
            "archiv_rules_count": int(st.get("archiv_rules_count", 0) or 0),
        }

    def _get_db_stats_cached(self) -> Dict[str, Any]:
        """DB-Statistiken für UI – TTL-Cache, niemals hart blockieren.

        - TTL in Sekunden via OROMA_TTT_DB_STATS_TTL_S (Default: 15s)
        - ttl<=0: liefert 0/None und vermeidet DB Reads komplett
        - Für UI-Reads wird zusätzlich ein kurzer busy_timeout (250ms) gesetzt,
          damit /api/state nicht minutenlang wartet, wenn SQLite gerade busy ist.
        """
        ttl = int(getattr(self, "_db_stats_ttl_s", 0) or 0)
        now = time.time()

        # ttl<=0 → keine DB Reads im UI
        if ttl <= 0:
            return {
                "snapchains_in_db": 0,
                "policy_rules_count": 0,
                "archiv_rules_count": 0,
                "db_stats_ttl_s": ttl,
                "db_stats_age_s": None,
            }

        cache = getattr(self, "_db_stats_cache", None)
        cache_ts = float(getattr(self, "_db_stats_cache_ts", 0.0) or 0.0)
        age = (now - cache_ts) if cache_ts else None

        if cache and age is not None and age < ttl:
            try:
                cache["db_stats_ttl_s"] = ttl
                cache["db_stats_age_s"] = round(age, 3)
            except Exception as e:
                log_suppressed('ui/tictactoe_ui.py:772', exc=e, level=logging.WARNING)
                pass
            return cache

        # refresh (best effort)
        snapchains_in_db = 0
        policy_rules_count = 0
        archiv_rules_count = 0

        if sql_manager:
            try:
                with sql_manager.get_conn() as c:
                    try:
                        c.execute("PRAGMA busy_timeout=250")
                    except Exception as e:
                        log_suppressed('ui/tictactoe_ui.py:787', exc=e, level=logging.WARNING)
                        pass

                    # SnapChains: nur TicTacToe, nicht global!
                    row = c.execute(
                        "SELECT COUNT(*) AS n FROM snapchains WHERE (origin = ? OR origin LIKE ?)",
                        ("game:tictactoe", "game:tictactoe%"),
                    ).fetchone()
                    snapchains_in_db = int(row["n"] if isinstance(row, dict) else (row[0] if row else 0))

                    # Policy-Regeln
                    ns = self.policy.namespace if self.policy else "game:tictactoe"
                    row = c.execute(
                        "SELECT COUNT(*) AS n FROM policy_rules WHERE namespace=?",
                        (ns,),
                    ).fetchone()
                    policy_rules_count = int(row["n"] if isinstance(row, dict) else (row[0] if row else 0))

                    # Archiv-Regeln: JSON in rules.content
                    row = c.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM rules
                        WHERE active=1
                          AND (content LIKE ? OR content LIKE ?)
                        """,
                        ('%"namespace":"game:tictactoe"%', '%"namespace": "game:tictactoe"%'),
                    ).fetchone()
                    archiv_rules_count = int(row["n"] if isinstance(row, dict) else (row[0] if row else 0))

            except Exception:
                # Lock/Busy: lieber Cache zurückgeben, als UI zu blockieren
                if cache:
                    try:
                        cache["db_stats_ttl_s"] = ttl
                        cache["db_stats_age_s"] = round((now - cache_ts), 3) if cache_ts else None
                    except Exception as e:
                        log_suppressed('ui/tictactoe_ui.py:824', exc=e, level=logging.WARNING)
                        pass
                    return cache

        new_cache = {
            "snapchains_in_db": snapchains_in_db,
            "policy_rules_count": policy_rules_count,
            "archiv_rules_count": archiv_rules_count,
            "db_stats_ttl_s": ttl,
            "db_stats_age_s": 0.0,
        }
        try:
            self._db_stats_cache = new_cache
            self._db_stats_cache_ts = now
        except Exception as e:
            log_suppressed('ui/tictactoe_ui.py:839', exc=e, level=logging.WARNING)
            pass
        return new_cache

    def _enqueue_ttt_snapchain_blob(self, blob_b: bytes) -> None:
        """Optionales RAM-Batching für TicTacToe SnapChain Inserts.

        save_batch_games:
          1  → direkt in DB schreiben (Default, keine Verhaltensänderung)
          >1 → erst nach N fertigen Games flushen (reduziert Writer-Contention)
        """
        if not blob_b:
            return
        try:
            item = {
                "blob": blob_b,
                "origin": "game:tictactoe",
                "namespace": "game:tictactoe",
                "quality": 0.0,
                "status": "active",
                "version": "v3.7.3",
                "ts": int(time.time()),
            }
            self._pending_snapchains.append(item)  # type: ignore[attr-defined]
            self._pending_games += 1  # type: ignore[attr-defined]
        except Exception:
            return

        batch = int(getattr(self, "_save_batch_games", 1) or 1)
        if batch <= 1:
            self._flush_pending_snapchains()
        elif int(getattr(self, "_pending_games", 0) or 0) >= batch:
            self._flush_pending_snapchains()

    def _flush_pending_snapchains(self) -> Dict[str, Any]:
        """Schreibt gepufferte SnapChain-Inserts in die DB (best effort)."""
        if not (sql_manager and hasattr(sql_manager, "insert_snapchain")):
            return {"ok": False, "flushed": 0, "left": 0, "reason": "sql_manager.insert_snapchain fehlt"}

        try:
            pending = list(getattr(self, "_pending_snapchains", []) or [])
        except Exception:
            pending = []

        if not pending:
            try:
                self._pending_games = 0  # type: ignore[attr-defined]
            except Exception as e:
                log_suppressed('ui/tictactoe_ui.py:887', exc=e, level=logging.WARNING)
                pass
            return {"ok": True, "flushed": 0, "left": 0}

        flushed = 0
        left: List[Any] = []

        for item in pending:
            try:
                sql_manager.insert_snapchain(item)
                flushed += 1
            except Exception as e:
                left.append(item)
                LOG.warning("[TTT] pending flush failed: %s", e)

        try:
            self._pending_snapchains = left  # type: ignore[attr-defined]
            self._pending_games = 0  # type: ignore[attr-defined]
        except Exception as e:
            log_suppressed('ui/tictactoe_ui.py:906', exc=e, level=logging.WARNING)
            pass

        # Cache invalidieren
        try:
            self._db_stats_cache_ts = 0.0  # type: ignore[attr-defined]
        except Exception as e:
            log_suppressed('ui/tictactoe_ui.py:913', exc=e, level=logging.WARNING)
            pass

        return {"ok": True, "flushed": flushed, "left": len(left)}

    def policy_state(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.policy and self.policy.enabled and self.policy_enabled),
            "eps": self._eps_current,
            "namespace": self.policy.namespace if self.policy else None,
            **self._policy_counts(),
        }

    def stm_state(self) -> Dict[str, Any]:
        try: return self.stm.stats()
        except Exception: return {"size": STM_SIZE, "stored": 0, "force_at": STM_FORCE, "top_prefix_hits": []}

    def state(self):
        tn_map = {
            "X": "ORÓMA",
            "O": "ORÓMA" if self.mode in ("oroma_vs_oroma_explore","oroma_vs_oroma_policy") else ("Human" if self.mode=="oroma_vs_human" else "KI"),
        }
        tn = tn_map.get(self.turn, "–")
        snaps_in_ram = self._snap_len()
        snaps_total_db = 0
        st_db = self._get_db_stats_cached()
        chains_in_db = int(st_db.get("snapchains_in_db", 0) or 0)
        pending = 0
        try: pending = len(getattr(self, "_pending_snapchains", []) or [])
        except Exception: pending = 0

        return {
            "board": self.board,
            "turn": self.turn,
            "turn_name": tn,
            "winner": self.winner,
            "winner_line": self.winner_line,
            "last_move": self.last_move,
            "mode": self.mode,
            "speed": self.speed_mode,
            "auto": self.auto,
            "stats": self.stats,
            "snaps_in_ram": snaps_in_ram,
            "snapchains_in_db": chains_in_db,
            "snaps_total_db": snaps_total_db,
            "pending_snapchains": pending,
            "policy": self.policy_state(),
            "stm": self.stm_state(),
        }

    def diag(self):
        return {
            "auto": self.auto,
            "mode": self.mode,
            "speed_mode": self.speed_mode,
            "tick_delay": self.tick_delay,
            "end_delay": self.end_delay,
            "lock_held_since": self._lock_held_since or None,
            "winner": self.winner,
            "turn": self.turn,
            "board": self.board,
            "snaps_in_ram": self._snap_len(),
            **self.policy_state(),
            **self.stm_state(),
        }

# =============================================================================
# Flask-API
# =============================================================================
def _rt():
    if "_tictactoe_rt" not in current_app.config:
        current_app.config["_tictactoe_rt"] = TicTacToeRuntime()
    return current_app.config["_tictactoe_rt"]

@tictactoe_bp.route("/")
def page(): return render_template("tictactoe.html")

@tictactoe_bp.route("/api/state")
def api_state(): return jsonify(_rt().state())

@tictactoe_bp.route("/api/diag")
def api_diag(): return jsonify(_rt().diag())

@tictactoe_bp.route("/api/policy", methods=["GET", "POST"])
def api_policy():
    rt = _rt()
    if request.method == "GET": return jsonify(rt.policy_state())
    d = request.get_json(force=True, silent=True) or {}
    if "enabled" in d:
        en = bool(d.get("enabled"))
        rt.policy_enabled = en
    if "eps" in d:
        try: rt._eps_current = max(0.0, min(1.0, float(d.get("eps"))))
        except Exception: pass
    st = rt.policy_state(); st["changed"] = True
    return jsonify(st)


@tictactoe_bp.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    rt = _rt()
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "db_stats_ttl_s": int(getattr(rt, "_db_stats_ttl_s", 0) or 0),
            "save_batch_games": int(getattr(rt, "_save_batch_games", 1) or 1),
            "pending_snapchains": len(getattr(rt, "_pending_snapchains", []) or []),
        })
    d = request.get_json(force=True, silent=True) or {}
    try:
        ttl = int(d.get("db_stats_ttl_s", getattr(rt, "_db_stats_ttl_s", 15) or 15))
        if ttl < 0: ttl = 0
        if ttl > 300: ttl = 300
        rt._db_stats_ttl_s = ttl
    except Exception as e:
        log_suppressed('ui/tictactoe_ui.py:1028', exc=e, level=logging.WARNING)
        pass
    try:
        batch = int(d.get("save_batch_games", getattr(rt, "_save_batch_games", 1) or 1))
        if batch < 1: batch = 1
        if batch > 10000: batch = 10000
        rt._save_batch_games = batch
    except Exception as e:
        log_suppressed('ui/tictactoe_ui.py:1036', exc=e, level=logging.WARNING)
        pass
    # Cache invalidieren
    try: rt._db_stats_cache_ts = 0.0
    except Exception: pass
    return jsonify({
        "ok": True,
        "db_stats_ttl_s": int(getattr(rt, "_db_stats_ttl_s", 0) or 0),
        "save_batch_games": int(getattr(rt, "_save_batch_games", 1) or 1),
        "pending_snapchains": len(getattr(rt, "_pending_snapchains", []) or []),
    })

@tictactoe_bp.route("/api/flush", methods=["POST"])
def api_flush():
    rt = _rt()
    try:
        res = rt._flush_pending_snapchains()
    except Exception as e:
        res = {"ok": False, "error": str(e), "flushed": 0, "left": len(getattr(rt, "_pending_snapchains", []) or [])}
    return jsonify(res)


@tictactoe_bp.route("/api/stm", methods=["GET", "POST"])
def api_stm():
    rt = _rt()
    if request.method == "GET": return jsonify(rt.stm_state())
    d = request.get_json(force=True, silent=True) or {}
    if d.get("reset"): rt.stm.clear(); return jsonify({"ok": True, "stm": rt.stm_state()})
    return jsonify({"ok": False, "error": "kein Befehl"}), 400

@tictactoe_bp.route("/api/move", methods=["POST"])
def api_move():
    d = request.get_json(force=True, silent=True) or {}
    idx = int(d.get("idx", -1)); _rt().move(idx, "human")
    return jsonify(_rt().state())

@tictactoe_bp.route("/api/reset", methods=["POST"])
def api_reset():
    rt = _rt(); rt.reset()
    return jsonify({"ok": True})

@tictactoe_bp.route("/api/toggle", methods=["POST"])
def api_toggle():
    rt = _rt(); rt.auto = not rt.auto
    if rt.auto and not rt.winner:
        try: rt.kick()
        except Exception as e: LOG.error("[api_toggle] Kick-Fehler: %s", e)
    return jsonify({"ok": True, "running": rt.auto})

@tictactoe_bp.route("/api/mode", methods=["POST"])
def api_mode():
    d = request.get_json(force=True, silent=True) or {}
    m = _normalize_mode(d.get("mode") or "")
    rt = _rt()
    if m in rt.MODES:
        rt.mode = m
        if rt.auto and not rt.winner: rt.kick()
        return jsonify({"ok": True, "mode": m})
    return jsonify({"ok": False, "error": "Ungültiger Modus"}), 400

@tictactoe_bp.route("/api/speed", methods=["POST"])
def api_speed():
    d = request.get_json(force=True, silent=True) or {}
    s = (d.get("speed") or "").lower(); rt = _rt()
    if s in ("normal","turbo"):
        rt.set_speed(s); return jsonify({"ok": True, "speed": s})
    return jsonify({"ok": False, "error": "Ungültige Speed-Angabe"}), 400
