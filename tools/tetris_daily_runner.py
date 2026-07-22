#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/tetris_daily_runner.py
# Projekt: ORÓMA – Games / Episodic Telemetry / UniversalPolicy
# Modul:   Tetris Daily Runner – Professional Policy/Explore Learning v3
# Version: v3.2-professional-strict-reuse-guard
# Stand:   2026-06-28
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.5 Thinking
# =============================================================================
#
# Zweck
# -----
#   Headless Tetris-Batchrunner für ORÓMA. Der Runner führt policy- und
#   explore-Batches gegen `core.tetris_engine.TetrisEngine` aus, schreibt
#   Episoden/Metriken nach `oroma.db` und trainiert im Explore-Zweig die
#   domänenübergreifende `core.universal_policy.Policy`.
#
#   Diese v2 ersetzt den alten reinen Heuristik-/Telemetry-Pfad durch einen
#   produktiven Lernpfad:
#     • abstrahierter taktischer State-Hash `tetris:pro_v3` statt exakter
#       Spaltenhöhen als einmalige Zustände,
#     • UniversalPolicy-Reuse mit Q-/N-Gate, damit schlechte oder dünne Regeln
#       nicht aktiv bevorzugt werden,
#     • professionelle Tetris-Fallback-Heuristik auf Kandidatenebene,
#     • eventbasiertes Lernen ohne Draw-Wand: Linien-Clears/Board-Verbesserung
#       erzeugen positive Beispiele, klar schädliche Platzierungen und Top-out
#       erzeugen negative Beispiele, neutrale Stücke werden nicht gelernt,
#     • sichtbare Diagnosemetriken: policy_used/fallback/q_rejected, line/risk/
#       topout credit, high_score/high_lines, holes/height/bumpiness.
#
# Produktionsinvarianten
# ----------------------
#   • Headless: keine Qt/Wayland/X11/pygame-Abhängigkeit.
#   • Keine direkten DB-Schemaänderungen.
#   • UniversalPolicy schreibt über den vorhandenen ORÓMA-Pfad; wenn DBWriter
#     aktiv ist, wird dessen Konfiguration von `core.universal_policy` beachtet.
#   • Policy-only ist Evaluation und lernt nicht; Explore ist Lernpfad.
#   • Neutrale Outcomes werden bewusst verworfen, damit keine Draw-Wand entsteht.
#
# CLI
# ---
#   cd /opt/ai/oroma
#   PYTHONPATH=. python3 tools/tetris_daily_runner.py \
#     --policy-games 10 --explore-games 10 --seed "$(date +%s)" --namespace game:tetris
#
# ENV
# ---
#   OROMA_TETRIS_EPS=0.08
#   OROMA_TETRIS_MAX_STEPS=5000
#   OROMA_TETRIS_POLICY_ENABLE=1
#   OROMA_TETRIS_POLICY_Q_MIN_GATE=-0.05        # Legacy/diagnostic gate
#   OROMA_TETRIS_POLICY_MIN_N=1                 # Legacy/diagnostic gate
#   OROMA_TETRIS_POLICY_ACCEPT_Q_MIN=0.80       # v3.2: nur stark positive Evidenz darf beraten
#   OROMA_TETRIS_POLICY_ACCEPT_MIN_N=5          # v3.2: dünne Einzelbeobachtungen bleiben Beratung
#   OROMA_TETRIS_POLICY_MAX_SCORE_GAP=0.0       # v3.2: Policy darf heuristisch nicht schlechter sein
#   OROMA_TETRIS_POLICY_MAX_EXTRA_HOLES=0       # v3.2: keine zusätzlichen Löcher gegenüber Fallback
#   OROMA_TETRIS_POLICY_MAX_EXTRA_TOP_DANGER=0  # v3.2: keine zusätzliche Top-Gefahr
#   OROMA_TETRIS_POLICY_MAX_EXTRA_HEIGHT=0      # v3.2: keine höhere Spitze gegenüber Fallback
#   OROMA_TETRIS_POLICY_MAX_EXTRA_BUMPINESS=0   # v3.2: keine rauere Oberfläche gegenüber Fallback
#   OROMA_TETRIS_DEATH_CREDIT_STEPS=12
#   OROMA_TETRIS_UP_AUTO_EXPORT=0        # Default: Tetris-Hotpath exportiert nicht ins Regelarchiv
#   OROMA_TETRIS_POLICY_DBW_CHUNK=500    # größerer DBWriter-Chunk für Batch-Lernen
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import random
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

from core.tetris_engine import HEIGHT, TETROMINOS, WIDTH, Piece, TetrisEngine

# UniversalPolicy wird bewusst lazy geladen. Auf großen produktiven SQLite-Dateien
# kann `core.universal_policy` beim Import Schema-/Indexprüfungen anstoßen; der
# Runner soll trotzdem schnell starten und bei deaktivierter Policy ohne diesen
# Import auskommen.
UniversalPolicy = None  # type: ignore
_UNIVERSAL_POLICY_IMPORT_TRIED = False


def _load_universal_policy() -> Any:
    global UniversalPolicy, _UNIVERSAL_POLICY_IMPORT_TRIED
    if _UNIVERSAL_POLICY_IMPORT_TRIED:
        return UniversalPolicy
    _UNIVERSAL_POLICY_IMPORT_TRIED = True
    try:
        from core.universal_policy import Policy as _Policy  # type: ignore
        UniversalPolicy = _Policy  # type: ignore
    except Exception:
        UniversalPolicy = None  # type: ignore
    return UniversalPolicy


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return bool(default)
    return v in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _now() -> int:
    return int(time.time())


def _bucket(v: float, cuts: Iterable[float]) -> int:
    x = float(v)
    for i, c in enumerate(cuts):
        if x <= float(c):
            return int(i)
    return int(len(list(cuts)))


def _clamp_i(v: int, lo: int, hi: int) -> int:
    return max(int(lo), min(int(hi), int(v)))


@dataclass(frozen=True)
class BoardStats:
    heights: Tuple[int, ...]
    aggregate_height: int
    max_height: int
    min_height: int
    holes: int
    bumpiness: int
    wells: int
    top_danger: int
    covered_holes: int


@dataclass(frozen=True)
class Candidate:
    action: str
    policy_action: str
    rot: int
    x: int
    board: List[List[int]]
    lines: int
    stats: BoardStats
    score: float


def _column_heights(board: List[List[int]]) -> List[int]:
    heights: List[int] = []
    for x in range(WIDTH):
        h = 0
        for y in range(HEIGHT):
            if board[y][x] != -1:
                h = HEIGHT - y
                break
        heights.append(int(h))
    return heights


def _count_holes(board: List[List[int]]) -> Tuple[int, int]:
    holes = 0
    covered = 0
    for x in range(WIDTH):
        seen_block = False
        blocks_above = 0
        for y in range(HEIGHT):
            if board[y][x] != -1:
                seen_block = True
                blocks_above += 1
            elif seen_block:
                holes += 1
                covered += blocks_above
    return int(holes), int(covered)


def _well_depth_sum(heights: List[int]) -> int:
    wells = 0
    for i, h in enumerate(heights):
        left = heights[i - 1] if i > 0 else HEIGHT
        right = heights[i + 1] if i < (WIDTH - 1) else HEIGHT
        rim = min(left, right)
        if rim > h:
            wells += int(rim - h)
    return int(wells)


def _board_stats(board: List[List[int]]) -> BoardStats:
    heights = _column_heights(board)
    holes, covered = _count_holes(board)
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(WIDTH - 1))
    top_danger = sum(1 for h in heights if h >= 16)
    return BoardStats(
        heights=tuple(int(h) for h in heights),
        aggregate_height=int(sum(heights)),
        max_height=int(max(heights) if heights else 0),
        min_height=int(min(heights) if heights else 0),
        holes=int(holes),
        bumpiness=int(bump),
        wells=int(_well_depth_sum(heights)),
        top_danger=int(top_danger),
        covered_holes=int(covered),
    )


def _clear_lines_in_board(board: List[List[int]]) -> Tuple[List[List[int]], int]:
    nb = deepcopy(board)
    full = [yy for yy in range(HEIGHT) if all(nb[yy][xx] != -1 for xx in range(WIDTH))]
    for yy in reversed(full):
        del nb[yy]
    for _ in full:
        nb.insert(0, [-1] * WIDTH)
    return nb, int(len(full))


def _place_piece_sim(board: List[List[int]], kind: str, rot: int, x: int) -> Optional[Tuple[List[List[int]], int]]:
    p = Piece(kind=kind, rot=rot % 4, x=int(x), y=-2)

    def can_place(pp: Piece) -> bool:
        for (cx, cy) in pp.cells():
            if cx < 0 or cx >= WIDTH or cy >= HEIGHT:
                return False
            if cy >= 0 and board[cy][cx] != -1:
                return False
        return True

    if not can_place(p):
        return None

    while True:
        np = Piece(kind=p.kind, rot=p.rot, x=p.x, y=p.y + 1)
        if can_place(np):
            p = np
            continue
        break

    # Locking above the visible board is a top-out candidate and therefore not
    # a useful legal placement for policy choice. The engine will end the game.
    if any(cy < 0 for (_cx, cy) in p.cells()):
        return None

    nb = deepcopy(board)
    for (cx, cy) in p.cells():
        if 0 <= cy < HEIGHT and 0 <= cx < WIDTH:
            nb[cy][cx] = 0

    nb, lines = _clear_lines_in_board(nb)
    return nb, int(lines)


def _state_hash(eng: TetrisEngine) -> str:
    """Grob abstrahierter Tetris-State fuer wiederverwendbare Regeln.

    v3 reduziert gegenueber v2 bewusst die Varianz: kein exakter Next-Stein,
    kein zehnstellig-feines Skyline-Profil, keine Score-/Tick-Daten. Enthalten
    bleiben nur taktische Board-Buckets, aktuelles Piece und grobe Next-Gruppe.
    """
    try:
        st = eng.get_state()
        cur = st.get("cur") or {}
        curk = str(cur.get("kind") or (eng.cur.kind if eng.cur else "-"))
        nxt = str(getattr(eng, "next_kind", "-") or "-")
        bs = _board_stats(eng.board)

        prof = _skyline_profile_v3(bs.heights)
        ah = _bucket(bs.aggregate_height, [20, 40, 65, 90, 120])
        mh = _bucket(bs.max_height, [5, 9, 13, 17])
        ho = _bucket(bs.holes, [0, 1, 3, 6, 10])
        bu = _bucket(bs.bumpiness, [6, 12, 20, 32])
        we = _bucket(bs.wells, [0, 2, 5, 9])
        td = _clamp_i(bs.top_danger, 0, 3)
        return f"tetris:pro_v3|C:{curk}|N:{_piece_group(nxt)}|ah:{ah}|mh:{mh}|ho:{ho}|bu:{bu}|we:{we}|td:{td}|p:{prof}"
    except Exception:
        return "tetris:pro_v3|fallback"

def _heuristic_score(before: BoardStats, after: BoardStats, lines: int, kind: str) -> float:
    """Professionelle Tetris-Kandidatenbewertung.

    Diese lineare Heuristik ist bewusst transparent und headless-tauglich. Sie
    priorisiert Line-Clears, bestraft neue Löcher/Top-Danger stark und nutzt
    Bumpiness/Aggregathöhe als sekundäre Stabilitätsmerkmale.
    """
    line_reward = {0: 0.0, 1: 3.5, 2: 9.0, 3: 20.0, 4: 55.0}.get(int(lines), 0.0)
    hole_delta = int(after.holes - before.holes)
    covered_delta = int(after.covered_holes - before.covered_holes)
    top_delta = int(after.top_danger - before.top_danger)
    max_delta = int(after.max_height - before.max_height)

    score = 0.0
    score += line_reward
    score -= 7.5 * float(after.holes)
    score -= 5.0 * float(max(0, hole_delta))
    score -= 0.20 * float(max(0, covered_delta))
    score -= 0.62 * float(after.aggregate_height)
    score -= 0.42 * float(after.bumpiness)
    score -= 0.90 * float(after.max_height)
    score -= 8.0 * float(after.top_danger)
    score -= 12.0 * float(max(0, top_delta))
    score -= 1.5 * float(max(0, max_delta))

    # I-Piece darf einen echten Brunnen erhalten; für andere Pieces bleibt der
    # Well-Term klein, damit keine künstlich tiefen Löcher erzeugt werden.
    if kind == "I":
        score += 0.20 * float(after.wells)
    else:
        score -= 0.15 * float(after.wells)
    return float(score)




def _piece_group(kind: str) -> str:
    """Grobe Next-Piece-Gruppe fuer Policy-Reuse.

    Der aktuelle Stein bleibt exakt entscheidungsrelevant. Der naechste Stein
    wird nur gruppiert, weil exakte Next-Kodierung in Tetris sehr viel Varianz
    erzeugt, aber fuer die unmittelbare Platzierungsentscheidung meist nur
    grob strategisch relevant ist.
    """
    k = str(kind or "-").upper()
    if k in {"S", "Z"}:
        return "SZ"
    if k in {"J", "L"}:
        return "JL"
    if k in {"I", "O", "T"}:
        return k
    return "-"


def _skyline_profile_v3(heights: Tuple[int, ...]) -> str:
    """Sehr grobes, min-normalisiertes Skyline-Profil.

    v2 kodierte zehn Spalten relativ fein. Das war fachlich korrekt, aber fuer
    Reuse zu spezifisch. v3 gruppiert je zwei Spalten und bucketed auf vier
    Stufen. Dadurch matchen aehnliche Boards wieder, ohne die Topologie ganz zu
    verlieren.
    """
    hs = [int(h) for h in heights]
    if not hs:
        return "00000"
    base = min(hs)
    groups = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10)]
    out: List[str] = []
    for a, b in groups:
        vals = hs[a:b]
        avg = sum(vals) / float(max(1, len(vals)))
        out.append(str(_clamp_i(int((avg - base) // 4), 0, 3)))
    return "".join(out)


def _action_zone(x: int) -> int:
    """Grobe horizontale Aktionszone fuer Action-Reuse."""
    xi = int(x)
    if xi <= 1:
        return 0
    if xi <= 3:
        return 1
    if xi <= 5:
        return 2
    if xi <= 7:
        return 3
    return 4


def _candidate_policy_action(kind: str, rot: int, x: int, lines: int, before: BoardStats, after: BoardStats) -> str:
    """Abstrakter Aktions-Key fuer tetris:pro_v3.

    Die konkrete Engine-Platzierung bleibt im Candidate erhalten. Gelernt wird
    aber eine grobe Aktionsklasse (Rotation + Zone + taktischer Effekt), damit
    Regeln nicht an einer einzelnen exakten x-Spalte kleben bleiben.
    """
    r = int(rot) % 4
    z = int(_action_zone(int(x)))
    l = _clamp_i(int(lines), 0, 4)
    hd = _bucket(int(after.holes - before.holes), [-2, -1, 0, 1, 2])
    td = _bucket(int(after.top_danger - before.top_danger), [-1, 0, 1])
    return f"r:{r}|z:{z}|l:{l}|hd:{hd}|td:{td}"

def _enumerate_candidates(eng: TetrisEngine) -> List[Candidate]:
    st = eng.get_state()
    cur = st.get("cur") or {}
    kind = str(cur.get("kind") or (eng.cur.kind if eng.cur else ""))
    if not kind or kind not in TETROMINOS:
        return []
    before = _board_stats(eng.board)
    out: List[Candidate] = []
    seen: set[Tuple[int, int]] = set()
    for rot in range(4):
        cells = TETROMINOS[kind][rot]
        min_cx = min(cx for cx, _ in cells)
        max_cx = max(cx for cx, _ in cells)
        for x in range(-min_cx, WIDTH - max_cx):
            key = (int(rot % 4), int(x))
            if key in seen:
                continue
            seen.add(key)
            sim = _place_piece_sim(eng.board, kind, rot, x)
            if sim is None:
                continue
            nb, lines = sim
            after = _board_stats(nb)
            action = f"r{int(rot % 4)}x{int(x)}"
            policy_action = _candidate_policy_action(kind, rot, x, lines, before, after)
            out.append(Candidate(action=action, policy_action=policy_action, rot=int(rot % 4), x=int(x), board=nb, lines=int(lines), stats=after, score=_heuristic_score(before, after, lines, kind)))
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def _apply_placement(eng: TetrisEngine, rot: int, x: int) -> int:
    cmds = 0
    for _ in range(int(rot) % 4):
        if eng.rotate():
            cmds += 1

    st = eng.get_state()
    cur = st.get("cur")
    if cur and cur.get("x") is not None:
        cx = int(cur.get("x") or 0)
        while cx < int(x):
            if eng.right():
                cmds += 1
                cx += 1
            else:
                break
        while cx > int(x):
            if eng.left():
                cmds += 1
                cx -= 1
            else:
                break

    eng.hard_drop()
    cmds += 1
    return int(cmds)


class PolicyShim:
    """Kleine, robuste UP-Schicht für Tetris.

    UniversalPolicy.choose() füllt unbekannte legale Actions mit q=0 auf. Für
    Tetris wäre das ungünstig, weil eine unbekannte Aktion dadurch besser wirkt
    als bekannte negative Evidenz. Deshalb liest diese Shim die vorhandenen
    Regeln selbst.

    v3.2 macht die Policy zu einem strengen Beratungs-/Bestätigungs-Pfad:
    Eine bekannte Regel darf die starke Fallback-Heuristik nur ersetzen, wenn sie
    sehr belastbare positive Evidenz hat UND der konkrete Placement-Kandidat
    board-technisch mindestens gleichwertig zum besten Fallback ist. Damit wird
    verhindert, dass abstrakte Reuse-Treffer zwar hohe Q-Werte haben, aber auf
    dem aktuellen Board eine leicht schlechtere Platzierung wählen, die erst
    viele Steine später als Top-out sichtbar wird.
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = str(namespace or "game:tetris")
        self.q_min_gate = float(_env_float("OROMA_TETRIS_POLICY_Q_MIN_GATE", -0.05))
        self.min_n = int(_env_int("OROMA_TETRIS_POLICY_MIN_N", 1))
        self.accept_q_min = float(_env_float("OROMA_TETRIS_POLICY_ACCEPT_Q_MIN", 0.80))
        self.accept_min_n = int(_env_int("OROMA_TETRIS_POLICY_ACCEPT_MIN_N", 5))
        self.max_score_gap = float(_env_float("OROMA_TETRIS_POLICY_MAX_SCORE_GAP", 0.0))
        self.max_extra_holes = int(_env_int("OROMA_TETRIS_POLICY_MAX_EXTRA_HOLES", 0))
        self.max_extra_top_danger = int(_env_int("OROMA_TETRIS_POLICY_MAX_EXTRA_TOP_DANGER", 0))
        self.max_extra_height = int(_env_int("OROMA_TETRIS_POLICY_MAX_EXTRA_HEIGHT", 0))
        self.max_extra_bumpiness = int(_env_int("OROMA_TETRIS_POLICY_MAX_EXTRA_BUMPINESS", 0))
        self.dbw_chunk = max(25, int(_env_int("OROMA_TETRIS_POLICY_DBW_CHUNK", 500)))
        self.used = 0
        self.fallback = 0
        self.q_rejected = 0
        self.seen = 0
        self.accepted = 0
        self.rejected_n = 0
        self.rejected_q = 0
        self.rejected_quality = 0
        self.rejected_unsafe = 0
        self.score_delta_sum = 0.0
        self.learn_ok = False
        self.auto_export_enabled = bool(_env_bool("OROMA_TETRIS_UP_AUTO_EXPORT", False))
        self._rule_cache: Dict[str, List[Any]] = {}

        policy_cls = None
        old_auto = os.environ.get("OROMA_UP_AUTO_EXPORT")
        try:
            # Tetris erzeugt viele Policy-Regeln pro Lauf. Der schnelle Hotpath
            # schreibt weiterhin policy_rules ueber UniversalPolicy/DBWriter,
            # aber exportiert standardmaessig nicht jede Kandidatenregel ins
            # allgemeine Regelarchiv. Bei Bedarf: OROMA_TETRIS_UP_AUTO_EXPORT=1.
            os.environ["OROMA_UP_AUTO_EXPORT"] = "1" if self.auto_export_enabled else "0"
            policy_cls = _load_universal_policy() if _env_bool("OROMA_TETRIS_POLICY_ENABLE", True) else None
            self.enabled = bool(policy_cls is not None and sql_manager is not None)
            self._up = policy_cls(namespace=self.namespace) if self.enabled and policy_cls is not None else None
        finally:
            if old_auto is None:
                os.environ.pop("OROMA_UP_AUTO_EXPORT", None)
            else:
                os.environ["OROMA_UP_AUTO_EXPORT"] = old_auto

    def _policy_candidate_is_unsafe(self, fallback: Candidate, cand: Candidate) -> bool:
        """Board-sicherheitsprüfung für konkrete Tetris-Reuse-Kandidaten.

        Abstrakte Policy-Actions können in einem ähnlichen State gelernt worden
        sein, aber auf dem aktuellen konkreten Board riskanter als der beste
        Fallback sein. Diese Prüfung blockiert genau solche Kandidaten.
        """
        if int(cand.stats.holes) > int(fallback.stats.holes) + int(self.max_extra_holes):
            return True
        if int(cand.stats.top_danger) > int(fallback.stats.top_danger) + int(self.max_extra_top_danger):
            return True
        if int(cand.stats.max_height) > int(fallback.stats.max_height) + int(self.max_extra_height):
            return True
        if int(cand.stats.bumpiness) > int(fallback.stats.bumpiness) + int(self.max_extra_bumpiness):
            return True
        return False

    def _policy_candidate_is_quality_bad(self, fallback: Candidate, cand: Candidate) -> bool:
        """Heuristik-Qualitätsprüfung relativ zum sicheren Fallback."""
        return float(fallback.score - cand.score) > float(self.max_score_gap)

    def choose(self, state_hash: str, candidates: List[Candidate]) -> Candidate:
        if not candidates:
            raise ValueError("no tetris candidates")
        fallback = candidates[0]
        if not self.enabled or sql_manager is None:
            self.fallback += 1
            return fallback
        legal: Dict[str, Candidate] = {}
        for c in candidates:
            # Kandidaten sind bereits heuristisch absteigend sortiert. Bei
            # gleicher abstrakter Aktionsklasse behalten wir den besten
            # konkreten Placement-Kandidaten.
            legal.setdefault(str(c.policy_action), c)
        rows: List[Any] = []
        try:
            rows = self._rule_cache.get(state_hash, [])
            if not rows:
                with sql_manager.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT action, q, n FROM policy_rules WHERE namespace=? AND state_hash=?",
                        (self.namespace, state_hash),
                    )
                    rows = cur.fetchall() or []
                if rows:
                    self._rule_cache[state_hash] = rows
                    if len(self._rule_cache) > 5000:
                        self._rule_cache.clear()
        except Exception:
            rows = []

        best: Optional[Tuple[float, int, Candidate]] = None
        saw_known = False
        rejected_n = False
        rejected_q = False
        for row in rows:
            try:
                action = str(row["action"] if hasattr(row, "keys") else row[0])
                q = float(row["q"] if hasattr(row, "keys") else row[1])
                n = int(row["n"] if hasattr(row, "keys") else row[2])
            except Exception:
                continue
            cand = legal.get(action)
            if cand is None:
                continue
            saw_known = True
            if n < max(int(self.min_n), int(self.accept_min_n)):
                rejected_n = True
                continue
            if q < max(float(self.q_min_gate), float(self.accept_q_min)):
                rejected_q = True
                continue
            if best is None or (q, n, cand.score) > (best[0], best[1], best[2].score):
                best = (float(q), int(n), cand)

        if saw_known:
            self.seen += 1

        if best is not None:
            cand = best[2]
            if self._policy_candidate_is_unsafe(fallback, cand):
                self.rejected_unsafe += 1
                self.q_rejected += 1
                self.fallback += 1
                return fallback
            if self._policy_candidate_is_quality_bad(fallback, cand):
                self.rejected_quality += 1
                self.q_rejected += 1
                self.fallback += 1
                return fallback
            self.used += 1
            self.accepted += 1
            self.score_delta_sum += float(cand.score - fallback.score)
            return cand

        if saw_known:
            self.q_rejected += 1
            if rejected_n:
                self.rejected_n += 1
            elif rejected_q:
                self.rejected_q += 1
        self.fallback += 1
        return fallback

    def learn_many(self, items: List[Dict[str, Any]]) -> int:
        if not items or self._up is None:
            self.learn_ok = False
            return 0
        old_chunk = os.environ.get("OROMA_POLICY_DBW_CHUNK")
        try:
            # UniversalPolicy liest den DBWriter-Chunk zur Laufzeit. Fuer den
            # Tetris-Batchpfad ist ein groesserer Chunk deutlich Pi-freundlicher
            # als viele kleine Low-Priority-DBWriter-Auftraege.
            os.environ["OROMA_POLICY_DBW_CHUNK"] = str(int(self.dbw_chunk))
            self._up.learn_many(items)
            self._rule_cache.clear()
            self.learn_ok = True
            return int(len(items))
        except Exception:
            self.learn_ok = False
            return 0
        finally:
            if old_chunk is None:
                os.environ.pop("OROMA_POLICY_DBW_CHUNK", None)
            else:
                os.environ["OROMA_POLICY_DBW_CHUNK"] = old_chunk


def _add_learn_item(items: List[Dict[str, Any]], *, state_hash: str, action: str, outcome: float, ts: Optional[int] = None) -> None:
    out = float(outcome)
    if abs(out) <= 1e-9:
        return
    items.append({"state_hash": str(state_hash), "action": str(action), "outcome": float(1.0 if out > 0 else -1.0), "ts": int(ts or _now())})


def _credit_piece(
    items: List[Dict[str, Any]],
    recent: List[Tuple[str, str]],
    *,
    before: BoardStats,
    after: BoardStats,
    lines: int,
    topout: bool,
    death_credit_steps: int,
) -> Tuple[int, int, int, int]:
    """Ereignis-Credit ohne neutrale Draws.

    Rückgabe: (line_credit, improve_credit, risk_credit, topout_credit)
    """
    line_credit = improve_credit = risk_credit = topout_credit = 0
    now = _now()

    if int(lines) > 0 and recent:
        reward_repeats = {1: 1, 2: 2, 3: 3, 4: 5}.get(int(lines), 1)
        for _ in range(reward_repeats):
            for sh, action in recent[-max(1, min(len(recent), 6 + int(lines) * 2)):]:
                _add_learn_item(items, state_hash=sh, action=action, outcome=1.0, ts=now)
                line_credit += 1

    # Positive Stabilisierung: weniger Löcher oder deutlich weniger Top-Danger.
    if int(after.holes) < int(before.holes) or int(after.top_danger) < int(before.top_danger):
        sh, action = recent[-1]
        _add_learn_item(items, state_hash=sh, action=action, outcome=1.0, ts=now)
        improve_credit += 1

    # Negative Evidenz nur für klar schädliche Platzierungen, nicht für jeden
    # neutralen Stein. So entsteht keine dauerhafte negative Wand.
    hole_delta = int(after.holes - before.holes)
    top_delta = int(after.top_danger - before.top_danger)
    max_delta = int(after.max_height - before.max_height)
    if hole_delta >= 2 or top_delta >= 1 or (after.max_height >= 18 and max_delta > 0):
        sh, action = recent[-1]
        _add_learn_item(items, state_hash=sh, action=action, outcome=-1.0, ts=now)
        risk_credit += 1

    if topout and recent:
        for sh, action in recent[-max(1, int(death_credit_steps)):]:
            _add_learn_item(items, state_hash=sh, action=action, outcome=-1.0, ts=now)
            topout_credit += 1

    return int(line_credit), int(improve_credit), int(risk_credit), int(topout_credit)


def _play_one(seed: int, *, eps: float, mode: str, max_steps: int, namespace: str, shim: PolicyShim) -> Dict[str, Any]:
    rng = random.Random(int(seed))
    eng = TetrisEngine(seed=int(seed))
    steps = pieces = commands = 0
    line_credit = improve_credit = risk_credit = topout_credit = 0
    t0 = time.time()
    learn_items: List[Dict[str, Any]] = []
    recent: List[Tuple[str, str]] = []
    death_credit_steps = int(_env_int("OROMA_TETRIS_DEATH_CREDIT_STEPS", 12))

    while eng.running and steps < int(max_steps):
        candidates = _enumerate_candidates(eng)
        if not candidates:
            break
        steps += 1
        before = _board_stats(eng.board)
        sh = _state_hash(eng)

        if mode == "explore" and rng.random() < float(eps):
            # Explore bleibt legal, aber nicht völlig blind. Zufall unter den
            # oberen 60% der Kandidaten erzeugt negative/positive Variation,
            # ohne sofort nur Top-out-Müll zu produzieren.
            k = max(1, int(len(candidates) * 0.60))
            chosen = rng.choice(candidates[:k])
        else:
            chosen = shim.choose(sh, candidates)

        score_before = int(getattr(eng, "score", 0))
        lines_before = int(getattr(eng, "lines_total", 0))
        commands += _apply_placement(eng, chosen.rot, chosen.x)
        pieces += 1
        score_after = int(getattr(eng, "score", 0))
        lines_after = int(getattr(eng, "lines_total", 0))
        lines_delta = max(0, int(lines_after - lines_before))
        after = _board_stats(eng.board)
        recent.append((sh, chosen.policy_action))
        if len(recent) > 32:
            recent = recent[-32:]

        topout = bool(not eng.running)
        if mode == "explore":
            lc, ic, rc, tc = _credit_piece(
                learn_items,
                recent,
                before=before,
                after=after,
                lines=lines_delta,
                topout=topout,
                death_credit_steps=death_credit_steps,
            )
            line_credit += lc
            improve_credit += ic
            risk_credit += rc
            topout_credit += tc

    sim_dt_ms = (time.time() - t0) * 1000.0
    learned = 0
    learn_dt_ms = 0.0
    if mode == "explore" and learn_items:
        lt0 = time.time()
        learned = shim.learn_many(learn_items)
        learn_dt_ms = (time.time() - lt0) * 1000.0

    dt_ms = (time.time() - t0) * 1000.0
    final_stats = _board_stats(eng.board)
    return {
        "steps": int(steps),
        "pieces": int(pieces),
        "commands": int(commands),
        "score_end": int(getattr(eng, "score", 0)),
        "lines_end": int(getattr(eng, "lines_total", 0)),
        "level_end": int(getattr(eng, "level", 0)),
        "duration_ms": float(dt_ms),
        "sim_duration_ms": float(sim_dt_ms),
        "learn_duration_ms": float(learn_dt_ms),
        "learn": bool(mode == "explore"),
        "learn_items": int(len(learn_items)),
        "learned_items": int(learned),
        "line_credit_items": int(line_credit),
        "improve_credit_items": int(improve_credit),
        "risk_credit_items": int(risk_credit),
        "topout_credit_items": int(topout_credit),
        "final_holes": int(final_stats.holes),
        "final_height": int(final_stats.max_height),
        "final_bumpiness": int(final_stats.bumpiness),
        "topout": 0 if eng.running else 1,
    }


def _run_batch(*, seed: int, games: int, eps: float, mode: str, max_steps: int, namespace: str) -> Dict[str, Any]:
    t0 = time.time()
    shim = PolicyShim(namespace=namespace)
    totals: Dict[str, float] = {
        "steps": 0.0,
        "score": 0.0,
        "lines": 0.0,
        "level": 0.0,
        "pieces": 0.0,
        "commands": 0.0,
        "learn_items": 0.0,
        "learned_items": 0.0,
        "line_credit_items": 0.0,
        "improve_credit_items": 0.0,
        "risk_credit_items": 0.0,
        "topout_credit_items": 0.0,
        "sim_duration_ms": 0.0,
        "learn_duration_ms": 0.0,
        "final_holes": 0.0,
        "final_height": 0.0,
        "final_bumpiness": 0.0,
        "topouts": 0.0,
    }
    high_score = high_lines = high_pieces = high_steps = 0

    for i in range(max(0, int(games))):
        res = _play_one(seed=int(seed) + i, eps=eps, mode=mode, max_steps=max_steps, namespace=namespace, shim=shim)
        totals["steps"] += float(res.get("steps", 0))
        totals["score"] += float(res.get("score_end", 0))
        totals["lines"] += float(res.get("lines_end", 0))
        totals["level"] += float(res.get("level_end", 0))
        totals["pieces"] += float(res.get("pieces", 0))
        totals["commands"] += float(res.get("commands", 0))
        for k in ("learn_items", "learned_items", "line_credit_items", "improve_credit_items", "risk_credit_items", "topout_credit_items", "sim_duration_ms", "learn_duration_ms", "final_holes", "final_height", "final_bumpiness"):
            totals[k] += float(res.get(k, 0))
        totals["topouts"] += float(res.get("topout", 0))
        high_score = max(high_score, int(res.get("score_end", 0)))
        high_lines = max(high_lines, int(res.get("lines_end", 0)))
        high_pieces = max(high_pieces, int(res.get("pieces", 0)))
        high_steps = max(high_steps, int(res.get("steps", 0)))

    dt_ms = (time.time() - t0) * 1000.0
    g = max(1, int(games))
    return {
        "games": int(games),
        "steps": int(totals["steps"]),
        "avg_score_end": float(totals["score"] / g),
        "high_score_end": float(high_score),
        "avg_lines_end": float(totals["lines"] / g),
        "high_lines_end": float(high_lines),
        "avg_level_end": float(totals["level"] / g),
        "avg_pieces": float(totals["pieces"] / g),
        "high_pieces": float(high_pieces),
        "avg_commands": float(totals["commands"] / g),
        "high_steps": float(high_steps),
        "duration_ms": float(dt_ms),
        "eps": float(eps),
        "mode": str(mode),
        "max_steps": int(max_steps),
        "namespace": str(namespace),
        "state_schema": "tetris:pro_v3",
        "policy_enabled": 1.0 if shim.enabled else 0.0,
        "policy_used": int(shim.used),
        "policy_fallback": int(shim.fallback),
        "policy_q_rejected": int(shim.q_rejected),
        "policy_seen": int(shim.seen),
        "policy_accepted": int(shim.accepted),
        "policy_rejected_n": int(shim.rejected_n),
        "policy_rejected_q": int(shim.rejected_q),
        "policy_rejected_quality": int(shim.rejected_quality),
        "policy_rejected_unsafe": int(shim.rejected_unsafe),
        "policy_score_delta_avg": float(shim.score_delta_sum / float(max(1, int(shim.accepted)))),
        "policy_q_min_gate": float(shim.q_min_gate),
        "policy_min_n": int(shim.min_n),
        "policy_accept_q_min": float(shim.accept_q_min),
        "policy_accept_min_n": int(shim.accept_min_n),
        "policy_max_score_gap": float(shim.max_score_gap),
        "policy_max_extra_holes": int(shim.max_extra_holes),
        "policy_max_extra_top_danger": int(shim.max_extra_top_danger),
        "policy_max_extra_height": int(shim.max_extra_height),
        "policy_max_extra_bumpiness": int(shim.max_extra_bumpiness),
        "policy_auto_export": 1.0 if shim.auto_export_enabled else 0.0,
        "policy_dbw_chunk": int(shim.dbw_chunk),
        "learn": bool(mode == "explore"),
        "learn_items": int(totals["learn_items"]),
        "learned_items": int(totals["learned_items"]),
        "policy_learn_ok": bool(shim.learn_ok) if mode == "explore" else False,
        "line_credit_items": int(totals["line_credit_items"]),
        "improve_credit_items": int(totals["improve_credit_items"]),
        "risk_credit_items": int(totals["risk_credit_items"]),
        "topout_credit_items": int(totals["topout_credit_items"]),
        "sim_duration_ms": float(totals["sim_duration_ms"]),
        "learn_duration_ms": float(totals["learn_duration_ms"]),
        "topouts": int(totals["topouts"]),
        "avg_final_holes": float(totals["final_holes"] / g),
        "avg_final_height": float(totals["final_height"] / g),
        "avg_final_bumpiness": float(totals["final_bumpiness"] / g),
        "source": "orchestrator",
        "runner": "tools/tetris_daily_runner.py",
        "shim": "tools.tetris_daily_runner.PolicyShim.pro_v3.strict_guard_v2",
    }


def _db_write(kind: str, label: str, meta: Dict[str, Any]) -> Optional[int]:
    if sql_manager is None:
        return None
    ts_start = int(meta.get("ts_start") or _now())
    ts_end = int(meta.get("ts_end") or _now())
    if ts_end <= ts_start:
        ts_end = ts_start + 1
    meta["ts_start"] = ts_start
    meta["ts_end"] = ts_end

    eid: Optional[int] = None
    last_err: Optional[Exception] = None
    for _attempt in range(3):
        try:
            _eid = sql_manager.insert_episode(kind=kind, ts_start=ts_start, ts_end=ts_end, label=label, meta=meta)
            if _eid is not None:
                eid = int(_eid)
                break
        except Exception as e:
            last_err = e
        time.sleep(0.5)

    if eid is None:
        if last_err is not None:
            meta["db_write_error"] = str(last_err)
        return None

    def m(key: str, val: Any) -> None:
        try:
            sql_manager.insert_episodic_metric(episode_id=eid, key=key, value=float(val))
        except Exception:
            return

    for k, v in list(meta.items()):
        if k in {"ts_start", "ts_end"}:
            continue
        if isinstance(v, bool):
            m(k, 1.0 if v else 0.0)
        elif isinstance(v, (int, float)):
            m(k, v)
    return int(eid)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy-games", type=int, default=100)
    ap.add_argument("--explore-games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--eps", type=float, default=_env_float("OROMA_TETRIS_EPS", 0.08))
    ap.add_argument("--max-steps", type=int, default=_env_int("OROMA_TETRIS_MAX_STEPS", 5000))
    ap.add_argument("--namespace", type=str, default="game:tetris")
    args = ap.parse_args()

    if sql_manager is not None:
        try:
            sql_manager.ensure_schema()
        except Exception:
            pass

    seed = int(args.seed or (_now() & 0xFFFFFFFF))
    namespace = str(args.namespace or "game:tetris")
    have_up = bool(_load_universal_policy() is not None) if _env_bool("OROMA_TETRIS_POLICY_ENABLE", True) else False
    out: Dict[str, Any] = {"ok": True, "have_db": sql_manager is not None, "have_up": have_up, "db_written": False, "seed": int(seed)}
    rc = 0
    try:
        p_t0 = time.time()
        policy_meta = _run_batch(seed=seed, games=args.policy_games, eps=0.0, mode="policy", max_steps=args.max_steps, namespace=namespace)
        policy_meta["ts_start"] = int(p_t0)
        policy_meta["ts_end"] = int(time.time())
        eid_p = _db_write(f"{namespace}:policy_batch", f"tetris:policy ({args.policy_games} games)", policy_meta)
        if eid_p is not None:
            policy_meta["episode_id"] = int(eid_p)

        e_t0 = time.time()
        explore_meta = _run_batch(seed=seed + 100000, games=args.explore_games, eps=args.eps, mode="explore", max_steps=args.max_steps, namespace=namespace)
        explore_meta["ts_start"] = int(e_t0)
        explore_meta["ts_end"] = int(time.time())
        eid_e = _db_write(f"{namespace}:explore_batch", f"tetris:explore ({args.explore_games} games)", explore_meta)
        if eid_e is not None:
            explore_meta["episode_id"] = int(eid_e)

        out["db_written"] = bool((eid_p is not None or int(args.policy_games) <= 0) and (eid_e is not None or int(args.explore_games) <= 0))
        out["policy"] = policy_meta
        out["explore"] = explore_meta
        if not out["db_written"] and sql_manager is not None:
            perr = policy_meta.get("db_write_error")
            eerr = explore_meta.get("db_write_error")
            out["err"] = f"db_write_failed: policy={perr or 'n/a'} explore={eerr or 'n/a'}"
    except Exception as e:
        rc = 2
        out["ok"] = False
        out["err"] = str(e)

    print(json.dumps(out, ensure_ascii=False, sort_keys=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
