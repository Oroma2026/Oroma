#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/snapchain_adapters.py
# Projekt:   ORÓMA (Offline-Realtime-Organic-Memory-AI)
# Modul:     SnapChain Adapter Layer – read-only Normalisierung historischer
#            SnapChains, moderner Game-Traces und Token-/Vector-Blobs
# Version:   v0.1.0-readonly-audit
# Stand:     2026-07-05
# Autor:     ORÓMA Project
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# ORÓMA speichert in der Tabelle `snapchains` mehrere Blob-Formate:
#   1) native SnapChain-Serialisierungen mit `patterns` / `metadata`,
#   2) kompakte Game-Traces mit `steps` / `state_hash` / `action` / `feat`,
#   3) Token-/Vector-Blobs mit `v`, `vector` oder `features`,
#   4) historische oder unbekannte Formate.
#
# Der bisherige Dream-/Replay-Pfad lädt viele dieser Blobs über
# `SnapChain.from_blob()`. Das ist für native SnapChains korrekt, macht aber
# moderne Game-Traces häufig zu leeren oder nicht verwertbaren Chains, obwohl die
# Daten im Blob vorhanden sind. Dieses Modul ist eine neutrale, read-only
# Normalisierungsschicht zwischen DB-Blob und höheren Verbrauchern wie:
#   - DreamWorker / Replay / Forgetting-Audit,
#   - PolicyEngine-Audit,
#   - Status-/Matrix-Tools.
#
# WICHTIGE ARCHITEKTURENTSCHEIDUNG
# ───────────────────────────────
# Dieses Modul schreibt NICHT in die Datenbank und verändert keine Policy-Regeln.
# Es erzeugt nur eine `NormalizedSnapTrace`-Sicht auf vorhandene Blob-Daten. Damit
# bleibt Blob-Formatlogik zentral, statt in DreamWorker, PolicyEngine und Tools
# mehrfach auseinanderzulaufen.
#
# POLICY VS. DREAM – GETRENNTE KRITERIEN
# ─────────────────────────────────────
# `is_policy_trainable(trace)` und `is_dream_processable(trace)` sind absichtlich
# getrennt:
#   - Policy-Training braucht mindestens state_hash + action + outcome/result.
#   - Dream-Verarbeitung braucht mindestens echte numerische Features/Centroid.
# Ein Trace kann policy-trainierbar sein, ohne dream-processable zu sein.
#
# PRODUKTIONSINVARIANTEN
# ─────────────────────
# - Read-only: keine SQLite-Writes, kein DBWriter, keine Mutation.
# - Headless: keine GUI/Qt/Wayland/X11-Abhängigkeiten.
# - Keine stillen Fehler: unbekannte Formate liefern skip_reason.
# - Robust gegen bytes/str/dict, JSON-Fehler und optionale Altformate.
# - Keine Imports schwerer ML-Bibliotheken.
#
# =============================================================================
# END HEADER
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import zlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class NormalizedSnapTrace:
    """Neutrale, read-only Sicht auf eine SnapChain-/Trace-DB-Zeile."""

    ok: bool
    source_format: str
    origin: str = ""
    namespace: str = ""
    kind: str = ""
    state_schema: str = ""
    action_schema: str = ""
    mode: str = ""
    source_id: Optional[int | str] = None
    pattern_count: int = 0
    event_count: int = 0
    feature_count: int = 0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    patterns: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    feature_centroid: List[float] = field(default_factory=list)
    policy_trainable: bool = False
    dream_processable: bool = False
    skip_reason: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "source_format": self.source_format,
            "origin": self.origin,
            "namespace": self.namespace,
            "kind": self.kind,
            "state_schema": self.state_schema,
            "action_schema": self.action_schema,
            "mode": self.mode,
            "source_id": self.source_id,
            "pattern_count": int(self.pattern_count),
            "event_count": int(self.event_count),
            "feature_count": int(self.feature_count),
            "policy_trainable": bool(self.policy_trainable),
            "dream_processable": bool(self.dream_processable),
            "feature_dim": int(len(self.feature_centroid or [])),
            "skip_reason": self.skip_reason,
            "warnings": list(self.warnings or []),
        }


# =============================================================================
# JSON / numeric helpers
# =============================================================================

def _decode_blob(blob: Any) -> Tuple[Optional[Any], str]:
    """Decode bytes/str/dict/list to Python object without throwing."""
    if blob is None:
        return None, "blob_null"
    if isinstance(blob, (dict, list)):
        return blob, ""
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)

    candidates: List[str] = []
    if isinstance(blob, bytes):
        try:
            candidates.append(blob.decode("utf-8"))
        except Exception:
            pass
        try:
            candidates.append(zlib.decompress(blob).decode("utf-8"))
        except Exception:
            pass
    else:
        candidates.append(str(blob))

    last_error = "json_decode_failed"
    for text in candidates:
        try:
            return json.loads(text), ""
        except Exception as exc:
            last_error = f"json_decode_failed:{type(exc).__name__}"
    return None, last_error


def _is_number(x: Any) -> bool:
    if isinstance(x, bool):
        return False
    try:
        f = float(x)
        return math.isfinite(f)
    except Exception:
        return False


def _as_float_list(seq: Any, *, max_len: int = 4096) -> List[float]:
    if not isinstance(seq, (list, tuple)):
        return []
    out: List[float] = []
    for v in seq[:max_len]:
        if _is_number(v):
            out.append(float(v))
        else:
            return []
    return out


def _flatten_numeric_dict(d: Any, *, prefix: str = "", max_items: int = 4096) -> List[float]:
    """Stable numeric dict flattening for feature dicts; nested dict/list tolerant."""
    if not isinstance(d, dict):
        return []
    items: List[Tuple[str, float]] = []

    def walk(obj: Any, key: str) -> None:
        if len(items) >= max_items:
            return
        if _is_number(obj):
            items.append((key, float(obj)))
            return
        if isinstance(obj, dict):
            for kk in sorted(obj.keys(), key=str):
                walk(obj.get(kk), f"{key}.{kk}" if key else str(kk))
            return
        if isinstance(obj, (list, tuple)):
            for i, vv in enumerate(obj):
                walk(vv, f"{key}[{i}]")

    walk(d, prefix)
    return [v for _, v in sorted(items, key=lambda kv: kv[0])]


def _extract_vector(obj: Any) -> List[float]:
    """Extract a numeric vector from common feature shapes."""
    if obj is None:
        return []
    if isinstance(obj, (list, tuple)):
        return _as_float_list(obj)
    if isinstance(obj, dict):
        for key in ("feat", "features", "vector", "v", "centroid", "embedding"):
            if key in obj:
                v = _extract_vector(obj.get(key))
                if v:
                    return v
        return _flatten_numeric_dict(obj)
    return []


def _extract_step_feature(step: Any) -> List[float]:
    """Extract Dream-relevant features from a game step.

    A compact game step may contain numeric bookkeeping fields such as ``t`` or
    ``action``. Those fields alone must not make the step dream-processable.
    Dream requires an explicit feature payload (feat/features/vector/v/centroid).
    """
    if not isinstance(step, dict):
        return []
    for key in ("feat", "features", "vector", "v", "centroid", "embedding"):
        if key in step:
            return _extract_vector(step.get(key))
    return []


def _centroid(vectors: Sequence[Sequence[float]]) -> List[float]:
    valid = [list(map(float, v)) for v in vectors if isinstance(v, (list, tuple)) and len(v) > 0]
    if not valid:
        return []
    dim = min(len(v) for v in valid)
    if dim <= 0:
        return []
    acc = [0.0] * dim
    n = 0
    for vec in valid:
        if len(vec) < dim:
            continue
        for i in range(dim):
            acc[i] += float(vec[i])
        n += 1
    if n <= 0:
        return []
    return [x / n for x in acc]


def _first_str(*values: Any) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _take_state_hash(step: Dict[str, Any]) -> str:
    for k in ("state_hash", "h", "sh"):
        if k in step and step.get(k) not in (None, ""):
            return str(step.get(k))
    st = step.get("state")
    if isinstance(st, dict):
        for k in ("state_hash", "h", "sh"):
            if k in st and st.get(k) not in (None, ""):
                return str(st.get(k))
    return ""


def _take_action(step: Dict[str, Any]) -> str:
    for k in ("action", "a", "action_canon", "ac", "action_name"):
        if k in step and step.get(k) not in (None, ""):
            return str(step.get(k))
    return ""


def _has_outcome(step: Dict[str, Any], root: Dict[str, Any]) -> bool:
    for k in ("outcome", "result", "reward"):
        if k in step and step.get(k) not in (None, ""):
            return True
    for k in ("result", "outcome", "reward"):
        if k in root and root.get(k) not in (None, ""):
            return True
    return False


# =============================================================================
# Format detection / normalization
# =============================================================================

def detect_blob_format(blob: Any) -> str:
    data, err = _decode_blob(blob)
    if data is None:
        return "decode_error" if err else "unknown"
    if isinstance(data, dict):
        if isinstance(data.get("patterns"), list):
            return "native_snapchain"
        kind = str(data.get("kind") or "")
        ns = str(data.get("namespace") or "")
        if isinstance(data.get("steps"), list) or kind == "game_trace" or ns.startswith("game:"):
            return "compact_game_trace"
        if any(k in data for k in ("v", "vector", "features", "embedding")):
            return "token_vector_trace"
    if isinstance(data, list):
        return "list_trace"
    return "unknown"


def _normalize_native_snapchain(data: Dict[str, Any], *, origin: str, namespace: str, source_id: Any) -> NormalizedSnapTrace:
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    patterns_raw = data.get("patterns") if isinstance(data.get("patterns"), list) else []
    patterns: List[Dict[str, Any]] = [p for p in patterns_raw if isinstance(p, dict)]
    vectors = [_extract_vector(p) for p in patterns]
    vectors = [v for v in vectors if v]
    ns = _first_str(namespace, metadata.get("namespace"), data.get("namespace"))
    org = _first_str(origin, metadata.get("origin"), data.get("origin"))
    trace = NormalizedSnapTrace(
        ok=bool(patterns or vectors),
        source_format="native_snapchain",
        origin=org,
        namespace=ns,
        kind=_first_str(data.get("kind"), metadata.get("kind"), "snapchain"),
        state_schema=_first_str(data.get("state_schema"), metadata.get("state_schema")),
        action_schema=_first_str(data.get("action_schema"), metadata.get("action_schema")),
        mode=_first_str(data.get("mode"), metadata.get("mode")),
        source_id=source_id,
        pattern_count=len(patterns),
        event_count=len(patterns),
        feature_count=len(vectors),
        steps=[],
        patterns=patterns,
        metadata=metadata,
        feature_centroid=_centroid(vectors),
    )
    trace.policy_trainable = is_policy_trainable(trace)
    trace.dream_processable = is_dream_processable(trace)
    if not trace.ok:
        trace.skip_reason = "native_snapchain_without_patterns"
    return trace


def _normalize_compact_game_trace(data: Dict[str, Any], *, origin: str, namespace: str, source_id: Any) -> NormalizedSnapTrace:
    steps_raw = data.get("steps") if isinstance(data.get("steps"), list) else []
    steps: List[Dict[str, Any]] = [s for s in steps_raw if isinstance(s, dict)]
    vectors = [_extract_step_feature(s) for s in steps]
    vectors = [v for v in vectors if v]
    metadata = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    if isinstance(data.get("metadata"), dict):
        metadata = {**metadata, **data.get("metadata", {})}
    ns = _first_str(namespace, data.get("namespace"), metadata.get("namespace"), origin)
    org = _first_str(origin, data.get("origin"), metadata.get("origin"), ns)
    trace = NormalizedSnapTrace(
        ok=bool(steps),
        source_format="compact_game_trace",
        origin=org,
        namespace=ns,
        kind=_first_str(data.get("kind"), "game_trace"),
        state_schema=_first_str(data.get("state_schema"), metadata.get("state_schema")),
        action_schema=_first_str(data.get("action_schema"), metadata.get("action_schema")),
        mode=_first_str(data.get("mode"), metadata.get("mode")),
        source_id=source_id,
        pattern_count=len(vectors),
        event_count=len(steps),
        feature_count=len(vectors),
        steps=steps,
        patterns=[{"feature": v} for v in vectors],
        metadata=metadata,
        feature_centroid=_centroid(vectors),
    )
    trace.policy_trainable = is_policy_trainable(trace, root=data)
    trace.dream_processable = is_dream_processable(trace)
    if not trace.ok:
        trace.skip_reason = "compact_game_trace_without_steps"
    elif not trace.dream_processable:
        trace.warnings.append("no_numeric_features_for_dream")
    if not trace.policy_trainable:
        trace.warnings.append("missing_state_hash_action_or_outcome_for_policy")
    return trace


def _normalize_token_vector_trace(data: Dict[str, Any], *, origin: str, namespace: str, source_id: Any) -> NormalizedSnapTrace:
    vector = _extract_vector(data)
    ns = _first_str(namespace, data.get("namespace"), origin)
    org = _first_str(origin, data.get("origin"), ns)
    trace = NormalizedSnapTrace(
        ok=bool(vector),
        source_format="token_vector_trace",
        origin=org,
        namespace=ns,
        kind=_first_str(data.get("kind"), "token_vector"),
        state_schema=_first_str(data.get("state_schema")),
        action_schema=_first_str(data.get("action_schema")),
        mode=_first_str(data.get("mode")),
        source_id=source_id,
        pattern_count=1 if vector else 0,
        event_count=1 if vector else 0,
        feature_count=1 if vector else 0,
        steps=[],
        patterns=[{"feature": vector}] if vector else [],
        metadata={k: v for k, v in data.items() if k not in ("v", "vector", "features", "embedding")},
        feature_centroid=list(vector),
    )
    trace.policy_trainable = False
    trace.dream_processable = is_dream_processable(trace)
    if not trace.ok:
        trace.skip_reason = "token_vector_without_vector"
    return trace


def normalize_snapchain_blob(
    blob: Any,
    *,
    origin: str = "",
    namespace: str = "",
    source_id: int | str | None = None,
) -> NormalizedSnapTrace:
    """Normalize a snapchains.blob payload into a read-only trace object."""
    data, err = _decode_blob(blob)
    if data is None:
        return NormalizedSnapTrace(
            ok=False,
            source_format="decode_error",
            origin=str(origin or ""),
            namespace=str(namespace or ""),
            source_id=source_id,
            skip_reason=err or "decode_error",
        )
    if isinstance(data, list):
        # Minimal legacy list support: list of step/pattern dicts or numeric vectors.
        data = {"kind": "list_trace", "steps": data}
    if not isinstance(data, dict):
        return NormalizedSnapTrace(
            ok=False,
            source_format="unknown",
            origin=str(origin or ""),
            namespace=str(namespace or ""),
            source_id=source_id,
            skip_reason=f"unsupported_root_type:{type(data).__name__}",
        )

    fmt = detect_blob_format(data)
    if fmt == "native_snapchain":
        return _normalize_native_snapchain(data, origin=origin, namespace=namespace, source_id=source_id)
    if fmt in ("compact_game_trace", "list_trace"):
        return _normalize_compact_game_trace(data, origin=origin, namespace=namespace, source_id=source_id)
    if fmt == "token_vector_trace":
        return _normalize_token_vector_trace(data, origin=origin, namespace=namespace, source_id=source_id)

    return NormalizedSnapTrace(
        ok=False,
        source_format="unknown",
        origin=str(origin or data.get("origin") or ""),
        namespace=str(namespace or data.get("namespace") or ""),
        kind=str(data.get("kind") or ""),
        source_id=source_id,
        skip_reason="no_supported_keys",
    )


# =============================================================================
# Capability predicates
# =============================================================================

def is_policy_trainable(trace: NormalizedSnapTrace, *, root: Optional[Dict[str, Any]] = None) -> bool:
    """PolicyEngine-Mindestanforderung: state_hash + action + outcome/result."""
    if not isinstance(trace, NormalizedSnapTrace):
        return False
    root = root if isinstance(root, dict) else {}
    if trace.source_format != "compact_game_trace":
        return False
    for step in trace.steps:
        if not isinstance(step, dict):
            continue
        if _take_state_hash(step) and _take_action(step) and _has_outcome(step, root):
            return True
    # PolicyEngine's prehash path can also learn pairs from adjacent steps when
    # root result is present; the action may be on the next step.
    if root and any(k in root for k in ("result", "outcome", "reward")):
        hashes = sum(1 for s in trace.steps if isinstance(s, dict) and _take_state_hash(s))
        actions = sum(1 for s in trace.steps if isinstance(s, dict) and _take_action(s))
        return hashes > 0 and actions > 0
    return False


def is_dream_processable(trace: NormalizedSnapTrace) -> bool:
    """Dream-Mindestanforderung: echte numerische Features/Centroid."""
    if not isinstance(trace, NormalizedSnapTrace):
        return False
    return bool(trace.feature_count > 0 and trace.feature_centroid)


def feature_centroid_from_trace(trace: NormalizedSnapTrace) -> List[float]:
    if not isinstance(trace, NormalizedSnapTrace):
        return []
    return list(trace.feature_centroid or [])


def summarize_trace(trace: NormalizedSnapTrace) -> Dict[str, Any]:
    if not isinstance(trace, NormalizedSnapTrace):
        return {"ok": False, "skip_reason": "not_a_trace"}
    return trace.to_dict()
