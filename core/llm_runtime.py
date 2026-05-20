#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:      /opt/ai/oroma/core/llm_runtime.py
# Projekt:   ORÓMA (Headless LLM Runtime · Offline-First)
# Modul:     LLMRuntime – Modell-Loader (llama_cpp/ctransformers) + MiniLLM Fallback + Session Chat API
# Version:   v3.7.3
# Stand:     2026-01-10
# Autor:     ORÓMA · KI-JWG-X1
# Lizenz:    MIT
# =============================================================================
#
# ZWECK
# ─────
# Dieses Modul stellt eine einheitliche, headless Laufzeitumgebung für ein LLM bereit:
#   - Modell-Discovery aus einem Directory (GGUF/GGML/.*.bin)
#   - Laden eines Modells über optionale Backends:
#       • llama-cpp-python (bevorzugt bei .gguf)
#       • ctransformers     (Fallback, wenn verfügbar)
#   - Wenn kein Backend/kein Modell verfügbar: MiniLLM Fallback (sehr leichtgewichtig)
#   - Session-basierter Chat (History, system_prompt, truncate)
#   - Globale Singleton-API (get(), chat(), status(), list_models(), load_model(), unload_model(), …)
#
# WARUM MINI LLM?
# ──────────────
# ORÓMA soll auch ohne GPU/NPU/LLM-Libraries produktiv booten (Headless-Prinzip).
# MiniLLM ist ein kleiner n-gram-Generator:
#   - trainiert aus der Session-History
#   - liefert „best effort“ Antworten
# Ziel: UI/Flows bleiben funktionsfähig, selbst wenn echte LLM-Backends fehlen.
#
# BACKEND-ERKENNUNG (AUTO)
# ───────────────────────
# Beim Import wird geprüft:
#   - llama_cpp importierbar? → _BACKEND_LLAMA_CPP_OK
#   - ctransformers importierbar? → _BACKEND_CTRANSFORMERS_OK
#
# load(..., backend_preference="auto|llama_cpp|ctransformers|mini") wählt:
#   1) llama_cpp (wenn .gguf + verfügbar + bevorzugt/auto)
#   2) ctransformers (wenn verfügbar + bevorzugt/auto)
#   3) mini fallback
#
# MODELLE / DATEIFORMATE
# ──────────────────────
# list_available_models() sucht im Modelle-Ordner nach:
#   *.gguf, *.ggml, *ggml*.bin, *.bin (inkl. Uppercase Varianten)
# Das ist bewusst tolerant, damit ältere Deployments funktionieren.
#
# SESSION-MODELL
# ──────────────
# Jede Session ist:
#   sessions[session_id] = [{"role":"system|user|assistant", "content":"..."}, ...]
# - new_session() erzeugt eine Session-ID
# - chat() hängt user prompt an, erzeugt assistant Antwort, speichert sie
# - _truncate_history() begrenzt History (RAM-Schutz)
#
# PARAMETER
# ─────────
# Unterstützte Chat-Parameter (global pro Runtime):
#   - temperature (>=0)
#   - top_p (0.1..1.0)
#   - max_tokens (>=1)
#
# Bei llama_cpp:
#   - create_completion(prompt=..., max_tokens=..., temperature=..., top_p=...)
#
# WICHTIGE ENV-VARIABLEN
# ─────────────────────
# Logging:
#   OROMA_LOG_LEVEL=INFO|DEBUG|...
#
# Modellpfad:
#   OROMA_LLM_DIR=/opt/ai/oroma/models/llm
#
# (Parameter wie max_tokens/temperature/top_p sind hier im Code als Defaults gesetzt;
#  UI/Tools können sie via set_params(...) verändern.)
#
# ÖFFENTLICHE API (STABIL, FÜR UI/TOOLS)
# ─────────────────────────────────────
# Singleton:
#   get() -> LLMRuntime
#
# Modellverwaltung:
#   load_model(model_path: Optional[str]=None, backend_preference: Optional[str]=None, n_ctx: int=2048, n_threads: Optional[int]=None) -> Dict
#   unload_model() -> Dict
#   list_models() -> List[str]
#
# Chat:
#   chat(prompt: str, session_id: Optional[str]=None, system_prompt: Optional[str]=None) -> Dict
#   status() -> Dict[str, Any]   (loaded, backend, model_name, sessions, params, ...)
#
# Sessions:
#   new_session(session_id: Optional[str]=None) -> str
#   drop_session(session_id: str) -> None
#   list_sessions() -> List[str]
#   get_history(session_id: str) -> List[Dict[str,str]]
#
# THREADING / SAFETY
# ──────────────────
# - LLMRuntime verwendet threading.RLock
# - alle mutierenden Operationen (load/unload/chat/session ops) laufen unter Lock
# - Ziel: UI Parallel-Requests sollen keinen inkonsistenten Runtime-State erzeugen
#
# =============================================================================
# END HEADER
# =============================================================================

from __future__ import annotations
import os
import sys
import glob
import time
import json
import math
import random
import logging
import threading
import hashlib
from typing import Optional, List, Dict, Any, Tuple

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
_LOG_LEVEL = os.environ.get("OROMA_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO),
                    format="[LLM] %(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("oroma.llm")

# -----------------------------------------------------------------------------
# Optionale Backends erkennen
# -----------------------------------------------------------------------------
_BACKEND_LLAMA_CPP_OK = False
_BACKEND_CTRANSFORMERS_OK = False
try:
    from llama_cpp import Llama  # type: ignore
    _BACKEND_LLAMA_CPP_OK = True
except Exception as e:
    log.debug("llama-cpp-python nicht verfügbar: %s", e)

try:
    from ctransformers import AutoModelForCausalLM  # type: ignore
    _BACKEND_CTRANSFORMERS_OK = True
except Exception as e:
    log.debug("ctransformers nicht verfügbar: %s", e)

# -----------------------------------------------------------------------------
# Konfiguration
# -----------------------------------------------------------------------------
DEFAULT_LLM_DIR = os.environ.get("OROMA_LLM_DIR", "/opt/ai/oroma/models/llm")
DEFAULT_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.95

# -----------------------------------------------------------------------------
# Hilfsfunktionen
# -----------------------------------------------------------------------------
def list_available_models(llm_dir: Optional[str] = None) -> List[str]:
    """Listet Modell-Dateien (GGUF/GGML)."""
    d = llm_dir or DEFAULT_LLM_DIR
    if not os.path.exists(d):
        return []
    patterns = ["*.gguf", "*.GGUF", "*.ggml", "*.GGML", "*ggml*.bin", "*GGML*.bin", "*.bin"]
    out = []
    for pat in patterns:
        out.extend(glob.glob(os.path.join(d, pat)))
    return sorted(list(set(out)))

def _truncate_history(history: List[Dict[str, str]], max_items: int = 20) -> List[Dict[str, str]]:
    """Begrenzt die Historie pro Session (RAM-Schutz)."""
    return history if len(history) <= max_items else history[-max_items:]

# -----------------------------------------------------------------------------
# MiniLLM Fallback
# -----------------------------------------------------------------------------
class MiniLLM:
    """Sehr leichter Fallback, wenn kein echtes LLM verfügbar ist."""

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed or int(time.time()))
        self.ngram: Dict[Tuple[str, ...], List[str]] = {}
        self.order = 2
        self.lock = threading.RLock()

    def _tokenize(self, text: str) -> List[str]:
        out, buf = [], ""
        for ch in text:
            if ch.isalnum() or ch in "_":
                buf += ch
            else:
                if buf:
                    out.append(buf.lower())
                    buf = ""
                if ch.strip():
                    out.append(ch)
        if buf:
            out.append(buf.lower())
        return out

    def _train_from_history(self, history: List[Dict[str, str]]) -> None:
        with self.lock:
            self.ngram.clear()
            tokens: List[str] = []
            for turn in history:
                content = (turn.get("role","") + ": " + turn.get("content","")).strip()
                toks = self._tokenize(content)
                tokens.extend(toks + ["<eos>"])
            for i in range(len(tokens) - self.order):
                k = tuple(tokens[i:i+self.order])
                nxt = tokens[i+self.order]
                self.ngram.setdefault(k, []).append(nxt)

    def _sample_next(self, context: Tuple[str, ...], temperature: float, top_p: float) -> str:
        candidates = self.ngram.get(context, None)
        if not candidates:
            if self.ngram:
                context = list(self.ngram.keys())[self.rng.randrange(len(self.ngram))]  # type: ignore
                candidates = self.ngram[context]
            else:
                return "<eos>"
        counts: Dict[str, int] = {}
        for c in candidates:
            counts[c] = counts.get(c, 0) + 1
        items = list(counts.items())
        items.sort(key=lambda x: x[1], reverse=True)
        total = sum(c for _, c in items) or 1

        cum, cut_items = 0.0, []
        for tok, _ in items:
            p = counts[tok] / total
            cum += p
            cut_items.append((tok, p))
            if cum >= max(0.1, min(1.0, top_p)):
                break

        if temperature <= 0.0:
            return cut_items[0][0]
        else:
            logits = [math.log(max(1e-9, p)) / max(1e-3, temperature) for _, p in cut_items]
            m = max(logits)
            exps = [math.exp(x - m) for x in logits]
            s = sum(exps) or 1.0
            norm = [e/s for e in exps]
            r, acc = self.rng.random(), 0.0
            for (tok, _), pr in zip(cut_items, norm):
                acc += pr
                if r <= acc:
                    return tok
            return cut_items[-1][0]

    def generate(self, history: List[Dict[str, str]], max_tokens: int = 128,
                 temperature: float = 0.7, top_p: float = 0.95) -> str:
        self._train_from_history(history)
        ctx_tokens = self._tokenize(history[-1].get("content","")) if history else []
        if len(ctx_tokens) < self.order:
            ctx_tokens = (["<bos>"] * (self.order - len(ctx_tokens))) + ctx_tokens
        context = tuple(ctx_tokens[-self.order:])
        out_tokens: List[str] = []
        for _ in range(max(1, max_tokens)):
            nxt = self._sample_next(context, temperature, top_p)
            if nxt == "<eos>":
                break
            out_tokens.append(nxt)
            context = tuple((list(context) + [nxt])[-self.order:])
        text = []
        for t in out_tokens:
            if t.isalnum() or t in "_":
                if text and text[-1].isalnum():
                    text.append(" ")
            text.append(t)
        return "".join(text).strip() or "Ich bin mir nicht sicher – erzähl mir mehr Kontext."

# -----------------------------------------------------------------------------
# Hauptklasse: LLMRuntime
# -----------------------------------------------------------------------------
class LLMRuntime:
    """Verwaltung eines einzelnen LLM-Modells mit Session-Speicher."""

    def __init__(self, models_dir: Optional[str] = None):
        self.models_dir = models_dir or DEFAULT_LLM_DIR
        self.model_path: Optional[str] = None
        self.backend: str = "mini"
        self.llm = None
        self.lock = threading.RLock()
        self.sessions: Dict[str, List[Dict[str, str]]] = {}
        self.loaded = False
        self.model_name: Optional[str] = None
        self.temperature = DEFAULT_TEMPERATURE
        self.top_p = DEFAULT_TOP_P
        self.max_tokens = DEFAULT_MAX_TOKENS
        self._mini = MiniLLM()

    # -------------------------- Modellverwaltung ------------------------------
    def discover_models(self) -> List[str]:
        return list_available_models(self.models_dir)

    def load(self, model_path: Optional[str] = None, backend_preference: Optional[str] = None,
             n_ctx: int = 2048, n_threads: Optional[int] = None) -> None:
        with self.lock:
            if self.loaded:
                self.unload()
            candidates = self.discover_models()
            if not model_path:
                if not candidates:
                    log.warning("Kein Modell gefunden – Fallback MiniLLM aktiv.")
                    self.backend = "mini"
                    self.llm = self._mini
                    self.loaded = True
                    self.model_name = "mini-llm"
                    return
                model_path = candidates[0]
            self.model_path = model_path
            self.model_name = os.path.basename(model_path)
            prefer = (backend_preference or "auto").lower()
            log.info("Lade Modell: %s (Präferenz: %s)", self.model_name, prefer)

            if prefer in ("auto", "llama_cpp") and _BACKEND_LLAMA_CPP_OK and model_path.endswith(".gguf"):
                try:
                    kwargs = dict(model_path=model_path, n_ctx=n_ctx)
                    if n_threads is not None:
                        kwargs["n_threads"] = int(max(1, n_threads))
                    self.llm = Llama(**kwargs)  # type: ignore
                    self.backend = "llama_cpp"
                    self.loaded = True
                    return
                except Exception as e:
                    log.warning("llama_cpp Laden fehlgeschlagen: %s", e)

            if prefer in ("auto", "ctransformers") and _BACKEND_CTRANSFORMERS_OK:
                try:
                    self.llm = AutoModelForCausalLM.from_pretrained(  # type: ignore
                        model_path, model_type="llama", gpu_layers=0
                    )
                    self.backend = "ctransformers"
                    self.loaded = True
                    return
                except Exception as e:
                    log.warning("ctransformers Laden fehlgeschlagen: %s", e)

            log.warning("Kein Backend verfügbar – MiniLLM aktiv.")
            self.backend = "mini"
            self.llm = self._mini
            self.loaded = True

    def unload(self) -> None:
        with self.lock:
            self.llm = None
            self.loaded = False
            self.model_path = None
            self.model_name = None
            self.backend = "mini"
            self._mini = MiniLLM()

    # -------------------------- Sessions --------------------------------------
    def new_session(self, session_id: Optional[str] = None) -> str:
        with self.lock:
            sid = session_id or f"sess_{int(time.time()*1000)}_{random.randint(0,9999)}"
            self.sessions[sid] = []
            return sid

    def drop_session(self, session_id: str) -> None:
        with self.lock:
            if session_id in self.sessions:
                del self.sessions[session_id]

    def list_sessions(self) -> List[str]:
        with self.lock:
            return sorted(list(self.sessions.keys()))

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        with self.lock:
            return list(self.sessions.get(session_id, []))

    # -------------------------- Parameter -------------------------------------
    def set_params(self, temperature: Optional[float] = None,
                   top_p: Optional[float] = None,
                   max_tokens: Optional[int] = None) -> None:
        with self.lock:
            if temperature is not None:
                self.temperature = max(0.0, float(temperature))
            if top_p is not None:
                self.top_p = float(min(1.0, max(0.1, top_p)))
            if max_tokens is not None:
                self.max_tokens = int(max(1, max_tokens))

    # -------------------------- Inference -------------------------------------
    def _ensure_session(self, session_id: Optional[str]) -> str:
        return session_id if session_id and session_id in self.sessions else self.new_session()

    def chat(self, prompt: str, session_id: Optional[str] = None,
             system_prompt: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            sid = self._ensure_session(session_id)
            hist = self.sessions[sid]
            if system_prompt and (not hist or hist[0].get("role") != "system"):
                hist.insert(0, {"role": "system", "content": system_prompt})
            hist.append({"role": "user", "content": prompt})
            hist[:] = _truncate_history(hist)

            t0 = time.time()
            out_text, used_backend = "", self.backend
            try:
                if self.backend == "llama_cpp" and self.llm is not None:
                    ctx = self._history_to_text(hist)
                    res = self.llm.create_completion(  # type: ignore
                        prompt=ctx,
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        top_p=self.top_p,
                        stop=["</s>"]
                    )
                    out_text = (res.get("choices",[{}])[0].get("text","") or "").strip()
                elif self.backend == "ctransformers" and self.llm is not None:
                    ctx = self._history_to_text(hist)
                    out_chunks = []
                    for tok in self.llm.generate(  # type: ignore
                            ctx, max_new_tokens=self.max_tokens,
                            temperature=self.temperature, top_p=self.top_p):
                        out_chunks.append(tok)
                    out_text = "".join(out_chunks).strip()
                else:
                    out_text = self._mini.generate(hist, self.max_tokens, self.temperature, self.top_p)
            except Exception as e:
                log.exception("Inference-Fehler: %s", e)
                used_backend = "mini-fallback"
                out_text = self._mini.generate(hist, self.max_tokens, self.temperature, self.top_p)

            t1 = time.time()
            hist.append({"role": "assistant", "content": out_text})
            hist[:] = _truncate_history(hist)
            return {
                "session_id": sid,
                "backend": used_backend,
                "model": self.model_name or "mini-llm",
                "temperature": self.temperature,
                "top_p": self.top_p,
                "max_tokens": self.max_tokens,
                "latency_ms": int((t1 - t0) * 1000),
                "text": out_text
            }

    def _history_to_text(self, hist: List[Dict[str, str]]) -> str:
        parts = [f"{turn.get('role','user').upper()}: {turn.get('content','')}\n" for turn in hist]
        parts.append("ASSISTANT: ")
        return "".join(parts)

    # -------------------------- Status / Diagnose -----------------------------
    def status(self) -> Dict[str, Any]:
        with self.lock:
            avail = self.discover_models()
            if not avail:
                avail = ["mini-llm"]
            return {
                "loaded": self.loaded,
                "backend": self.backend,
                "model_name": self.model_name or "mini-llm",
                "model_path": self.model_path,
                "sessions": len(self.sessions),
                "temperature": self.temperature,
                "top_p": self.top_p,
                "max_tokens": self.max_tokens,
                "available_models": avail,
            }

    # -------------------------- v3.5 Erweiterungen ----------------------------
    def embed_text(self, text: str) -> List[float]:
        """
        Erzeugt deterministische Embeddings.
        - Falls kein echter Embedder → Hash-basiert, gleiche Texte → ähnliche Vektoren.
        """
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [((b / 255.0) * 2 - 1) for b in h[:128]]

    def tokenize(self, text: str) -> List[str]:
        if hasattr(self, "_tokenizer"):
            return self._tokenizer.tokenize(text)
        return text.strip().split()

    def normalize_concepts(self, tokens: List[str]) -> List[str]:
        seen, out = set(), []
        for t in tokens:
            t2 = t.lower()
            if t2 not in seen:
                seen.add(t2)
                out.append(t2)
        return out

# -----------------------------------------------------------------------------
# Singleton-Fassade
# -----------------------------------------------------------------------------
_GLOBAL: Optional[LLMRuntime] = None
_GLOBAL_LOCK = threading.Lock()

def get() -> LLMRuntime:
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = LLMRuntime()
        return _GLOBAL

def load_model(model_path: Optional[str] = None, backend_preference: Optional[str] = None,
               n_ctx: int = 2048, n_threads: Optional[int] = None) -> Dict[str, Any]:
    llm = get()
    llm.load(model_path=model_path, backend_preference=backend_preference,
             n_ctx=n_ctx, n_threads=n_threads)
    return llm.status()

def unload_model() -> Dict[str, Any]:
    llm = get()
    llm.unload()
    return llm.status()

def list_models() -> List[str]:
    return get().discover_models()

def set_params(temperature: Optional[float] = None, top_p: Optional[float] = None,
               max_tokens: Optional[int] = None) -> Dict[str, Any]:
    llm = get()
    llm.set_params(temperature=temperature, top_p=top_p, max_tokens=max_tokens)
    return llm.status()

def chat(prompt: str, session_id: Optional[str] = None,
         system_prompt: Optional[str] = None) -> Dict[str, Any]:
    return get().chat(prompt=prompt, session_id=session_id, system_prompt=system_prompt)

def status() -> Dict[str, Any]:
    return get().status()

def new_session(session_id: Optional[str] = None) -> str:
    return get().new_session(session_id)

def drop_session(session_id: str) -> None:
    return get().drop_session(session_id)

def list_sessions() -> List[str]:
    return get().list_sessions()

def get_history(session_id: str) -> List[Dict[str, str]]:
    return get().get_history(session_id)

# -----------------------------------------------------------------------------
# CLI-Test
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("[LLM] Quicktest …")

    # 1) Modell laden (auto-discover oder MiniLLM-Fallback)
    st = load_model(None, backend_preference="auto")
    print("Status:", json.dumps(st, indent=2))

    # 2) Neue Session
    sid = new_session()
    print("Session:", sid)

    # 3) Kurzer Chat
    user_q = "Hallo, wer bist du?"
    print(">", user_q)
    out = chat(user_q, session_id=sid, system_prompt="Du bist ORÓMAs lokaler Assistent.")
    print("<", out["text"])

    # 4) v3.5-APIs demonstrieren (Embeddings, Tokenize, Normalize)
    rt = get()
    sample_text = "Fotosynthese wandelt Lichtenergie in chemische Energie um."
    emb = rt.embed_text(sample_text)
    toks = rt.tokenize(sample_text)
    conc = rt.normalize_concepts(toks)
    print(f"[v3.5] embed_text: dim={len(emb)}  first3={emb[:3] if emb else []}")
    print(f"[v3.5] tokenize:   {toks}")
    print(f"[v3.5] concepts:   {conc}")

    # 5) Parameter ändern & zweiter Chat
    set_params(temperature=0.6, top_p=0.9, max_tokens=160)
    follow_q = "Erkläre in einem Satz, was die Lichtreaktion ist."
    print(">", follow_q)
    out2 = chat(follow_q, session_id=sid)
    print("<", out2["text"])

    print("[LLM] Quicktest fertig.")