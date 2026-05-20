#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/pong_arena.py
# Projekt:   ORÓMA (Offline-First · Headless · Mini-Games Learning)
# Modul:     Pong Arena – headless Pong-Simulation (Autoplay) + Reward/Snap/SnapChain Logging
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert eine **headless Pong-Arena** als kleine, autonome
# Simulationsumgebung (Ball + 2 Paddles mit einfacher KI).
#
# Hauptziel in ORÓMA:
# - kontrollierte, wiederholbare Interaktionen erzeugen
# - bei relevanten Events (Score) Telemetrie schreiben:
#     1) Reward-Log (core.reward.RewardLogger)
#     2) Snap (core.snap.Snap) in SQLite
#     3) SnapChain (core.snapchain.SnapChain) in SQLite
#
# Die Arena läuft in einem daemon-Thread (start/stop), kann aber auch nur „state()“
# liefern (UI/REST Status-Anzeige).
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: kein pygame/GUI erforderlich.
# - Ressourcenarm: kleine Physics, fester dt, geringer CPU-Footprint.
# - Thread-sicher: zentrale Lock-Section für State-Mutationen.
# - Stabilität vor Perfektion: Logging-Fehler werden **gefangen** und nur auf stdout gemeldet,
#   damit die Simulation weiterläuft.
#
# ABHÄNGIGKEITEN (EXAKT)
# ──────────────────────
# - core.reward:
#     • RewardLogger().log(source, step, reward, raw, tag)
# - core.snap:
#     • Snap(kind="event", label="pong:<desc>", data={...}).save(conn)
# - core.snapchain:
#     • SnapChain(snaps=[snap]) + Feature-Felder:
#         chain.f_game = "pong"
#         chain.f_act  = <desc>
#         chain.f_st   = {"scoreL":..,"scoreR":..}
#         chain.f_ex   = {"bx":..,"by":..}
# - core.sql_manager:
#     • get_conn()
#     • Insert-Funktion für SnapChains (dynamisch aufgelöst; siehe _resolve_chain_insert)
#
# DYNAMISCHE INSERT-AUFLÖSUNG (LEGACY-KOMPATIBILITÄT)
# ───────────────────────────────────────────────────
# Dieses Modul muss mit verschiedenen sql_manager-Versionen kompatibel bleiben.
# Dafür existiert:
#   _resolve_chain_insert(sql_manager) -> Callable[[SnapChain], None]
#
# Auflösungs-Reihenfolge (präferiert):
#   1) sql_manager.insert_snapchain
#   2) sql_manager.insert_chain
#   3) sql_manager.insert_chain_quick
#
# Wenn keine dieser Funktionen vorhanden ist:
#   → RuntimeError (beim Initialisieren), da SnapChain-Persistenz dann unmöglich wäre.
#
# KONFIGURATION (ENV – EXAKT IM CODE)
# ───────────────────────────────────
# OROMA_PONG_DT      (default: "0.05")
#   - Schlafzeit pro Loop-Iteration in Sekunden (Simulationstakt)
#
# OROMA_PONG_REWARD  (default: "1.0")
#   - Reward-Wert, der beim Score-Event geschrieben wird
#
# KERNKLASSE: PongArena
# ─────────────────────
# PongArena(w=320, h=200)
# - Attribute (vereinfacht):
#     • w, h            : Spielfeldgröße
#     • bx, by          : Ballposition
#     • bvx, bvy        : Ballgeschwindigkeit
#     • lp, rp          : Paddle-Top Position (links/rechts)
#     • scoreL, scoreR  : Scores
#     • running         : läuft die Simulation?
#     • _dt             : aus ENV OROMA_PONG_DT
#     • _reward_on_score: aus ENV OROMA_PONG_REWARD
#     • lock            : threading.Lock
#     • thread          : daemon Thread (run_loop)
#     • logger          : RewardLogger
#     • _insert_chain   : aufgelöste Insert-Funktion für SnapChains
#
# ÖFFENTLICHE METHODEN (EXAKT IM CODE)
# ────────────────────────────────────
# __init__(w: int=320, h: int=200)
#   - initialisiert Logger/Insert-Resolver, setzt dt/reward aus ENV und ruft reset()
#
# reset() -> None
#   - setzt Ball, Paddle-Positionen und Scores zurück
#
# start() -> None
#   - startet run_loop() in einem daemon Thread, wenn nicht bereits running
#
# stop() -> None
#   - setzt running=False (Thread beendet sich selbst)
#
# run_loop() -> None
#   - while running:
#       step()
#       sleep(_dt)
#
# step() -> None
#   - eine Simulationstaktung:
#       • Ballbewegung + Bounce oben/unten
#       • einfache KI: beide Paddles folgen dem Ball (lp/rp adjust um 2px)
#       • Kollision Paddle/Score:
#           - bei Score wird _log_reward("pong", +reward, "<side> scores") ausgelöst
#           - Ball wird via _reset_ball() neu gesetzt
#
# state() -> dict
#   - UI-freundlicher Zustand (unter Lock), Keys:
#       {"w","h","bx","by","lp","rp","scoreL","scoreR","running","step"}
#
# _log_reward(source: str, val: float, desc: str) -> None
#   - schreibt 3 Dinge (best effort, exceptions werden gefangen):
#       1) RewardLogger.log(..., tag="arena", raw={"desc": desc})
#       2) Snap(kind="event", label=f"pong:{desc}", data={score/bx/by}) + save/commit
#       3) SnapChain(snaps=[snap]) mit f_game/f_act/f_st/f_ex + Insert via _insert_chain
#
# PERSISTENZ / FEHLERSTRATEGIE
# ────────────────────────────
# - Snap Save/Commit erfolgt über core.sql_manager.get_conn() + s.save(conn) + conn.commit().
# - SnapChain Insert erfolgt über die dynamisch aufgelöste Insert-Funktion.
# - Jede Exception in _log_reward wird gefangen:
#     print("[pong_arena] Fehler beim Reward/Snap/SnapChain: ...")
#   Damit bleibt die Arena stabil auch bei temporären DB-Locks oder Schema-Divergenzen.
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - Headless bleiben (keine pygame Pflicht einführen).
# - state() Key-Struktur stabil halten (UI/REST).
# - _resolve_chain_insert Reihenfolge beibehalten (Legacy-Kompatibilität).
# - _log_reward muss **niemals** die Simulation zum Absturz bringen (Exception-Catcher behalten).
# - ENV Defaults (dt=0.05, reward=1.0) stabil halten, um reproduzierbare Tests zu ermöglichen.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import threading
import time
import random
from typing import Optional, Callable

# ORÓMA Core
from core import snap, reward, sql_manager
from core.snapchain import SnapChain


def _resolve_chain_insert(sql_mod) -> Callable[[SnapChain], None]:
    """
    Ermittelt zur Laufzeit die passende Insert-Funktion für SnapChains.
    Präferenz: insert_snapchain → insert_chain → insert_chain_quick.
    Hebt eine RuntimeError, falls keine passende Funktion existiert.
    """
    for name in ("insert_snapchain", "insert_chain", "insert_chain_quick"):
        fn = getattr(sql_mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        "core.sql_manager enthält keine Insert-Funktion für SnapChains "
        "(erwartet: insert_snapchain / insert_chain / insert_chain_quick)."
    )


class PongArena:
    """
    Headless Pong-Umgebung für ORÓMA.
    Nutzt einen Hintergrund-Thread für die Simulationsschleife und loggt
    relevante Ereignisse (Scores) als Reward + Snap + SnapChain.
    """

    def __init__(self, w: int = 320, h: int = 200):
        self.w, self.h = w, h
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None

        # interne Simulationswerte
        self._step = 0
        self._dt = float(os.environ.get("OROMA_PONG_DT", "0.05"))
        self._reward_on_score = float(os.environ.get("OROMA_PONG_REWARD", "1.0"))

        # Logger & Insert-Resolver
        self.logger = reward.RewardLogger()
        self._insert_chain = _resolve_chain_insert(sql_manager)

        self.reset()

    # --------------------------------------------------------------------- #
    # Grund-Loop
    # --------------------------------------------------------------------- #
    def reset(self) -> None:
        """Setzt Spielzustand (Ball, Paddles, Score) zurück."""
        with self.lock:
            self.bx, self.by = self.w // 2, self.h // 2
            self.bvx = random.choice([-3, 3])
            self.bvy = random.choice([-2, 2])
            self.lp = self.h // 2  # left paddle top
            self.rp = self.h // 2  # right paddle top
            self.scoreL, self.scoreR = 0, 0

    def step(self) -> None:
        """Eine Simulationsiteration: Ballphysik, Paddle-KI, Scores, Rewards."""
        with self.lock:
            if not self.running:
                return

            self._step += 1
            self.bx += self.bvx
            self.by += self.bvy

            # Ball prallt oben/unten ab
            if self.by <= 0 or self.by >= self.h:
                self.bvy *= -1

            # Linkes Paddle Kollision / Score
            if self.bx <= 10 and self.lp <= self.by <= self.lp + 36:
                self.bvx *= -1
            elif self.bx < 0:
                self.scoreR += 1
                self._log_reward("pong", +self._reward_on_score, "right scores")
                self._reset_ball()

            # Rechtes Paddle Kollision / Score
            if self.bx >= self.w - 10 and self.rp <= self.by <= self.rp + 36:
                self.bvx *= -1
            elif self.bx > self.w:
                self.scoreL += 1
                self._log_reward("pong", +self._reward_on_score, "left scores")
                self._reset_ball()

            # Simple KI: Paddles folgen dem Ball
            if self.by < self.lp:
                self.lp = max(0, self.lp - 2)
            elif self.by > self.lp + 36:
                self.lp = min(self.h - 36, self.lp + 2)

            if self.by < self.rp:
                self.rp = max(0, self.rp - 2)
            elif self.by > self.rp + 36:
                self.rp = min(self.h - 36, self.rp + 2)

    def _reset_ball(self) -> None:
        """Ballposition nach Score neu setzen; Paddles/Score bleiben bestehen."""
        self.bx, self.by = self.w // 2, self.h // 2
        self.bvx = random.choice([-3, 3])
        self.bvy = random.choice([-2, 2])

    def run_loop(self) -> None:
        """Hintergrundschleife; beendet sich, wenn self.running False wird."""
        while self.running:
            self.step()
            time.sleep(self._dt)

    def start(self) -> None:
        """Startet die Simulationsschleife in einem daemon-Thread."""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self.run_loop, daemon=True)
            self.thread.start()

    def stop(self) -> None:
        """Stoppt die Simulationsschleife."""
        self.running = False

    # --------------------------------------------------------------------- #
    # Zustand / Telemetrie
    # --------------------------------------------------------------------- #
    def state(self) -> dict:
        """Gibt den aktuellen Zustand als dict zurück (UI-/API-freundlich)."""
        with self.lock:
            return {
                "w": self.w,
                "h": self.h,
                "bx": self.bx,
                "by": self.by,
                "lp": self.lp,
                "rp": self.rp,
                "scoreL": self.scoreL,
                "scoreR": self.scoreR,
                "running": self.running,
                "step": self._step,
            }

    # --------------------------------------------------------------------- #
    # Logging: Reward + Snap + SnapChain
    # --------------------------------------------------------------------- #
    def _log_reward(self, source: str, val: float, desc: str) -> None:
        """
        Persistiert ein Ereignis als Reward (metrics), Snap (events) und SnapChain.
        Fehler werden gefangen und auf stdout gemeldet, damit die Arena stabil läuft.
        """
        try:
            # 1) Reward in DB schreiben
            self.logger.log(
                source=source,
                step=int(self._step),
                reward=float(val),
                raw={"desc": desc},
                tag="arena",
            )

            # 2) Snap erzeugen + speichern
            s = snap.Snap(
                kind="event",
                label=f"pong:{desc}",
                data={
                    "scoreL": self.scoreL,
                    "scoreR": self.scoreR,
                    "bx": self.bx,
                    "by": self.by,
                },
            )
            conn = sql_manager.get_conn()
            s.save(conn)
            conn.commit()

            # 3) SnapChain erzeugen + persistieren
            chain = SnapChain(snaps=[s])
            chain.f_game = "pong"
            chain.f_act = desc
            chain.f_st = {"scoreL": self.scoreL, "scoreR": self.scoreR}
            chain.f_ex = {"bx": self.bx, "by": self.by}

            # bevorzugt insert_snapchain, sonst Fallbacks (Legacy)
            self._insert_chain(chain)

        except Exception as e:
            print(f"[pong_arena] Fehler beim Reward/Snap/SnapChain: {e}")