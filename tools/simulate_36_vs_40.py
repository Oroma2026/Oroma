#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/simulate_36_vs_40.py
# Projekt: ORÓMA
# Modul:   Simulationsvergleich v3.6 (Kern) vs. v4.x (Awakening-Konzept)
# Version: v1.0
# Stand:   2025-12-07
# Autor:   ORÓMA · KI-JWG-X1 + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# ─────
#   Erzeugt eine **synthetische** 60-Tage-Simulation für zwei Szenarien:
#
#       • "v3.6_core"  – klassischer Lernkern (Snap/Dream/Replay)
#       • "v4x_awake"  – hypothetischer Awakening-Layer (Goal/Meta/Strategy)
#
#   Die Kurven orientieren sich an docs/simulationsvergleich_3.6vs4.0.md:
#
#       • Qualitätsindex 0–1 pro Tag
#       • Zielerfüllungsquote (Task-Success) pro Zeitraum
#
#   Ausgabe:
#       • data/sim_36_vs_40.csv   – tabellarisch (Tag, Qualität, Success-Quote)
#       • data/sim_36_vs_40.json  – für weitere Auswertungen
#       • optional: data/sim_36_vs_40.png (Plot, wenn --plot gesetzt ist)
#
# Wichtige Hinweise
# ─────────────────
#   • Dies ist eine **Modell-Simulation**, KEINE Auswertung echter ORÓMA-Logs.
#   • Die Zahlen sind plausibilisiert, nicht gemessen.
#   • Ziel: Forschungs-/Zielbild + technische Basis, um später reale Metriken
#     (v3.7.x + v4.x) danebenlegen zu können.
#
# Nutzung
# ───────
#   cd /opt/ai/oroma
#   export PYTHONPATH=/opt/ai/oroma
#
#   # Standardlauf (60 Tage, fester Seed):
#   python3 tools/simulate_36_vs_40.py
#
#   # Mit Plot:
#   python3 tools/simulate_36_vs_40.py --plot
#
#   # Andere Tage / Seed:
#   python3 tools/simulate_36_vs_40.py --days 90 --seed 123
#
# Erweiterungsidee
# ────────────────
#   Später können reale Metriken aus core/sql_manager (metrics, coverage_log,
#   gaps, episodes) dazu geladen und im Plot überlagert werden.
# =============================================================================

import argparse
import json
import math
import os
import random
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple

# Optionales Plotting (nur benutzt, wenn --plot gesetzt ist)
try:
    import matplotlib.pyplot as plt  # type: ignore
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False


# =============================================================================
# Datenstrukturen
# =============================================================================


@dataclass
class DayPoint:
    day: int
    quality_core: float
    quality_awake: float
    success_core: float
    success_awake: float


@dataclass
class SimulationConfig:
    days: int = 60
    seed: int = 42
    noise_level_quality: float = 0.01
    noise_level_success: float = 0.01


@dataclass
class SimulationResult:
    config: SimulationConfig
    points: List[DayPoint]

    def to_json(self) -> Dict[str, Any]:
        return {
            "config": asdict(self.config),
            "points": [asdict(p) for p in self.points],
        }


# =============================================================================
# Modellkurven (Baseline aus dem Markdown-Dokument)
# =============================================================================

# Qualitätsindex aus dem MD (Tag, Wert) – v3.6/v4.x:
_BASE_QUALITY_CORE = {
    0: 0.28,
    5: 0.39,
    10: 0.44,
    15: 0.48,
    20: 0.51,
    25: 0.54,
    30: 0.56,
    40: 0.58,
    50: 0.59,
    60: 0.59,
}

_BASE_QUALITY_AWAKE = {
    0: 0.28,
    5: 0.41,
    10: 0.49,
    15: 0.57,
    20: 0.62,
    25: 0.67,
    30: 0.71,
    40: 0.76,
    50: 0.79,
    60: 0.81,
}

# Task-Success aus dem MD (Zeitraum → Prozent)
# Wir mappen das grob auf Tageswerte:
#   • 0–7  Tage   → um 52 % / 68 %
#   • 0–30 Tage   → um 59 % / 78 %
#   • 0–60 Tage   → um 61 % / 84 %
_BASE_SUCCESS_CORE = {
    7: 0.52,
    30: 0.59,
    60: 0.61,
}

_BASE_SUCCESS_AWAKE = {
    7: 0.68,
    30: 0.78,
    60: 0.84,
}


# =============================================================================
# Hilfsfunktionen – Interpolation
# =============================================================================


def _sorted_items(d: Dict[int, float]) -> List[Tuple[int, float]]:
    return sorted(d.items(), key=lambda kv: kv[0])


def interpolate_piecewise_linear(x: int, points: Dict[int, float]) -> float:
    """
    Einfache stückweise lineare Interpolation über einem Dict{Tag: Wert}.

    • für x < min: Wert bei min
    • für x > max: Wert bei max
    • sonst linear zwischen den beiden benachbarten Stützstellen
    """
    items = _sorted_items(points)
    xs = [k for k, _ in items]
    ys = [v for _, v in items]

    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]

    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, y0 = xs[i - 1], ys[i - 1]
            x1, y1 = xs[i], ys[i]
            t = (x - x0) / float(x1 - x0)
            return y0 + t * (y1 - y0)

    # Fallback (sollte nicht auftreten)
    return ys[-1]


# =============================================================================
# Simulation
# =============================================================================


def simulate(config: SimulationConfig) -> SimulationResult:
    random.seed(config.seed)

    points: List[DayPoint] = []

    for day in range(0, config.days + 1):
        # Qualitäten
        q_core = interpolate_piecewise_linear(day, _BASE_QUALITY_CORE)
        q_awake = interpolate_piecewise_linear(day, _BASE_QUALITY_AWAKE)

        # Task-Success grob als "kumulative" Annäherung modellieren
        s_core = interpolate_piecewise_linear(day, _BASE_SUCCESS_CORE)
        s_awake = interpolate_piecewise_linear(day, _BASE_SUCCESS_AWAKE)

        # Rauschen hinzufügen (leichter, symmetrischer Noise)
        q_core_noisy = max(0.0, min(1.0, q_core + random.gauss(0.0, config.noise_level_quality)))
        q_awake_noisy = max(0.0, min(1.0, q_awake + random.gauss(0.0, config.noise_level_quality)))

        s_core_noisy = max(0.0, min(1.0, s_core + random.gauss(0.0, config.noise_level_success)))
        s_awake_noisy = max(0.0, min(1.0, s_awake + random.gauss(0.0, config.noise_level_success)))

        points.append(
            DayPoint(
                day=day,
                quality_core=q_core_noisy,
                quality_awake=q_awake_noisy,
                success_core=s_core_noisy,
                success_awake=s_awake_noisy,
            )
        )

    return SimulationResult(config=config, points=points)


# =============================================================================
# Output-Helfer (CSV/JSON/Plot)
# =============================================================================


def ensure_data_dir() -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    if not os.path.isdir(base):
        os.makedirs(base, exist_ok=True)
    return base


def write_csv(result: SimulationResult, path: str) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [
                "day",
                "quality_core",
                "quality_awake",
                "success_core",
                "success_awake",
            ]
        )
        for p in result.points:
            writer.writerow(
                [
                    p.day,
                    f"{p.quality_core:.4f}",
                    f"{p.quality_awake:.4f}",
                    f"{p.success_core:.4f}",
                    f"{p.success_awake:.4f}",
                ]
            )


def write_json(result: SimulationResult, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_json(), f, ensure_ascii=False, indent=2)


def write_plot(result: SimulationResult, path: str) -> None:
    if not HAVE_MPL:
        print("[simulate_36_vs_40] matplotlib nicht verfügbar – Plot wird übersprungen.")
        return

    days = [p.day for p in result.points]
    q_core = [p.quality_core for p in result.points]
    q_awake = [p.quality_awake for p in result.points]

    # Qualität
    plt.figure()
    plt.plot(days, q_core, label="v3.6_core (Qualität)")
    plt.plot(days, q_awake, label="v4x_awake (Qualität)")
    plt.xlabel("Tag")
    plt.ylabel("Qualitätsindex (0–1)")
    plt.title("Simulationsvergleich – Qualitätsverlauf (synthetisch)")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


# =============================================================================
# CLI
# =============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthetischer Simulationsvergleich ORÓMA v3.6 vs v4.x (Awakening-Konzept)."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="Anzahl der simulierten Tage (Default: 60).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random-Seed für reproduzierbare Kurven (Default: 42).",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Erzeuge zusätzlich einen PNG-Plot in data/sim_36_vs_40.png.",
    )
    args = parser.parse_args()

    cfg = SimulationConfig(days=args.days, seed=args.seed)
    result = simulate(cfg)

    data_dir = ensure_data_dir()
    csv_path = os.path.join(data_dir, "sim_36_vs_40.csv")
    json_path = os.path.join(data_dir, "sim_36_vs_40.json")
    png_path = os.path.join(data_dir, "sim_36_vs_40.png")

    write_csv(result, csv_path)
    write_json(result, json_path)

    print(f"[simulate_36_vs_40] CSV geschrieben:   {csv_path}")
    print(f"[simulate_36_vs_40] JSON geschrieben:  {json_path}")

    if args.plot:
        write_plot(result, png_path)
        print(f"[simulate_36_vs_40] Plot geschrieben:  {png_path}")


if __name__ == "__main__":
    main()