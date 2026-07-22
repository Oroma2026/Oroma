#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/core/acquisition_state_machine.py
# Projekt: ORÓMA
# Modul:   Generische Targeted-Acquisition-Zustandsregistry
# Version: v0.1.0
# Stand:   2026-07-15
# =============================================================================
"""Fail-closed registry for versioned targeted-acquisition transitions."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
Validator = Callable[[Mapping[str, Any]], bool]
@dataclass
class StateMachineRegistry:
    _transitions: Dict[Tuple[str, str], List[Validator]] = field(default_factory=dict)
    def register_transition(self, from_status: str, to_status: str, validator: Optional[Validator] = None) -> None:
        key=(str(from_status),str(to_status)); self._transitions.setdefault(key,[])
        if validator is not None: self._transitions[key].append(validator)
    def decision(self, from_status: str, to_status: str, context: Optional[Mapping[str,Any]]=None) -> Dict[str,Any]:
        key=(str(from_status),str(to_status)); validators=self._transitions.get(key)
        if validators is None: return {"allowed":False,"reason":"transition_not_registered","from_status":key[0],"to_status":key[1]}
        ctx=dict(context or {})
        try:
            if not all(bool(v(ctx)) for v in validators): return {"allowed":False,"reason":"transition_validator_rejected","from_status":key[0],"to_status":key[1]}
        except Exception as exc:
            return {"allowed":False,"reason":f"transition_validator_error:{type(exc).__name__}","from_status":key[0],"to_status":key[1]}
        return {"allowed":True,"reason":"transition_registered","from_status":key[0],"to_status":key[1]}
REGISTRY=StateMachineRegistry()
for _f,_t in (("acquisition_pending","acquiring"),("acquisition_pending","source_state_missing"),("acquisition_pending","blocked"),("source_state_missing","acquisition_pending"),("source_state_missing","blocked"),("acquiring","acquiring"),("acquiring","evidence_acquired"),("acquiring","exhausted_no_direct_outcome"),("acquiring","blocked")):
    REGISTRY.register_transition(_f,_t)
__all__=["StateMachineRegistry","REGISTRY"]
