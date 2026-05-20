#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Datei:    /opt/ai/oroma/mini_programs/snake.py
# Projekt:  ORÓMA – Mini-Programm Snake (Console/pygame)
# Modul:    Universal Policy (UP) integrierte Snake-Engine
# Version:  v3.7.3-r1 (UP + D4-orientierte Kanonisierung, Trajektorien, UPSERT)
# Stand:    2025-11-10
# Autor:    ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#  • Mensch vs KI, KI vs Board (autonom)
#  • Universal Policy (core.universal_policy.Policy) – bevorzugt
#    – Kanonischer State relativ zum Kopf (Heading→"UP")
#    – Actions im kanonischen Raum: {0:FWD, 1:LEFT, 2:RIGHT}
#    – Live-Feedback nach Spielende: UPSERT in policy_rules
#  • Fallback ohne Policy: einfache sichere Heuristik (Food-nah, Kollisionsvermeidung)
#  • Optional: SnapChain-Logging (falls core.snap/core.snapchain vorhanden)
#
# Abhängigkeiten
# ──────────────
#  • pygame (für GUI), numpy (optional)
#  • core.universal_policy (optional), core.sql_manager (UPSERT), core.snap/chain (optional)
#
# Steuerung (Human):
#  • Pfeiltasten ↑ ↓ ← → (im Human-Modus)
#
# ENV (sinnvolle Defaults)
#  • OROMA_SNAKE_W=20, OROMA_SNAKE_H=20, OROMA_SNAKE_FPS=10
#  • OROMA_SNAKE_EPS=0.08, OROMA_SNAKE_EPS_DECAY=1.0, OROMA_SNAKE_EPS_MIN=0.00
# =============================================================================

import sys, time, random
import os
import json
import hashlib
from typing import List, Tuple, Optional
import logging
from core.log_guard import log_suppressed

# pygame (GUI); bei headless notfalls nur Console-Loop nutzen
try:
    import pygame
except Exception:
    pygame = None  # type: ignore

try:
    import numpy as np  # nur für Snap-Feature optional
except Exception:
    np = None  # type: ignore

# ORÓMA Core (optional)
try:
    from core.sql_manager import get_conn as _get_conn
except Exception:
    _get_conn = None

try:
    import core.universal_policy as upol
    _HAVE_UP = True
except Exception:
    upol = None  # type: ignore
    _HAVE_UP = False

try:
    from core.snap import Snap
    from core.snapchain import SnapChain
except Exception:
    Snap = None       # type: ignore
    SnapChain = None  # type: ignore

# -----------------------------------------------------------------------------
# Gitter/Defaults
# -----------------------------------------------------------------------------
W = int((__import__("os").environ.get("OROMA_SNAKE_W") or 20))
H = int((__import__("os").environ.get("OROMA_SNAKE_H") or 20))
FPS = int((__import__("os").environ.get("OROMA_SNAKE_FPS") or 10))

# -----------------------------------------------------------------------------
# Option B: Snake → SnapChains (DB + optional JSON) für Policy-Training
# -----------------------------------------------------------------------------
# Warum:
#   - TicTacToe lernt über `snapchains`/`policy_rules`.
#   - Snake hatte (historisch) nur Datei-Exports ohne das Format, das der
#     UniversalAdapter (PolicyEngine) auswertet → 0 Training-Steps.
#
# Ziel:
#   - Pro Snake-Episode eine SnapChain in `snapchains` (origin=game:snake)
#   - Blob enthält:
#       * patterns[*].patterns → Vektorfolge (State)
#       * spec               → Indizes für Action-Ableitung (dir2)
#       * result             → Episode-Outcome (+1/-1)
#
# Steuerung:
#   - OROMA_SNAKE_EXPORT_DB=1   (default 1)
#   - OROMA_SNAKE_EXPORT_JSON=0 (default 0)
#   - OROMA_SNAPCHAINS_DIR=/opt/ai/oroma/data/snapchains (default)
SNAKE_EXPORT_DB = str(os.environ.get("OROMA_SNAKE_EXPORT_DB") or "1").strip() not in ("0", "false", "False")
SNAKE_EXPORT_JSON = str(os.environ.get("OROMA_SNAKE_EXPORT_JSON") or "0").strip() not in ("0", "false", "False")
SNAPCHAINS_DIR = os.environ.get("OROMA_SNAPCHAINS_DIR") or "/opt/ai/oroma/data/snapchains"

# Spec für UniversalAdapter (mini_programs/universal_policy/adapter_universal.py)
SNAKE_POLICY_SPEC = {
    "space": "world2d",
    "symmetry": "square_D4",
    "action": {"kind": "dir2"},
    "indices": {
        # vec = [rel_x, rel_y, head_x, head_y, food_x, food_y, len_norm]
        "rel": [0, 1],
        "head": [2, 3],
        "food": [4, 5],
    },
}


def _sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _export_episode_to_db(*, chain_dict: dict, origin: str, namespace: str, quality: float, notes: str) -> None:
    """Write one Snake episode into `snapchains` (DB).

    We intentionally do NOT reuse LangzeitGedaechtnis.save_snapchain() here,
    because we want to persist extra top-level keys (spec/result) exactly as
    UniversalAdapter expects them.
    """
    # Lazy import: Snake can run without ORÓMA core in minimal mode.
    try:
        from core import sql_manager
    except Exception:
        return

    blob = json.dumps(chain_dict, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ts = float(chain_dict.get("ts_created") or time.time())

    with sql_manager.get_conn() as conn:
        # Schema-adaptiv: unsere ORÓMA-Snapchains-Tabelle hat (aktuell) KEINE "steps"-Spalte.
        # Deshalb schreiben wir nur die Spalten, die wirklich existieren.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(snapchains)")}

        fields = []
        values = []

        if "ts" in cols:
            fields.append("ts")
            values.append(int(ts))  # Schema: ts INTEGER

        if "namespace" in cols:
            fields.append("namespace")
            values.append(None if namespace in ("", None) else str(namespace))

        if "origin" in cols:
            fields.append("origin")
            values.append(str(origin))

        if "quality" in cols:
            fields.append("quality")
            values.append(float(quality))

        if "blob" in cols:
            fields.append("blob")
            values.append(blob)

        # Optional: Notizen (Debug/Explain)
        if "notes" in cols:
            fields.append("notes")
            values.append(str(notes))

        # Optional: Status/Weight wenn vorhanden
        if "status" in cols:
            fields.append("status")
            values.append("active")
        if "weight" in cols:
            fields.append("weight")
            values.append(1.0)

        placeholders = ",".join(["?"] * len(fields))
        sql = f"INSERT INTO snapchains({','.join(fields)}) VALUES({placeholders})"
        conn.execute(sql, values)
        conn.commit()


def _export_episode_to_json(*, chain_dict: dict, notes: str) -> None:
    """Optional: write the same payload into SNAPCHAINS_DIR for debugging."""
    try:
        os.makedirs(SNAPCHAINS_DIR, exist_ok=True)
        fn = os.path.join(SNAPCHAINS_DIR, f"snake_{int(time.time())}_{notes[:12]}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(chain_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_suppressed('mini_programs/snake.py:190', exc=e, level=logging.WARNING)
        pass

# -----------------------------------------------------------------------------
# Option B: Snake-Learning in DB (PolicyEngine-kompatibel)
# -----------------------------------------------------------------------------
#
# Problem (dein Befund):
#   - Es existieren Snake-JSONs im SnapDir, aber keine DB-Snapchains für
#     game:snake, daher bleibt train_snake_policy bei "0 Schritte".
#
# Lösung (Option B):
#   - Beim Spielende exportieren wir die Episode als SnapChain-JSON in die DB
#     Tabelle `snapchains` (origin='game:snake').
#   - Das Blob-Format enthält zusätzlich `spec` + `result` auf Root-Ebene, damit
#     UniversalAdapter (mini_programs/universal_policy/adapter_universal.py)
#     daraus Trainingsschritte ableiten kann.
#   - Zusätzlich schreiben wir optional eine JSON-Datei ins SnapDir (Audit).
#
# Minimal-Datenlast:
#   - Vektoren sind klein (typisch 7 floats pro Tick). Pro Episode sind das
#     wenige KB bis wenige 100KB.
# -----------------------------------------------------------------------------

OROMA_SNAKE_EXPORT_DB = (os.environ.get("OROMA_SNAKE_EXPORT_DB") or "1") not in ("0", "false", "False")
OROMA_SNAKE_EXPORT_JSON = (os.environ.get("OROMA_SNAKE_EXPORT_JSON") or "1") not in ("0", "false", "False")
OROMA_SNAKE_EXPORT_MIN_STEPS = int(os.environ.get("OROMA_SNAKE_EXPORT_MIN_STEPS") or 25)
OROMA_SNAPCHAINS_DIR = os.environ.get("OROMA_SNAPCHAINS") or "/opt/ai/oroma/data/snapchains"

# Backward-Compatibility: ältere Codepfade erwarteten SNAKE_EXPORT_ENABLE
# (wir verwenden ab jetzt die ORÓMA-ENV-Flags als Quelle der Wahrheit).
SNAKE_EXPORT_ENABLE = OROMA_SNAKE_EXPORT_DB
SNAKE_EXPORT_DB = OROMA_SNAKE_EXPORT_DB
SNAKE_EXPORT_JSON = OROMA_SNAKE_EXPORT_JSON


# Absolute Richtungen (dx,dy)
# WICHTIG:
#   Wir prefixen die Tupel bewusst mit DIR_, damit es keinen Namenskonflikt mit
#   der (Universal) Policy-Shim-Klasse namens "UP" weiter unten gibt.
DIR_UP, DIR_RIGHT, DIR_DOWN, DIR_LEFT = (0, -1), (1, 0), (0, 1), (-1, 0)
DIRS = (DIR_UP, DIR_RIGHT, DIR_DOWN, DIR_LEFT)

def _dir_idx(d: Tuple[int,int]) -> int:
    try: return DIRS.index(d)
    except Exception: return 0

def _rot_k_for_heading(heading: Tuple[int,int]) -> int:
    """Wie oft 90° rechts drehen, um heading auf 'DIR_UP' zu bringen."""
    idx = _dir_idx(heading)
    # DIR_UP→0, DIR_RIGHT→3 (eine Links), DIR_DOWN→2 (2x), DIR_LEFT→1 (eine Rechts) → wir nutzen Rechtsdrehungen
    # Rechtsdrehungen: DIR_UP(0):0, DIR_RIGHT(1):3, DIR_DOWN(2):2, DIR_LEFT(3):1
    return (4 - idx) % 4

def _rot_dir(d: Tuple[int,int], k: int) -> Tuple[int,int]:
    """Rotiere Richtung k*90° rechts."""
    dx, dy = d
    for _ in range(k):
        dx, dy = dy, -dx
    return (dx, dy)

def _wrap(x: int, y: int) -> Tuple[int,int]:
    return (x % W, y % H)

def _torus_delta(ax:int, ay:int, bx:int, by:int) -> Tuple[int,int]:
    """Kürzeste Differenz (Torus)."""
    dx = bx - ax
    dy = by - ay
    if abs(dx) > W//2: dx -= (W if dx>0 else -W)
    if abs(dy) > H//2: dy -= (H if dy>0 else -H)
    return dx, dy

def _left_of(d:Tuple[int,int]) -> Tuple[int,int]:
    return (-d[1], d[0])

def _right_of(d:Tuple[int,int]) -> Tuple[int,int]:
    return (d[1], -d[0])

# -----------------------------------------------------------------------------
# UP Shim
# -----------------------------------------------------------------------------
class UP:
    """
    Universal Policy Shim für Snake.
    Actions (kanonisch): 0=FWD, 1=LEFT, 2=RIGHT
    """
    def __init__(self, namespace: str = "game:snake"):
        self.namespace = namespace
        self.impl = None
        if _HAVE_UP and hasattr(upol, "Policy"):
            try:
                self.impl = upol.Policy(namespace=self.namespace)  # type: ignore[attr-defined]
            except Exception:
                self.impl = None

    def choose(self, state_hash: str, legal_actions: List[int]) -> Optional[int]:
        if not (self.impl and legal_actions):
            return None
        try:
            if hasattr(self.impl, "choose"):
                return self.impl.choose(state_hash, legal_actions, side="oroma")  # type: ignore[attr-defined]
        except Exception:
            return None
        return None

    def learn_many(self, items: List[dict]):
        # Impl lernen lassen
        try:
            if self.impl and hasattr(self.impl, "learn"):
                self.impl.learn(items)  # type: ignore[attr-defined]
        except Exception as e:
            log_suppressed('mini_programs/snake.py:301', exc=e, level=logging.WARNING)
            pass
        # DB-UPSERT policy_rules
        if not _get_conn: return
        try:
            with _get_conn() as c:
                now = int(time.time())
                for it in items:
                    st = it["state_hash"]; ac = int(it["action_canon"])
                    out = float(it["outcome"])
                    pos = 1 if out > 0 else 0
                    neg = 1 if out < 0 else 0
                    drw = 1 if out == 0 else 0
                    c.execute(
                        """INSERT INTO policy_rules
                           (namespace, state_hash, action, n, pos, neg, draw, q, last_ts)
                           VALUES (?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(namespace, state_hash, action) DO UPDATE SET
                             n    = policy_rules.n + 1,
                             pos  = policy_rules.pos + excluded.pos,
                             neg  = policy_rules.neg + excluded.neg,
                             draw = policy_rules.draw + excluded.draw,
                             q    = (policy_rules.q + excluded.q)/2.0,
                             last_ts = excluded.last_ts""",
                        (self.namespace, st, ac, 1, pos, neg, drw, out, now)
                    )
                c.commit()
        except Exception as e:
            log_suppressed('mini_programs/snake.py:329', exc=e, level=logging.WARNING)
            pass

# -----------------------------------------------------------------------------
# Snake Game Kern
# -----------------------------------------------------------------------------
class SnakeGame:
    def __init__(self, ai_mode: bool = False):
        self.ai_mode = ai_mode
        self.eps = float((__import__("os").environ.get("OROMA_SNAKE_EPS") or 0.08))
        self.eps_decay = float((__import__("os").environ.get("OROMA_SNAKE_EPS_DECAY") or 1.0))
        self.eps_min = float((__import__("os").environ.get("OROMA_SNAKE_EPS_MIN") or 0.00))

        # ORÓMA-Schlange
        self.snake: List[Tuple[int,int]] = [(W//2, H//2)]
        self.heading: Tuple[int,int] = random.choice(DIRS)
        self.food: Tuple[int,int] = self._spawn_food()
        self.score = 0
        self.ticks = 0
        self.alive = True

        # Universal Policy
        self.policy = UP(namespace="game:snake")
        self.policy_enabled = True if self.policy.impl else False
        self.traj: List[dict] = []   # [{state_hash, action_canon, ts}]

        # SnapChain (optional)
        # Option B: record episodes as SnapChains for offline Policy-Training.
        # NOTE: We store origin/namespace in metadata (SnapChain has no namespace kwargs).
        self.snapchain = (
            SnapChain(
                patterns=[],
                metadata={
                    "game": "snake",
                    "format": "oroma_snake_v3_8_9",
                    "namespace": "game:snake",
                    "origin": "game:snake",
                },
            )
            if SnapChain
            else None
        )
        self._episode_ts_start = time.time()

    # ---- Helpers ----
    def _spawn_food(self) -> Tuple[int,int]:
        while True:
            pos = (random.randint(0, W-1), random.randint(0, H-1))
            if pos not in self.snake:
                return pos

    def _danger(self, pt: Tuple[int,int]) -> bool:
        return (pt in self.snake)  # andere Gefahren in Console nicht vorhanden

    def _canon_state_and_legal(self) -> Tuple[str, List[int]]:
        """
        State Hash relativ zum Kopf/Heading:
          • Heading → rotiert zu UP
          • Features: danger_front/left/right, food_dx, food_dy, len
          • Actions: 0=FWD, 1=LEFT, 2=RIGHT (immer legal; Reverse vermeiden wir implizit)
        """
        head = self.snake[0]
        k = _rot_k_for_heading(self.heading)

        # Zellen vor/links/rechts in absoluter Welt
        fwd_abs = _rot_dir(DIR_UP, k)         # entspricht current heading
        left_abs = _rot_dir(DIR_LEFT, k)      # relativ links
        right_abs = _rot_dir(DIR_RIGHT, k)    # relativ rechts

        def step(p, d): return _wrap(p[0]+d[0], p[1]+d[1])

        front_pt = step(head, fwd_abs)
        left_pt  = step(head, left_abs)
        right_pt = step(head, right_abs)

        dang_f = int(self._danger(front_pt))
        dang_l = int(self._danger(left_pt))
        dang_r = int(self._danger(right_pt))

        # Food-Delta in Torus-Metrik, danach in Heading-Koordinaten rotieren
        dx, dy = _torus_delta(head[0], head[1], self.food[0], self.food[1])
        # rotiere das Vektorpaar k-mal rechts herum
        v = (dx, dy)
        for _ in range(k):
            v = (v[1], -v[0])
        fdx, fdy = v  # in kanonischer Sicht: fdy<0 == vorwärts

        sh = f"v1|F{dang_f}L{dang_l}R{dang_r}|DX{fdx}|DY{fdy}|LEN{len(self.snake)}"
        legal = [0,1,2]  # FWD/LEFT/RIGHT
        return sh, legal

    def _canon_to_abs_dir(self, a: int) -> Tuple[int,int]:
        """Mappe kanonische Action (0/1/2) auf absolute (dx,dy)."""
        # In kanonischer Sicht: DIR_UP=fwd, DIR_LEFT, DIR_RIGHT – rotiere zurück
        if a == 0: d_can = DIR_UP
        elif a == 1: d_can = DIR_LEFT
        else: d_can = DIR_RIGHT
        # zurückrotieren: k_inv = (4-k)
        k = _rot_k_for_heading(self.heading)
        k_inv = (4 - k) % 4
        return _rot_dir(d_can, k_inv)

    # ---- Schritt ----
    def step(self) -> bool:
        if not self.alive:
            return False

        self.ticks += 1

        # Policy / Exploration / Heuristik
        sh, legal = self._canon_state_and_legal()
        use_explore = (self.policy_enabled and (random.random() < self.eps))
        a_canon: Optional[int] = None
        if self.policy_enabled and not use_explore:
            a_canon = self.policy.choose(sh, legal)

        if a_canon is None:
            # Fallback-Heuristik: sichere Wahl Richtung Food
            a_canon = self._heuristic_pick(sh)

        # Trajektorie merken
        self.traj.append({"state_hash": sh, "action_canon": int(a_canon), "ts": int(time.time())})

        # Absolut bewegen
        d_abs = self._canon_to_abs_dir(int(a_canon))
        new_head = _wrap(self.snake[0][0] + d_abs[0], self.snake[0][1] + d_abs[1])

        # Kollision?
        if new_head in self.snake:
            self.alive = False
            self._learn_outcome(final="oroma_dead")
            # Option B: persist episode for offline Policy-Training
            self.persist_episode(final="oroma_dead")
            return False

        # Vorwärts
        self.snake.insert(0, new_head)
        ate = (new_head == self.food)
        if ate:
            self.score += 1
            self.food = self._spawn_food()
        else:
            self.snake.pop()

        # SnapChain (optional)
        try:
            if Snap and self.snapchain:
                # Policy-Training Vektor (v3.8.9 Spec):
                #   [rel_x, rel_y, head_x, head_y, food_x, food_y, len_norm]
                hx, hy = self.snake[0]
                fx, fy = self.food
                dx, dy = _torus_delta(hx, hy, fx, fy)
                # normiere Relativvektor auf [-1..+1]
                rel_x = float(dx) / max(1.0, float(W / 2.0))
                rel_y = float(dy) / max(1.0, float(H / 2.0))
                len_norm = float(len(self.snake)) / max(5.0, float(W * H))
                vec = [
                    rel_x,
                    rel_y,
                    float(hx) / float(W),
                    float(hy) / float(H),
                    float(fx) / float(W),
                    float(fy) / float(H),
                    len_norm,
                ]
                meta = {"action": int(a_canon), "score": int(self.score), "tick": int(self.tick)}
                self.snapchain.append(Snap(vec, metadata=meta))  # type: ignore
        except Exception as e:
            log_suppressed('mini_programs/snake.py:497', exc=e, level=logging.WARNING)
            pass

        return True

    def _heuristic_pick(self, state_hash: str) -> int:
        """
        Sehr einfache Wahl:
          • Vermeide unmittelbare Kollision (front/left/right)
          • Bevorzuge Food-nähere Richtung
        """
        head = self.snake[0]
        k = _rot_k_for_heading(self.heading)
        # Kandidaten in kanonischer Sicht → abs prüfen
        candidates = [0,1,2]  # fwd,left,right
        scored = []
        for a in candidates:
            d = self._canon_to_abs_dir(a)
            nh = _wrap(head[0] + d[0], head[1] + d[1])
            if nh in self.snake:
                s = 1e9  # stark schlecht
            else:
                dx, dy = _torus_delta(nh[0], nh[1], self.food[0], self.food[1])
                s = abs(dx) + abs(dy)
            scored.append((s, a))
        scored.sort()
        best_a = scored[0][1]
        return best_a

    # ---- Lernen am Ende ----
    def _learn_outcome(self, final: str):
        if not (self.policy and self.policy_enabled and self.traj):
            return
        # Einfaches Ergebnis:
        #  • Tod → -1.0
        #  • Sonst neutral (hier nur "oroma_dead" möglich in Console)
        out = -1.0 if final == "oroma_dead" else 0.0
        items = []
        for tr in self.traj:
            items.append({
                "state_hash": tr["state_hash"],
                "action_canon": tr["action_canon"],
                "side": "oroma",
                "outcome": float(out),
                "ts": tr["ts"],
            })
        self.policy.learn_many(items)

    def persist_episode(self, final: str) -> None:
        """Option B: persist one finished episode as SnapChain into DB + optional JSON.

        This is intentionally **best-effort**:
          - if snapchain recording is disabled/unavailable -> no-op
          - DB write errors should not crash the game
        """
        if not (SNAKE_EXPORT_ENABLE and self.snapchain and SnapChain and Snap):
            return

        try:
            d = self.snapchain.to_dict()
            # Root-level spec/result for UniversalAdapter.
            d["spec"] = dict(SNAKE_POLICY_SPEC)
            # Ergebnis: binär (>=1 Food = +1, sonst -1)
            d["result"] = 1.0 if int(self.score) > 0 else -1.0

            blob = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            h = _sha1_hex(blob)[:12]
            notes = f"snake_episode:{int(self.episode_ts_start)}:{h}"

            # quality steuern wir explizit, damit spätere Filter nicht "0 Schritte" ergeben.
            quality = float(d["result"])

            _export_episode_to_db(
                chain_dict=d,
                origin="game:snake",
                namespace="",
                quality=quality,
                notes=notes,
            )

            if SNAKE_EXPORT_JSON:
                _export_episode_to_json(d, notes)

        except Exception:
            # bewusst still: Snake soll nicht abstürzen, nur weil Export nicht klappt
            return

# -----------------------------------------------------------------------------
# Pygame-Frontend
# -----------------------------------------------------------------------------
def run_gui(ai_mode: bool = False):
    if not pygame:
        print("pygame nicht verfügbar – wechsle in Console-Mode.")
        return run_console(ai_mode)

    CELL = 20
    ww, hh = W*CELL, H*CELL

    pygame.init()
    screen = pygame.display.set_mode((ww, hh))
    pygame.display.set_caption("ORÓMA Snake (UP)")
    clock = pygame.time.Clock()

    game = SnakeGame(ai_mode=ai_mode)
    running = True

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN and not ai_mode:
                if ev.key == pygame.K_UP:      game.heading = DIR_UP
                elif ev.key == pygame.K_DOWN:  game.heading = DIR_DOWN
                elif ev.key == pygame.K_LEFT:  game.heading = DIR_LEFT
                elif ev.key == pygame.K_RIGHT: game.heading = DIR_RIGHT

        alive = game.step()
        if not alive:
            print(f"Game Over! Score={game.score}")
            time.sleep(1.0)
            running = False

        # Render
        screen.fill((0,0,0))
        # Food
        pygame.draw.rect(screen, (220,40,40), (game.food[0]*CELL, game.food[1]*CELL, CELL, CELL))
        # Snake
        for i,(x,y) in enumerate(game.snake):
            col = (30,180,255) if i==0 else (30,140,220)
            pygame.draw.rect(screen, col, (x*CELL, y*CELL, CELL-1, CELL-1))
        pygame.display.flip()
        clock.tick(FPS)

    # Option B: if the window gets closed while still alive, persist what we have.
    if game.alive:
        game.persist_episode(final="quit")

    pygame.quit()

# -----------------------------------------------------------------------------
# Console-Loop (fallback/headless)
# -----------------------------------------------------------------------------
def run_console(ai_mode: bool = False):
    g = SnakeGame(ai_mode=ai_mode)
    print("Starte Snake (Console). 'ai_mode' ignoriert in Console – ORÓMA steuert.")
    while g.step():
        pass
    print("Fertig. Score:", g.score)

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    ai_mode = ("--ai" in sys.argv)
    if "--nogui" in sys.argv or not pygame:
        run_console(ai_mode=ai_mode)
    else:
        run_gui(ai_mode=ai_mode)
