<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__games_and_tasks.md
  Part:   2
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

⸻

3. Problem & Motivation für Erweiterung

3.1 Problem
	•	Einige Expressions haben 10.000+ Tasks, alle korrekt.
	•	ORÓMA lernt zwar sehr solide diese wenigen Aufgaben,
aber:
	•	Neuheitswert (Novelty) sinkt,
	•	Curriculum-Durchläufe sind monoton,
	•	es gibt wenig Variation in Struktur und Kontext.

3.2 Ziel
	•	Die starke Curriculum-Mechanik behalten,
	•	aber den Inhaltsraum erweitern:
	1.	Mehr Arten von Aufgaben (z. B. Lückentexte, Zahlenfolgen, Vergleichsaufgaben).
	2.	Parametrisierte Aufgaben (randomisierte Zahlen, Patterns).
	3.	Optionale „Spielmodi“ (z. B. Sudoku, logischere Rätsel), wenn dem System langweilig ist.

⸻

4. Neue Aufgabentypen (Konzept)

4.1 Lückenaufgaben (Fill-in-the-blank)

Beispiele:
	•	3 + ? = 10 → Wahrheit: 7
	•	? + 5 = 12 → Wahrheit: 7
	•	10 - ? = 4 → Wahrheit: 6

Idee:
	•	Format im expr:
"3 + ? = 10"
oder "__ + 5 = 12".
	•	truth_json: enthält die fehlende Zahl und optional Kontext:

{
  "type": "fill_sum",
  "missing": "left",
  "a": 3,
  "b": 10,
  "solution": 7
}

Verwendung im Curriculum:
	•	Level 1–2: kleine Zahlen (1–20), nur Addition/Subtraktion.
	•	Level 3–4: auch einfache Multiplikation/Division.
	•	Level 5–6: gemischte Aufgaben, Kombination mit Brüchen.

⸻

4.2 Zahlenfolgen (Sequences)

Beispiele:
	•	„Wie lautet die nächste Zahl?“
	•	3, 6, 9, ? → 12
	•	2, 4, 8, 16, ? → 32
	•	5, 10, 15, ? → 20

Expr-Format:
	•	"3, 6, 9, ?" oder "Seq: 3, 6, 9, ?"

truth_json:

{
  "type": "sequence_linear",
  "sequence": [3, 6, 9],
  "step": 3,
  "solution": 12
}

Level-Zuordnung:
	•	Level 1–2: einfache lineare Folgen (konstanter Schritt).
	•	Level 3–4: Verdopplungsfolgen (2, 4, 8, 16, …).
	•	Level 5–6: gemischte Muster (z. B. +2, +4, +6, …) oder abwechselnde Schritte.

⸻

4.3 Logik-/Vergleichsaufgaben

Beispiele:
	•	7 > 3? → True
	•	5 + 2 > 10? → False
	•	x^2 ≥ 0 (Meta-Aufgabe)

Expr-Format:
	•	"7 > 3" oder "5 + 2 > 10".

truth_json:

{
  "type": "comparison",
  "op": ">",
  "left": 7,
  "right": 3,
  "solution": true
}

Verwendung:
	•	Dienen als leichtere Aufgaben zur Auflockerung,
	•	können in Wiederholungs-Blöcke eingebettet werden:
	•	z. B. nach schwerer Quadratik-Aufgabe eine simple Vergleichsfrage.

⸻

4.4 Sudoku & „wenn langweilig“ – Spielmodus

Idee:
	•	Wenn Curriculum-Level stabil durchlaufen werden (alle Aufgaben korrekt, hohe Confidence),
kann ORÓMA optionale Sudoku- oder Zahlenrätsel einstreuen.

Sudoku (4×4 als Start):
	•	Kleines Grid:

. 2 . 4
. . 3 .
3 . . .
. 1 . .

Expr-Format:
	•	"SUDOKU4X4: <kodiertes Grid>"
	•	oder Mensch-sichtbar im UI, intern:

{
  "type": "sudoku_4x4",
  "grid": [[0,2,0,4], ...],
  "solution": [[1,2,3,4], ...]
}

Wichtig:
	•	Sudoku ist eher Spiel-/Explorationsaufgabe als klassischer Calculator-Task.
	•	Integration:
	•	Entweder eigener Mode (z. B. setcalc/scicalc-ähnlich),
	•	oder spezielles Curriculum-Level „Spielzeit/Mentales Training“.

⸻

5. Technische Umsetzung: neues Modul core/curriculum_math_tasks.py

Ziel dieses Moduls:
	•	Keine DB-Zugriffe, keine direkten Side-Effects.
	•	Nur: Aufgabenobjekte generieren, die vom bestehenden Patch-1/Curriculum-Code konsumiert werden können.
	•	Vereinheitlichte Struktur:

task = {
  "expr": "3 + ? = 10",
  "truth": 7,
  "family": "fill_sum",
  "difficulty": "easy",
  "level": 1,
  "meta": {...}
}

Das Mapping von diesem Task-Dict auf die realen Tabellen (calculator_tasks, calculator_results, truth_json) bleibt im bereits bestehenden Calculator-/Curriculum-Code (z. B. in hooks_patch1 oder Adapter).

⸻

6. Geplante Einbindung ins Curriculum
	1.	Bestehende Kernaufgaben behalten:
	•	Die bisherigen Klassiker bleiben im Pool (1/2, 2/4, …),
	•	werden aber ergänzt, nicht ersetzt.
	2.	Stufenweise Einführung der neuen Tasks:
	•	Level 1–2:
	•	einfacher Mix aus bisherigen Aufgaben + Lückenaufgaben + leichte Sequenzen.
	•	Level 3–4:
	•	mehr Sequenzen, erste Vergleichsaufgaben, gemischte lineare Gleichungen.
	•	Level 5–6:
	•	Quadratische + Spezialzahlen (pi, phi, e) + komplexere Sequenzen.
	3.	Optionale Sudoku-/Spielzeit-Phase:
	•	Kann an Curriculum-Ende eingefügt werden („Alle Levels abgeschlossen → optional Sudoku“),
	•	oder als separater „Freizeit-Mode“ außerhalb des Curriculums.
	4.	Logging & Analyse:
	•	Jeder erzeugte Task bekommt im meta-Feld klare Tags:
	•	"family": "sequence" / "fill_sum" / "sudoku" etc.
	•	Damit können wir später:
	•	pro Task-Familie Auswertungen fahren,
	•	Gaps erkennen (z. B. schlecht bei Sequenzen, gut bei Gleichungen).

⸻

7. Nächste Schritte
	1.	Implementierung von core/curriculum_math_tasks.py
– Generatorfunktionen für:
	•	Lückenaufgaben (Addition/Subtraktion/Multiplikation),
	•	Sequenzen (lineare & Verdopplungsfolgen),
	•	einfache Vergleichsaufgaben,
	•	Sudoku-Stub (4×4) für Spielmodus.
	2.	Anpassung von core/curriculum_math.py / hooks_patch1:
	•	Integration der neuen Generatoren als zusätzliche Slots pro Level.
	3.	Tests & Beobachtung:
	•	Kurzläufe des Curriculums,
	•	DB-Analyse wie oben (tasks/results pro expr / family),
	•	Kontrolle, ob:
	•	Neuheitswert steigt,
	•	keine Aufgabe explodiert (100k Wiederholungen desselben Ausdrucks),
	•	SelfAssessment/Transfer weiterhin sauber mitlaufen.

Damit ist der Rahmen definiert; im nächsten Schritt kommt die konkrete Python-Implementierung des Aufgabengenerators.

---

## 2. Neues Modul: `core/curriculum_math_tasks.py`

Und hier der vorgeschlagene Code für das neue Modul mit Generatoren.  
Es fasst „nur“ die Logik für neue Aufgabenfamilien zusammen – **kein DB-Zugriff, keine ORÓMA-Imports**, damit es leicht in `hooks_patch1` oder `curriculum_math` eingebunden werden kann.

```python
#!/usr/bin/env python3
# =============================================================================
# Datei:     core/curriculum_math_tasks.py
# Projekt:   ORÓMA – KI-JWG-X1
# Version:   v1.0
# Stand:     2025-12-13
# Autor:     Jörg Werner + GPT-5.1 Thinking
# =============================================================================
#
# Zweck
# -----
# Dieses Modul bündelt Generatorfunktionen für erweiterte Mathe-Aufgaben,
# die vom bestehenden Patch-1-Curriculum (Calculator + SelfAssessment +
# TransferSnaps) genutzt werden können.
#
# WICHTIG:
#   - Dieses Modul schreibt NICHT in die Datenbank.
#   - Es kennt keine ORÓMA-spezifischen DB-Funktionen.
#   - Es erzeugt lediglich Python-Dicts, die einheitlich aufgebaut sind
#     und vom Curriculum-/Calculator-Code in `calculator_tasks` usw.
#     überführt werden können.
#
# Grundstruktur eines Tasks (VORSCHLAG)
# ------------------------------------
#   task = {
#       "expr": "3 + ? = 10",      # Anzeige-String für UI/Logs
#       "truth": 7,               # "richtige Lösung" (Zahl, bool oder strukturierter Typ)
#       "family": "fill_sum",     # Aufgabenfamilie (z. B. "fill_sum", "sequence", "compare", "sudoku4x4")
#       "difficulty": "easy",     # grobe Schwierigkeit ("easy" | "medium" | "hard")
#       "level": 1,               # vorgesehener Curriculum-Level (int)
#       "meta": {                 # zusätzliche Infos, frei erweiterbar
#           "hint": "...",
#           "params": {...},
#           "tags": ["curriculum", "math", "sequence"]
#       }
#   }
#
# Die Zuordnung dieses Dicts auf:
#   - calculator_tasks.expr
#   - calculator_tasks.truth_json
#   - weitere Felder (z. B. difficulty, level, family)
# erfolgt IM CURRICULUM-/CALCULATOR-CODE (z. B. hooks_patch1 / curriculum_math).
#
# Empfohlene Verwendung
# ---------------------
#   from core import curriculum_math_tasks as cm_tasks
#
#   task = cm_tasks.generate_fill_sum(level=1)
#   # oder:
#   task = cm_tasks.generate_sequence_linear(level=2)
#
#   # Anschließend:
#   #   - Task in calculator_tasks eintragen
#   #   - truth_json aus task["truth"] / task["meta"] bauen
#   #   - Curriculum-Logik updaten (Level, Slot, Wiederholung, ...)
#
# Environment / Konfiguration
# ---------------------------
# In diesem Modul selbst werden keine ENV-Variablen gelesen. Falls benötigt,
# kann der aufrufende Code die Parameter (Min/Max etc.) von außen steuern.
#
# =============================================================================

from __future__ import annotations

import math
import random
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple, Literal

Difficulty = Literal["easy", "medium", "hard"]

@dataclass
class MathTask:
    """
    Interne Repräsentation einer Mathe-Aufgabe für das Curriculum.

    Hinweis:
      - Diese Klasse wird NICHT direkt in der DB gespeichert.
      - Sie dient als saubere Zwischenstruktur, die problemlos
        in ein calculator_tasks-Row konvertiert werden kann.
    """
    expr: str
    truth: Any
    family: str
    difficulty: Difficulty
    level: int
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """
        Liefert einen einfachen Dict, der vom restlichen System
        (Calculator-/Curriculum-Code) weiterverarbeitet werden kann.
        """
        return asdict(self)

# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _make_task(
    expr: str,
    truth: Any,
    family: str,
    difficulty: Difficulty,
    level: int,
    params: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    hint: Optional[str] = None,
) -> MathTask:
    meta: Dict[str, Any] = {
        "family": family,
        "difficulty": difficulty,
        "level": level,
    }
    if params:
        meta["params"] = params
    if tags:
        meta["tags"] = tags
    if hint:
        meta["hint"] = hint
    return MathTask(
        expr=expr,
        truth=truth,
        family=family,
        difficulty=difficulty,
        level=level,
        meta=meta,
    )

def _rand_int(a: int, b: int) -> int:
    """Kleine Abstraktion, falls wir später Seed/Determinismus zentralisieren wollen."""
    return random.randint(a, b)

# =============================================================================
# 1) LÜCKENAUFGABEN ("3 + ? = 10")
# =============================================================================

def generate_fill_sum(level: int = 1) -> MathTask:
    """
    Erzeugt eine Lückenaufgabe der Form:
        a + ? = b
    oder
        ? + a = b

    Logik:
      - Für niedrige Levels kleine Zahlen, für höhere größere Spannweite.
    """
    # Zahlenbereiche je nach Level grob staffeln:
    if level <= 1:
        a_min, a_max = 1, 10
        b_min, b_max = 5, 20
        difficulty: Difficulty = "easy"
    elif level <= 3:
        a_min, a_max = 5, 30
        b_min, b_max = 10, 60
        difficulty = "medium"
    else:
        a_min, a_max = 10, 50
        b_min, b_max = 20, 100
        difficulty = "hard"

    a = _rand_int(a_min, a_max)
    b = _rand_int(b_min, b_max)
    # Stelle sicher, dass eine sinnvolle fehlende Zahl entsteht:
    # missing = b - a, muss positiv und integer sein
    missing = b - a
    if missing <= 0:
        # notfalls umdrehen: a + missing = b → a = b - missing, etc.
        # Einfach: tausche a/b und rechne neu
        a, b = min(a, b), max(a, b)
        missing = b - a

    # Zufällig entscheiden, ob das Fragezeichen links oder rechts steht:
    left_missing = bool(_rand_int(0, 1))

    if left_missing:
        expr = f"? + {a} = {b}"
        missing_pos = "left"
    else:
        expr = f"{a} + ? = {b}"
        missing_pos = "right"

    truth = missing

    params = {
        "a": a,
        "b": b,
        "missing_pos": missing_pos,
        "solution": missing,
        "type": "fill_sum",
    }

    hint = "Finde die Zahl, die zusammen mit der bekannten Zahl die Summe ergibt."

    return _make_task(
        expr=expr,
        truth=truth,
        family="fill_sum",
        difficulty=difficulty,
        level=level,
        params=params,
        tags=["math", "curriculum", "fill", "sum"],
        hint=hint,
    )

def generate_fill_diff(level: int = 2) -> MathTask:
    """
    Erzeugt eine Lückenaufgabe der Form:
        a - ? = b
    oder
        ? - a = b   (mit passenden Werten)

    Fokus: Subtraktion, einfache Umformung.
    """
    if level <= 2:
        a_min, a_max = 5, 20
        b_min, b_max = 0, 10
        difficulty: Difficulty = "easy"
    else:
        a_min, a_max = 10, 50
        b_min, b_max = 0, 25
        difficulty = "medium"

    a = _rand_int(a_min, a_max)
    b = _rand_int(b_min, b_max)

    # Fall 1: a - ? = b → ? = a - b
    missing = a - b
    if missing <= 0:
        # Korrigiere: stelle sicher, dass missing > 0
        a, b = max(a, b), min(a, b)
        missing = a - b

    left_missing = bool(_rand_int(0, 1))

    if left_missing:
        # ? - a = b → missing = a + b
        missing = a + b
        expr = f"? - {a} = {b}"
        missing_pos = "left"
    else:
        expr = f"{a} - ? = {b}"
        missing_pos = "right"

    truth = missing

    params = {
        "a": a,
        "b": b,
        "missing_pos": missing_pos,
        "solution": missing,
        "type": "fill_diff",
    }

    hint = "Überlege, welche Zahl fehlt, damit die Differenz stimmt."

    return _make_task(
        expr=expr,
        truth=truth,
        family="fill_diff",
        difficulty=difficulty,
        level=level,
        params=params,
        tags=["math", "curriculum", "fill", "diff"],
        hint=hint,
    )

# =============================================================================
# 2) ZAHLENFOLGEN ("3, 6, 9, ?")
# =============================================================================

def generate_sequence_linear(level: int = 1) -> MathTask:
    """
    Erzeugt eine einfache lineare Folge:
        start, start+step, start+2*step, ?, ...

    Beispiel:
        3, 6, 9, ?  → Lösung: 12
    """
    if level <= 1:
        start_min, start_max = 1, 5
        step_min, step_max = 1, 3
        length = 3
        difficulty: Difficulty = "easy"
    elif level <= 3:
        start_min, start_max = 1, 10
        step_min, step_max = 2, 5
        length = 4
        difficulty = "medium"
    else:
        start_min, start_max = 1, 20
        step_min, step_max = 2, 10
        length = 4
        difficulty = "hard"

    start = _rand_int(start_min, start_max)
    step = _rand_int(step_min, step_max)

    seq = [start + i * step for i in range(length)]
    solution = start + length * step

    expr = ", ".join(str(x) for x in seq) + ", ?"

    params = {
        "start": start,
        "step": step,
        "length": length,
        "sequence": seq,
        "solution": solution,
        "type": "sequence_linear",
    }

    hint = "Suche die Regel: Um wie viel wächst die Folge von Zahl zu Zahl?"

    return _make_task(
        expr=expr,
        truth=solution,
        family="sequence",
        difficulty=difficulty,
        level=level,
        params=params,
        tags=["math", "curriculum", "sequence", "linear"],
        hint=hint,
    )

def generate_sequence_geometric(level: int = 2) -> MathTask:
    """
    Erzeugt eine einfache geometrische Folge:
        start, start*factor, start*factor^2, ?, ...

    Beispiel:
        2, 4, 8, 16, ? → Lösung: 32
    """
    if level <= 2:
        start_min, start_max = 1, 5
        factor_min, factor_max = 2, 3
        length = 3
        difficulty: Difficulty = "medium"
    else:
        start_min, start_max = 1, 10
        factor_min, factor_max = 2, 4
        length = 4
        difficulty = "hard"

    start = _rand_int(start_min, start_max)
    factor = _rand_int(factor_min, factor_max)

    seq = [start * (factor ** i) for i in range(length)]
    solution = start * (factor ** length)

    expr = ", ".join(str(x) for x in seq) + ", ?"

    params = {
        "start": start,
        "factor": factor,
        "length": length,
        "sequence": seq,
        "solution": solution,
        "type": "sequence_geometric",
    }

    hint = "Achte darauf, mit welchem Faktor die Zahlen jeweils multipliziert werden."

    return _make_task(
        expr=expr,
        truth=solution,
        family="sequence",
        difficulty=difficulty,
        level=level,
        params=params,
        tags=["math", "curriculum", "sequence", "geometric"],
        hint=hint,
    )

# =============================================================================
# 3) VERGLEICHS-/LOGIKAUFGABEN
# =============================================================================

def generate_comparison(level: int = 1) -> MathTask:
    """
    Erzeugt eine Vergleichsaufgabe der Form:
        <Ausdruck> <op> <Ausdruck>  → Ergebnis: True/False  <!-- TODO linkfix: Ausdruck -> docs/module_fusion.md | op -> docs/roadmap.md -->

    Beispiele:
        7 > 3           → True
        5 + 2 > 10      → False
    """
    ops = [">", "<", ">=", "<=", "==", "!="]

    if level <= 1:
        difficulty: Difficulty = "easy"
        max_val = 10
        allow_expr = False
    elif level <= 3:
        difficulty = "medium"
        max_val = 20
        allow_expr = True
    else:
        difficulty = "hard"
        max_val = 50
        allow_expr = True

    def _rand_term() -> str:
        if not allow_expr or _rand_int(0, 1) == 0:
            return str(_rand_int(0, max_val))
        # kleine binäre Ausdrücke
        a = _rand_int(0, max_val)
        b = _rand_int(0, max_val)
        op = random.choice(["+", "-", "*"])
        return f"{a}{op}{b}"

    left_str = _rand_term()
    right_str = _rand_term()
    op = random.choice(ops)

    # Bewerte die Ausdrücke sicher mit eval in eingeschränktem Kontext:
    def _safe_eval(expr: str) -> float:
        return float(eval(expr, {"__builtins__": None}, {}))

    left_val = _safe_eval(left_str)
    right_val = _safe_eval(right_str)

    expr = f"{left_str} {op} {right_str}"

    # Wahrheit berechnen:
    if op == ">":
        truth = left_val > right_val
    elif op == "<":
        truth = left_val < right_val
    elif op == ">=":
        truth = left_val >= right_val
    elif op == "<=":
        truth = left_val <= right_val
    elif op == "==":
        truth = left_val == right_val
    else:
        truth = left_val != right_val

    params = {
        "left_expr": left_str,
        "right_expr": right_str,
        "left_val": left_val,
        "right_val": right_val,
        "op": op,
        "type": "comparison",
        "solution": truth,
    }

    hint = "Vergleiche die beiden Ausdrücke und entscheide, ob die Beziehung stimmt."

    return _make_task(
        expr=expr,
        truth=truth,
        family="comparison",
        difficulty=difficulty,
        level=level,
        params=params,
        tags=["math", "curriculum", "logic", "comparison"],
        hint=hint,
    )

# =============================================================================
# 4) SUDOKU 4x4 – Stub für „Spielmodus bei Langeweile“
# =============================================================================

def generate_sudoku_4x4(level: int = 1) -> MathTask:
    """
    Erzeugt ein einfaches 4x4-Sudoku (Stub):

    - Wertebereich 1..4
    - Lösung ist vollständig, Puzzle hat ein paar leere Felder (0)
    - expr: "SUDOKU4X4: <kompakte Kodierung>"
    - truth: vollständige Lösung (4x4-Liste)

    WICHTIG:
      - Diese Aufgabe ist für einen "Spielmodus" gedacht, nicht zwingend
        für den bestehenden Calculator-UI-Flow.
      - Die Integration (Anzeige im UI, Antwortformat) muss separat
        definiert werden.
    """
    # Einfaches festes Beispiel + zufällige Permutation der Zahlen 1..4
    base_solution = [
        [1, 2, 3, 4],
        [3, 4, 1, 2],
        [2, 1, 4, 3],
        [4, 3, 2, 1],
    ]

    # Optional: zufällige Permutation der Symbole
    symbols = [1, 2, 3, 4]
    random.shuffle(symbols)
    mapping = {i + 1: symbols[i] for i in range(4)}

    solution = [[mapping[v] for v in row] for row in base_solution]

    # Ein paar Felder leeren (0 = leer)
    puzzle = [row[:] for row in solution]
    holes = 6 if level <= 2 else 8
    coords = [(r, c) for r in range(4) for c in range(4)]
    random.shuffle(coords)
    for r, c in coords[:holes]:
        puzzle[r][c] = 0

    # Kompakte Kodierung für expr (Zeilen durch "/" getrennt)
    def _encode_grid(grid):
        return "/".join("".join(str(v) for v in row) for row in grid)

    encoded_puzzle = _encode_grid(puzzle)
    encoded_solution = _encode_grid(solution)

    expr = f"SUDOKU4X4: {encoded_puzzle}"

    params = {
        "type": "sudoku4x4",
        "grid": puzzle,
        "solution_grid": solution,
        "encoded_puzzle": encoded_puzzle,
        "encoded_solution": encoded_solution,
    }

    hint = "Fülle das 4x4-Gitter so, dass jede Zahl von 1 bis 4 in jeder Zeile und Spalte genau einmal vorkommt."

    return _make_task(
        expr=expr,
        truth=encoded_solution,  # oder direkt 'solution' – Mapping übernimmt der Caller
        family="sudoku4x4",
        difficulty="medium",
        level=level,
        params=params,
        tags=["math", "game", "sudoku", "curriculum_optional"],
        hint=hint,
    )

# =============================================================================
# 5) TASK-POOL-HILFEN (für Curriculum-Code)
# =============================================================================

def get_task_families() -> List[str]:
    """
    Liefert die verfügbaren Aufgabenfamilien dieses Moduls.
    """
    return [
        "fill_sum",
        "fill_diff",
        "sequence_linear",
        "sequence_geometric",
        "comparison",
        "sudoku4x4",
    ]

def generate_random_task(level: int) -> MathTask:
    """
    Erzeugt eine zufällige Aufgabe aus dem Pool dieses Moduls
    für einen gegebenen Curriculum-Level.

    Die Verteilung kann bei Bedarf angepasst werden.
    """
    # Gewichtung: Sudoku eher selten im normalen Curriculum
    families = []

    if level <= 2:
        families = [
            "fill_sum",
            "sequence_linear",
            "comparison",
        ]
    elif level <= 4:
        families = [
            "fill_sum",
            "fill_diff",
            "sequence_linear",
            "sequence_geometric",
            "comparison",
        ]
    else:
        families = [
            "fill_sum",
            "fill_diff",
            "sequence_linear",
            "sequence_geometric",
            "comparison",
            "sudoku4x4",  # nur selten, daher ggf. zusätzlich per Zufall filtern
        ]

    family = random.choice(families)

    if family == "fill_sum":
        return generate_fill_sum(level=level)
    if family == "fill_diff":
        return generate_fill_diff(level=level)
    if family == "sequence_linear":
        return generate_sequence_linear(level=level)
    if family == "sequence_geometric":
        return generate_sequence_geometric(level=level)
    if family == "comparison":
        return generate_comparison(level=level)
    if family == "sudoku4x4":
        return generate_sudoku_4x4(level=level)

    # Fallback sollte praktisch nie passieren:
    return generate_fill_sum(level=level)

⸻

3. Wie weiter?

Mein Vorschlag für die nächsten Schritte bei dir:
	1.	Datei anlegen:
	•	core/curriculum_math_tasks.py mit obigem Inhalt speichern.
	2.	Im Curriculum-Code andocken:
	•	In core/curriculum_math.py oder core/hooks_patch1.py:
	•	from core import curriculum_math_tasks as cm_tasks
	•	an geeigneten Stellen (z. B. bei Wiederholungsblöcken oder Zusatzslots pro Level) statt eines festen expr einen task = cm_tasks.generate_random_task(level) ziehen und in calculator_tasks schreiben.
	3.	Kleinen Testlauf fahren:
	•	Kurz-Lauf des Curiculums starten
	•	in calculator_tasks nach neuen expr suchen wie:
	•	"3 + ? = 10",
	•	"3, 6, 9, ?",
	•	"SUDOKU4X4: ...".
	•	Gucken, ob Logs weiterhin lesbar sind und ob die neuen Aufgaben mitlaufen, ohne das alte Schema zu zerstören.

Wenn du magst, kann ich dir im nächsten Schritt ganz konkret zeigen, wie du im bestehenden Curriculum-Code (so wie er bei dir läuft) einen ersten Slot pro Level mit generate_random_task(level) füllst – ohne deine bisherigen Kernaufgaben zu verlieren.

<a id="docs_curriculum_mathandpuzzles_v1_1_md"></a>

## Quelle: `docs/curriculum_mathandpuzzles_v1_1.md`

**Originaltitel:** ORÓMA – Curriculum für Rechenaufgaben & Logikpuzzles (v1.1)

Ich habe aktuell Zugriff auf deine letzte ZIP /mnt/data/oroma_20251213_114645_with_db_patched_objects_ui.zip und schreibe dir jetzt die Markdown-Spezifikation für das erweiterte Curriculum mit neuen Aufgaben- und Puzzle-Typen (inkl. Sudoku/Sequenzen), so dass wir es danach gezielt hinterfragen und testen können.

⸻

# ORÓMA – Curriculum für Rechenaufgaben & Logikpuzzles (v1.1)

**Datei:** `docs/curriculum_mathandpuzzles_v1_1.md`  
**Projekt:** ORÓMA – KI-JWG-X1  
**Stand:** 2025-12-13  
**Autor:** Jörg Werner + GPT-5.1 Thinking  

---

## 1. Zweck & Ausgangslage

Dieses Dokument präzisiert das **Calculator-/Curriculum-System** in ORÓMA und erweitert es:

- von wenigen, stark wiederholten Aufgaben  
  (`1/2`, `2/4`, `x^2+2x+1=0`, `x^2-4=0`, `2x+3=7`, `3x-4=5`, `pi`, `phi`, `e`)
- hin zu einem **breiten Set einfacher bis mittlerer Mathe- und Logikaufgaben**, inkl.:
  - Ergänzungsaufgaben wie `3 + ? = 10`
  - Zahlenfolgen wie `3, 6, 9, ?`
  - einfache Gleichungen / Umformungen
  - kleine **Logik-/Zahlenpuzzles** (perspektivisch auch Sudoku-Mini-Varianten)

Ziel:

- mehr **Varianz** in den Aufgaben,
- sinnvolle **Curriculum-Stufen** (Levels),
- Wiederholungen, die sich *lohnend* anfühlen (nicht nur „immer dieselben 9 Ausdrücke“),
- ein Einstiegspunkt für **boredom-busting**: wenn ORÓMA „langweilig“ ist, darf es kleine Puzzles spielen.

---

## 2. Status heute (Beobachtung aus Logs & DB)

### 2.1 Technische Basis

- Tabellen:
  - `calculator_tasks` – Aufgaben, z. B. `expr="x^2+2x+1=0"`, `truth_json`, `meta_json`.
  - `calculator_results` – Ergebnisse mit `task_id`, `correct`, `got_json`, `ts`, `meta_json`.
- Hooks (Patch 1):
  - erzeugen **Calculator-Tasks**,
  - speichern **Results** und **SelfAssessment-Snaps**,
  - erzeugen **TransferSnaps**,
  - loggen Curriculum-Events, z. B.:
    - `[Curriculum] Level 3, Aufgabe 2/4 erledigt`
    - `[Curriculum] Wiederholung erledigt: x^2+2x+1=0`
- MangelSpeak/TTS:
  - reagiert auf niedrige Confidence/Boredom:
    - „Meine Confidence ist niedrig: 0.55. Meine Coverage ist 1.00. Neuheit: 0.40. Zeit bis Ziel (normiert): 0.50. Ich passe mein Üben an.“

### 2.2 Problem (aus deiner Sicht)

- Die **Aufgabenauswahl ist extrem eng**:
  - nur wenige feste Ausdrücke,
  - sehr viele Wiederholungen pro Ausdruck (teilweise >10.000 Tasks pro Typ).
- Im Log sieht man:
  - Level-Aufstiege,
  - Wiederholungsmarker,
  - MangelSpeak-Texte,
- aber die **konkreten Ergebnisse** und die Variation der Ausdrücke sind im UI kaum sichtbar.

---

## 3. Design-Ziel für v1.1

Wir definieren ein **erweitertes Curriculum**, das:

1. mehrere **Aufgabentypen** unterstützt (nicht nur „Berechne Wert von expr“),
2. jede Aufgabe sauber in `calculator_tasks`/`calculator_results` abbildet,
3. das bestehende Level-System (Level 1–6, X/Y Aufgaben) weiter nutzt,
4. mit **MangelSpeak**/Empathie/Confidence harmoniert,
5. später an **Sudoku/Spiele** anschließbar ist.

---

## 4. Neue Aufgabentypen

### 4.1 Ergänzungsaufgaben (Fill-in-the-Blank)

**Beispiel:**

- `3 + ? = 10`
- `? + 5 = 12`
- `7 + ? = 15`

**Semantik:**

- Es geht um die **fehlende Zahl**.
- Darstellung in `calculator_tasks`:

  - `expr`: `"3 + ? = 10"`
  - `truth_json`: `{"missing": 7}`
  - `meta_json` (Beispiele):

    ```json
    {
      "kind": "fill_simple_add",
      "a": 3,
      "b": 10,
      "op": "+",
      "difficulty": 1
    }
    ```

**Varianten:**

- einfache Plusaufgaben (<= 20),
- ggf. später Minusvarianten wie `? - 3 = 7`.

### 4.2 Zahlenfolgen / Sequenzen

**Beispiele:**

- `3, 6, 9, ?` → `12`
- `2, 4, 6, 8, ?` → `10`
- `5, 10, 15, ?` → `20`
- spätere Stufen:
  - `1, 2, 4, 8, ?` (Verdopplung),
  - `1, 3, 6, 10, ?` (Dreieckszahlen).

**Darstellung:**

- `expr`: `"seq: 3, 6, 9, ?"`
- `truth_json`: `{"next": 12}`
- `meta_json`:

  ```json
  {
    "kind": "sequence_arith",
    "pattern": "+3",
    "step": 3,
    "difficulty": 1
  }

4.3 Einfache Gleichungen / Umformungen

Ergänzung zu bestehenden:
	•	x + 7 = 10 → x = 3
	•	2x + 3 = 7 → x = 2
	•	3x - 4 = 5 → x = 3
	•	neue Kandidaten:
	•	x - 5 = 2 → x = 7
	•	4x = 12 → x = 3

Darstellung (Beispiel):
	•	expr: "x - 5 = 2"
	•	truth_json: {"x": 7}
	•	meta_json:

{
  "kind": "linear_equation_1d",
  "difficulty": 2
}


4.4 Quadratische Mini-Aufgaben (weiter wie bisher)
	•	x^2 + 2x + 1 = 0 → x = -1
	•	x^2 - 4 = 0 → x = -2 oder 2

Darstellung (Beispiel):
	•	expr: "x^2 + 2x + 1 = 0"
	•	truth_json: {"solutions": [-1]}
	•	meta_json:

{
  "kind": "quadratic_eq",
  "form": "(x+1)^2",
  "difficulty": 3
}


4.5 Zahlkonstanten & Begriffe (pi, e, phi)
	•	Aufgaben wie:
	•	„pi ≈ ?“ → 3.14
	•	„phi ≈ ?“ → 1.62
	•	„e ≈ ?“ → 2.72

Darstellung:
	•	expr: "pi"
	•	truth_json: {"approx": 3.14}
	•	meta_json:

{
  "kind": "constant_approx",
  "symbol": "pi",
  "difficulty": 2
}


4.6 Mini-Puzzles (Sudoku & Co., konzeptionell)

Ziel: Wenn das Curriculum „durch“ ist oder Langeweile erkannt wird, kann ORÓMA ein kleines Puzzle „spielen“.

Erste Stufe: sehr kleine Sudoku-Variante
	•	z. B. 4×4-Sudoku mit den Symbolen {1, 2, 3, 4}.
	•	Aufgabe:
	•	expr: "sudoku4x4:#<id>"
	•	truth_json: komplette Lösung (4×4-Grid),
	•	meta_json: Start-Grid, Schwierigkeit, Anzahl Leerfelder.

Beispiel-JSON im meta_json:

{
  "kind": "sudoku_4x4",
  "difficulty": 1,
  "grid_start": [
    [0, 0, 3, 4],
    [3, 4, 0, 0],
    [0, 0, 4, 2],
    [4, 2, 0, 0]
  ],
  "grid_solution": [
    [1, 2, 3, 4],
    [3, 4, 1, 2],
    [2, 1, 4, 3],
    [4, 3, 2, 1]
  ]
}

	•	Für den Anfang reicht:
	•	CPU-generierte Sudoku-Instanzen (kein NPU-Thema).
	•	ORÓMA löst sie oder „spielt“ sie Schritt für Schritt.
	•	Wichtig:
	•	Logs/Results sollten Teil-Lösungen speichern können
(z. B. „Feld (0,0) = 1“ als Zwischenaufgabe).

⸻

5. Curriculum-Mapping (Level → Aufgabentypen)

5.1 Levels (bestehende Struktur beibehalten)
	•	Level 1: 4 Aufgaben
	•	Level 2: 4 Aufgaben
	•	Level 3: 4 Aufgaben
	•	Level 4: 3 Aufgaben
	•	Level 5: 3 Aufgaben
	•	Level 6: 3 Aufgaben
→ dann „Alle Levels abgeschlossen 🎉 – Neustart bei Level 1“

5.2 Vorschlag Verteilung v1.1

Level 1 – Basis-Arithmetik & Ergänzungsaufgaben
	•	2× Ergänzungsaufgabe (3 + ? = 10, ? + 5 = 12, …).
	•	1× Sequenz mit kleinem Schritt (3, 6, 9, ?).
	•	1× einfacher Bruch (1/2, 2/4 o. ä.).

Level 2 – Brüche & Sequenzen
	•	2× Bruch-Äquivalenzen (1/2, 2/4, 3/6, …).
	•	2× Sequenzen:
	•	arithmetisch (+2, +3),
	•	evtl. erste Multiplikationsfolgen (2, 4, 8, ?).

Level 3 – Lineare Gleichungen
	•	3× Aufgaben wie x + 7 = 10, 2x + 3 = 7, 3x - 4 = 5, x - 5 = 2.
	•	1× Wiederholung aus Level 1–2 als Konsolidierung.

Level 4 – Quadratische Formen
	•	2× quadratische Standard-Aufgaben (x^2+2x+1=0, x^2-4=0 + Variationen).
	•	1× einfache Sequenz/Bruch als „Auflockerung“.

Level 5 – Konstanten & Mischformen
	•	1× pi, 1× e, 1× phi (oder je nach Curriculum-Status ausgewählt).
	•	optional gemischt mit Sequenz/Bruch, abhängig von Erfolg.

Level 6 – „Boredom-Break“ & Wiederholungen
	•	1–2× Wiederholungsaufgaben (aus „Fehlerhistorie“ oder lange nicht geübten Ausdrücken).
	•	1× Mini-Puzzle:
	•	Sequenz mit leichtem Twist,
	•	oder kleine Sudoku-Teilaufgabe (z. B. „Trage die fehlende Zahl in Zelle (0,0) ein“).

Wenn alle Levels abgeschlossen und:
	•	Coverage nahe 1.0,
	•	Neuheit niedrig,
	•	Confidence ok → Schwerpunkt auf Puzzles/Varianten.
	•	Confidence niedrig → Schwerpunkt auf Wiederholungen bekannter Aufgaben.

⸻

6. Random-Generator (Konzept)

Die Generatoren laufen weiter im Patch1-Hook / Calculator-Engine, erzeugen aber variantenreiche Aufgaben.

6.1 Beispiel-Pseudocode: Ergänzungsaufgaben

def gen_fill_add_task():
    # Zielsumme zwischen 8 und 20
    total = rng.randint(8, 20)
    a = rng.randint(1, total - 1)
    missing = total - a

    expr = f"{a} + ? = {total}"
    truth = {"missing": missing}
    meta = {
        "kind": "fill_simple_add",
        "a": a,
        "total": total,
        "difficulty": 1,
    }
    return expr, truth, meta

6.2 Beispiel-Pseudocode: Sequenzaufgaben

def gen_sequence_arith_task():
    step = rng.choice([1, 2, 3, 5])
    start = rng.randint(0, 10)
    length = 3  # 3 sichtbare Zahlen
    seq = [start + i * step for i in range(length)]
    next_val = start + length * step

    expr = "seq: " + ", ".join(str(x) for x in seq) + ", ?"
    truth = {"next": next_val}
    meta = {
        "kind": "sequence_arith",
        "pattern": f"+{step}",
        "step": step,
        "difficulty": 1,
    }
    return expr, truth, meta

6.3 Sudoku (später)
	•	eigener Generator (z. B. tools/gen_sudoku4.py),
	•	Integration in Calculator-Curriculum über:
	•	expr="sudoku4x4:#<id>",
	•	truth_json/meta_json wie oben definiert,
	•	Ergebnisse als Teil-Lösungen in calculator_results.

⸻

7. Verbindung zu Empathie/MangelSpeak/Boredom

Trigger-Idee:
	•	Wenn:
	•	coverage ≈ 1.0,
	•	novelty < 0.4,
	•	bestimmte Ausdrücke sehr oft wiederholt wurden,
	•	dann:
	•	MangelSpeak-Text wie bisher:
	•	„Meine Confidence ist niedrig: … Ich passe mein Üben an.“
	•	aber Aufgaben-Generator schaltet von:
	•	„reine Wiederholung“ → auf „Mix aus Wiederholung + neuen Puzzle-Aufgaben“.

Einfache Heuristik:
	•	Pro Lauf:
	•	max. 50 % Wiederholungsaufgaben,
	•	mind. 50 % variantenreiche Aufgaben (Fill-in, Sequenzen, einfache Gleichungen, Puzzles).

⸻

8. Tests & Validierungsideen

Nach Implementierung:
	1.	DB-Statistik prüfen
	•	SELECT DISTINCT expr FROM calculator_tasks LIMIT 50;
	•	Erwartung:
	•	nicht nur 1/2, 2/4, ..., sondern eine bunte Mischung.
	2.	Verhältnis Tasks/Results
	•	weiterhin tasks == results pro expr (keine Task-Leichen).
	3.	Curriculum-Logs
	•	prüfen, ob in Logs je Level die erwarteten Typen auftauchen
(Fill-in, Sequenz, Gleichungen, Konstanten, Puzzles).
	4.	MangelSpeak-Botschaften
	•	beobachten, ob nach „langweiligen“ Phasen mehr neue Aufgaben erzeugt werden.
	5.	Sudoku-Minimaltest (wenn umgesetzt)
	•	ein 4×4-Sudoku erzeugen,
	•	eine Teilaufgabe lösen,
	•	prüfen, ob:
	•	Task in calculator_tasks,
	•	Result in calculator_results,
	•	Meta sauber gefüllt.

⸻

9. Fazit

Mit dieser Erweiterung:
	•	bleibt der Kern (Calculator-Engine, Curriculum, SelfAssessment, TransferSnaps) unangetastet,
	•	aber die Aufgabenvielzahl und die Lernqualität steigen deutlich:
	•	mehr Variation,
	•	mehr Kontext (Fill-in, Sequenzen, Gleichungen),
	•	Puzzles als „geistige Spielecke“, wenn es sonst zu monoton wird.

Nächster Schritt nach diesem Dokument:
	•	konkrete Implementierung der Generator-Funktionen in der Calculator-Engine / Patch1-Hooks,
	•	UI-Anpassungen, damit die neuen Aufgabentypen und Ergebnisse besser sichtbar sind.

Wenn du magst, können wir im nächsten Schritt direkt den Code-Split machen:  
1) `core/calculator_engine.py` (Aufgaben-Generatoren ergänzen),  
2) eine kleine `tools/calc_curriculum_probe.py`, die genau solche Stats wie dein SQL-Snippet ausgibt – nur schöner.

