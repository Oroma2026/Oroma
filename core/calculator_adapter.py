#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/adapters/calculator_adapter.py
# Projekt: ORÓMA – Adapter für Curriculum-Taschenrechner
# Version: v3.8-r1
# Stand:   2025-10-22
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#  Vereinheitlichte Schnittstelle (wie TTT) für den Calculator, damit Agent/Loops
#  und Routinen stabil bleiben – unabhängig von internen Moduldetails.
#
#  Öffentliche API:
#    • create_task(level:int=1, *, mode="random"| "curriculum", index:int=0) -> int
#    • solve_task(task_id:int|None=None, *, got:float=0.0, truth:float=0.0)     -> int
#
#  Zusätzlich (BaseAdapter-Contract, minimal):
#    • observe(...)       – nicht genutzt (Calculator ist kein Sensor-Stream)
#    • encode_state(...)  – generiert kleinen State-Vektor (optional für Policies)
#
# Steuerung / Logging
# ───────────────────
#  • OROMA_CALC_LOG=0|1               – Adapter-Logs aus/an (Default: 0)
#  • OROMA_CALC_MODE=random|curriculum – Defaultmodus für create_task()
#
# Abhängigkeiten
# ──────────────
#  • core.adapters.base_adapter.BaseAdapter   (Fallback: core.base_adapter.BaseAdapter)
#  • mini_programs.calculator.Calculator      (new_task_random/new_task_curriculum/solve_task)
#
# Sicherheit
# ──────────
#  • Adapter wirft KEINE Exceptions nach außen (−1 bei Fehlern)
#  • Headless-tauglich, keine GUI-Deps
# =============================================================================

from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple, Optional

# --- BaseAdapter Import robust machen (beide Pfade unterstützen) -------------
try:
    from core.adapters.base_adapter import BaseAdapter  # bevorzugter Pfad lt. Header
except Exception:
    from core.base_adapter import BaseAdapter  # Fallback (so liegt's in der ZIP)

from mini_programs import calculator as _calc

_CALC_LOG = os.getenv("OROMA_CALC_LOG", "0").strip().lower() not in ("0", "false", "no", "off")
_DEFAULT_MODE = os.getenv("OROMA_CALC_MODE", "random").strip().lower()

def _alog(msg: str) -> None:
    if _CALC_LOG:
        print(f"[CalculatorAdapter] {msg}", flush=True)

class CalculatorAdapter(BaseAdapter):
    """
    Adapter für Calculator – einheitliches API analog zu anderen Mini-Programmen.
    """
    name: str = "calculator"
    state_dim: int = 4   # kleiner Vektor: [level, last_ok, reward, bias]

    def __init__(self) -> None:
        super().__init__()
        self._last_task_id: Optional[int] = None
        self._last_ok: float = 0.0
        self._last_reward: float = 0.0
        self._level: int = 1

    # ------------------------ vereinheitlichte API --------------------------- #
    def create_task(self, level: int = 1, *, mode: Optional[str] = None, index: int = 0) -> int:
        m = (mode or _DEFAULT_MODE).lower()
        self._level = int(level)
        try:
            if m == "curriculum":
                tid = int(_calc.Calculator.new_task_curriculum(level=level, index=index))
            else:
                tid = int(_calc.Calculator.new_task_random(level=level))
            self._last_task_id = tid
            _alog(f"create_task(level={level}, mode={m}, index={index}) → {tid}")
            return tid
        except Exception as e:
            _alog(f"create_task Fehler: {e}")
            return -1

    def solve_task(self, task_id: Optional[int] = None, *, got: float = 0.0, truth: float = 0.0) -> int:
        try:
            tid = int(task_id if task_id is not None else (self._last_task_id or -1))
            if tid < 0:
                _alog("solve_task: keine gültige task_id")
                return -1
            rid = int(_calc.Calculator.solve_task(task_id=tid, got=got, truth=truth))
            # Reward-Heuristik wie im Calculator: 1.0 korrekt, -0.5 falsch
            ok = 1.0 if abs(got - truth) < 1e-6 else 0.0
            self._last_ok = ok
            self._last_reward = 1.0 if ok == 1.0 else -0.5
            _alog(f"solve_task(task_id={tid}, got={got}, truth={truth}) → {rid}")
            return rid
        except Exception as e:
            _alog(f"solve_task Fehler: {e}")
            return -1

    # -------------------------- BaseAdapter-Contract ------------------------- #
    def observe(self, raw: Any) -> Dict[str, Any]:
        # Calculator braucht keine Rohdaten; wir liefern minimalen State zurück.
        return {
            "level": self._level,
            "last_ok": self._last_ok,
            "last_reward": self._last_reward,
        }

    def encode_state(self, obs: Dict[str, Any]) -> Tuple[List[float], List[str], str]:
        # sehr kompakter Zustandsvektor + Tokens
        v = [
            float(obs.get("level", 1)),
            float(obs.get("last_ok", 0.0)),
            float(obs.get("last_reward", 0.0)),
            1.0,  # Bias-Term
        ]
        tokens = [f"lvl:{int(v[0])}", f"ok:{int(v[1])}", f"R:{v[2]:.1f}"]
        desc = f"calc lvl={int(v[0])} ok={int(v[1])} R={v[2]:.1f}"
        return v, tokens, desc