# =============================================================================
# Pfad:      /opt/ai/oroma/mini_programs/capture_the_flag.py
# Projekt:   ORÓMA – Offline-First Edge-KI (Headless · Mini-Programs)
# Modul:     Capture The Flag (CTF) – 2-Agenten Grid-Environment (A/B) inkl. Reward-Shaping, Feature-Vektor, ASCII-Render
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert ein vereinfachtes „Capture-the-Flag“ (CTF) Grid-Game
# als **Mini-Program** für ORÓMA.
#
# Motivation in ORÓMA:
# - kontrollierte Multi-Agent Lernarena (2 Agenten A & B)
# - klar definierte Rewards, deterministische Zustandsübergänge
# - ideal für Policy-/Rules-Lernen, Curriculum und Reward-Metriken
#
# Das Environment ist bewusst „leicht“:
# - keine externen RL-Frameworks nötig (kein gymnasium, kein pettingzoo)
# - headless, rein in Python/stdlib + dataclasses
# - liefert Feature-Vektoren (Observe) als einfache float-Liste
#
# SPIELREGELN (KURZ, EXAKT)
# ─────────────────────────
# - Grid: width × height
# - Agent A startet in A_base, Agent B startet in B_base
# - Jede Seite hat eine Flagge (A_flag, B_flag) auf der jeweiligen Seite
# - Flag aufnehmen: Agent betritt das gegnerische Flag-Feld → carry=True
# - Scoring: Agent mit gegnerischer Flagge betritt eigene Base → score +1,
#            carry wird gelöscht, Agent respawnt in Base (Positionsreset)
# - Tag/Kollision: Wenn nach Bewegung beide auf gleichem Feld stehen:
#     • gegnerischer Carrier wird „getaggt“ → verliert Flag (drop) + respawn in Base
#     • Tag kann eine kleine Penalty erzeugen (tag_penalty)
#
# ACTION SPACE (EXAKT)
# ────────────────────
# Pro Agent eine diskrete Aktion:
#   0 = Stay
#   1 = Up
#   2 = Down
#   3 = Left
#   4 = Right
#
# Intern: ACTIONS Dict mappt action → (dx, dy)
# und _clip() begrenzt Positionen auf das Grid.
#
# REWARD-SYSTEM (EXAKT AUS CTFConfig)
# ───────────────────────────────────
# step_reward   (Default: +0.01)  → pro Schritt (kleiner Anreiz zu handeln)
# score_reward  (Default: +1.0)   → wenn Score erzielt wird (Flag in Base)
# tag_penalty   (Default: -0.2)   → wenn ein Agent „getaggt“ wird (Carrier Drop/Respawn)
# end_penalty   (Default: -0.5)   → wenn Episode durch max_steps endet (Anti-Stall)
#
# Termination:
# - score_limit erreicht (Default: 3)  → done=True, reason="score_limit"
# - max_steps erreicht (Default: 1000)→ done=True, reason="max_steps" + end_penalty auf beide
#
# DATENMODELLE (KLASSEN)
# ──────────────────────
# @dataclass CTFConfig:
#   width:int=11, height:int=7,
#   max_steps:int=1000, score_limit:int=3,
#   respawn_on_tag:bool=True,
#   step_reward:float=0.01, score_reward:float=1.0,
#   tag_penalty:float=-0.2, end_penalty:float=-0.5,
#   seed:Optional[int]=None
#
# @dataclass CTFState:
#   enthält vollständigen Zustand (Positions, Bases, Flags, Carry, Scores, Steps, Grid size)
#   → geeignet für Serialisierung/Logging/Debug
#
# class CTFEnv:
#   - reset() -> CTFState
#   - step(actions: {"A":int,"B":int}) -> (CTFState, rewards:{"A":float,"B":float}, done:bool, info:dict)
#   - observe(agent:"A"|"B") -> List[float] Feature-Vektor
#   - render_ascii() -> str (Box-Drawing + Symbole)
#
# OBSERVATION / FEATURE-VEKTOR (EXAKT)
# ────────────────────────────────────
# observe(agent) liefert eine float-Liste der Länge 13:
#   [ sx, sy, ox, oy, bx, by, fx, fy,
#     carry_self, carry_opp,
#     score_self_norm, score_opp_norm, steps_norm ]
#
# Bedeutungen:
# - sx,sy   : eigene Position normiert auf [0..1]
# - ox,oy   : Gegnerposition normiert
# - bx,by   : eigene Base normiert
# - fx,fy   : gegnerische Flagge normiert
# - carry_self: 1.0 wenn Agent gegnerische Flag trägt, sonst 0.0
# - carry_opp : 1.0 wenn Gegner die eigene Flag trägt, sonst 0.0
# - score_self_norm: score_self / score_limit (clamped 0..1)
# - score_opp_norm : score_opp  / score_limit (clamped 0..1)
# - steps_norm     : steps / max_steps (clamped 0..1)
#
# Hinweis:
# - Feature-Vektor ist bewusst minimalistisch und stabil, damit Policy-Lernen
#   auf Edge-Hardware schnell bleibt.
#
# ASCII-RENDER (EXAKT)
# ────────────────────
# render_ascii() zeichnet:
# - leeres Grid mit Punkten
# - Flaggen: 'f' (A_flag), 'g' (B_flag)
# - Bases:   'A' (A_base), 'B' (B_base)
# - Agenten:
#     'α' / 'β'  normal
#     'Α' / 'Β'  wenn Agent eine Flag trägt (groß = trägt Flag)
# + Rahmen (┌─┐ … └─┘) + Statuszeile (Scores + Steps)
#
# HEURISTIK-AGENTS (OPTIONAL, FÜR DEMO/BASELINE)
# ──────────────────────────────────────────────
# greedy_carrier(env, agent):
# - simple Heuristik: wenn nicht carrying → zur gegnerischen Flag; sonst zur eigenen Base
#
# chaser_defender(env, agent):
# - simple Heuristik: jagt den Gegner wenn dieser carry hat, sonst verteidigt/balanciert
#
# Diese Heuristiken sind für:
# - Debug
# - deterministische Baseline-Performance
# - schnelle Smoke-Tests
# gedacht (kein „optimaler“ Agent).
#
# DEMO / CLI
# ──────────
# Direktlauf (nur Demo, kein ORÓMA-UI):
#   python3 /opt/ai/oroma/mini_programs/capture_the_flag.py
#
# Demo:
# - env.reset()
# - Heuristik A vs Heuristik B
# - alle 5 Steps oder bei Events: ASCII render + Reward + Info
#
# ORÓMA-INTEGRATION (WIE ES ÜBLICHERWEISE EINGEHÄNGT WIRD)
# ───────────────────────────────────────────────────────
# Dieses Mini-Program ist typischerweise über mini_programs Registry / UI erreichbar:
# - UI kann Episodes starten und SnapChains loggen (origin="game:capture_the_flag")
# - Policy-Training kann den Feature-Vektor nutzen (observe) + reward shaping
#
# Dieses Modul selbst schreibt **nicht** in die DB.
# Persistenz/Logging (SnapChains/Rewards/Metrics) ist Aufgabe der Caller (UI/Core Hooks).
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - Action Space bleibt 0..4 mit identischer Bedeutung (Policy-Kompatibilität).
# - observe() liefert stabil 13 floats in identischer Reihenfolge (Model-Kompatibilität).
# - step() bleibt deterministisch (außer seed-abhängige Startzustände, falls erweitert).
# - Reward-Felder bleiben in CTFConfig zentral (kein Hardcode an mehreren Stellen).
# - Keine UI/GUI Abhängigkeiten einführen (Headless-Anforderung).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional
import random
import time

# ----------------------------
# Konfiguration
# ----------------------------

@dataclass
class CTFConfig:
    width: int = 11
    height: int = 7
    max_steps: int = 1000
    score_limit: int = 3
    respawn_on_tag: bool = True
    step_reward: float = 0.01
    score_reward: float = 1.0
    tag_penalty: float = -0.2
    end_penalty: float = -0.5
    seed: Optional[int] = None

# ----------------------------
# State-Dataclass (für Serialisierung)
# ----------------------------

@dataclass
class CTFState:
    A_pos: Tuple[int, int]
    B_pos: Tuple[int, int]
    A_base: Tuple[int, int]
    B_base: Tuple[int, int]
    A_flag: Tuple[int, int]
    B_flag: Tuple[int, int]
    A_carry: bool
    B_carry: bool
    A_score: int
    B_score: int
    steps: int
    width: int
    height: int
    done: bool

# ----------------------------
# Umgebung
# ----------------------------

class CTFEnv:
    ACTIONS = {
        0: (0, 0),
        1: (0, -1),  # up
        2: (0, 1),   # down
        3: (-1, 0),  # left
        4: (1, 0),   # right
    }

    def __init__(self, cfg: Optional[CTFConfig] = None):
        self.cfg = cfg or CTFConfig()
        self.rng = random.Random(self.cfg.seed or int(time.time() * 1e6) & 0xffffffff)
        self.reset(seed=self.cfg.seed)

    # --- Helpers ---

    def _centered_positions(self):
        W, H = self.cfg.width, self.cfg.height
        mid_x = W // 2
        A_base = (1, H // 2)
        B_base = (W - 2, H // 2)
        A_flag = (W - 2, H // 2)   # B-Seite
        B_flag = (1, H // 2)      # A-Seite
        return A_base, B_base, A_flag, B_flag

    def _clip(self, x: int, y: int) -> Tuple[int, int]:
        return max(0, min(x, self.cfg.width - 1)), max(0, min(y, self.cfg.height - 1))

    # --- API ---

    def reset(self, seed: Optional[int] = None) -> CTFState:
        if seed is not None:
            self.rng.seed(seed)

        self.A_base, self.B_base, self.A_flag, self.B_flag = self._centered_positions()
        self.A_pos = self.A_base
        self.B_pos = self.B_base
        self.A_carry = False
        self.B_carry = False
        self.A_score = 0
        self.B_score = 0
        self.steps = 0
        self.done = False
        return self._mk_state()

    def _move(self, pos: Tuple[int, int], a: int) -> Tuple[int, int]:
        dx, dy = self.ACTIONS.get(a, (0, 0))
        nx, ny = pos[0] + dx, pos[1] + dy
        return self._clip(nx, ny)

    def step(self, actions: Dict[str, int]) -> Tuple[CTFState, Dict[str, float], bool, Dict]:
        if self.done:
            return self._mk_state(), {"A": 0.0, "B": 0.0}, True, {"reason": "already_done"}

        self.steps += 1
        aA = int(actions.get("A", 0))
        aB = int(actions.get("B", 0))

        # 1) Bewegung simultan berechnen
        new_A = self._move(self.A_pos, aA)
        new_B = self._move(self.B_pos, aB)

        # 2) Kollision auf Zielkachel (gleiches Feld nach Zug) -> Tagging
        tag_A = False
        tag_B = False
        if new_A == new_B and self.cfg.respawn_on_tag:
            # Gegenseitig getaggt? -> beide respawnen
            tag_A = True
            tag_B = True
        else:
            # sonst bewegen
            self.A_pos = new_A
            self.B_pos = new_B
            # Tagging falls nach Bewegung gleiches Feld (Sicherheitsnetz – sollte durch oben abgedeckt sein)
            if self.A_pos == self.B_pos and self.cfg.respawn_on_tag:
                tag_A = True
                tag_B = True

        # 3) Tagging/Effekte
        reward = {"A": self.cfg.step_reward, "B": self.cfg.step_reward}
        info: Dict[str, any] = {"event": []}

        if tag_A:
            self.A_pos = self.A_base
            if self.A_carry:
                self.A_carry = False  # drop
                info["event"].append("A_drop_flag")
            reward["A"] += self.cfg.tag_penalty
            info["event"].append("A_tagged")
        if tag_B:
            self.B_pos = self.B_base
            if self.B_carry:
                self.B_carry = False  # drop
                info["event"].append("B_drop_flag")
            reward["B"] += self.cfg.tag_penalty
            info["event"].append("B_tagged")

        # 4) Flag-Aufnahme (nur wenn Agent auf gegnerischem Flag-Feld)
        if self.A_pos == self.A_flag and not self.A_carry:
            self.A_carry = True
            info["event"].append("A_carry_start")
        if self.B_pos == self.B_flag and not self.B_carry:
            self.B_carry = True
            info["event"].append("B_carry_start")

        # 5) Score, wenn Carrier eigene Base erreicht
        if self.A_carry and self.A_pos == self.A_base:
            self.A_score += 1
            self.A_carry = False
            reward["A"] += self.cfg.score_reward
            info["event"].append("A_score")
            # Respawn beide zur Fairness? Hier nur Carrier resetten:
            self.A_pos = self.A_base
        if self.B_carry and self.B_pos == self.B_base:
            self.B_score += 1
            self.B_carry = False
            reward["B"] += self.cfg.score_reward
            info["event"].append("B_score")
            self.B_pos = self.B_base

        # 6) Terminalbedingungen
        if self.cfg.score_limit and (self.A_score >= self.cfg.score_limit or self.B_score >= self.cfg.score_limit):
            self.done = True
            info["reason"] = "score_limit"
        elif self.steps >= self.cfg.max_steps:
            self.done = True
            info["reason"] = "max_steps"
            # kleine Anti-Stall-Strafe
            reward["A"] += self.cfg.end_penalty
            reward["B"] += self.cfg.end_penalty

        return self._mk_state(), reward, self.done, info

    # --- Serialisierung/Features/Render ---

    def _mk_state(self) -> CTFState:
        return CTFState(
            A_pos=self.A_pos,
            B_pos=self.B_pos,
            A_base=self.A_base,
            B_base=self.B_base,
            A_flag=self.A_flag,
            B_flag=self.B_flag,
            A_carry=self.A_carry,
            B_carry=self.B_carry,
            A_score=self.A_score,
            B_score=self.B_score,
            steps=self.steps,
            width=self.cfg.width,
            height=self.cfg.height,
            done=self.done,
        )

    def get_state(self) -> Dict:
        return asdict(self._mk_state())

    def _norm(self, x: int, y: int) -> Tuple[float, float]:
        return x / (self.cfg.width - 1), y / (self.cfg.height - 1)

    def features(self, agent: str = "A") -> List[float]:
        """
        KI-Features (normiert) aus Sicht von Agent A oder B:
          [ self_x, self_y, opp_x, opp_y, base_x, base_y, flag_x, flag_y,
            carry_self, carry_opp, score_self_norm, score_opp_norm, steps_norm ]
        """
        if agent not in ("A", "B"):
            agent = "A"
        if agent == "A":
            sx, sy = self.A_pos
            ox, oy = self.B_pos
            bx, by = self.A_base
            fx, fy = self.A_flag
            carry_self = 1.0 if self.A_carry else 0.0
            carry_opp  = 1.0 if self.B_carry else 0.0
            s_self, s_opp = self.A_score, self.B_score
        else:
            sx, sy = self.B_pos
            ox, oy = self.A_pos
            bx, by = self.B_base
            fx, fy = self.B_flag
            carry_self = 1.0 if self.B_carry else 0.0
            carry_opp  = 1.0 if self.A_carry else 0.0
            s_self, s_opp = self.B_score, self.A_score

        sx, sy = self._norm(sx, sy)
        ox, oy = self._norm(ox, oy)
        bx, by = self._norm(bx, by)
        fx, fy = self._norm(fx, fy)
        score_self_norm = min(1.0, s_self / max(1, self.cfg.score_limit))
        score_opp_norm  = min(1.0, s_opp / max(1, self.cfg.score_limit))
        steps_norm = min(1.0, self.steps / max(1, self.cfg.max_steps))
        return [
            sx, sy, ox, oy, bx, by, fx, fy,
            carry_self, carry_opp,
            score_self_norm, score_opp_norm, steps_norm
        ]

    def render_ascii(self) -> str:
        W, H = self.cfg.width, self.cfg.height
        grid = [[" " for _ in range(W)] for _ in range(H)]

        def place(p, ch):
            x, y = p
            if 0 <= x < W and 0 <= y < H:
                grid[y][x] = ch

        # Flaggen
        place(self.B_flag, "⚑")  # Flagge auf A-Seite (B-Flag)
        place(self.A_flag, "⚑")  # Flagge auf B-Seite (A-Flag)

        # Basen
        place(self.A_base, "A")
        place(self.B_base, "B")

        # Agenten
        place(self.A_pos, "α" if not self.A_carry else "Α")  # groß = trägt Flag
        place(self.B_pos, "β" if not self.B_carry else "Β")

        # Rahmen + Info
        lines = ["".join(row) for row in grid]
        top = "┌" + "─"*W + "┐"
        bot = "└" + "─"*W + "┘"
        box = [top] + [f"│{ln}│" for ln in lines] + [bot]
        box.append(f"score A={self.A_score} B={self.B_score}  steps={self.steps}/{self.cfg.max_steps}")
        return "\n".join(box)

# ----------------------------
# Baseline-Bots (optional)
# ----------------------------

def greedy_carrier(env: CTFEnv, agent: str = "A") -> int:
    """
    Heuristik:
    - Wenn Flagge nicht getragen wird: laufe zur gegnerischen Flag
    - Wenn Flagge getragen wird: laufe zur eigenen Base
    - Kollisionsvermeidung rudimentär (bleib stehen, wenn Gegner auf Zielkachel)
    """
    sx, sy = env.A_pos if agent == "A" else env.B_pos
    base = env.A_base if agent == "A" else env.B_base
    flag = env.A_flag if agent == "A" else env.B_flag
    carry = env.A_carry if agent == "A" else env.B_carry
    ox, oy = env.B_pos if agent == "A" else env.A_pos

    target = base if carry else flag
    tx, ty = target

    # simple Manhattan-Schritt
    if sx < tx: a = 4
    elif sx > tx: a = 3
    elif sy < ty: a = 2
    elif sy > ty: a = 1
    else: a = 0

    # primitive Vermeidung
    nx, ny = CTFEnv.ACTIONS[a]
    nx, ny = sx + nx, sy + ny
    if (nx, ny) == (ox, oy):
        return 0
    return a

def chaser_defender(env: CTFEnv, agent: str = "A") -> int:
    """
    Heuristik:
    - Wenn Gegner Flagge trägt → verfolge Gegner (Tagging)
    - Sonst patrouilliere zwischen eigener Base und Flag
    """
    sx, sy = env.A_pos if agent == "A" else env.B_pos
    ox, oy = env.B_pos if agent == "A" else env.A_pos
    my_flag = env.B_flag if agent == "A" else env.A_flag  # eigene Seite
    my_base = env.A_base if agent == "A" else env.B_base
    opp_carry = env.B_carry if agent == "A" else env.A_carry

    tx, ty = (ox, oy) if opp_carry else (my_flag if (abs(sx - my_flag[0]) + abs(sy - my_flag[1]) > 1) else my_base)

    if sx < tx: a = 4
    elif sx > tx: a = 3
    elif sy < ty: a = 2
    elif sy > ty: a = 1
    else: a = 0
    return a

# ----------------------------
# CLI-Demo
# ----------------------------

def _demo():
    env = CTFEnv(CTFConfig(seed=42, width=11, height=7, score_limit=3, max_steps=300))
    env.reset()
    print(env.render_ascii())
    while True:
        aA = greedy_carrier(env, "A")
        aB = chaser_defender(env, "B")
        state, R, done, info = env.step({"A": aA, "B": aB})
        if env.steps % 5 == 0 or info.get("event"):
            print(env.render_ascii())
            print("R:", R, "info:", info)
        if done:
            print("DONE:", state["A_score"], state["B_score"], "reason:", info.get("reason"))
            break

if __name__ == "__main__":
    _demo()