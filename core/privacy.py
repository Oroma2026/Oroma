#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/privacy.py
# Projekt:   ORÓMA (Offline-First · Headless · Privacy-by-Default)
# Modul:     PII Guard – Redaction + ASR-RAM-Buffer (TTL) ohne Persistenz (Default)
# Version:   v3.7.3
# Stand:     2026-01-11
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ÜBERBLICK / ZWECK
# ─────────────────
# Dieses Modul implementiert eine extrem leichtgewichtige Privacy-Schicht für ORÓMA:
#   1) Redaction: Ersetzt offensichtliche personenbezogene Daten (PII) in Strings
#      (z. B. Logs, UI-Anzeigen, Debug-Ausgaben).
#   2) ASR Buffer: Hält erkannte ASR-Texte im RAM-Puffer mit TTL (Time-To-Live),
#      um kurzfristige UI-/Kontext-Anforderungen zu bedienen, ohne Klartext dauerhaft
#      zu persistieren.
#
# Grundsatz (Privacy-by-Default):
# - Standardverhalten: kein dauerhafter Klartext (kein DB-Write).
# - Alles, was gespeichert werden müsste, soll später über einen expliziten,
#   sicheren Persistenzpfad erfolgen (Policy/Consent/Audit), NICHT stillschweigend.
#
# HEADLESS / PRODUKTIONS-PRINZIPIEN
# ─────────────────────────────────
# - Headless: keine externen Abhängigkeiten außer stdlib.
# - Schnell: regex-basiert, bewusst konservativ (lieber zu viel redigieren als zu wenig).
# - Stabil: Puffer ist klein (deque), O(n) Cleanup pro ingest ist akzeptabel, weil n klein bleibt.
#
# REDACTION (PII-PATTERNS)
# ───────────────────────
# Das Modul nutzt bewusst einfache Regex-Muster (kein NLP/ML):
#   - E-Mail:
#       [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}
#     → "[EMAIL]"
#   - Phone (sehr grob, um +49 151 2345678 zu treffen; min length Schutz):
#       \+?\d[\d\s\-]{6,}
#     → "[PHONE]"
#   - IBAN (Ländercode + 2 Prüfziffern + alphanumerisch):
#       [A-Z]{2}\d{2}[A-Z0-9]{1,30}
#     → "[IBAN]"
#
# Hinweis:
# - Muster sind absichtlich „konservativ“ und können False Positives produzieren.
# - Ziel ist Schutz in Logs/UI, nicht forensische Erkennung.
#
# RAM-PUFFER (ASR TTL)
# ────────────────────
# Datenstruktur:
#   _buffer: collections.deque[tuple[int, str]]
#   - speichert (ts_unix, text) in RAM
#   - wird bei ingest_asr_text() um neue Texte ergänzt
#   - purge erfolgt bei jedem ingest anhand cutoff = now - BUF_TTL
#
# Ausgaben:
# - recent_buffer() gibt standardmäßig die letzten 5 Einträge (String) zurück
#   (konkateniert), damit UI schnell einen kurzen Kontext anzeigen kann.
#
# ENV / KONFIGURATION (EXAKT IM CODE)
# ───────────────────────────────────
# OROMA_PII_SAFE (Default: "1" → True)
#   - Wenn True:
#       ingest_asr_text() redigiert sofort (redact), bevor es in den Puffer geht
#
# OROMA_LOG_REDACT (Default: "1" → True)
#   - Wenn True:
#       redact(s) ersetzt PII
#   - Wenn False:
#       redact(s) gibt s unverändert zurück
#
# OROMA_ASR_BUFFER_TTL (Default: "300")
#   - TTL in Sekunden für RAM-Puffer
#
# OROMA_ASR_SAVE_TEXT (Default: "0" → False)
#   - Wenn True:
#       ingest_asr_text() würde prinzipiell Persistenz erlauben,
#       ist aber im aktuellen Code absichtlich NO-OP (kein DB-Write)
#       und kehrt direkt zurück.
#   - Das ist ein „Schalter für spätere Erweiterung“, aber Default bleibt: keine Persistenz.
#
# ÖFFENTLICHE API (FUNKTIONEN)
# ────────────────────────────
# redact(s: str) -> str
#   - redigiert PII in einem String, aber nur wenn OROMA_LOG_REDACT aktiv ist
#
# ingest_asr_text(text: str) -> None
#   - nimmt frischen ASR-Text entgegen
#   - wenn OROMA_PII_SAFE aktiv: text wird sofort redigiert
#   - wenn OROMA_ASR_SAVE_TEXT aktiv: KEIN Persistenzpfad (absichtlich leer), return
#   - sonst: speichert text in _buffer + TTL-Purge
#
# recent_buffer() -> str
#   - liefert einen kompakten RAM-Kontext (letzte 5 Einträge)
#
# SICHERHEIT / GOVERNANCE
# ───────────────────────
# - Dieses Modul ist bewusst nicht „perfekt“, sondern ein pragmatischer Schutz:
#   • verhindert, dass typische PII in Logs/Debug/UI auftaucht
#   • verhindert standardmäßig DB-Persistenz von Klartext-ASR
# - Wenn später Persistenz benötigt wird, muss das:
#   • explizit implementiert werden (separates Modul/DB-Tabelle)
#   • auditierbar sein (Policy + Zustimmung + Retention)
#   • idealerweise zusätzlich verschlüsselt (at rest) und getrennt von SnapChains
#
# PRODUKTIONSINVARIANTEN (BITTE NICHT „VEREINFACHEN“)
# ───────────────────────────────────────────────────
# - Default bleibt: OROMA_ASR_SAVE_TEXT=0 → keine Klartext-Persistenz.
# - ingest_asr_text() muss in jedem Fall schnell und crash-sicher sein (kein IO/DB).
# - Redaction bleibt leichtgewichtig (regex), damit sie überall genutzt werden kann.
# - recent_buffer() bleibt kurz (letzte 5), damit UI/Logs nicht „aus Versehen“ Datenmengen leaken.
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations

import os
import re
import time
import collections

__all__ = ["PII_SAFE", "redact", "ingest_asr_text", "recent_buffer"]

# --------------------------------------------------------------------------- #
# Konfiguration über ENV
# --------------------------------------------------------------------------- #

PII_SAFE = os.getenv("OROMA_PII_SAFE", "1").lower() in ("1", "true", "yes")
SAVE_TEXT = os.getenv("OROMA_ASR_SAVE_TEXT", "0").lower() in ("1", "true", "yes")
BUF_TTL = int(float(os.getenv("OROMA_ASR_BUFFER_TTL", "300")))
LOG_REDACT = os.getenv("OROMA_LOG_REDACT", "1").lower() in ("1", "true", "yes")

# --------------------------------------------------------------------------- #
# Einfache PII-Pattern (leichtgewichtig, bewusst konservativ)
# --------------------------------------------------------------------------- #

_PAT_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Telefonnummern: bewusst grob, um +49 123 456789 abzudecken; vermeidet sehr kurze Treffer
_PAT_PHONE = re.compile(r"\+?\d[\d\s\-]{6,}")
# IBAN: Länderkennung + 2 Prüfziffern + alphanumerisch
_PAT_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{1,30}")

# RAM-Puffer (kein Persist)
_buffer: "collections.deque[tuple[int, str]]" = collections.deque()

# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def redact(s: str) -> str:
    """
    Reduziert offensichtliche PII in einem String (Log/Anzeige).
    Wirkt nur, wenn LOG_REDACT=1; sonst passtredigiert die Eingabe unverändert.
    """
    if not LOG_REDACT:
        return s
    s = _PAT_EMAIL.sub("[EMAIL]", s)
    s = _PAT_PHONE.sub("[PHONE]", s)
    s = _PAT_IBAN.sub("[IBAN]", s)
    return s


def ingest_asr_text(text: str) -> None:
    """
    Nimmt frischen ASR-Text entgegen:
      • Bei PII_SAFE → sofortige Redaction.
      • Standard: kein DB-Write; Text verbleibt nur im RAM-Puffer (TTL).
    """
    t = redact(text) if PII_SAFE else text

    if SAVE_TEXT:
        # Absichtlich leer gelassen: Hier KEIN DB-Write per Default.
        # Wenn später gewünscht, kann ein sicherer Persist-Pfad ergänzt werden.
        return

    now = int(time.time())
    _buffer.append((now, t))
    cutoff = now - BUF_TTL
    # Abgelaufene Einträge entfernen (in-place, O(n) pro Aufruf, n ist klein)
    while _buffer and _buffer[0][0] < cutoff:
        _buffer.popleft()


def recent_buffer() -> str:
    """
    Liefert eine kurze, redigierte Kontext-Konkatenation der letzten
    ASR-Schnipsel (max. 5), ausschließlich aus dem RAM-Puffer.
    """
    return " ".join([x[1] for x in list(_buffer)[-5:]])


# --------------------------------------------------------------------------- #
# Optionaler Selftest
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    os.environ.setdefault("OROMA_PII_SAFE", "1")
    os.environ.setdefault("OROMA_LOG_REDACT", "1")
    sample = "Mail: max@example.com Tel: +49 151 2345678 IBAN DE12 3456 7890 1234 5678 90"
    print("redact:", redact(sample))
    ingest_asr_text("Hallo, das ist ein kurzer Test.")
    ingest_asr_text(sample)
    print("recent_buffer:", recent_buffer())