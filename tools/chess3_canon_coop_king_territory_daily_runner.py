#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/chess3_canon_coop_king_territory_daily_runner.py
# Projekt: ORÓMA (Offline-First · Headless · SQLite-First)
# Modul:   Chess3 Canon+Cooperation+King+Territory Daily Runner
# Version: v3.8-r2
# Stand:   2026-03-22
# Autor:   Jörg + GPT-5.4 Thinking
# Lizenz:  MIT
# =============================================================================
# Zweck
# -----
# Dieser Runner ist der produktive Einstiegspunkt für die neue Chess3-Linie in
# genau der bislang stärksten Repräsentationskombination:
#
# - canonical
# - cooperation
# - king weighting
# - territory weighting
#
# Fachliche Rolle
# ---------------
# Chess2 bleibt als Referenzlinie bestehen. Chess3 läuft dagegen auf einer
# eigenen Namespace und kann später selektiven Lookahead, King-Pressure-Logik,
# Endspiel-Korrekturen und zusätzliche Telemetrie erhalten, ohne Chess2 zu
# verfälschen. Für die produktive Einführungsphase soll Chess3 jedoch zunächst
# NICHT schwächer oder anders konfiguriert starten als der zuletzt erfolgreich
# getestete Chess2-Stand.
#
# Genau deshalb setzt dieser Wrapper nicht nur die passende Namespace und die
# fachlichen Modus-Flags, sondern ergänzt – falls der Benutzer sie nicht selbst
# explizit übergibt – auch dieselben bewährten Heuristik-/Safety-/Spezialzug-
# Defaults, mit denen die starke Chess2-Serie gelaufen ist. Dadurch gilt:
#
# - Chess3 startet auf neuer Lernspur,
# - aber mit identischem produktiven Tuning-Profil wie Chess2,
# - sodass spätere Verbesserungen (Lookahead, King Pressure, Endgame-Korrektur)
#   sauber als Delta gegenüber diesem stabilen Referenzniveau messbar bleiben.
#
# Standard-Namespace:
#   game:chess3_canon_coop_king_territory_v1
#
# Produktive Default-Parameter dieser Phase
# -----------------------------------------
# Die nachfolgenden Werte werden nur dann eingefügt, wenn der Anwender die
# jeweilige CLI-Option nicht bereits selbst übergeben hat:
#
# - --draw-stress 0.02
# - --draw-stress-threshold-n 8
# - --draw-stress-q-band 0.05
# - --draw-stress-max-extra-per-key 2
# - --win-weight 2.5
# - --capture-bias 0.12
# - --king-shuffle-penalty 0.10
# - --piece-variety-bias 0.04
# - --hanging-piece-bias 0.18
# - --underdefended-piece-bias 0.08
# - --opening-seed-book 1
# - --opening-seed-policy-only 1
# - --self-hanging-penalty 0.24
# - --retaliation-penalty 0.18
# - --defended-attack-bonus 0.06
# - --discovery-exposure-penalty 0.12
# - --castle-bias 0.22
# - --promotion-bias 0.55
# - --en-passant-bias 0.10
# - --check-bias 0.06
#
# Wichtig: Benutzer-Overrides haben immer Vorrang. Der Wrapper arbeitet also
# produktiv konservativ und überschreibt keine bewusst gesetzten Testwerte.
#
# Nutzung:
#   python3 tools/chess3_canon_coop_king_territory_daily_runner.py
#   python3 tools/chess3_canon_coop_king_territory_daily_runner.py --policy-games 100 --explore-games 100
#   python3 tools/chess3_canon_coop_king_territory_daily_runner.py --win-weight 3.0 --check-bias 0.04
# =============================================================================
from __future__ import annotations

import pathlib
import sys
from typing import Iterable, List, Sequence, Tuple

if __package__ in {None, ""}:
    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
    _PROJECT_ROOT_STR = str(_PROJECT_ROOT)
    if _PROJECT_ROOT_STR not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT_STR)

from tools.chess3_daily_runner import main

DEFAULT_NAMESPACE = 'game:chess3_canon_coop_king_territory_v1'

# Reihenfolge bewusst stabil gehalten, damit im Ergebnis-JSON klar nachvollziehbar
# bleibt, mit welchem produktiven Profil Chess3 gestartet wurde.
DEFAULT_KV_ARGS: Sequence[Tuple[str, str]] = (
    ('--namespace', DEFAULT_NAMESPACE),
    ('--canonical', '1'),
    ('--cooperation', '1'),
    ('--king', '1'),
    ('--territory', '1'),
    ('--enable-flip-pass', '0'),
    ('--draw-stress', '0.02'),
    ('--draw-stress-threshold-n', '8'),
    ('--draw-stress-q-band', '0.05'),
    ('--draw-stress-max-extra-per-key', '2'),
    ('--win-weight', '2.5'),
    ('--capture-bias', '0.12'),
    ('--king-shuffle-penalty', '0.10'),
    ('--piece-variety-bias', '0.04'),
    ('--hanging-piece-bias', '0.18'),
    ('--underdefended-piece-bias', '0.08'),
    ('--opening-seed-book', '1'),
    ('--opening-seed-policy-only', '1'),
    ('--self-hanging-penalty', '0.24'),
    ('--retaliation-penalty', '0.18'),
    ('--defended-attack-bonus', '0.06'),
    ('--discovery-exposure-penalty', '0.12'),
    ('--castle-bias', '0.22'),
    ('--promotion-bias', '0.55'),
    ('--en-passant-bias', '0.10'),
    ('--check-bias', '0.06'),
)


def _flags_present(argv: Iterable[str]) -> set[str]:
    """Liefert alle long-option Namen aus dem übergebenen argv.

    Die Funktion ist bewusst simpel und robust gehalten, da unsere Runner-Aufrufe
    ausschließlich mit long options arbeiten. Dadurch können wir Default-Werte
    nur dort ergänzen, wo der Benutzer den jeweiligen Schalter nicht selbst
    explizit gesetzt hat.
    """
    present: set[str] = set()
    for token in argv:
        if token.startswith('--'):
            present.add(token)
    return present



def _merge_default_args(user_argv: Sequence[str]) -> List[str]:
    """Ergänzt produktive Chess3-Defaults ohne Benutzer-Overrides zu brechen."""
    args = list(user_argv)
    present = _flags_present(args)
    merged: List[str] = []
    for flag, value in DEFAULT_KV_ARGS:
        if flag not in present:
            merged.extend([flag, value])
    merged.extend(args)
    return merged


if __name__ == '__main__':
    raise SystemExit(main(_merge_default_args(sys.argv[1:])))
