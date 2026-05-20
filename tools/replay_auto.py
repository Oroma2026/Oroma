#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/replay_auto.py
# Projekt: ORÓMA – Replay Auto-Runner (oneshot, systemd-freundlich)
# Version: v3.8-r7 (Replay Universal: Bundle-Support + DB-first AutoPick Fix)
# Stand:   2026-02-15
# Autor:   ORÓMA · KI-JWG-X1
# Lizenz:  MIT
# =============================================================================
#
# Zweck
# ─────
#  • `--auto`  : nehme die jüngste SnapChain-Datei in OROMA_SNAPCHAINS (ohne replay_*).
#  • `--chain` : explizite Chain-ID/Dateiname (ohne .json).
#  • `--speed` : Abspielgeschwindigkeit.
#
# Verhalten
# ─────────
#  • Kein chown/chgrp – Ownership wird durch systemd ExecStartPre geregelt.
#  • Wartet, bis der Replay-Thread fertig ist; Exitcode 0/1 je nach Erfolg.
# =============================================================================

from __future__ import annotations
import os, sys, time, argparse, glob, logging
import sqlite3
import json
import zlib
import re

# Pygame schreibt beim Import standardmäßig eine "Hello from the pygame community"-Zeile nach stdout.
# Da `replay_auto.py` häufig sehr regelmäßig (Timer/Orchestrator) gestartet wird, bläht das die Logs
# massiv auf. Diese Env-Var muss gesetzt sein, bevor irgendwo pygame importiert wird (das passiert
# indirekt in ReplayManager/MiniPrograms).
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

BASE = os.environ.get("OROMA_BASE", "/opt/ai/oroma")
if BASE not in sys.path:
    sys.path.insert(0, BASE)

LOGS = os.path.join(BASE, "logs")
os.makedirs(LOGS, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [replay_auto] %(message)s"
)
LOG = logging.getLogger("replay_auto")


def _load_chain_dict_from_db(db_path: str, chain_id: str):
    """Load a SnapChain (stored in snapchains.blob) as a Python dict.

    Notes (important for your current DB state):
    - Many rows are marked status='compressed' but the blob is *still JSON text*.
    - Some rows may truly be zlib-compressed JSON; we try both.
    """
    con = None
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT blob FROM snapchains WHERE id=?", (str(chain_id),)).fetchone()
        if not row:
            return None
        blob = row["blob"]
        if blob is None:
            return None

        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        if isinstance(blob, str):
            raw = blob.encode("utf-8", "replace")
        else:
            raw = bytes(blob)

        # 1) plain JSON
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            pass

        # 2) zlib JSON
        try:
            dec = zlib.decompress(raw)
            return json.loads(dec.decode("utf-8"))
        except Exception:
            return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def _events_list(chain_dict) -> list:
    if not isinstance(chain_dict, dict):
        return []
    ev = chain_dict.get("events")
    return ev if isinstance(ev, list) else []


def ensure_chain_file_from_db(db_path: str, chain_id: str) -> bool:
    """Ensure `/data/snapchains/<id>.json` exists by reconstructing from SQLite.

    ORÓMA speichert SnapChains häufig *primär* in SQLite (`snapchains.blob`).
    Der klassische Replay-Pfad (`core.snapchain.load_chain`) erwartet jedoch
    eine JSON-Datei im Filesystem. Wenn der Auto-Picker eine numerische ID
    liefert, deren Datei fehlt, schlagen Replays sonst mit
    "SnapChain-Datei nicht gefunden" fehl.
    """
    try:
        cid = str(chain_id)
        if not cid.isdigit():
            return True

        fp = os.path.join(SNAPCHAIN_DIR, f"{cid}.json")
        if os.path.exists(fp):
            return True

        os.makedirs(SNAPCHAIN_DIR, exist_ok=True)

        # Prefer canonical loader (handles blob codecs)
        sc = None
        try:
            from core.model_registry import model_registry  # type: ignore
            sc = model_registry.load_chain(db_path, cid)
        except Exception:
            sc = None

        def _adapt_events_dict_to_snapchain_obj(d: dict) -> Optional[object]:
            """Altformat {id, events:[{features:[...], ...}, ...]} → SnapChain(patterns=[SnapPattern]).

            Replay arbeitet primär mit `SnapChain.patterns`. Manche historische/alternative
            Pipelines speichern jedoch nur eine Events-Liste. Wir erzeugen 1 Pattern,
            dessen Vektoren aus `event['features']` stammen.

            Rückgabe:
              - SnapChain-Objekt oder None (wenn keine verwertbaren Features vorhanden).
            """
            try:
                events = d.get("events") or []
                if not isinstance(events, list) or not events:
                    return None
                vectors: List[List[float]] = []
                for ev in events:
                    if isinstance(ev, dict) and isinstance(ev.get("features"), (list, tuple)):
                        try:
                            vectors.append([float(x) for x in ev.get("features")])
                        except Exception:
                            continue
                if not vectors:
                    return None
                from core.snappattern import SnapPattern
                from core.snapchain import SnapChain
                pat = SnapPattern(
                    id=0,
                    vectors=vectors,
                    meta={
                        "adapter": "events→pattern",
                        "n_events": int(len(events)),
                        "n_vectors": int(len(vectors)),
                    },
                )
                return SnapChain(
                    chain_id=str(d.get("id") or d.get("chain_id") or cid),
                    origin=str(d.get("origin") or "db/events"),
                    patterns=[pat],
                    meta={
                        "adapted_from": "events",
                        "adapter_version": "2026-02-15",
                    },
                )
            except Exception:
                return None

        # Falls model_registry.load_chain(...) ein Dict liefert (Altformat), adaptieren.
        if isinstance(sc, dict) and ("events" in sc) and ("patterns" not in sc):
            adapted = _adapt_events_dict_to_snapchain_obj(sc)
            if adapted is None:
                return False
            sc = adapted

        if sc is None:
            d = _load_chain_dict_from_db(db_path, cid)
            if d is None:
                return False
            # Altformat-Adapter auch für direct-DB-Loader
            if isinstance(d, dict) and ("events" in d) and ("patterns" not in d):
                adapted = _adapt_events_dict_to_snapchain_obj(d)
                if adapted is None:
                    return False
                sc = adapted
            else:
                try:
                    from core.snapchain import SnapChain
                    sc = SnapChain.from_dict(d)
                except Exception:
                    return False

        try:
            from core.snapchain import save_chain
            save_chain(cid, sc)
        except Exception:
            return False

        return os.path.exists(fp)
    except Exception:
        return False

try:
    from core.snapchain import SNAPCHAIN_DIR
except Exception:
    SNAPCHAIN_DIR = os.environ.get("OROMA_SNAPCHAINS", os.path.join(BASE, "data", "snapchains"))

from core import replay_manager as RM  # nutzt ALIAS in core/__init__.py
from core.sql_manager import get_db_path

def pick_latest_chain_id(db_path: str):
    """Wählt im Auto-Modus eine sinnvolle SnapChain.

    Hintergrund
    ----------
    In ORÓMA werden viele Origins als Einzel-Snap (len=1) gespeichert (z.B. vision/token,
    audio/token, calc/result). Für echtes Sequenz-Replay (Learning/Consolidation) ist
    eine Chain mit mehreren Events oft hilfreicher.

    Strategie
    ---------
    1) Kandidaten aus `list_recent_snapchains()` scannen (limit = OROMA_REPLAY_PICK_SCAN_LIMIT)
    2) Chains bevorzugen, die "episode-artig" sind (link/*, game:*, calc/result)
       und Chains vermeiden, die i.d.R. nur technische/metainterne Einträge sind (dream/mut).
    3) Wenn der beste Kandidat eine Token-Quelle ist und nur 1 Event hat, wird automatisch
       auf ein DB-Bundle umgestellt: bundle:<origin>:<n> (n = OROMA_REPLAY_BUNDLE_N).

    Umgebungsvariablen
    ------------------
    OROMA_REPLAY_MIN_EVENTS           Default: 2
    OROMA_REPLAY_PICK_SCAN_LIMIT      Default: 400
    OROMA_REPLAY_BUNDLE_N             Default: 120
    OROMA_REPLAY_DEBUG_PICK           Default: 0/1
    """
    min_events = int(os.environ.get("OROMA_REPLAY_MIN_EVENTS", "2") or "2")
    scan_limit = int(os.environ.get("OROMA_REPLAY_PICK_SCAN_LIMIT", "400") or "400")
    bundle_n = int(os.environ.get("OROMA_REPLAY_BUNDLE_N", "120") or "120")
    debug_pick = (os.environ.get("OROMA_REPLAY_DEBUG_PICK", "0") == "1")

    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = [
                {"id": r["id"], "origin": r["origin"], "status": r["status"]}
                for r in con.execute(
                    "SELECT id, origin, status FROM snapchains ORDER BY ts DESC LIMIT ?",
                    (scan_limit,),
                ).fetchall()
            ]
        finally:
            try:
                con.close()
            except Exception:
                pass
        if not rows:
            return "bootstrap"

        best = None  # (score, chain_id, origin, n_events)

        for r in rows:
            cid = str(r.get("id"))
            origin = str(r.get("origin") or "")
            status = str(r.get("status") or "")

            # Harte Excludes (typisch reine Meta-/Mutations-Chains)
            if origin.startswith("dream/mut"):
                continue

            # Event-Liste direkt aus SQLite-Blob laden (robust gegen "compressed-but-json")
            ch = _load_chain_dict_from_db(db_path, cid)
            events = _events_list(ch)
            n_events = len(events)

            # Mindestlänge (konfigurierbar über OROMA_REPLAY_MIN_EVENTS)
            if n_events < min_events:
                continue

            # Zusatz-Filtersicherheit: mindestens ein Event muss verwertbare Feature-Vektoren haben.
            # Ohne Features entsteht bei DB→FS Rekonstruktion typischerweise 0 Patterns → steps=0.
            has_vec = False
            for _e in events:
                if (
                    isinstance(_e, dict)
                    and isinstance(_e.get("features"), (list, tuple))
                    and len(_e.get("features")) > 0
                ):
                    has_vec = True
                    break
            if not has_vec:
                continue

            # scoring
            score = 0
            if origin.startswith("link/"):
                score += 120
            elif origin.startswith("game:"):
                score += 110
            elif origin == "calc/result":
                score += 100
            elif origin in ("audio/token", "vision/token"):
                score += 40
            else:
                score += 10

            # kleine Feinheiten
            if status == "active":
                score += 5
            score += min(50, n_events)

            # Mindestlänge bevorzugen (aber nicht komplett ausschließen: calc/result ist oft 1)
            if n_events < min_events and origin not in ("calc/result", "link/a_label", "link/av_label") and not origin.startswith("game:"):
                score -= 30

            if best is None or score > best[0]:
                best = (score, cid, origin, n_events)

            if debug_pick:
                LOG.debug("[pick] id=%s origin=%s n=%s score=%s", cid, origin, n_events, score)

        if not best:
            return "bootstrap"

        _score, cid, origin, n_events = best

        # Falls Token-Quelle (len=1) -> Bundle
        if origin in ("vision/token", "audio/token") and n_events < min_events:
            bundle_id = f"bundle:{origin}:{bundle_n}"
            LOG.info("Auto-Pick: Token-Chain len=%s -> Bundle-Replay (%s)", n_events, bundle_id)
            return bundle_id

        return cid

    except Exception as e:
        LOG.warning("Auto-Pick fehlgeschlagen (%s) → fallback bootstrap", e)
        return "bootstrap"




def _build_bundle_chain_spec(db_path: str, chain_spec: str) -> str | None:
    """bundle:<origin>:<N> → temporäre SnapChain-Datei erzeugen und ID zurückgeben."""
    if not chain_spec.startswith("bundle:"):
        return None
    try:
        _, origin, n_str = chain_spec.split(":", 2)
        n = int(n_str)
    except Exception:
        logging.getLogger("replay_auto").error("Bundle-Syntax ungültig: %s", chain_spec)
        return None

    if n <= 0:
        logging.getLogger("replay_auto").error("Bundle-N muss >0 sein: %s", chain_spec)
        return None

    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        ids = [
            str(r["id"]) for r in con.execute(
                "SELECT id FROM snapchains WHERE origin=? ORDER BY ts DESC LIMIT ?",
                (origin, n),
            ).fetchall()
        ]
    except Exception as e:
        logging.getLogger("replay_auto").error("Bundle: DB query fehlgeschlagen: %s", e)
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass

    if not ids:
        logging.getLogger("replay_auto").warning("Bundle: keine Chains gefunden (origin=%s)", origin)
        return None

    all_events = []
    for cid in reversed(ids):  # chronologisch alt->neu
        ch = _load_chain_dict_from_db(db_path, cid)
        ev = _events_list(ch)
        if ev:
            all_events.extend(ev)

    if not all_events:
        logging.getLogger("replay_auto").warning("Bundle: alle Chains leer (origin=%s, n=%d)", origin, n)
        return None

    from core.snapchain import SnapChain, save_chain

    ts = int(time.time())
    tmp_id = f"bundle_tmp_{origin.replace('/','_')}_{n}_{ts}"
    tmp_sc = SnapChain(id=tmp_id, events=all_events, meta={
        "kind": "bundle",
        "origin": origin,
        "n": n,
        "built_ts": ts,
        "source_ids": ids,
    })
    try:
        save_chain(tmp_id, tmp_sc)
    except Exception as e:
        logging.getLogger("replay_auto").error("Bundle: save_chain fehlgeschlagen: %s", e)
        return None

    logging.getLogger("replay_auto").info("Bundle: tmp_chain gespeichert: %s (events=%d)", tmp_id, len(all_events))
    return tmp_id
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="neueste SnapChain wählen")
    ap.add_argument("--chain", type=str, default=os.environ.get("OROMA_REPLAY_CHAIN_ID"), help="Chain-ID (ohne .json)")
    ap.add_argument("--speed", type=float, default=1.0, help="Speed-Faktor")
    ap.add_argument("--verbose", action="store_true", help="Verbose/DEBUG logging (setzt OROMA_REPLAY_LOGLEVEL=DEBUG)")
    args = ap.parse_args()

    if args.verbose:
        # ReplayManager respektiert OROMA_REPLAY_LOGLEVEL. Zusätzlich heben wir auch diesen Script-Logger an.
        os.environ["OROMA_REPLAY_LOGLEVEL"] = "DEBUG"
        logging.getLogger().setLevel(logging.DEBUG)
        LOG.setLevel(logging.DEBUG)
        LOG.debug("Verbose aktiviert: Root-Loglevel=DEBUG, OROMA_REPLAY_LOGLEVEL=DEBUG")

    # DB-Pfad (wichtig für Auto-Pick + Bundle)
    # In diesem Repo ist die kanonische API: core.sql_manager.get_db_path() → MAIN-DB.
    # Zusätzlich erlauben wir ENV overrides für schnelle Tests/Hotfixes.
    db_path = (
        os.environ.get("OROMA_DB_MAIN")
        or os.environ.get("OROMA_DB_PATH")
        or get_db_path()
        or "/opt/ai/oroma/data/oroma.db"
    )

    chain_id = args.chain
    # bundle:<origin>:<N> → temporäre Chain-Datei bauen (damit das bestehende Replay-System unverändert bleibt)
    if chain_id and isinstance(chain_id, str) and chain_id.startswith("bundle:"):
        tmp_id = _build_bundle_chain_spec(db_path, chain_id)
        if tmp_id:
            chain_id = tmp_id
        else:
            LOG.warning("Bundle-Erzeugung fehlgeschlagen (%s) – fahre mit Auto/Bootstrap fort.", chain_id)
            chain_id = None
    if args.auto or not chain_id:
        chain_id = pick_latest_chain_id(db_path)
        if chain_id:
            LOG.info("Auto-Modus: jüngste Chain = %s", chain_id)

    if not chain_id:
        LOG.warning("Keine Chain gefunden – erzeuge Minimal-Replay 'bootstrap'")
        chain_id = "bootstrap"
        # Optional: kleinen Dummy anlegen, damit kein 0-Step Replay exportiert wird
        try:
            from core.snapchain import SnapChain, save_chain
            ch = SnapChain(metadata={"origin": "replay_bootstrap"})
            ch.add_text("bootstrap start")
            save_chain(chain_id, ch)
            LOG.info("Bootstrap-SnapChain geschrieben: %s.json", chain_id)
        except Exception as e:
            LOG.warning("Bootstrap-Erzeugung fehlgeschlagen: %s (fahre mit Dummy fort)", e)

    # Numerische IDs können DB-only sein (kein JSON im SNAPCHAIN_DIR). Für das
    # bestehende Replay-System stellen wir die Datei ggf. aus der DB wieder her.
    if isinstance(chain_id, str) and chain_id.isdigit():
        if not ensure_chain_file_from_db(db_path, chain_id):
            LOG.error(
                "Auto/Replay: SnapChain #%s liegt in der DB, aber die FS-Rekonstruktion scheiterte. "
                "Hinweis: Falls die Chain als {id,events} (Altformat) vorliegt, muss sie über Bundle/DB-Adapter laufen.",
                chain_id,
            )
            return 1

    LOG.info("Starte Replay: chain=%s speed=%.2f", chain_id, args.speed)
    try:
        RM.start(chain_id, speed=float(args.speed))
    except Exception as e:
        LOG.error("Start fehlgeschlagen: %s", e)
        return 1

    # warten bis Ende
    t0 = time.time()
    last_pct = -1.0
    while True:
        st = RM.status()
        if not st.get("running"):
            break
        pct = float(st.get("progress_pct") or 0.0)
        if pct != last_pct:
            LOG.info("Fortschritt: %.2f%%", pct)
            last_pct = pct
        time.sleep(0.2)

    err = RM.status().get("error")
    if err:
        LOG.error("Replay endete mit Fehler: %s", err)
        return 1

    LOG.info("Replay fertig.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
