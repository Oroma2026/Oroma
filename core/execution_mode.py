#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/execution_mode.py
# Projekt: ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:   Zentrale Ausführungsmodus- und Policy-Mutationsentscheidung
# Version: v0.1.0-vertical-proof-isolation
# Stand:   2026-07-13
#
# ZWECK / ARCHITEKTURROLLE
# ────────────────────────
# Dieses Modul ist die einzige gemeinsame Interpretation der systemweiten
# Ausführungsmodi. Es verhindert, dass einzelne Runner, Trainer, Shims oder
# manuell gestartete Tools Umgebungsvariablen unterschiedlich auslegen.
#
# Der Modus ``vertical_proof`` dient der kausalen Sterilisierung eines eng
# begrenzten Referenznachweises. Innerhalb des Proof-Scopes dürfen Sensorik,
# Spiele, Episoden, SnapChains, direkte Step-Credits, Gap-/Evidence-Analyse und
# die registrierte Policy Mutation Boundary weiterarbeiten. Historische oder
# unregistrierte Policy-Writer werden dagegen fail-closed blockiert.
#
# SICHERHEITS-HIERARCHIE
# ──────────────────────
#   OROMA_EXECUTION_MODE / Proof-Scope
#       -> dieses zentrale Modul
#       -> Orchestrator-Job-Gates
#       -> lokale Writer-Gates
#       -> gemeinsamer UniversalPolicy-Mutationspunkt
#       -> Policy Mutation Boundary als letzte Autorität
#
# KONFIGURATION
# ─────────────
# OROMA_EXECUTION_MODE
#   normal         regulärer historischer Betrieb (Default bei fehlender ENV)
#   evidence_only  Datenerzeugung erlaubt, Legacy-Policy-Mutation im Scope aus
#   vertical_proof kausal steriler End-to-End-Nachweis
#   maintenance    keine Policy-Mutation im konfigurierten Scope
#
# OROMA_VERTICAL_PROOF_NAMESPACE_ALLOWLIST
#   Kommagetrennte Namespace-Patterns. Default im vertical_proof-Modus:
#   ``game:snake``. Unterstützt exakte Werte und ``*``-Suffixe.
#
# OROMA_VERTICAL_PROOF_BOUNDARY_WRITER_ALLOWLIST
#   Registrierte Writer-IDs, die nur mit explizitem ``boundary_authorized=True``
#   eine Mutation im Proof-Scope durchführen dürfen.
#
# INVARIANTEN
# ───────────
# - Fehlende OROMA_EXECUTION_MODE-ENV bleibt rückwärtskompatibel ``normal``.
# - Ein unbekannter expliziter Modus ist ``invalid`` und blockiert Mutationen.
# - Im Proof-Scope ist jeder unregistrierte/Legacy-Writer fail-closed.
# - Entscheidungen sind strukturierte, auditierbare Datenobjekte.
# - Dieses Modul schreibt niemals Datenbank-, Queue- oder Policy-Daten.
# =============================================================================

from __future__ import annotations

from dataclasses import asdict, dataclass
import fnmatch
import os
from typing import Any, Dict, Iterable, List, Optional

VERSION = "v0.1.0-vertical-proof-isolation"
_VALID_MODES = {"normal", "evidence_only", "vertical_proof", "maintenance"}
_DEFAULT_PROOF_SCOPE = "game:snake"
_DEFAULT_BOUNDARY_WRITERS = "writer:core.gap_policy_mini_write:v0.3"


def _csv(name: str, default: str = "") -> List[str]:
    raw = str(os.environ.get(name, default) or "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _matches(value: str, patterns: Iterable[str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    for pattern in patterns:
        p = str(pattern or "").strip()
        if not p:
            continue
        if fnmatch.fnmatchcase(text, p):
            return True
    return False


def get_execution_mode() -> str:
    raw = str(os.environ.get("OROMA_EXECUTION_MODE", "") or "").strip().lower()
    if not raw:
        return "normal"
    return raw if raw in _VALID_MODES else "invalid"


def proof_namespace_allowlist() -> List[str]:
    mode = get_execution_mode()
    default = _DEFAULT_PROOF_SCOPE if mode in {"vertical_proof", "evidence_only", "maintenance"} else ""
    return _csv("OROMA_VERTICAL_PROOF_NAMESPACE_ALLOWLIST", default)


def boundary_writer_allowlist() -> List[str]:
    return _csv("OROMA_VERTICAL_PROOF_BOUNDARY_WRITER_ALLOWLIST", _DEFAULT_BOUNDARY_WRITERS)


def namespace_in_proof_scope(namespace: str) -> bool:
    return _matches(namespace, proof_namespace_allowlist())


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    execution_mode: str
    writer_id: str
    namespace: str
    mutation_type: str
    in_proof_scope: bool
    boundary_required: bool
    boundary_authorized: bool
    module_version: str = VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def policy_mutation_decision(
    *,
    writer_id: str,
    namespace: str,
    mutation_type: str = "UPDATE_RULE_STATISTICS",
    boundary_authorized: bool = False,
) -> ExecutionDecision:
    mode = get_execution_mode()
    wid = str(writer_id or "").strip() or "unregistered"
    ns = str(namespace or "").strip()
    mtype = str(mutation_type or "POLICY_MUTATION").strip()
    in_scope = namespace_in_proof_scope(ns)

    if mode == "normal":
        return ExecutionDecision(True, "normal_mode", mode, wid, ns, mtype, in_scope, False, bool(boundary_authorized))

    if mode == "invalid":
        return ExecutionDecision(False, "invalid_execution_mode", mode, wid, ns, mtype, in_scope, True, bool(boundary_authorized))

    if not in_scope:
        return ExecutionDecision(True, "namespace_outside_proof_scope", mode, wid, ns, mtype, False, False, bool(boundary_authorized))

    registered_boundary = _matches(wid, boundary_writer_allowlist())
    if registered_boundary and bool(boundary_authorized):
        return ExecutionDecision(True, "registered_boundary_authorized", mode, wid, ns, mtype, True, True, True)

    if registered_boundary and not boundary_authorized:
        return ExecutionDecision(False, "boundary_authorization_missing", mode, wid, ns, mtype, True, True, False)

    if wid == "unregistered" or wid.startswith("unregistered"):
        reason = "unregistered_writer_in_proof_scope"
    else:
        reason = "legacy_policy_mutation_blocked_in_proof_scope"
    return ExecutionDecision(False, reason, mode, wid, ns, mtype, True, True, bool(boundary_authorized))


def legacy_policy_training_allowed(*, writer_id: str, namespace: str) -> ExecutionDecision:
    return policy_mutation_decision(
        writer_id=writer_id,
        namespace=namespace,
        mutation_type="LEGACY_POLICY_TRAINING",
        boundary_authorized=False,
    )


def evidence_collection_allowed(*, namespace: str) -> ExecutionDecision:
    mode = get_execution_mode()
    ns = str(namespace or "").strip()
    in_scope = namespace_in_proof_scope(ns)
    if mode == "invalid":
        return ExecutionDecision(False, "invalid_execution_mode", mode, "evidence_collector", ns, "EVIDENCE_COLLECTION", in_scope, False, False)
    return ExecutionDecision(True, "evidence_collection_allowed", mode, "evidence_collector", ns, "EVIDENCE_COLLECTION", in_scope, False, False)


def execution_mode_status() -> Dict[str, Any]:
    mode = get_execution_mode()
    return {
        "version": VERSION,
        "execution_mode": mode,
        "valid": mode in _VALID_MODES,
        "proof_namespace_allowlist": proof_namespace_allowlist(),
        "boundary_writer_allowlist": boundary_writer_allowlist(),
        "legacy_policy_training_blocked_in_scope": mode in {"evidence_only", "vertical_proof", "maintenance"},
        "evidence_collection_allowed": mode != "invalid",
    }
