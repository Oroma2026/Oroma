#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/train_snake_policy.py
# Projekt: ORÓMA
# Modul:   Snake Policy Trainer (DB→policy_rules, Auto-Export, Preflight-Diagnose)
# Version: v3.7.8
# Stand:   2025-12-29
# Autor:   ORÓMA · KI-JWG-X1
# =============================================================================
#
# Zweck
# ─────
#  Liest jüngste SnapChains (origin='game:snake') aus der SQLite-DB und
#  trainiert die tabellarische Policy (policy_rules) via core.policy_engine.
#  Schwellwerte + Auto-Export werden aus ENV übernommen (UP- und PE-Präfix).
#
# Nutzung
# ───────
#  PYTHONPATH=/opt/ai/oroma python3 -m core.train_snake_policy --limit 3000 --verbose
#
# Diagnostik (warum "0 Schritte"?)
# ───────────────────────────────
#  Dieser Trainer kann optional eine Preflight-Diagnose ausgeben, die exakt
#  die typischen Ursachen für "0 Schritte" im Snake-Training sichtbar macht:
#
#   • quality zu niedrig / NULL (falls andere Pipelines Filter nutzen)
#   • status='compressed' (Trainer default: nur active)
#   • blob NULL/leer (kein Lerninhalt)
#   • origin-Variante: "game:snake:*" wurde früher evtl. nicht gematcht
#
#  Preflight ist standardmäßig EIN (kann per --no-preflight deaktiviert werden).
#
# ENV (beide Präfixe akzeptiert)
# ───────────────────────────────
#  OROMA_UP_AUTO_EXPORT / OROMA_PE_AUTO_EXPORT        (1)
#  OROMA_UP_MIN_N       / OROMA_PE_EXPORT_MIN_N       (3)
#  OROMA_UP_MIN_ABS_Q   / OROMA_PE_EXPORT_MIN_ABS_Q   (0.15)
#  OROMA_UP_MAJ_CONF    / OROMA_PE_EXPORT_MAJ_CONF    (0.00)
#  OROMA_UP_COOLDOWN_S  / OROMA_PE_EXPORT_COOLDOWN_S  (600)
# =============================================================================
from __future__ import annotations
import os, sys, argparse, logging
from core.log_guard import log_suppressed
import logging

if "/opt/ai/oroma" not in sys.path:
    sys.path.append("/opt/ai/oroma")

LOG = logging.getLogger("oroma.train_snake_policy")
if not LOG.handlers:
    # stdout statt stderr: Routine-Preflight/0-Schritte-Hinweise sollen im
    # Orchestrator nicht im Error-Log landen.
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    LOG.addHandler(h)
LOG.setLevel(logging.INFO)


def _db_preflight(namespace: str, include_compressed: bool, min_abs_quality: float) -> None:
    """Preflight-Diagnose für die häufigsten "0 Schritte"-Ursachen.

    Diese Funktion ist bewusst robust (best-effort) und bricht das Training
    nicht ab, selbst wenn die DB kurzzeitig gelocked ist.
    """
    try:
        from core import sql_manager
    except Exception as e:
        LOG.debug("preflight: sql_manager Importfehler: %s", e)
        return

    org = str(namespace or "").strip() or "game:snake"
    status_clause = "(status IS NULL OR status IN ('active','compressed'))" if include_compressed else "(status IS NULL OR status = 'active')"

    try:
        with sql_manager.get_conn() as conn:
            cur = conn.cursor()

            # 1) Quality-Stat (wie in deinen Checks)
            cur.execute(
                f"""
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN quality IS NULL THEN 1 ELSE 0 END) AS q_null,
                  SUM(CASE WHEN ABS(quality) >= ? THEN 1 ELSE 0 END) AS usable,
                  MIN(quality) AS q_min,
                  AVG(quality) AS q_avg,
                  MAX(quality) AS q_max
                FROM snapchains
                WHERE (origin = ? OR origin LIKE ?)
                  AND {status_clause}
                """,
                (float(min_abs_quality), org, org + "%"),
            )
            r = cur.fetchone()
            if r is not None:
                def _v(k, i):
                    try:
                        return r[k]  # type: ignore[index]
                    except Exception:
                        try:
                            return r[i]
                        except Exception:
                            return None
                total = int(_v("total", 0) or 0)
                q_null = int(_v("q_null", 1) or 0)
                usable = int(_v("usable", 2) or 0)
                q_min = _v("q_min", 3)
                q_avg = _v("q_avg", 4)
                q_max = _v("q_max", 5)
                LOG.info("[preflight] snapchains(origin~=%s, %s): total=%d q_null=%d usable(|q|>=%.3f)=%d q=[%s / %s / %s]",
                         org, "+compressed" if include_compressed else "active-only", total, q_null, float(min_abs_quality), usable,
                         str(q_min), str(q_avg), str(q_max))

            # 2) Status-Verteilung
            cur.execute(
                """
                SELECT COALESCE(status,'(NULL)') AS status, COUNT(*) c
                FROM snapchains
                WHERE (origin = ? OR origin LIKE ?)
                GROUP BY COALESCE(status,'(NULL)')
                ORDER BY c DESC
                """,
                (org, org + "%"),
            )
            rows = cur.fetchall() or []
            if rows:
                parts = []
                for rr in rows:
                    try:
                        st = rr["status"]  # type: ignore[index]
                        c = rr["c"]
                    except Exception:
                        st = rr[0]
                        c = rr[1]
                    parts.append(f"{st}:{int(c)}")
                LOG.info("[preflight] status distribution: %s", ", ".join(parts))

            # 3) Blob-Stat
            cur.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN blob IS NULL THEN 1 ELSE 0 END) AS blob_null,
                  SUM(CASE WHEN length(blob) > 0 THEN 1 ELSE 0 END) AS blob_nonempty,
                  MIN(length(blob)) AS len_min,
                  AVG(length(blob)) AS len_avg,
                  MAX(length(blob)) AS len_max
                FROM snapchains
                WHERE (origin = ? OR origin LIKE ?)
                """,
                (org, org + "%"),
            )
            rb = cur.fetchone()
            if rb is not None:
                def _vb(k, i):
                    try:
                        return rb[k]  # type: ignore[index]
                    except Exception:
                        try:
                            return rb[i]
                        except Exception:
                            return None
                total = int(_vb("total", 0) or 0)
                blob_null = int(_vb("blob_null", 1) or 0)
                blob_nonempty = int(_vb("blob_nonempty", 2) or 0)
                len_min = _vb("len_min", 3)
                len_avg = _vb("len_avg", 4)
                len_max = _vb("len_max", 5)
                LOG.info("[preflight] blob: total=%d null=%d nonempty=%d len=[%s / %s / %s]",
                         total, blob_null, blob_nonempty, str(len_min), str(len_avg), str(len_max))

            # Heuristische Hinweise
            try:
                cur.execute(
                    """
                    SELECT SUM(CASE WHEN status='compressed' THEN 1 ELSE 0 END) AS c
                    FROM snapchains
                    WHERE (origin=? OR origin LIKE ?)
                    """,
                    (org, org + "%"),
                )
                cc = cur.fetchone()
                ccomp = 0
                if cc is not None:
                    try:
                        ccomp = int(cc["c"] or 0)  # type: ignore[index]
                    except Exception:
                        ccomp = int(cc[0] or 0)
                if ccomp > 0 and not include_compressed:
                    LOG.debug("[preflight] Hinweis: %d compressed-Chains vorhanden → Training ggf. mit --include-compressed ausführen.", ccomp)
            except Exception as e:
                log_suppressed(
                    logging.getLogger(__name__),
                    key="core.train_snake_policy.pass.1",
                    exc=e,
                    msg="Suppressed exception (was: pass)",
                )

    except Exception as e:
        LOG.debug("preflight: Fehler: %s", e)
        return


def _env_bool(*names: str, default: bool) -> bool:
    for n in names:
        v = os.environ.get(n)
        if v is None: continue
        if str(v).lower() in ("1","true","yes","on"): return True
        if str(v).lower() in ("0","false","no","off"): return False
    return default

def _env_int(*names: str, default: int) -> int:
    for n in names:
        v = os.environ.get(n)
        if v is None: continue
        try: return int(v)
        except Exception: pass
    return default

def _env_float(*names: str, default: float) -> float:
    for n in names:
        v = os.environ.get(n)
        if v is None: continue
        try: return float(v)
        except Exception: pass
    return default


def main():
    ap = argparse.ArgumentParser(description="Trainiert Snake-Policy aus DB und exportiert ggf. Regeln.")
    ap.add_argument("--limit", type=int, default=3000, help="Max. Chains fürs Training (jüngste zuerst)")
    ap.add_argument("--namespace", type=str, default="game:snake", help="Origin/Namespace-Filter")
    ap.add_argument("--include-compressed", action="store_true", help="compressed-Chains mit einbeziehen")
    ap.add_argument("--no-preflight", action="store_true", help="Preflight-Diagnose vor dem Training deaktivieren")
    ap.add_argument("--verbose", action="store_true", help="Mehr Logging")
    args = ap.parse_args()
    if args.verbose:
        LOG.setLevel(logging.DEBUG)
        for h in LOG.handlers:
            try: h.setLevel(logging.DEBUG)
            except Exception: pass

    try:
        from core.policy_engine import PolicyEngine
    except Exception as e:
        LOG.error("policy_engine Importfehler: %s", e)
        return 2

    eng = PolicyEngine()
    eng.namespace = args.namespace

    eng.auto_export_on = _env_bool("OROMA_UP_AUTO_EXPORT","OROMA_PE_AUTO_EXPORT", default=True)
    eng.exp_min_n      = _env_int ("OROMA_UP_MIN_N","OROMA_PE_EXPORT_MIN_N", default=3)
    eng.exp_min_abs_q  = _env_float("OROMA_UP_MIN_ABS_Q","OROMA_PE_EXPORT_MIN_ABS_Q", default=0.15)
    eng.exp_maj_conf   = _env_float("OROMA_UP_MAJ_CONF","OROMA_PE_EXPORT_MAJ_CONF", default=0.0)
    eng.exp_cooldown_s = _env_int ("OROMA_UP_COOLDOWN_S","OROMA_PE_EXPORT_COOLDOWN_S", default=600)

    LOG.info("Start Training: ns=%s limit=%d auto_export=%s min_n=%d min|q|=%.3f maj=%.2f cool=%ds",
             eng.namespace, args.limit, eng.auto_export_on, eng.exp_min_n,
             eng.exp_min_abs_q, eng.exp_maj_conf, eng.exp_cooldown_s)

    if not args.no_preflight:
        _db_preflight(namespace=eng.namespace, include_compressed=bool(args.include_compressed), min_abs_quality=float(eng.exp_min_abs_q))

    try:
        steps = eng.train_from_db(limit=int(args.limit),
                                  origin=eng.namespace,
                                  include_compressed=bool(args.include_compressed))
    except Exception as e:
        LOG.error("Training fehlgeschlagen: %s", e)
        return 1

    if steps == 0:
        LOG.info("Fertig. Trainierte Schritte: 0")
        LOG.debug("Hinweis: 0 Schritte – wenn oben blob_nonempty>0 ist, kann es sein, dass das SnapChain-Format "
                  "keine verwertbaren Vektor-/PreHash-Paare enthält. In dem Fall: 1 Episode als JSON exportieren "
                  "(Snake-Option B), prüfen ob steps[].f oder steps[].h vorhanden ist.")
    else:
        LOG.info("Fertig. Trainierte Schritte: %d", steps)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())