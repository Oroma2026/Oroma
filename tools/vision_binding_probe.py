#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/tools/vision_binding_probe.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     Binding Stage A (Vision) – Wiederholungs-Probe / Metriken
# Version:   v3.7.3
# Stand:     2026-04-28
#
# Autor (öffentlich / Zenodo):
#   Jörg Werner
#   - Whitepaper (EN, Referenz): https://doi.org/10.5281/zenodo.19596002
#   - Whitepaper (DE, Übersetzung): https://doi.org/10.5281/zenodo.19629298
#
# Autor (intern / Implementierung):
#   ORÓMA Project
#
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# -----
# "Binding" im Sinne von ORÓMA wird hier (Stage A) als messbare Wiederholung in
# der Vision-Schicht operationalisiert: Welche Muster/Tokens treten wiederholt
# auf (24h Fenster), wie stark ist die Wiederholung (repeat>=2/3/5), wie viele
# Unique-Keys existieren, und welche Labels dominieren.
#
# WICHTIG:
# - Stage A = "Measure-only": Es werden KEINE neuen Graph-Edges geschrieben.
# - Ziel ist, die Wiederholungs-Dichte sichtbar zu machen, bevor Materialisierung
#   (Stage B) überhaupt diskutiert wird.
#
# DATENQUELLE
# -----------
# - snapchains.origin = "vision/token" (Default)
# - `blob` kann in ORÓMA sowohl als BLOB(bytes) als auch als TEXT(str) vorliegen.
#
# OUTPUT
# ------
# - schreibt StatsPoints in stats.db via DBWriter (DBWriter-first, kein Fallback)
#   series:
#     binding.v.keys.unique_24h
#     binding.v.keys.repeat_ge_2_24h
#     binding.v.keys.repeat_ge_3_24h
#     binding.v.keys.repeat_ge_5_24h
#     binding.v.events.count_24h
#     binding.v.label.unique_24h
#     binding.v.top_label.<label>_24h   (nur Top-K, bounded)
#
# ENV
# ---
#   OROMA_DBW_ENABLE=1                      (empfohlen; Script setzt nicht automatisch)
#   OROMA_VISION_BIND_WINDOW_SEC            Default: 86400
#   OROMA_VISION_BIND_LIMIT_CHAINS          Default: 5000
#   OROMA_VISION_BIND_MAX_RUNTIME_S         Default: 30
#   OROMA_VISION_BIND_ORIGIN                Default: vision/token
#   OROMA_VISION_BIND_TOPK_LABELS           Default: 8
#   OROMA_VISION_BIND_GRID                  Default: 8   (BBox Bucket Grid, 4..16 sinnvoll)
#
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import math
import json
import os
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core import sql_manager  # noqa: E402
from core.log_guard import log_suppressed  # noqa: E402
from core import db_writer_client  # noqa: E402


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None else str(v).strip()


def _json_loads_blob(blob: Any) -> Optional[Dict[str, Any]]:
    try:
        if blob is None:
            return None
        if isinstance(blob, (bytes, bytearray, memoryview)):
            s = bytes(blob).decode("utf-8", errors="replace")
        else:
            s = str(blob)
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _bbox_bucket(meta: Dict[str, Any], grid: int) -> Optional[str]:
    # Expect typical bbox keys: bbox=[x,y,w,h] or [x1,y1,x2,y2] or dict forms.
    bb = meta.get("bbox")
    if bb is None:
        # sometimes: x,y,w,h as separate keys
        keys = ("x", "y", "w", "h")
        if all(k in meta for k in keys):
            try:
                bb = [float(meta["x"]), float(meta["y"]), float(meta["w"]), float(meta["h"])]
            except Exception:
                return None
        else:
            return None

    try:
        if isinstance(bb, dict):
            x = float(bb.get("x", 0.0))
            y = float(bb.get("y", 0.0))
            w = float(bb.get("w", 0.0))
            h = float(bb.get("h", 0.0))
        else:
            bb = list(bb)
            if len(bb) == 4:
                x, y, w, h = map(float, bb)
            else:
                return None

        # Normalize: if values look like x1,y1,x2,y2 (w/h > 1 and x2>x1), convert to w/h.
        if w > 1.0 and h > 1.0 and (w > x) and (h > y) and (w <= 4096) and (h <= 4096):
            # ambiguous; keep as is
            pass

        # Center bucket
        cx = x + (w / 2.0)
        cy = y + (h / 2.0)

        # If coords appear pixel-ish, we can't normalize reliably without frame size.
        # Use coarse buckets on raw scale: map to grid by log-ish compression.
        # If coords look like 0..1 range, use direct grid.
        if 0.0 <= cx <= 1.2 and 0.0 <= cy <= 1.2:
            bx = max(0, min(grid - 1, int(cx * grid)))
            by = max(0, min(grid - 1, int(cy * grid)))
        else:
            # pixel-ish: bucket by sqrt scale (stable-ish across resolutions)
            bx = max(0, min(grid - 1, int((cx ** 0.5) % grid)))
            by = max(0, min(grid - 1, int((cy ** 0.5) % grid)))

        # Size bucket
        area = max(0.0, float(w) * float(h))
        if area <= 0:
            sb = 0
        else:
            sb = int(min(9, max(0, (math.log10(area + 1e-6) + 1.0) * 2.0)))
        return f"b{bx}{by}s{sb}"
    except Exception:
        return None


def _stable_label(meta: Dict[str, Any]) -> Optional[str]:
    for k in ("label", "name", "cls", "class", "tag"):
        v = meta.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s[:64]
    return None


def _vector_fingerprint(v: List[float], n: int = 32, decimals: int = 2) -> str:
    """Coarse, repetition-friendly vector fingerprint.

    Rationale: vision/token has empty meta (no label/bbox). To measure repetition we
    need a stable key that tolerates small noise. We therefore:
      - take first `n` dims (default 32)
      - round to `decimals` (default 2)
    This is intentionally coarse (Stage A measure-only).
    """
    head = []
    n = max(4, min(84, int(n)))
    decimals = max(0, min(4, int(decimals)))
    fmt = "{:." + str(decimals) + "f}"
    for x in v[:n]:
        try:
            head.append(fmt.format(float(x)))
        except Exception:
            head.append(fmt.format(0.0))
    raw = ",".join(head)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _make_key(obj: Dict[str, Any], grid: int, fp_dims: int, fp_decimals: int) -> Tuple[str, Optional[str]]:
    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else {}
    label = _stable_label(meta)
    v = obj.get("v") or []
    v = v if isinstance(v, list) else []
    bb = _bbox_bucket(meta, grid) if isinstance(meta, dict) else None

    if label:
        if bb:
            return f"label:{label}|{bb}", label
        return f"label:{label}", label

    if v:
        fp = _vector_fingerprint(v, fp_dims, fp_decimals)
        if bb:
            return f"raw:{fp}|{bb}", None
        return f"raw:{fp}", None

    # worst-case: fallback to kind only
    kind = str(obj.get("kind", "vision")).strip() or "vision"
    return f"kind:{kind}", None


def _dbw_required() -> None:
    # DBWriter-first: no local writes
    try:
        if not db_writer_client.enabled():
            raise RuntimeError("DBWriter not enabled (set OROMA_DBW_ENABLE=1)")
        if not db_writer_client.ping(timeout_ms=500):
            raise RuntimeError("DBWriter not reachable (ping failed)")
    except Exception as e:
        raise RuntimeError(f"DBWriter required but not available: {e}")


def _write_stat(ts: int, series: str, value: float) -> None:
    # write into stats.db stats_points
    db_writer_client.exec("INSERT INTO stats_points(ts, series, value, src_table, src_id, meta, src_uid) VALUES (?, ?, ?, ?, ?, ?, ?)", [int(ts), str(series), float(value), "vision_binding_probe", 0, None, str(int(ts))], tag="binding.v.probe", priority="normal", timeout_ms=2000, expect="rowcount", db="stats")


def run_once(window_sec: int, limit_chains: int, max_runtime_s: int, origin: str, topk_labels: int, grid: int, fp_dims: int, fp_decimals: int) -> Dict[str, Any]:
    t0 = time.time()
    _dbw_required()

    now = int(time.time())
    since = int(now) - int(window_sec)
    limit_chains = max(1, int(limit_chains))
    topk_labels = max(1, min(25, int(topk_labels)))
    grid = max(4, min(16, int(grid)))
    fp_dims = max(4, min(84, int(fp_dims)))
    fp_decimals = max(0, min(4, int(fp_decimals)))

    # fetch bounded set
    rows = []
    try:
        with sql_manager.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, ts, blob FROM snapchains WHERE origin=? AND ts>=? ORDER BY id DESC LIMIT ?",
                (str(origin), int(since), int(limit_chains)),
            ).fetchall()
    except Exception as e:
        log_suppressed(logger="vision_binding_probe.fetch", key="vision_binding_probe.fetch", msg="fetch failed", exc=e)
        return {"ok": False, "error": "fetch_failed"}

    keys = Counter()
    labels = Counter()
    events = 0
    meta_empty = 0
    meta_has_any = 0
    meta_has_label = 0
    meta_has_bbox = 0

    for r in rows:
        if time.time() - t0 > float(max_runtime_s):
            break
        try:
            obj = _json_loads_blob(r["blob"])
            if not obj:
                continue
            m = obj.get("meta") if isinstance(obj.get("meta"), dict) else None
            if not m:
                meta_empty += 1
            else:
                meta_has_any += 1
                if any(k in m for k in ("label","name","cls","class","tag")):
                    meta_has_label += 1
                if ("bbox" in m) or all(k in m for k in ("x","y","w","h")):
                    meta_has_bbox += 1
            k, lab = _make_key(obj, grid, fp_dims, fp_decimals)
            keys[k] += 1
            if lab:
                labels[lab] += 1
            events += 1
        except Exception as e:
            log_suppressed(logger="vision_binding_probe.parse", key="vision_binding_probe.parse", msg="parse failed", exc=e)
            continue

    unique = len(keys)
    rep2 = sum(1 for _,c in keys.items() if c >= 2)
    rep3 = sum(1 for _,c in keys.items() if c >= 3)
    rep5 = sum(1 for _,c in keys.items() if c >= 5)
    unique_labels = len(labels)

    # write stats points
    ts = int(time.time())
    try:
        _write_stat(ts, "binding.v.events.count_24h", float(events))
        _write_stat(ts, "binding.v.meta.empty_24h", float(meta_empty))
        _write_stat(ts, "binding.v.meta.has_any_24h", float(meta_has_any))
        _write_stat(ts, "binding.v.meta.has_label_24h", float(meta_has_label))
        _write_stat(ts, "binding.v.meta.has_bbox_24h", float(meta_has_bbox))
        _write_stat(ts, "binding.v.fp.dims_24h", float(fp_dims))
        _write_stat(ts, "binding.v.fp.decimals_24h", float(fp_decimals))
        _write_stat(ts, "binding.v.keys.unique_24h", float(unique))
        _write_stat(ts, "binding.v.keys.repeat_ge_2_24h", float(rep2))
        _write_stat(ts, "binding.v.keys.repeat_ge_3_24h", float(rep3))
        _write_stat(ts, "binding.v.keys.repeat_ge_5_24h", float(rep5))
        _write_stat(ts, "binding.v.label.unique_24h", float(unique_labels))

        for lab, c in labels.most_common(topk_labels):
            safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(lab))[:48]
            _write_stat(ts, f"binding.v.top_label.{safe}_24h", float(c))
    except Exception as e:
        log_suppressed(logger="vision_binding_probe.dbw", key="vision_binding_probe.dbw", msg="stats_points write failed", exc=e)
        return {"ok": False, "error": "dbw_write_failed"}

    return {
        "ok": True,
        "window_sec": int(window_sec),
        "origin": str(origin),
        "scanned": int(len(rows)),
        "events": int(events),
        "unique_keys": int(unique),
        "repeat_ge_2": int(rep2),
        "repeat_ge_3": int(rep3),
        "repeat_ge_5": int(rep5),
        "unique_labels": int(unique_labels),
        "meta_empty": int(meta_empty),
        "meta_has_any": int(meta_has_any),
        "meta_has_label": int(meta_has_label),
        "meta_has_bbox": int(meta_has_bbox),
        "fp_dims": int(fp_dims),
        "fp_decimals": int(fp_decimals),
        "top_labels": labels.most_common(topk_labels),
        "dt_sec": round(time.time() - t0, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ORÓMA Vision Binding Probe (Stage A, measure-only)")
    ap.add_argument("--window-sec", type=int, default=_env_int("OROMA_VISION_BIND_WINDOW_SEC", 86400))
    ap.add_argument("--limit-chains", type=int, default=_env_int("OROMA_VISION_BIND_LIMIT_CHAINS", 5000))
    ap.add_argument("--max-runtime-s", type=int, default=_env_int("OROMA_VISION_BIND_MAX_RUNTIME_S", 30))
    ap.add_argument("--origin", default=_env_str("OROMA_VISION_BIND_ORIGIN", "vision/token"))
    ap.add_argument("--topk-labels", type=int, default=_env_int("OROMA_VISION_BIND_TOPK_LABELS", 8))
    ap.add_argument("--grid", type=int, default=_env_int("OROMA_VISION_BIND_GRID", 8))
    ap.add_argument("--fp-dims", type=int, default=_env_int("OROMA_VISION_BIND_FP_DIMS", 32))
    ap.add_argument("--fp-decimals", type=int, default=_env_int("OROMA_VISION_BIND_FP_DECIMALS", 2))
    args = ap.parse_args()

    try:
        res = run_once(args.window_sec, args.limit_chains, args.max_runtime_s, args.origin, args.topk_labels, args.grid, args.fp_dims, args.fp_decimals)
        print(json.dumps({"binding_stage": "A", "vision": res}, ensure_ascii=False))
        return 0 if res.get("ok") else 2
    except Exception as e:
        log_suppressed(logger="vision_binding_probe.error", key="vision_binding_probe.error", msg="vision binding probe failed", exc=e)
        print(json.dumps({"binding_stage": "A", "ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
