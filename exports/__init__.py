#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/exports/__init__.py
# Projekt: ORÓMA
# Version: v3.7 (final)
# Stand:   2025-09-29
#
# Zweck
# ─────
#   Zentrale Bündelung aller Export-/Import-Funktionen für ORÓMA. Dieses Paket
#   bietet eine einheitliche Programmierschnittstelle für:
#     • SnapChain-/Regelarchiv-Exporte (tar/zip-Bundles)
#     • SnapChain-Importe (Merge mit Deduplikation)
#     • Optionale Zielplattform-Exporte (Hailo, DeGirum)
#
# Export-Policy (Produktivleitplanken)
# ────────────────────────────────────
#   • Qualitätsfilter: exportiere nur Inhalte ≥ OROMA_EXPORT_QUALITY_THRESHOLD
#   • Alter/Abkühlzeit: erst nach OROMA_EXPORT_DELAY_DAYS (Default: 30)
#   • Nicht-destruktiv: SnapChains bleiben erhalten; „exported=1“ statt Löschen
#   • Herkunft: importierte Modelle werden mit origin_instance_id markiert
#   • Versionierung: Bundles werden mit Zeitstempel und Semver gekennzeichnet
#   • Datenschutz: keine Roh-Audio-/Videodaten im Modellbundle; nur abgeleitete
#     Features/Snaps/Regeln gemäß den Export-Implementierungen
#
# Relevante ENV-Variablen (siehe .env v3.7)
# ─────────────────────────────────────────
#   OROMA_EXPORT_DELAY_DAYS           (Default 30)
#   OROMA_EXPORT_QUALITY_THRESHOLD    (Default 0.7)
#   OROMA_EXPORT_DIR                  (Standard: /opt/ai/oroma/exports)
#   OROMA_IMPORT_DIR                  (Standard: /opt/ai/oroma/uploads)
#   OROMA_MAX_IMPORT_MB               (Größenlimit für Uploads)
#
# Enthaltene Module
# ─────────────────
#   - model_export    → SnapChain-/Regelarchiv-Exporte
#   - model_import    → SnapChain-Import (Merge, Dedupe, Herkunftsmarkierung)
#   - hailo_export    → (optional) Hailo-NPU-kompatible Exporte
#   - degirum_export  → (optional) DeGirum-SDK-kompatible Exporte
#
# Hinweise
# ────────
#   • Alle Untermodule werden „best effort“ geladen (Optionals sind erlaubt).
#   • Diese __init__ exportiert eine stabile, knappe API über __all__.
#   • Aufrufbeispiele:
#         from exports import export_snapchains, import_snapchains
#         export_snapchains(out_dir="/opt/ai/oroma/exports")
#         import_snapchains("/path/to/bundle.tar.gz")
#
# Lizenz
# ──────
#   MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

# --- Model-Export (SnapChains / Regelarchiv) ---------------------------------
try:
    from .model_export import create_export as export_snapchains        # Haupt-API (Export)
    from .model_export import list_candidates as check_export_eligibility
except Exception:  # ImportError u. a. bewusst tolerant
    export_snapchains = None  # type: ignore
    check_export_eligibility = None  # type: ignore

# --- Model-Import ------------------------------------------------------------
try:
    from .model_import import import_package as import_snapchains        # Haupt-API (Import)
except Exception:
    import_snapchains = None  # type: ignore

# --- Hailo-Export (optional) -------------------------------------------------
try:
    from .hailo_export import export_hailo_package as export_hailo_model
except Exception:
    export_hailo_model = None  # type: ignore

# --- DeGirum-Export (optional) ----------------------------------------------
try:
    from .degirum_export import export_to_degirum_zip as export_degirum_model
except Exception:
    export_degirum_model = None  # type: ignore

__all__ = [
    "export_snapchains",
    "check_export_eligibility",
    "import_snapchains",
    "export_hailo_model",
    "export_degirum_model",
]

# -----------------------------------------------------------------------------
# Debug / Selbsttest (nicht-produktiv, nur direkter Aufruf)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    print("🔎 ORÓMA Exports Package – Selbsttest")
    print("BASE_DIR =", os.path.dirname(os.path.abspath(__file__)))

    if export_snapchains:
        try:
            print("Test: export_snapchains (dry_run=True) →", export_snapchains(dry_run=True))
        except Exception as e:
            print("⚠️  export_snapchains Fehler:", e)
    else:
        print("⏭  export_snapchains nicht verfügbar (Optional).")

    if import_snapchains:
        try:
            print("Test: import_snapchains (dry_run=True) →",
                  import_snapchains("dummy_oroma_bundle.tar.gz", dry_run=True))
        except Exception as e:
            print("⚠️  import_snapchains Fehler:", e)
    else:
        print("⏭  import_snapchains nicht verfügbar (Optional).")

    if export_hailo_model:
        try:
            print("Test: export_hailo_model (simulate=True) →",
                  export_hailo_model("dummy.onnx", "dummy_calib", simulate=True))
        except Exception as e:
            print("⚠️  export_hailo_model Fehler:", e)
    else:
        print("⏭  export_hailo_model nicht verfügbar (Optional).")

    if export_degirum_model:
        try:
            print("Test: export_degirum_model →",
                  export_degirum_model(days=1, min_quality=0.1, max_items=1, out_dir="/tmp"))
        except Exception as e:
            print("⚠️  export_degirum_model Fehler:", e)
    else:
        print("⏭  export_degirum_model nicht verfügbar (Optional).")