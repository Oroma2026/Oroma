<!--
  ORÓMA Docs (auto-split for chat)
  Source: .__tmp__Konzeption_Architektur_v3.7.1.md
  Part:   1
  Max lines per file: 2000
  Generated: 2025-12-28 14:33:14
-->

📄 /opt/ai/oroma/docs/Konzeption_Architektur_v3.7.1.md
Projekt: ORÓMA
Version: v3.7.1 (Regelarchiv-First + Kanonraum + PolicyEngine v3.8 + DreamWorker v3.7)
Stand: 2025-10-18

⸻

ORÓMA – Konzeption & Architektur v3.7.1

Pfad: /opt/ai/oroma/docs/Konzeption_Architektur_v3.7.1.md
Projekt: ORÓMA
Version: v3.7.1 (Regelarchiv-First + Kanonraum + PolicyEngine v3.8 + DreamWorker v3.7)
Stand: 2025-10-18

⸻

1) Ziel & Kontext

v3.7.1 verlagert den Schwerpunkt auf ein **Regelarchiv-First**-Design und macht
die Laufzeitentscheidungen deterministischer und erklärbarer:

• **Kanonraum**: Zustände werden kanonisiert (Symmetrien/Normalformen), sodass
  äquivalente Situationen zusammenfallen. → dichteres Lernen, weniger Rauschen.
• **DecisionStack**: Regelarchiv → PolicyEngine → Heuristik (Adapter).
• **PolicyEngine v3.8-r3**: lernt (state, action)-Qualitäten aus SnapChains,
  unterstützt `status='compressed'` und exportiert Regeln ins Archiv.
• **DreamWorker v3.7**: self-healing Replay, MetaSnaps, Mutation, Vergessen,
  LTM-Weight-Sync; vector-first Normalisierung für heterogene Exporte.
• **Adapter-Schicht**: gemeinsame API für TTT, Audio, Video, (später) LLM/NLU,
  inkl. Feature-Extraktion, Kanonisierung und Aktionsmapping.

Ziel: Robust „menschlich“ entscheiden: Wahrnehmung → Verdichtung → Erfahrung → Regel.

⸻

2) Neue & geänderte Komponenten (v3.7.1)

Neu
• core/decision_engine.py
  – Regelarchiv-First; Fallback PolicyEngine, danach Heuristik (Adapter).
  – Robustes Rule-Parsing; Score = f(weight, q, n), Legalitätsprüfung per Adapter.
• core/policy_engine.py (v3.8-r3)
  – Training direkt aus DB (inkl. status='compressed').
  – Namespace/Origin-Handling; robustes Upsert; Archiv-Export.
• core/ttt_adapter.py (aktualisiert)
  – Kanonraum (D4-Symmetrien), robustes Extract für 9D-Vektoren, Fallback „Mitte>Ecken>Kanten“.

Geändert
• core/dream_worker.py (v3.7)
  – Vector-First-Loader, Meta-Zentroid, Mutation, Forgetting (weight-decay + Kompression),
    optionale Loops (Research/Missions/Curriculum/Auto-Tuner), LTM-Dedupe/Weight-Sync.
• core/regelarchiv.py
  – Upsert/Prune; Regeln als textuelle Statements mit Gewicht.

Optional (unverändert aktivierbar)
• reward.py, episodic.py, explain.py – via leichte Adapter nutzbar.
• hypothesis.py, missions.py, curriculum.py, auto_tuner.py.

⸻

3) Datenmodell / Schema (relevant)

Unverändert (idempotent via ensure_schema)
• snapchains(id, origin, status, weight, quality, blob)
• meta_snaps(id, label, score, sources)
• rules(id, content, weight, active)                 ← Regelarchiv (mensch-lesbar)
• policy_rules(namespace, state_hash, action, n, pos, neg, draw, q, last_ts, centroid)
• rewards_log(...), episodic(...), metrics/coverage_log(...)

Empfohlene Indizes (Leistung)
• CREATE INDEX IF NOT EXISTS idx_rules_active_ns ON rules(active);
• CREATE INDEX IF NOT EXISTS idx_policy_ns_sh ON policy_rules(namespace, state_hash);
• CREATE INDEX IF NOT EXISTS idx_snapchains_status ON snapchains(status);

Status-Semantik
• snapchains.status ∈ {'active','compressed', NULL}; Training kann compressed optional einbeziehen.

⸻

4) Laufzeit-Architektur (vereinfacht)

Sensoren (UI/AV) → Adapter (Feature-Extraktion + Kanonisierung)
   → SnapChains (Sequenzen) → DreamWorker (offline: Replay/Meta/Mut/Fading)
   → PolicyEngine (Training aus DB, optional compressed) → Export → Regelarchiv

Zur Laufzeit (Entscheidung):
   DecisionEngine(adapter)
      1) Regeln (Archiv) matchen state_hash (Kanonraum)
      2) Beste legale Aktion wählen
      3) Fallback: PolicyEngine(adapter).choose_action(...)
      4) Fallback: adapter.fallback_action(...)

⸻

5) Adapter-API (Domänen: TTT, Audio, Video, LLM)

Pflicht (von PolicyEngine/DecisionEngine genutzt)
• namespace: str
• extract_vectors(chain_or_dict) -> List[List[float]]         # Sequenz von State-Vektoren
• final_outcome(final_vec) -> int                              # +1 / 0 / −1
• action_from_delta(prev_vec, next_vec) -> Optional[str]       # welche Aktion wurde ausgeführt?
• canonicalize(vec) -> (state_hash: str, perm: List[int], inv_perm: List[int])
• map_action_through_perm(action_str, perm_or_invperm) -> str  # Aktion in/aus Kanonraum mappen
• legal_actions(vec) -> List[str]
• fallback_action(vec) -> Optional[str]

Optional (Komfort)
• vectorize_board(board_obj) -> List[float]

Hinweise für AV/LLM-Adapter
• Audio: Feature-Frames (z. B. 32…128D), Kanonisierung via Zeit-/Pitch-Invarianten, Aktionen als „intent:…“.
• Video: Detektionen/Tracks („person@2m“, „gaze_me“), Kanonisierung über Geometrie (Rotation/Flip), Aktionen „focus:left“, „greet“.
• LLM/NLU: Token-/Intent-Zustände + „Antwort-Aktionen“; Kanonisierung z. B. über Normalisierung/Slots.

⸻

6) Entscheidungsfluss (Scoring & Legalität)

1) `adapter.canonicalize(state_vec)` → (state_hash, perm, inv_perm)
2) Regeln lesen (`rules.active=1`, Namespace im Content):
     "game:tictactoe :: IF state='___X__O__' THEN action='4'  // q=0.73 n=120"
3) Score pro Regel: `score = 0.6*weight + 0.35*map(q) + 0.05*min(n,2000)/1000`
   (map(q): [-1..1] → [0..1])
4) Nur **legale** Aktionen (per Adapter) zulassen.
5) Wenn keine Regel → PolicyEngine(policy_rules) im Kanonraum befragen;
   Aktion via `inv_perm` in Originalkoordinaten zurückmappen.
6) Wenn keine Policy → `adapter.fallback_action`.

⸻

7) Prozesse & CLI

DreamWorker (offline Lernschritte)
• Single-Run (Timer/Oneshot):
  PYTHONPATH=/opt/ai/oroma python3 -m core.dream_worker --interval 0 --verbose
• Loop alle 60 s:
  PYTHONPATH=/opt/ai/oroma python3 -m core.dream_worker --interval 60

PolicyEngine (Training/Export)
• Training (nur active):
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --train-db --limit 20000 --namespace game:tictactoe --verbose
• Training inkl. compressed:
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --train-db --limit 20000 --namespace game:tictactoe --include-compressed --verbose
• Export ins Regelarchiv:
  PYTHONPATH=/opt/ai/oroma python3 -m core.policy_engine \
    --export-archiv --namespace game:tictactoe --min-n 3 --min-abs-q 0.15 --verbose

DecisionEngine (Beispiel – TTT)
• Python-Nutzung:
  from core.decision_engine import TTTDecision
  dec = TTTDecision()
  act = dec.choose_action_from_board(["X","O","","","","","","",""])

⸻

8) Konfiguration (ENV)

General
• OROMA_LOG_DIR=/opt/ai/oroma/logs
• OROMA_SNAPCHAINS=/opt/ai/oroma/data/snapchains

DreamWorker
• OROMA_ENABLE_METASNAP=true|false
• OROMA_FORGET_DECAY_RATE=0.95
• OROMA_FORGET_THRESHOLD=0.20
• ENABLE_RESEARCH/MISSIONS/CURRICULUM=true|false
• RESEARCH_BUDGET_PER_NIGHT=0

Policy/Archiv
• Keine Pflicht-ENV; Namespace über Adapter/CLI.

⸻

9) Rollout-Checkliste

1. Dateien deployen:
   • core/decision_engine.py
   • core/policy_engine.py (v3.8-r3)
   • core/ttt_adapter.py (aktualisiert)
   • core/dream_worker.py (v3.7)
   • core/regelarchiv.py (falls nicht vorhanden/aktualisiert)

2. DB-Schema sicherstellen:
   python3 -m core.sql_manager --ensure

3. (Optional) Indizes ergänzen (siehe Abschnitt 3).

4. Systemd-Timer für DreamWorker (falls genutzt) aktivieren:
   systemctl enable --now oroma-dream.timer

5. Training & Export anstoßen (einmalig):
   policy_engine --train-db ... && policy_engine --export-archiv ...

6. App/Agent-Loop: DecisionEngine verwenden (statt direktem Policy-Lookup).

⸻

10) Smoke-Tests

• TTT-End-to-End:
  – SnapChains vorhanden → policy_engine --train-db --include-compressed
  – export_archiv → rules füllen
  – DecisionEngine wählt eine **legale** Aktion; bei leerem Archiv greift Policy/Fallback.

• DreamWorker:
  – Single-Run erzeugt MetaSnaps/Mutationen (Logs: dream.out.log)
  – Forgetting reduziert `weight`; bei Unterschreiten `compress_threshold` → Kompression geloggt.

• Regelarchiv-Match:
  – `rules.content` mit passendem state_hash liefert Aktion; Score-Reihenfolge korrekt.

⸻

11) Monitoring & KPIs

Kurzfristig
• policy_rules: wachsende n, |q| ≠ 0
• rules: steigende Abdeckung (mehr state_hash-Varianten)

Mittelfristig
• Decision-Hit-Rate (Archiv/Policy/Fallback)
• Sinkender Fallback-Anteil

Langfristig
• Stabilere Qualität bei neuen, symmetrischen Situationen (Kanonraum-Effekt)

⸻

12) Troubleshooting

• Keine Aktion? → Legalitätscheck: Adapter.legal_actions(...) prüfen.
• Archiv leer? → policy_engine --export-archiv ausgeführt? Filter (min-n, |q|) zu streng?
• Training „0 Schritte“? → origin/namespace-Filter, status-Filter (include-compressed) prüfen.
• DreamWorker komprimiert „zu viel“? → Schwelle `OROMA_FORGET_THRESHOLD` anheben (z. B. 0.3).

⸻

13) Sicherheit & Ethik

• Archiviert werden ausschließlich technische Zustände/Aktionen, keine personenbeziehbaren Daten.
• Adapter für AV sollten nur abstrakte Features/Tokens persistieren (z. B. „person@2m“, nicht Rohbilder).
• Alle Optional-Engines per ENV deaktivierbar.

⸻

14) Performance-Budget

• DreamWorker: CPU-leicht, I/O gebunden; Meta/Mutation < O(n) pro Chain.
• PolicyEngine: Training in Batches; Indizes auf policy_rules entscheidend.
• DecisionEngine: O(#Regeln im Namespace) + Policy-Lookup; typ. ≪ 5 ms/Entscheidung.

⸻

15) Roadmap

v3.7.2
• AV-Adapter-Prototyp (Audio/Video) mit Kanonisierung & Token-Policy.
• Archiv-Explainability: Top-Gründe je Entscheidung (Rule→Policy→Heuristik-Pfad loggen).

v3.8
• Kooperatives Lernen (ORÓMA↔ORÓMA), Curriculum-Compiler, priorisiertes Replay.
• Export-Gate → Edge/NPU (Hailo) für Leuchtturm-Demo: Pi-Edge-LLM + Archiv-Policy auf NPU.

⸻

16) Diff-Übersicht v3.7 → v3.7.1

Neu:
• core/decision_engine.py

Geändert:
• core/policy_engine.py (v3.8-r3, compressed-Training, robustes Upsert, Archiv-Export)
• core/ttt_adapter.py (Kanonraum, robuster Extractor)
• core/dream_worker.py (v3.7, Vector-First, Forgetting/Kompression/Meta/Mutation)

Refactor/Docs:
• Diese Datei (Konzeption_Architektur_v3.7.1.md), Kommentare & Header vereinheitlicht.