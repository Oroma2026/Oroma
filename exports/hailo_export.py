#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/exports/hailo_export.py
# Projekt:   ORÓMA – Offline-First Edge-KI (Headless · Raspberry Pi · optional NPU)
# Modul:     Hailo Export Pipeline – Paketordner + optional .hef Build + Registry-Eintrag
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul erzeugt ein „Hailo-Paket“ aus einem ONNX-Modell + Kalibrier-Daten:
#   1) erstellt einen Paketordner:  <out_root>/<name>_<timestamp>/
#   2) schreibt Metadateien:
#        - policy.json
#        - meta.json
#        - preproc.json
#        - postproc.json
#        - labels.txt (optional)
#        - compile.json (Ergebnis/Logs)
#   3) versucht optional ein **model.hef** über die Hailo-Toolchain zu bauen
#   4) registriert einen erfolgreichen Build in der ORÓMA Model-Registry (SQLite)
#
# Wichtig (Einordnung in v3.7.3):
# - Die Datei ist bewusst „optional“: ohne Hailo-SDK/Toolchain läuft sie im
#   Simulationsmodus bzw. liefert compile.ok=False, ohne ORÓMA zu crashen.
# - Sie ist eine Plattform-Export-Brücke für Vision-Modelle (z. B. YOLO-Familien).
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine GUI/Qt Abhängigkeiten.
# - Toolchain-optional: wenn keine Hailo-Tools gefunden werden, wird „Simulation“
#   genutzt oder compile.ok=False zurückgegeben.
# - Paketordner ist deterministic & auditierbar: alle Inputs/Hashes werden dokumentiert.
# - Registry-Schreiben ist best effort: Registry-Fehler dürfen Export nicht hart abbrechen.
#
# DB / REGISTRY-INTEGRATION
# ─────────────────────────
# Dieses Modul nutzt core.sql_manager:
#   - ensure_schema()         → stellt DB-Schema sicher
#   - register_model(...)     → legt Eintrag in models-Registry an
#
# Registry-Eintrag passiert nur wenn:
#   compile.ok == True UND produced_hef vorhanden
#
# Hinweis: Dieses Modul setzt version in register_model(...) historisch auf "v3.5".
# In v3.7.3 bleibt das Verhalten aus Kompatibilitätsgründen bestehen; die Paket-Metadaten
# (policy/meta) enthalten dennoch die relevanten Infos für spätere Verarbeitung.
#
# PAKETORDNER-STRUKTUR
# ────────────────────
# pack_dir = <out_root>/<base_name>_<timestamp>/
#
# Dateien:
#   policy.json    → Governance/Policy (min_quality/min_age_days + created_utc + flags)
#   meta.json      → Modell-Metadaten (task/family/input_size + absolute paths + hashes)
#   preproc.json   → Preprocessing Defaults oder Caller-Werte (letterbox/divisor/mean)
#   postproc.json  → Postprocessing Defaults oder Caller-Werte (nms_iou/conf_thres)
#   labels.txt     → optional, falls labels_path gesetzt
#   model.hef      → optional, nur bei erfolgreichem Build
#   compile.json   → { ok, hef_path, log }
#
# HASHING / REPRODUZIERBARKEIT
# ────────────────────────────
# meta.json enthält:
#   - source_hash: SHA-1 des ONNX-Files
#   - calib_hash:  SHA-1 über alle Dateien im calib_dir (rekursiv, file-content)
#
# Dadurch kann ORÓMA erkennen, ob ein Paket exakt aus denselben Inputs erzeugt wurde.
#
# COMPILE / TOOLCHAIN-DETEKTION
# ─────────────────────────────
# Intern:
#   _find_hailo_cmd() sucht nach möglichen Toolchain-Commands:
#     - hailomz
#     - hailo8
#     - hailo_compile
#   Optional wird ein Shell-Wrapper über „bash -lc“ verwendet, um ENV sauber zu setzen.
#
# Compile-Call:
#   _compile_with_hailo_toolchain(onnx_path, calib_dir, out_hef_path, family, input_size)
# liefert:
#   (ok: bool, produced_hef: Optional[str], log_text: str)
#
# Simulation:
#   simulate=True → (False, None, "[SIM] ...") und compile.json dokumentiert das.
#
# ÖFFENTLICHE API (STABIL)
# ───────────────────────
# export_hailo_package(
#   *,
#   onnx_path: str,
#   calib_dir: str,
#   out_root: str,
#   name: Optional[str] = None,
#   min_quality: float = 0.0,     # wird in policy.json dokumentiert
#   min_age_days: int = 0,        # wird in policy.json dokumentiert
#   task: str = "detector",
#   family: str = "yolov8n",
#   input_size: str = "640x640",
#   labels_path: Optional[str] = None,
#   preproc: Optional[Dict] = None,
#   postproc: Optional[Dict] = None,
#   simulate: bool = False,
#   archive_older_policy: bool = False  # wird in policy.json dokumentiert
# ) -> Dict[str, Any]
#
# Rückgabe enthält u. a.:
#   {
#     "ok": True/False,
#     "out_dir": "<pack_dir>",
#     "compile": {"ok": bool, "hef_path": <str|None>, "log": <str>},
#     "registry_id": <int|None>,
#     "meta": {...},
#     "policy": {...}
#   }
#
# PRE/POSTPROC DEFAULTS
# ─────────────────────
# Wenn preproc/postproc nicht gesetzt sind:
#   preproc  = {"letterbox": True, "divisor": 255.0, "mean": [0,0,0]}
#   postproc = {"nms_iou": 0.5, "conf_thres": 0.25}
#
# ENV (NUR INTERN FÜR COMPILE SHELL)
# ──────────────────────────────────
# Für den Compile-Subprozess werden ENV-Variablen gesetzt (innerhalb des Prozesses):
#   - OROMA_ONNX
#   - OROMA_CALIB
#   - OROMA_OUT_HEF
#   - OROMA_FAMILY
#   - OROMA_INPUT_SIZE
# (Diese sind keine globalen ORÓMA-Settings, sondern Debug/Glue für Toolchain-Calls.)
#
# CLI (OPTIONAL)
# ──────────────
# python3 /opt/ai/oroma/exports/hailo_export.py \
#   --onnx /pfad/model.onnx \
#   --calib /pfad/calib_dir \
#   --out /opt/ai/oroma/exports_hailo \
#   --task detector --family yolov8n --input-size 640x640 \
#   --labels /pfad/labels.txt
#
# Simulation (ohne Toolchain):
#   python3 /opt/ai/oroma/exports/hailo_export.py --onnx ... --calib ... --out ... --simulate
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT BRECHEN)
# ────────────────────────────────────────────
# - Paketordner muss alle Metadateien schreiben (policy/meta/preproc/postproc/compile).
# - Simulation muss ohne Hailo-SDK funktionieren (compile.ok=False, aber out_dir existiert).
# - Registry-Insert nur bei echtem Build-Erfolg (ok + produced_hef).
# - Hashing muss stabil bleiben (source_hash/calib_hash) für Repro/Audit.
# - Toolchain-Detektion muss tolerant bleiben (nicht crashen, sondern sauber loggen).
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import json
import shutil
import hashlib
import subprocess
from typing import Any, Dict, Optional, Tuple, List

# DB-Registry (v3.5)
try:
    from core.sql_manager import ensure_schema, register_model
except Exception as e:
    raise RuntimeError("core.sql_manager.register_model/ensure_schema erforderlich") from e


# -----------------------------------------------------------------------------
# Hilfsfunktionen
# -----------------------------------------------------------------------------

def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _ts_for_path() -> str:
    # Dateinamensfreundlicher Timestamp, z.B. 2025-09-21T13:05:44Z -> 20250921T130544Z
    return _now_iso().replace("-", "").replace(":", "")


def _sha1(path: str) -> str:
    sha = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _collect_calib_hash(calib_dir: str) -> str:
    h = hashlib.sha1()
    for root, _, files in os.walk(calib_dir):
        for name in sorted(files):
            p = os.path.join(root, name)
            try:
                with open(p, "rb") as f:
                    # nur kleine Chunks, damit große Dirs ok sind
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except Exception:
                continue
    return h.hexdigest()


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _safe_copy(src: Optional[str], dst: str) -> Optional[str]:
    if not src:
        return None
    if not os.path.exists(src):
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


# -----------------------------------------------------------------------------
# Hailo-Toolchain
# -----------------------------------------------------------------------------

def _detect_hailo_compile_cmd() -> Optional[List[str]]:
    """
    Versucht eine Compile-CLI zu finden.
    - bevorzugt $HAILO_COMPILE_CMD (ganze Shell-Zeile)
    - ansonsten eher generische Binaries ('hailomz', 'hailo8', 'hailo_compile')
    """
    env_cmd = os.getenv("HAILO_COMPILE_CMD")
    if env_cmd:
        # komplette Shell-Zeile über bash -lc
        return ["bash", "-lc", env_cmd]

    for cand in ("hailomz", "hailo8", "hailo_compile"):
        if shutil.which(cand):
            return [cand]
    return None


def _compile_with_hailo_toolchain(
    onnx_path: str,
    calib_dir: str,
    out_hef_path: str,
    family: str,
    input_size: str,
) -> Tuple[bool, Optional[str], str]:
    """
    Versucht, eine Hailo-Compile-CLI aufzurufen und .hef zu erzeugen.
    Rückgabe: (ok, hef_path, log_text)
    """
    cmd = _detect_hailo_compile_cmd()
    logs: List[str] = []

    if cmd is None:
        logs.append("[WARN] Keine Hailo-Compile-CLI gefunden (HAILO_COMPILE_CMD / hailomz / hailo8 / hailo_compile).")
        return (False, None, "\n".join(logs))

    try:
        if cmd[0] in ("hailomz", "hailo8", "hailo_compile"):
            # Generischer Beispielaufruf – ggf. an eigene Toolchain anpassen.
            args = cmd + [
                "--onnx", onnx_path,
                "--calib", calib_dir,
                "--output", out_hef_path,
                "--family", family,
                "--input-size", input_size,
            ]
            p = subprocess.run(args, capture_output=True, text=True, check=False)
            logs.append(p.stdout or "")
            logs.append(p.stderr or "")
        else:
            # $HAILO_COMPILE_CMD via Shell – Parameter per ENV anhängen
            env = os.environ.copy()
            env["OROMA_ONNX"] = onnx_path
            env["OROMA_CALIB"] = calib_dir
            env["OROMA_OUT_HEF"] = out_hef_path
            env["OROMA_FAMILY"] = family
            env["OROMA_INPUT_SIZE"] = input_size
            p = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
            logs.append(p.stdout or "")
            logs.append(p.stderr or "")
    except Exception as e:
        logs.append(f"[ERR] Compile-Aufruf fehlgeschlagen: {type(e).__name__}: {e}")
        return (False, None, "\n".join(logs))

    # Erfolg prüfen
    if os.path.exists(out_hef_path) and os.path.getsize(out_hef_path) > 0:
        return (True, out_hef_path, "\n".join(logs))

    logs.append(f"[WARN] .hef nicht erzeugt: {out_hef_path}")
    return (False, None, "\n".join(logs))


# -----------------------------------------------------------------------------
# Öffentliche API (test-kompatibel)
# -----------------------------------------------------------------------------

def export_hailo_package(
    *,
    onnx_path: str,
    calib_dir: str,
    out_root: str,
    name: Optional[str] = None,
    min_quality: float = 0.0,       # wird nur in policy.json dokumentiert
    min_age_days: int = 0,          # wird nur in policy.json dokumentiert
    task: str = "detector",
    family: str = "yolov8n",
    input_size: str = "640x640",
    labels_path: Optional[str] = None,
    preproc: Optional[Dict[str, Any]] = None,
    postproc: Optional[Dict[str, Any]] = None,
    simulate: bool = False,
    archive_older_policy: bool = False
) -> Dict[str, Any]:
    """
    Erzeugt ein Paketverzeichnis mit Metadateien und optional .hef.
    - Simulation: compile.ok=False, kein .hef, registry_id=None
    - Real: bei Erfolg .hef + Registry-Eintrag
    Rückgabe (u. a.):
      - out_dir (Pfad)
      - compile: { ok, hef_path, log }
      - registry_id (oder None)
    """
    # DB-Schema sicherstellen (Registry)
    ensure_schema()

    # Eingaben prüfen
    if not os.path.isfile(onnx_path):
        raise FileNotFoundError(f"ONNX nicht gefunden: {onnx_path}")
    if not os.path.isdir(calib_dir):
        raise FileNotFoundError(f"Kalibrier-Verzeichnis nicht gefunden: {calib_dir}")

    os.makedirs(out_root, exist_ok=True)

    # Paketordner
    base_name = name or os.path.splitext(os.path.basename(onnx_path))[0]
    ts = _ts_for_path()  # z. B. 20250921T130544Z
    pack_dir = os.path.join(out_root, f"{base_name}_{ts[:15]}")
    os.makedirs(pack_dir, exist_ok=True)

    # Policy/Meta
    policy = {
        "min_quality": float(min_quality),
        "min_age_days": int(min_age_days),
        "created_utc": _now_iso(),
        "archive_older_policy": bool(archive_older_policy),
    }
    meta = {
        "task": task,
        "family": family,
        "input_size": input_size,
        "onnx_path": os.path.abspath(onnx_path),
        "calib_dir": os.path.abspath(calib_dir),
        "source_hash": _sha1(onnx_path),
        "calib_hash": _collect_calib_hash(calib_dir),
    }
    _write_json(os.path.join(pack_dir, "policy.json"), policy)
    _write_json(os.path.join(pack_dir, "meta.json"), meta)

    # Pre/Post/Labels – Standardwerte, falls nicht gesetzt
    if preproc is None:
        preproc = {"letterbox": True, "divisor": 255.0, "mean": [0, 0, 0]}
    if postproc is None:
        postproc = {"nms_iou": 0.5, "conf_thres": 0.25}
    _write_json(os.path.join(pack_dir, "preproc.json"), preproc)
    _write_json(os.path.join(pack_dir, "postproc.json"), postproc)
    if labels_path:
        _safe_copy(labels_path, os.path.join(pack_dir, "labels.txt"))

    # Compile (oder Simulation)
    hef_path = os.path.join(pack_dir, "model.hef")
    if simulate:
        ok, produced_hef, log_text = (False, None, "[SIM] Simulation aktiv – keine .hef erzeugt.")
    else:
        ok, produced_hef, log_text = _compile_with_hailo_toolchain(
            onnx_path=onnx_path,
            calib_dir=calib_dir,
            out_hef_path=hef_path,
            family=family,
            input_size=input_size,
        )

    compile_info = {"ok": bool(ok), "hef_path": produced_hef, "log": log_text}
    _write_json(os.path.join(pack_dir, "compile.json"), compile_info)

    # Registry-Eintrag, falls Build erfolgreich
    registry_id: Optional[int] = None
    if ok and produced_hef:
        try:
            pre_str = json.dumps(preproc, ensure_ascii=False)
            post_str = json.dumps(postproc, ensure_ascii=False)
            labels_txt_path = os.path.join(pack_dir, "labels.txt") if os.path.exists(os.path.join(pack_dir, "labels.txt")) else None

            registry_id = register_model(
                task=task,
                path=produced_hef,                # nicht im Schema, aber wir geben path faktisch als hef weiter
                family=family,
                version="v3.5",
                input_size=input_size,
                preproc_json=pre_str,
                postproc_json=post_str,
                labels_txt=labels_txt_path,
                hef_path=produced_hef,
                source_hash=meta["source_hash"],
                calib_hash=meta["calib_hash"],
                status="active",
            )
        except Exception:
            # Registry schluckt Fehler (Tests verlangen None bei Simulation; im Realfall soll nicht crashen)
            registry_id = None

    return {
        "out_dir": pack_dir,
        "policy": policy,
        "meta": meta,
        "compile": compile_info,
        "registry_id": registry_id,
    }


# -----------------------------------------------------------------------------
# CLI (optional)
# -----------------------------------------------------------------------------

def _parse_cli():
    import argparse
    ap = argparse.ArgumentParser(description="ORÓMA v3.5 – Hailo-Export (.hef + Registry)")
    ap.add_argument("--onnx", required=True, help="Pfad zum ONNX-Modell")
    ap.add_argument("--calib", required=True, help="Kalibrier-Verzeichnis")
    ap.add_argument("--out", required=True, help="Root-Ausgabeverzeichnis")
    ap.add_argument("--name", default=None, help="Paketname (Default: ONNX-Basisname)")
    ap.add_argument("--task", default="detector", help="Task (z. B. detector, embedder)")
    ap.add_argument("--family", default="yolov8n", help="Familie/Backbone-Bezeichnung")
    ap.add_argument("--input-size", default="640x640", help="Eingabegröße, z. B. 640x640")
    ap.add_argument("--labels", default=None, help="Pfad zu labels.txt (optional)")
    ap.add_argument("--preproc", default=None, help="JSON-String für preproc (optional)")
    ap.add_argument("--postproc", default=None, help="JSON-String für postproc (optional)")
    ap.add_argument("--min-quality", type=float, default=0.0, help="Policy-Min-Qualität (Meta)")
    ap.add_argument("--min-age-days", type=int, default=0, help="Policy-Min-Alter (Meta)")
    ap.add_argument("--simulate", action="store_true", help="Toolchain simulieren (keine .hef erzeugen)")
    ap.add_argument("--archive-older-policy", action="store_true", help="Nur Policy-Flag (keine Aktion)")
    return ap.parse_args()


def main():
    args = _parse_cli()
    pre = json.loads(args.preproc) if args.preproc else None
    post = json.loads(args.postproc) if args.postproc else None

    result = export_hailo_package(
        onnx_path=args.onnx,
        calib_dir=args.calib,
        out_root=args.out,
        name=args.name,
        min_quality=args.min_quality,
        min_age_days=args.min_age_days,
        task=args.task,
        family=args.family,
        input_size=args.input_size,
        labels_path=args.labels,
        preproc=pre,
        postproc=post,
        simulate=bool(args.simulate),
        archive_older_policy=bool(args.archive_older_policy),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()