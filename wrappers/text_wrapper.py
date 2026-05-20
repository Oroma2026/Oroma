#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/wrappers/text_wrapper.py
# Projekt: ORÓMA
# Version: v3.7 (final, verbessert)
# Stand:   2025-09-29
#
# Zweck
# ─────
# - TextWrapper: stabile Text-I/O-Schicht (Konsole/Dashboard) + Snap/Token.
# - TextRunner: produktiver Chat/LLM-Layer mit Auto-Backend:
#       llama.cpp → GPT4All → HuggingFace (transformers) → Offline-Echo.
# - Offline-NLP (ohne Abhängigkeiten): normalize/tokenize/sentences/keywords/
#   summary/sentiment – schnelle Analyse, fallbackfähig.
#
# ENV (optional)
# ──────────────
# - OROMA_MODELS_DIR, OROMA_LLM_DIR   : Modelldirs (Scan).
# - OROMA_LLM_BACKEND                : auto|llama|gpt4all|hf|echo  (Default: auto)
# - OROMA_LLM_MAXTOKENS              : Maximal generierte Tokens (Default: 256)
# - OROMA_LOG_LEVEL                  : INFO/DEBUG/…
#
# Abhängigkeiten
# ──────────────
# - Pflicht:   nur Standardbibliothek + ORÓMA-Core (Snap/SnapToken).
# - Optional:  llama_cpp, gpt4all, transformers (werden automatisch & lazy genutzt).
#
# Lizenz: MIT (Projekt ORÓMA)
# =============================================================================

from __future__ import annotations

import os
import re
import json
import glob
import time
import logging
from core.log_guard import log_suppressed
import threading
import queue
from typing import Optional, Callable, List, Dict, Any, Tuple

from core.snap import Snap
from core.snaptoken import SnapToken

logger = logging.getLogger("oroma.text_wrapper")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] text_wrapper: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(getattr(logging, os.environ.get("OROMA_LOG_LEVEL", "INFO").upper(), logging.INFO))

# =============================================================================
# Hilfs-NLP: rein offline, keine Fremdpakete
# =============================================================================

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", re.UNICODE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])")

_POS_WORDS = {
    "gut", "toll", "super", "prima", "danke", "freue", "glücklich", "zufrieden",
    "great", "awesome", "love", "happy", "nice", "cool", "yay", "thanks",
}
_NEG_WORDS = {
    "schlecht", "traurig", "wütend", "ärgerlich", "fehler", "problem",
    "bad", "sad", "angry", "upset", "error", "issue", "bug", "fail",
}
_STOPWORDS = {
    "der","die","das","und","oder","aber","ein","eine","ist","im","in","zu","auf","für","mit","ohne",
    "the","a","an","and","or","but","of","to","in","on","for","with","without","this","that","it","is","are",
}

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def _sentences(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENT_SPLIT_RE.split(text)
    out: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > 400:
            out.extend([s.strip() for s in re.split(r"[;:]\s+", p) if s.strip()])
        else:
            out.append(p)
    return out

def _tokens(text: str) -> List[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]

def _keyword_scores(tokens: List[str]) -> Dict[str, float]:
    freq: Dict[str, int] = {}
    for t in tokens:
        if t in _STOPWORDS:
            continue
        freq[t] = freq.get(t, 0) + 1
    if not freq:
        return {}
    maxf = max(freq.values())
    return {k: v / maxf for k, v in freq.items()}

def _summarize(sentences: List[str], kw_scores: Dict[str, float], max_sentences: int = 3) -> List[str]:
    scored: List[Tuple[float, int, str]] = []
    n = max(1, len(sentences))
    for i, s in enumerate(sentences):
        toks = _tokens(s)
        s_kw = sum(kw_scores.get(t, 0.0) for t in toks)
        length = max(1, len(toks))
        len_bonus = 1.0 - abs(length - 16) / 32.0
        pos_bonus = 1.0 - (i / n) * 0.15
        score = s_kw + 0.2 * len_bonus + 0.1 * pos_bonus
        scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [s for _, _, s in scored[:max_sentences]]
    order = {s: i for i, s in enumerate(sentences)}
    top.sort(key=lambda s: order.get(s, 0))
    return top

def _sentiment(tokens: List[str]) -> float:
    if not tokens:
        return 0.0
    pos = sum(1 for t in tokens if t in _POS_WORDS)
    neg = sum(1 for t in tokens if t in _NEG_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / float(total)

def offline_analyze(text: str, max_summary_sentences: int = 3) -> Dict[str, Any]:
    text_n = _normalize(text)
    sents = _sentences(text_n)
    toks = _tokens(text_n)
    kws = _keyword_scores(toks)
    top_kw = sorted(kws.items(), key=lambda kv: kv[1], reverse=True)[:10]
    summary = _summarize(sents, kws, max_summary_sentences)
    pol = _sentiment(toks)
    return {
        "normalized": text_n,
        "sentences": sents,
        "tokens": toks,
        "keywords": [{"term": k, "score": float(v)} for k, v in top_kw],
        "summary": summary,
        "sentiment": float(pol),
        "length": len(text or ""),
    }

# =============================================================================
# TextWrapper – klassische Text-I/O + Snap/Token + optionale Analyse
# =============================================================================

class TextWrapper:
    def __init__(self, enable_console: bool = True):
        self.enable_console = enable_console
        self.input_queue: "queue.Queue[str]" = queue.Queue()
        self.output_callback: Optional[Callable[[str], None]] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.running = True
        if self.enable_console and (self.thread is None or not self.thread.is_alive()):
            self.thread = threading.Thread(target=self._console_loop, daemon=True)
            self.thread.start()
            logger.info("TextWrapper: Konsoleingabe gestartet.")

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            logger.info("TextWrapper: Stoppe Konsoleingabe…")
            try:
                self.thread.join(timeout=1.0)
            except Exception as e:
                log_suppressed(logger, key="wrappers_text_wrapper.pass.1", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        self.thread = None

    def register_output_callback(self, cb: Callable[[str], None]) -> None:
        self.output_callback = cb

    def send_text(self, text: str, return_analysis: bool = False):
        msg = _normalize(text)
        logger.debug("TextWrapper: Eingehender Text: %s", msg)
        snap = Snap(content=msg, metadata={"modality": "text"})
        token = SnapToken(msg)
        if self.output_callback:
            try:
                self.output_callback(msg)
            except Exception:
                logger.exception("Output-Callback warf Exception")
        if return_analysis:
            return snap, token, offline_analyze(msg)
        return snap, token

    def read_text(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self.input_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def analyze(self, text: str) -> Dict[str, Any]:
        return offline_analyze(text)

    def _console_loop(self) -> None:
        while self.running:
            try:
                text = input("> ")
                if text.strip().lower() in {"exit", "quit"}:
                    self.running = False
                    break
                self.input_queue.put(text)
                if self.output_callback:
                    try:
                        self.output_callback(f"[Konsole] {text}")
                    except Exception:
                        logger.exception("Output-Callback (Konsole) warf Exception")
            except (EOFError, KeyboardInterrupt):
                self.running = False
                break
            except Exception:
                logger.exception("Fehler im Konsolenloop")
                time.sleep(0.1)
# =============================================================================
# TextRunner – LLM Chat + Modell-Verwaltung (produktiv, mehrstufig)
# =============================================================================

class _EchoModel:
    """Deterministischer Fallback ohne Abhängigkeiten – heuristische Kurzfassung."""
    def __init__(self, name: str = "echo/offline"):
        self.name = name

    def generate(self, prompt: str, max_tokens: int = 256) -> str:
        a = offline_analyze(prompt, max_summary_sentences=2)
        if a["summary"]:
            summary = " ".join(a["summary"])
        else:
            summary = (a["normalized"][:160] + "…") if len(a["normalized"]) > 160 else a["normalized"]
        senti = a["sentiment"]
        mood = "neutral"
        if senti > 0.25:
            mood = "positiv"
        elif senti < -0.25:
            mood = "negativ"
        tips = []
        if len(a["tokens"]) > 60:
            tips.append("Text gekürzt; Details auf Wunsch ausführbar.")
        if a["keywords"]:
            top = ", ".join(k["term"] for k in a["keywords"][:5])
            tips.append(f"Keywords: {top}.")
        tip_line = (" " + " ".join(tips)) if tips else ""
        return f"[{self.name}] Stimmung: {mood}. Kurzfassung: {summary}{tip_line}"


class TextRunner:
    """
    TextRunner – Kapselt LLM-Chat.
    Backends (lazy & optional):
      1) llama_cpp (GGUF/BIN)
      2) gpt4all
      3) transformers (lokale HF-Modelle)
      4) Echo/Heuristik (immer verfügbar)
    """

    def __init__(self):
        self.model = None
        self.model_name: Optional[str] = None
        self.backend: str = "echo"
        self._lock = threading.RLock()
        self._pref = os.environ.get("OROMA_LLM_BACKEND", "auto").strip().lower()
        self._llm_dir = os.environ.get(
            "OROMA_LLM_DIR",
            os.environ.get("OROMA_MODELS_DIR", "/opt/ai/oroma/models"),
        )

        # SnapChain-Logger (optional)
        try:
            from core import snapchain
            self._snapchain = snapchain
        except Exception:
            self._snapchain = None

    # -----------------------------------------------------------------
    # Modelle: entdecken & laden
    # -----------------------------------------------------------------
    def _detect_llama_models(self) -> List[str]:
        paths = []
        for ext in ("*.gguf", "*.bin"):
            paths.extend(glob.glob(os.path.join(self._llm_dir, "**", ext), recursive=True))
        return sorted(set(paths))

    def _detect_gpt4all_models(self) -> List[str]:
        base = os.path.join(self._llm_dir, "gpt4all")
        paths = []
        for directory in [self._llm_dir, base]:
            for ext in ("*.gguf", "*.bin"):
                paths.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
        return sorted(set(paths))

    def _detect_hf_models(self) -> List[str]:
        candidates = []
        for d in glob.glob(os.path.join(self._llm_dir, "**"), recursive=True):
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "config.json")):
                candidates.append(d)
        return sorted(set(candidates))

    def list_models(self) -> List[str]:
        models: List[str] = []
        try:
            models.extend(self._detect_llama_models())
        except Exception as e:
            log_suppressed(logger, key="wrappers_text_wrapper.pass.2", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        try:
            models.extend(self._detect_gpt4all_models())
        except Exception as e:
            log_suppressed(logger, key="wrappers_text_wrapper.pass.3", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        try:
            models.extend(self._detect_hf_models())
        except Exception as e:
            log_suppressed(logger, key="wrappers_text_wrapper.pass.4", msg="Suppressed exception (was: pass)", exc=e, level=logging.WARNING, interval_s=600)
        models.append("echo://offline")
        return sorted(set(models))

    # -----------------------------------------------------------------
    # Laden
    # -----------------------------------------------------------------
    def _try_import(self, modname: str):
        try:
            return __import__(modname, fromlist=["*"])
        except Exception as e:
            log_suppressed(logger, key="wrappers_text_wrapper.ret.5", msg="Suppressed exception (returning default)", exc=e, level=logging.DEBUG, interval_s=300)
            return None

    def _pick_backend_for_path(self, path: str) -> str:
        p = path.lower()
        if p.startswith("echo://"):
            return "echo"
        if p.endswith(".gguf") or p.endswith(".bin"):
            if self._try_import("llama_cpp"):
                return "llama"
            if self._try_import("gpt4all"):
                return "gpt4all"
            return "echo"
        if os.path.isdir(path):
            if os.path.isfile(os.path.join(path, "config.json")) and self._try_import("transformers"):
                return "hf"
        return "echo"

    def load_model(self, path_or_name: str) -> str:
        with self._lock:
            backend = self._pref
            if backend == "auto":
                backend = self._pick_backend_for_path(path_or_name)

            if backend == "echo":
                self.model = _EchoModel()
                self.model_name = path_or_name
                self.backend = "echo"
                logger.info("TextRunner: Echo/Offline aktiv.")
                return self.backend

            if backend == "llama":
                llama_cpp = self._try_import("llama_cpp")
                if llama_cpp is None:
                    raise RuntimeError("llama_cpp nicht installiert.")
                from llama_cpp import Llama  # type: ignore
                if not os.path.isfile(path_or_name):
                    raise FileNotFoundError(f"Llama-Modell nicht gefunden: {path_or_name}")
                self.model = Llama(model_path=path_or_name, n_ctx=2048, logits_all=False)
                self.model_name = path_or_name
                self.backend = "llama"
                logger.info("TextRunner: llama.cpp geladen (%s)", path_or_name)
                return self.backend

            if backend == "gpt4all":
                gpt4all = self._try_import("gpt4all")
                if gpt4all is None:
                    raise RuntimeError("gpt4all nicht installiert.")
                from gpt4all import GPT4All  # type: ignore
                if not os.path.isfile(path_or_name):
                    raise FileNotFoundError(f"GPT4All-Modell nicht gefunden: {path_or_name}")
                self.model = GPT4All(model_name=os.path.basename(path_or_name), model_path=os.path.dirname(path_or_name))
                self.model_name = path_or_name
                self.backend = "gpt4all"
                logger.info("TextRunner: GPT4All geladen (%s)", path_or_name)
                return self.backend

            if backend == "hf":
                transformers = self._try_import("transformers")
                if transformers is None:
                    raise RuntimeError("transformers nicht installiert.")
                from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline  # type: ignore
                if not os.path.isdir(path_or_name):
                    raise FileNotFoundError(f"HF-Modellordner nicht gefunden: {path_or_name}")
                tok = AutoTokenizer.from_pretrained(path_or_name, local_files_only=True)
                mdl = AutoModelForCausalLM.from_pretrained(path_or_name, local_files_only=True)
                self.model = pipeline("text-generation", model=mdl, tokenizer=tok, device=-1)
                self.model_name = path_or_name
                self.backend = "hf"
                logger.info("TextRunner: HF/transformers geladen (%s)", path_or_name)
                return self.backend

            # Fallback Echo
            self.model = _EchoModel()
            self.model_name = path_or_name
            self.backend = "echo"
            logger.info("TextRunner: Fallback Echo.")
            return self.backend

    # -----------------------------------------------------------------
    # Chat
    # -----------------------------------------------------------------
    def chat(self, prompt: str, max_tokens: Optional[int] = None) -> str:
        max_tokens = max_tokens or int(os.environ.get("OROMA_LLM_MAXTOKENS", "256"))
        with self._lock:
            if not self.model:
                return "❌ Kein Modell geladen – bitte load_model(...) aufrufen."
            try:
                if self.backend == "llama":
                    res = self.model(prompt=prompt, max_tokens=max_tokens, temperature=0.7, echo=False)  # type: ignore
                    txt = res["choices"][0]["text"]
                elif self.backend == "gpt4all":
                    txt = self.model.generate(prompt, max_tokens=max_tokens).strip()  # type: ignore
                elif self.backend == "hf":
                    outs = self.model(prompt, max_new_tokens=max_tokens, do_sample=True, temperature=0.7)  # type: ignore
                    if isinstance(outs, list) and outs:
                        txt = outs[0].get("generated_text", "")
                        txt = txt[len(prompt):].strip() if txt.startswith(prompt) else txt.strip()
                    else:
                        txt = ""
                else:
                    txt = self.model.generate(prompt, max_tokens=max_tokens)  # type: ignore

                # SnapChain-Log (falls verfügbar)
                if self._snapchain:
                    try:
                        self._snapchain.add({"role": "user", "content": prompt})
                        self._snapchain.add({"role": "assistant", "content": txt})
                    except Exception:
                        logger.debug("SnapChain-Logging übersprungen.")
                return txt
            except Exception as e:
                logger.error("TextRunner.chat Fehler: %s", e, exc_info=True)
                return f"❌ Fehler: {e}"


# =============================================================================
# Selbsttest
# =============================================================================

def _selftest() -> None:
    logging.basicConfig(level=logging.DEBUG)
    print("== TextWrapper Selbsttest ==")
    tw = TextWrapper(enable_console=False)
    tw.register_output_callback(lambda m: print(f"[CB] {m}"))
    snap, token, ana = tw.send_text(
        "Hallo Welt! Ich freue mich sehr. Aber es gab einen kleinen Fehler.",
        return_analysis=True,
    )
    print("[Snap]", getattr(snap, "to_dict", lambda: {"content": "n/a"})())
    print("[Token]", token)
    print("[Analyse]", json.dumps(ana, ensure_ascii=False, indent=2))

    print("\n== TextRunner Backend-Erkennung ==")
    tr = TextRunner()
    models = tr.list_models()
    print("Modelle (lokal erkannt):", json.dumps(models, ensure_ascii=False, indent=2))
    tr.load_model("echo://offline")
    print("Antwort:", tr.chat("Bitte fasse Folgendes kurz zusammen:\n" +
                              "Ich bin heute richtig gut drauf, auch wenn ein kleines Problem aufgetreten ist."))


if __name__ == "__main__":
    _selftest()