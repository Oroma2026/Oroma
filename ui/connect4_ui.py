#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/ui/connect4_ui.py
# Projekt: ORÓMA v3.7.x – Connect4 Arena (UI + UniversalPolicy)
# Version: v3.7.3
# Stand:   2026-02-19
# Autor:   Jörg + GPT-5.2 Thinking
# Lizenz:  MIT
# =============================================================================
#
# ZWECK
# -----
# Connect4 (Vier Gewinnt) als ORÓMA-Referenzspiel für:
#   • UI-Standardisierung (analog TicTacToe UI)
#   • UniversalPolicy (policy_rules) – state→action Lernen
#   • Headless-freundliche Runtime (kein X/Qt/Wayland)
#   • Optionales SnapChain-Logging (Dream-kompatibel)
#
# WARUM DIESER PATCH
# ------------------
# In ORÓMA v3.7.x dient TicTacToe als "Goldstandard":
#   • getrennte Modi: oroma_vs_oroma_explore / oroma_vs_oroma_policy
#   • UI zeigt Rule-Counter + Status + Speed
#   • Daily Runner schreibt Episoden/Metriken nach oroma.db
#
# Diese Datei hebt Connect4 auf denselben Standard:
#   • Modus-Split wie TicTacToe
#   • UniversalPolicyShim mit side-aware, spiegel-symmetrischer Canonicalisierung
#   • UI-State enthält echte Rule-Counter (policy_rules / rules active)
#   • UI zeigt den letzten Daily-Explore/Policy-Status direkt aus episodes
#   • Bestehende SnapChain-Exportlogik bleibt erhalten (nicht destruktiv)
#
# KONFIGURATION (ENV)
# -------------------
# OROMA_SNAPCHAINS                 Pfad für SnapChain-JSON Exporte
# OROMA_C4_POLICY_NAMESPACE         Default: game:connect4
# OROMA_C4_EPS                      Explore-Epsilon (Default: 0.08)
# OROMA_C4_EXPLORE_MOVES_PER_GAME   Mindest-Random-Moves pro Game (Default: 1)
#
# ROUTES
# ------
# GET  /connect4/                   UI-Seite
# GET  /connect4/api/state          JSON-State (inkl. Policy-Counter)
# POST /connect4/api/drop           Stein fallen lassen (UI click)
# POST /connect4/api/move           Alias für /api/drop (Standardisierung)
# POST /connect4/api/reset          Reset
# POST /connect4/api/toggle         Auto-Loop Start/Pause
# POST /connect4/api/set_mode       Mode setzen
# POST /connect4/api/set_speed      Speed-Profil setzen
#
# HINWEIS ZU ORCHESTRATOR
# -----------------------
# Die Daily-Batches (100 policy + 100 explore) laufen NICHT über diese UI,
# sondern über tools/connect4_daily_runner.py (siehe Orchestrator-Job).
# =============================================================================

import os, random, threading, time, pwd
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from flask import Blueprint, jsonify, render_template, request, current_app
import logging
from core.log_guard import log_suppressed

try:
    from core import universal_policy as upol
    _HAVE_UP = True
except Exception:
    upol = None  # type: ignore
    _HAVE_UP = False

try:
    from core import sql_manager
except Exception:
    sql_manager = None  # type: ignore

# -----------------------------------------------------------------------------
# Logging Setup (systemd-freundlich, mit robustem Fallback)
# -----------------------------------------------------------------------------
LOG_PATH = Path("/opt/ai/oroma/logs/ui_core_import.log")
try:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
except Exception as e:
    # Falls das wegen systemd-Hardening nicht klappt, loggen wir eben nur in journald.
    log_suppressed('ui/connect4_ui.py:42', exc=e, level=logging.WARNING)
    pass

LOG = logging.getLogger("oroma.connect4")
if not LOG.handlers:
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [Connect4] %(message)s")

    # 1) Konsole / journald
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOG.addHandler(sh)

    # 2) Datei (optional; PermissionErrors tolerieren)
    try:
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        LOG.addHandler(fh)
    except Exception as e:
        print(f"[connect4_ui] ⚠️ FileHandler-Fehler: {e}")

# -----------------------------------------------------------------------------
# Core-Import (robust, systemd-sicher)
# -----------------------------------------------------------------------------
import sys
# Systemd startet oft mit WorkingDirectory=/opt/ai/oroma, aber sicher ist sicher:
if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

try:
    from core.snap import Snap
    from core.snapchain import SnapChain, save_chain
    LOG.info(
        "✅ SnapChain-Core importiert (Snap=%s, SnapChain=%s, save_chain=%s)",
        bool(Snap), bool(SnapChain), callable(save_chain) if 'save_chain' in globals() else False
    )
except Exception as e:
    Snap = None
    SnapChain = None
    save_chain = None
    LOG.exception("⚠️ Core-Importfehler: %s", e)

# -----------------------------------------------------------------------------
# Export-Pfad (ENV-kompatibel wie im oroma.service) + Rechte-Log
# -----------------------------------------------------------------------------
EXPORT_DIR = os.environ.get("OROMA_SNAPCHAINS", "/opt/ai/oroma/data/snapchains")
try:
    os.makedirs(EXPORT_DIR, exist_ok=True)
except Exception:
    LOG.exception("❌ Konnte Exportverzeichnis nicht erstellen: %s", EXPORT_DIR)

try:
    uid, gid = os.getuid(), os.getgid()
    user = pwd.getpwuid(uid).pw_name
    LOG.info("👤 Laufzeit: user=%s (uid=%d, gid=%d), cwd=%s", user, uid, gid, os.getcwd())
except Exception:
    LOG.debug("Hinweis: pwd/getuid ggf. nicht verfügbar (Plattform).")

LOG.info("📂 Exportverzeichnis: %s (beschreibbar=%s)", EXPORT_DIR, os.access(EXPORT_DIR, os.W_OK))

# -----------------------------------------------------------------------------
# Flask Blueprint
# -----------------------------------------------------------------------------
connect4_bp = Blueprint("connect4_ui", __name__, url_prefix="/connect4", template_folder="templates")

# Spielfeldgröße
ROWS, COLS = 6, 7


def _get_db_path() -> str:
    base = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
    return os.path.join(base, "data", "oroma.db")


def _query_scalar_fallback(sql: str, params: tuple) -> int:
    try:
        import sqlite3
        conn = sqlite3.connect(_get_db_path())
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            return int(row[0] if row and row[0] is not None else 0)
        finally:
            conn.close()
    except Exception:
        return 0


def _load_connect4_daily_status() -> Dict[str, Any]:
    """Liest die letzten Connect4-Daily-Batches aus episodes/episodic_metrics.

    Zeigt in der UI direkt, ob der Nightly-Pfad Daten schreibt und wie die
    letzten Policy-/Explore-Batches ausgegangen sind.
    """
    out: Dict[str, Any] = {"latest_policy": None, "latest_explore": None}
    if not sql_manager:
        return out
    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT e.id, e.kind, e.ts_end, e.label, m.key, m.value
                FROM episodes e
                LEFT JOIN episodic_metrics m ON m.episode_id = e.id
                WHERE e.kind IN ('game:connect4:policy_batch','game:connect4:explore_batch')
                ORDER BY e.id DESC, m.key
                LIMIT 400
                """
            )
            rows = cur.fetchall() or []
        grouped: Dict[int, Dict[str, Any]] = {}
        for eid, kind, ts_end, label, key, value in rows:
            bucket = grouped.setdefault(int(eid), {"id": int(eid), "kind": str(kind or ""), "ts_end": int(ts_end or 0), "label": str(label or ""), "metrics": {}})
            if key:
                bucket["metrics"][str(key)] = value
        for item in sorted(grouped.values(), key=lambda x: x["id"], reverse=True):
            tgt = "latest_policy" if item["kind"] == 'game:connect4:policy_batch' else "latest_explore"
            if out[tgt] is None:
                m = item.get("metrics", {})
                out[tgt] = {
                    "id": item["id"],
                    "label": item["label"],
                    "ts_end": item["ts_end"],
                    "games": int(float(m.get("games", 0) or 0)),
                    "wins_x": int(float(m.get("wins_x", 0) or 0)),
                    "wins_o": int(float(m.get("wins_o", 0) or 0)),
                    "draws": int(float(m.get("draws", 0) or 0)),
                    "avg_moves": float(m.get("avg_moves", 0.0) or 0.0),
                    "eps": float(m.get("eps", 0.0) or 0.0),
                    "explore_moves_per_game": int(float(m.get("explore_moves_per_game", 0) or 0)),
                    "chains_count": int(float(m.get("chains_count", 0) or 0)),
                }
        return out
    except Exception:
        LOG.exception("❌ Connect4 daily status konnte nicht geladen werden")
        return out



# =============================================================================
# Laufzeitklasse
# =============================================================================
class Connect4Runtime:
    """
    Spiellogik + Kopplung an ORÓMA-SnapChain.
    Wichtig: Der Runtime-Thread kann durch Reloads neu erzeugt werden.
             Deswegen prüfen wir VOR JEDEM Snap-/Export-Zugriff mit
             _ensure_snapchain(), ob eine valide Chain existiert.
    """
    # Standard-Mode-Split (analog TicTacToe)
    MODES = (
        "oroma_vs_ki",
        "oroma_vs_human",
        "ki_vs_ki",
        "oroma_vs_oroma_explore",
        "oroma_vs_oroma_policy",
        # Legacy Alias (UI soll ihn nicht prominent zeigen, aber kompatibel behalten)
        "oroma_solo",
    )

    def __init__(self):
        self.mode = "oroma_vs_oroma_policy"
        self.auto = False
        self.lock = threading.Lock()

        self.speed = "normal"  # normal|turbo
        self.tick_sec = 0.8

        self.policy_namespace = (os.environ.get("OROMA_C4_POLICY_NAMESPACE") or "game:connect4").strip()
        self.eps = float(os.environ.get("OROMA_C4_EPS") or "0.08")
        self.explore_moves_per_game = int(os.environ.get("OROMA_C4_EXPLORE_MOVES_PER_GAME") or "1")

        self._shim = UniversalPolicyShim(namespace=self.policy_namespace)

        # SnapChain erst mal bewusst None; _ensure_snapchain() erzeugt sie
        self.snapchain: Optional["SnapChain"] = None
        self._ensure_snapchain(context="__init__")

        self.stats = {"games": 0, "wins": {"oroma": 0, "ki": 0, "human": 0}, "snaps_total": 0}
        self.reset()
        threading.Thread(target=self._loop, daemon=True).start()

    # -------------------------------------------------------------------------
    # 🧰 Robustheits-Helfer
    # -------------------------------------------------------------------------
    def _ensure_snapchain(self, context: str = "") -> bool:
        """
        Stellt sicher, dass self.snapchain existiert.
        Rückgabe: True = vorhanden/neu erstellt, False = fehlgeschlagen.
        """
        if self.snapchain is not None:
            return True
        if 'SnapChain' in globals() and callable(SnapChain):
            try:
                self.snapchain = SnapChain(patterns=[], metadata={"game": "connect4"})
                LOG.info("🧩 SnapChain neu initialisiert (%s)", context or "n/a")
                return True
            except Exception:
                LOG.exception("❌ SnapChain-Initialisierung fehlgeschlagen (%s)", context or "n/a")
                self.snapchain = None
                return False
        LOG.warning("⚠️ SnapChain-Klasse nicht verfügbar (%s) – bleibt None", context or "n/a")
        self.snapchain = None
        return False

    # -------------------------------------------------------------------------
    # Grundlogik
    # -------------------------------------------------------------------------
    def reset(self):
        """Setzt Spiel zurück und erhöht Zähler."""
        self.board = [["" for _ in range(COLS)] for _ in range(ROWS)]
        # turn: "oroma" | "ki" | "human"  (bei oroma_vs_oroma_* alternieren wir oroma/ki)
        self.turn = "oroma"
        self.last_winner = None
        self.stats["games"] += 1
        LOG.info("🎮 Neues Connect4-Spiel gestartet (games=%d)", self.stats["games"])

        # pro Spiel: Lern-Trace (state_hash, action, side)
        self._trace = []  # type: ignore[var-annotated]
        self._explore_used = 0

    def _loop(self):
        """Auto-Loop für KI-Züge (separater Daemon-Thread)."""
        while True:
            time.sleep(max(0.01, float(self.tick_sec)))
            if self.auto:
                self._auto_turn()

    def _auto_turn(self):
        """Automatischer Zug je nach aktuellem Spieler."""
        with self.lock:
            player = self.turn
            game_over = self.last_winner is not None
        if game_over or player not in ("oroma", "ki"):
            return
        col = self._choose_col(player)
        self.drop(col, player)

    def _choose_col(self, who: str) -> int:
        """Action-Wahl: UniversalPolicy (policy/explore), sonst Fallback."""
        legal = [c for c in range(COLS) if not self.board[0][c]]
        if not legal:
            return random.randint(0, COLS - 1)

        # Legacy Alias: oroma_solo → explore (bewusst, aber UI sollte es nicht anbieten)
        mode = "oroma_vs_oroma_explore" if self.mode == "oroma_solo" else self.mode

        # Explore: erzwinge pro Game mindestens explore_moves_per_game Zufallsmoves
        do_explore = (mode == "oroma_vs_oroma_explore")
        if do_explore:
            if self._explore_used < max(0, int(self.explore_moves_per_game)):
                self._explore_used += 1
                return random.choice(legal)
            if random.random() < max(0.0, float(self.eps)):
                return random.choice(legal)

        # Policy: UniversalPolicy
        a = self._shim.choose(board=self.board, legal=legal, who=who)
        if a is None:
            return random.choice(legal)
        return int(a)

    def drop(self, col: int, who: str):
        """
        Lässt einen Stein in Spalte col fallen (wer: 'oroma' | 'ki' | 'human').
        UI ruft das immer für den aktuell an der Reihe befindlichen Spieler auf.
        """
        with self.lock:
            if self.last_winner or not (0 <= col < COLS):
                return {"ok": False}
            # unterste freie Reihe suchen
            for row in reversed(range(ROWS)):
                if not self.board[row][col]:
                    self.board[row][col] = "O" if who == "oroma" else "X"
                    break
            else:
                return {"ok": False}  # Spalte voll

            # Trace (für Policy-Lernen): vor dem Zug den Hash bilden
            try:
                sh = self._shim.state_hash(self.board, who, pre_move=True)
                if sh:
                    self._trace.append({"state_hash": sh, "action": int(col), "who": who})
            except Exception:
                pass

            winner = self._check_winner()
            if winner:
                self._finish_game(winner)
            else:
                # Nächster dran (in Human-Modi macht die UI die Eingaben)
                if self.mode == "ki_vs_ki":
                    self.turn = "ki" if who == "oroma" else "oroma"
                elif self.mode == "oroma_vs_human":
                    self.turn = "human" if who == "oroma" else "oroma"
                else:
                    # oroma_vs_ki / oroma_vs_oroma_* / oroma_solo
                    self.turn = "ki" if who == "oroma" else "oroma"
        return {"ok": True, "board": self.board}

    def _check_winner(self):
        """Sucht 4er-Ketten in alle Richtungen; liefert 'oroma'/'ki'/'draw'/None."""
        dirs = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for r in range(ROWS):
            for c in range(COLS):
                p = self.board[r][c]
                if not p:
                    continue
                for dr, dc in dirs:
                    seq = [(r + dr * k, c + dc * k) for k in range(4)]
                    if all(0 <= rr < ROWS and 0 <= cc < COLS and self.board[rr][cc] == p for rr, cc in seq):
                        return "oroma" if p == "O" else "ki"
        # Voll? → Unentschieden
        if all(self.board[0][c] for c in range(COLS)):
            return "draw"
        return None

    # -------------------------------------------------------------------------
    # 📊 Diagnose-Ablauf: Ende → Log → Export → Reset
    # -------------------------------------------------------------------------
    def _finish_game(self, winner: str):
        """
        Werten, loggen, 2s Anzeige, exportieren, resetten.
        Achtung: SnapChain-Existenz wird hier selbstheilend sichergestellt.
        """
        LOG.info("🏁 [BEGIN] _finish_game – Gewinner: %s", winner)

        # 1) Statistik und Reward ermitteln
        val = 0.0
        if winner == "draw":
            pass
        elif winner == "oroma":
            val = +2.0
            self.stats["wins"]["oroma"] += 1
        elif winner == "ki":
            val = -1.0
            self.stats["wins"]["ki"] += 1
        else:
            # Falls später „human“ als Gewinner auftaucht
            val = -0.5
            self.stats["wins"]["human"] += 1

        self.last_winner = winner
        LOG.info("📊 Aktuelle Statistik: %s", self.stats)

        # 2) SnapChain sicherstellen + Reward-Snap anhängen (auch bei draw=0.0)
        self._ensure_snapchain(context="_finish_game")
        self._log(val, winner)

        # 2b) Policy-Lernen (nur in Explore-Modus)
        try:
            mode = "oroma_vs_oroma_explore" if self.mode == "oroma_solo" else self.mode
            if mode == "oroma_vs_oroma_explore":
                self._shim.learn_from_trace(self._trace, winner)
        except Exception:
            LOG.exception("❌ Policy-Lernen fehlgeschlagen")

        # 3) Brett noch 2 Sekunden sichtbar lassen (UX-Wunsch)
        LOG.info("⏳ Warte 2s vor Export/Reset (Board sichtbar lassen)")
        time.sleep(2.0)

        # 4) Export der Chain
        self._export_snapchain()

        LOG.info("🏁 [END] _finish_game → Reset() …")
        self.reset()

    # -------------------------------------------------------------------------
    # 🧠 Snap-Erzeugung (mit Selbstheilung)
    # -------------------------------------------------------------------------
    def _log(self, reward_value: float, who: str):
        LOG.info("🧠 [BEGIN] _log() – reward=%.2f player=%s", reward_value, who)

        if not self._ensure_snapchain(context="_log"):
            LOG.warning("⚠️ Kein SnapChain-Objekt aktiv (Snap=%s, SnapChain=%s)",
                        bool(Snap), bool(self.snapchain))
            LOG.info("🧠 [END] _log() (abgebrochen mangels SnapChain)")
            return

        try:
            s = Snap([reward_value], metadata={"player": who, "reward": reward_value})
            self.snapchain.append(s)  # type: ignore[union-attr]
            self.stats["snaps_total"] += 1
            LOG.info("➕ Snap hinzugefügt (%.2f für %s) → Chain-Länge=%d",
                     reward_value, who, len(self.snapchain.patterns))  # type: ignore[union-attr]
        except Exception:
            LOG.exception("❌ Snap-Fehler in _log()")

        LOG.info("🧠 [END] _log()")

    # -------------------------------------------------------------------------
    # 💾 Export (Pfad, Größe, Rechte, Owner; frische Chain danach)
    # -------------------------------------------------------------------------
    def _export_snapchain(self):
        LOG.info("🧠 [BEGIN] _export_snapchain() – SnapObjekt=%s save_chain=%s",
                 bool(self.snapchain), bool(save_chain))

        if not (self.snapchain and save_chain and callable(save_chain)):
            LOG.warning("⚠️ Kein SnapChain-Objekt oder save_chain() fehlt – Export übersprungen")
            LOG.info("🧠 [END] _export_snapchain() (abgebrochen)")
            return

        try:
            length = len(self.snapchain.patterns)  # type: ignore[union-attr]
            LOG.info("🔢 SnapChain-Länge vor Export: %d", length)
            if length == 0:
                LOG.warning("⚠️ SnapChain leer – kein Export durchgeführt")
                LOG.info("🧠 [END] _export_snapchain() (leer)")
                return

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            cwd = os.getcwd()
            user = os.getenv("USER") or os.getenv("LOGNAME") or "unknown"
            LOG.info("💡 Exportdetails: cwd=%s, user=%s, ts=%s", cwd, user, ts)

            path = save_chain(f"connect4_{ts}", self.snapchain)  # type: ignore[arg-type]
            LOG.info("💾 Exportversuch gestartet → %s", path)

            if os.path.exists(path):
                st = os.stat(path)
                mode = oct(st.st_mode)[-3:]
                try:
                    owner = pwd.getpwuid(st.st_uid).pw_name
                except Exception:
                    owner = str(st.st_uid)
                LOG.info("📦 Datei vorhanden: %s (%d Bytes, Mode=%s, Owner=%s)", path, st.st_size, mode, owner)
            else:
                LOG.warning("❌ Datei existiert NICHT nach save_chain(): %s", path)

            # Frische Chain für die nächste Partie (wie in TicTacToe)
            self.snapchain = None
            self._ensure_snapchain(context="post-export reset")
            LOG.info("🔄 SnapChain zurückgesetzt (neue leere Chain initialisiert)")

        except Exception:
            LOG.exception("❌ Exportfehler in _export_snapchain()")

        LOG.info("🧠 [END] _export_snapchain()")

    # -------------------------------------------------------------------------
    # Zustand für die UI
    # -------------------------------------------------------------------------
    def state(self):
        # Policy-Counter (sichtbar in UI)
        pr = self._shim.count_policy_rules()
        ar = self._shim.count_rules_archive()
        return {
            "board": self.board,
            "turn": self.turn,
            "winner": self.last_winner,
            "mode": self.mode,
            "speed": self.speed,
            "stats": self.stats,
            "snaps_in_ram": len(self.snapchain.patterns) if self.snapchain else 0,
            "policy": {
                "namespace": self.policy_namespace,
                "have_up": bool(_HAVE_UP),
                "eps": float(self.eps),
                "explore_moves_per_game": int(self.explore_moves_per_game),
                "policy_rules": int(pr or 0),
                "rules_active": int(ar or 0),
            },
            "daily": _load_connect4_daily_status(),
        }


# =============================================================================
# UniversalPolicy Shim (Connect4)
# =============================================================================
class UniversalPolicyShim:
    """
    Kompakter Shim für Connect4 → UniversalPolicy.

    Canonicalisierung:
      • Symmetrie: horizontale Spiegelung (Spalten 0..6)
      • Wir wählen lexikographisch den kleineren Board-String als Canon.
      • Aktionen werden bei Spiegelung gemappt: col → (6-col)

    Side-awareness:
      • State-Hash enthält side, damit Policy für "am Zug" getrennt lernen kann.
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = (namespace or "game:connect4").strip()
        self._pol = upol.Policy(namespace=self.namespace) if _HAVE_UP else None

    def _board_str(self, board, mirror: bool) -> str:
        # board enthält ""/"O"/"X" – wir normalisieren zu 0/1/2
        # Reihenfolge: row-major (oben→unten)
        parts = []
        for r in range(ROWS):
            row = board[r]
            for c in range(COLS):
                cc = (COLS - 1 - c) if mirror else c
                v = row[cc]
                parts.append("1" if v == "O" else "2" if v == "X" else "0")
        return "".join(parts)

    def canonicalize(self, board) -> tuple[str, bool]:
        a = self._board_str(board, mirror=False)
        b = self._board_str(board, mirror=True)
        if b < a:
            return b, True
        return a, False

    def state_hash(self, board, who: str, pre_move: bool = False) -> str:
        # pre_move ist aktuell nur Dokumentation (der Runner nutzt pre_move auch).
        canon, mirrored = self.canonicalize(board)
        side = "O" if who == "oroma" else "X"
        return f"c4:{canon}|side:{side}"

    def choose(self, board, legal, who: str) -> Optional[int]:
        if not self._pol:
            return None
        canon, mirrored = self.canonicalize(board)
        side = "O" if who == "oroma" else "X"
        sh = f"c4:{canon}|side:{side}"
        # legal in canon-space
        legal_canon = [int(COLS - 1 - a) if mirrored else int(a) for a in legal]
        a_c = self._pol.choose(sh, legal_canon, side=side)
        if a_c is None:
            return None
        # map back to real space
        return int(COLS - 1 - int(a_c)) if mirrored else int(a_c)

    def learn_from_trace(self, trace, winner: str) -> None:
        if not self._pol:
            return
        # Outcome pro Move aus Sicht des Spielers, der den Move gemacht hat.
        # winner: oroma|ki|draw
        items = []
        ts = int(time.time())
        for t in (trace or []):
            sh = str(t.get("state_hash", "") or "").strip()
            if not sh:
                continue
            who = str(t.get("who", ""))
            a = t.get("action", 0)
            out = 0.0
            if winner == "draw":
                out = 0.0
            elif winner == "oroma":
                out = 1.0 if who == "oroma" else -1.0
            elif winner == "ki":
                out = 1.0 if who == "ki" else -1.0
            items.append({"state_hash": sh, "action_canon": int(a), "outcome": float(out), "ts": ts})
        if items:
            self._pol.learn_many(items)

    def count_policy_rules(self) -> int:
        if sql_manager:
            try:
                with sql_manager.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", (self.namespace,))
                    row = cur.fetchone()
                    val = int(row[0] if row and row[0] is not None else 0)
                    if val > 0:
                        return val
            except Exception:
                LOG.exception("❌ count_policy_rules() über sql_manager fehlgeschlagen")
        return _query_scalar_fallback("SELECT COUNT(*) FROM policy_rules WHERE namespace=?", (self.namespace,))

    def count_rules_archive(self) -> int:
        # rules archive table exists in many builds; if missing: 0
        if sql_manager:
            try:
                with sql_manager.get_conn() as conn:
                    cur = conn.cursor()
                    try:
                        cur.execute("SELECT COUNT(*) FROM rules WHERE namespace=? AND active=1", (self.namespace,))
                    except Exception:
                        cur.execute("SELECT COUNT(*) FROM rules WHERE namespace=?", (self.namespace,))
                    row = cur.fetchone()
                    return int(row[0] if row and row[0] is not None else 0)
            except Exception:
                LOG.exception("❌ count_rules_archive() über sql_manager fehlgeschlagen")
        return 0


# =============================================================================
# Flask-Routen
# =============================================================================
def _rt():
    """Singleton-Runtime im Flask-App-Kontext (Reload-sicher)."""
    if "_connect4_rt" not in current_app.config:
        current_app.config["_connect4_rt"] = Connect4Runtime()
    return current_app.config["_connect4_rt"]

@connect4_bp.route("/")
def page():
    return render_template("connect4.html")

@connect4_bp.route("/api/state")
def api_state():
    return jsonify(_rt().state())

@connect4_bp.route("/api/drop", methods=["POST"])
def api_drop():
    data = request.get_json(force=True)
    col = int(data.get("col", 0))
    who = _rt().turn
    return jsonify(_rt().drop(col, who))


@connect4_bp.route("/api/move", methods=["POST"])
def api_move_alias():
    """Alias für Standardisierung (TicTacToe nutzt /api/move)."""
    return api_drop()

@connect4_bp.route("/api/reset", methods=["POST"])
def api_reset():
    _rt().reset()
    return jsonify({"ok": True})

@connect4_bp.route("/api/toggle", methods=["POST"])
def api_toggle():
    rt = _rt()
    rt.auto = not rt.auto
    LOG.info("🔁 Auto-Toggle: %s", rt.auto)
    return jsonify({"ok": True, "running": rt.auto})


@connect4_bp.route("/api/set_mode", methods=["POST"])
def api_set_mode():
    rt = _rt()
    data = request.get_json(force=True) or {}
    mode = str(data.get("mode", "") or "").strip()
    if mode not in rt.MODES:
        return jsonify({"ok": False, "error": "invalid_mode", "modes": list(rt.MODES)})
    with rt.lock:
        rt.mode = mode
        # oroma_solo bleibt Alias → explore
        LOG.info("🎛️ Mode gesetzt: %s", rt.mode)
    return jsonify({"ok": True, "mode": rt.mode, "modes": list(rt.MODES)})


@connect4_bp.route("/api/set_speed", methods=["POST"])
def api_set_speed():
    rt = _rt()
    data = request.get_json(force=True) or {}
    sp = str(data.get("speed", "") or "").strip().lower()
    if sp not in ("normal", "turbo"):
        return jsonify({"ok": False, "error": "invalid_speed", "speeds": ["normal", "turbo"]})
    with rt.lock:
        rt.speed = sp
        rt.tick_sec = 0.8 if sp == "normal" else 0.08
        LOG.info("⏱️ Speed gesetzt: %s (tick=%.3fs)", rt.speed, rt.tick_sec)
    return jsonify({"ok": True, "speed": rt.speed, "tick_sec": rt.tick_sec})