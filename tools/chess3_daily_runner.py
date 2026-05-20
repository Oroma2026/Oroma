#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess3_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess3 Daily Runner – eigenständige Chess3-Linie auf Chess2-Basis
# Version: v3.8-r2
# Stand:   2026-03-21
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
# Zweck
# -----
# Dieser Runner führt die neue Chess3-Linie ein, ohne die bewährte Chess2-
# Implementierung zu duplizieren oder die bestehende Chess2-Referenzlinie zu
# verändern. Die fachliche Architektur ist bewusst konservativ und produktiv:
#
# - Chess2 bleibt die historische Referenz und unveränderte Lernspur.
# - Chess3 nutzt dieselbe ORÓMA-Infrastruktur (SQLite, Policy, Episoden,
#   SnapChains, Daily-Runner-Mechanik), aber eine eigene Namespace.
# - Die initiale Wissensbasis von Chess3 wird über den separaten Bootstrap-
#   Schritt aus Chess2 übernommen.
# - Dieser Wrapper sorgt anschließend dafür, dass neue Daily-Runs standardmäßig
#   gegen die Chess3-Namespace laufen, ohne dass bestehende Chess2-Skripte oder
#   Umgebungsvariablen umgebaut werden müssen.
#
# Warum ein Wrapper statt einer Vollkopie?
# ---------------------------------------
# Die aktuelle Chess2-Runner-Datei ist groß, produktiv erprobt und enthält viele
# Headless-/DB-/Logging-/Heuristik-Details. Eine harte Dateiduplikation würde
# sofort das Risiko schaffen, dass Chess2 und Chess3 unbeabsichtigt auseinander-
# laufen oder Bugfixes nur noch in einer Linie landen. Der produktiv saubere Weg
# ist deshalb:
#
# - gemeinsame Kernlogik weiter in `tools/chess2_daily_runner.py`
# - neue fachliche Generation Chess3 als eigener Einstiegspunkt
# - explizite Default-Namespace für Chess3
# - weiterhin volle CLI-Kompatibilität mit allen bestehenden Schaltern
#
# Betriebsmodell
# --------------
# Dieser Wrapper setzt nur dann eine Chess3-Default-Namespace, wenn der Aufruf
# nicht bereits explizit `--namespace ...` enthält. Das ist wichtig, damit:
#
# - man Chess3 produktiv ohne Zusatzparameter starten kann,
# - aber Sondertests mit alternativen Chess3-Namespaces weiterhin möglich sind.
#
# Standard-Ziel-Namespace:
#   game:chess3
#
# Für die konkrete Canon/Coop/King/Territory-Linie existiert zusätzlich der
# spezialisierte Wrapper:
#   tools/chess3_canon_coop_king_territory_daily_runner.py
#
# Hinweise zur DB-/Lernspur-Trennung
# ----------------------------------
# Dieses Modul verwendet dieselben physischen DB-Dateien wie Chess2, aber nicht
# dieselbe fachliche Policy-Namespace. Dadurch bleiben folgende Eigenschaften
# erhalten:
#
# - kein Kaltstart für Chess3 nötig, wenn die Bootstrap-Namespace bereits befüllt
#   wurde,
# - keine Vermischung von Chess2- und Chess3-Regeln,
# - saubere Vergleichbarkeit Chess2 vs Chess3,
# - minimales Wartungsrisiko, da die bewährte Kernlogik direkt weiterverwendet
#   wird.
#
# Nutzung
# -------
# Direktstart:
#   python3 tools/chess3_daily_runner.py
#
# Mit eigenen Parametern:
#   python3 tools/chess3_daily_runner.py --policy-games 100 --explore-games 100
#
# Mit expliziter alternativer Namespace:
#   python3 tools/chess3_daily_runner.py --namespace game:chess3_test
# =============================================================================

from __future__ import annotations

import pathlib
import sys
from typing import List

if __package__ in {None, ""}:
    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT_STR)

from tools.chess2_daily_runner import main as chess2_main

DEFAULT_CHESS3_NAMESPACE = "game:chess3"


def _argv_with_default_namespace(argv: List[str]) -> List[str]:
    """Ergänzt genau dann eine Chess3-Default-Namespace, wenn der Benutzer
    keine explizite `--namespace` übergeben hat.

    Der Wrapper soll produktiv deterministisch sein und keine vorhandenen
    Override-Parameter überschreiben. Deshalb wird nur am Anfang der Argumente
    `--namespace game:chess3` vorangestellt, wenn `--namespace` in `argv` nicht
    vorkommt.
    """
    args = list(argv)
    if "--namespace" in args:
        return args
    return ["--namespace", DEFAULT_CHESS3_NAMESPACE, *args]


def main(argv: List[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return int(chess2_main(_argv_with_default_namespace(args)))


if __name__ == "__main__":
    raise SystemExit(main())
