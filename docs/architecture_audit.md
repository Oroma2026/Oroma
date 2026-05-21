# ORÓMA Architecture Audit

**Project:** ORÓMA — Offline-Realtime-Organic-Memory-AI  
**Subtitle:** An offline-first adaptive edge intelligence architecture  
**Date:** 2026-05-21  
**Scope:** Architecture review based on the provided ORÓMA ZIP snapshots and the public release line.

---

## 1. Baseline and scope

This audit distinguishes two different ZIP types that must not be mixed conceptually:

| ZIP / source | Role | Status |
|---|---|---|
| `oroma_20260521_215009_with_db.zip` | Internal system snapshot | Contains code, docs, logs, `.git`, runtime data and SQLite databases. Suitable for internal audit, not for public release. |
| `oroma_20260518_zenodo_public_release_mit.zip` | Public Zenodo software release package | Clean source distribution. Contains code, documentation, systemd units, tests and package metadata. Does not contain logs, DBs, `.git`, caches or compiled Python files. |

The internal system snapshot proves what exists in the running project. The public release ZIP proves that a clean distribution path exists for GitHub/Zenodo publication.

---

## 2. Executive summary

ORÓMA is no longer only a concept or whitepaper idea. The reviewed system shows a real, layered architecture for an offline-first adaptive edge AI system.

The strongest architectural characteristics are:

- persistent episodic memory based on Snaps and SnapChains;
- Day/Dream separation for online experience collection and offline consolidation;
- Replay as a learning primitive, not only as playback;
- SQLite-based local persistence with a DBWriter/single-writer discipline;
- modular sensor, wrapper and actuator layers;
- policy learning through games, PTZ scenarios and daily runners;
- a broad Flask UI for observability and diagnosis;
- systemd and orchestrator integration for long-running headless edge deployment.

The key open challenge is no longer basic storage or runtime orchestration. The critical next step is proving the cognitive feedback loop:

```text
Experience
→ SnapChain
→ Replay
→ Binding
→ Generalization
→ Policy change
→ changed future behavior
→ new experience
```

In short: ORÓMA has a strong memory and runtime foundation. The next scientific step is to prove that Dream, Replay and Binding measurably improve later decisions.

---

## 3. High-level architecture

ORÓMA is best described as:

> An offline-first, memory-centric edge intelligence system with persistent episodic memory, Day/Dream processing, replay-driven consolidation, policy learning, sensor/PTZ integration, UI-based diagnosis and systemd/orchestrator-based operation.

It is not primarily a chatbot frontend. It is a local memory, replay and adaptation system.

The core layered model is:

```text
Hardware / sensors
        ↓
DeviceHub / wrappers
        ↓
Snap / SnapToken / SnapPattern
        ↓
SnapChain / episodes
        ↓
SQLite memory layer
        ↓
Replay / Dream / Forgetting / MetaSnap
        ↓
Policy / Transfer / Mutation
        ↓
UI / tools / systemd / orchestrator
```

This separation is one of the main strengths of the project. It keeps perception, memory, consolidation, policy and operation distinct enough to be analyzed and improved independently.

---

## 4. Source distribution status

### 4.1 Internal system ZIP

The internal snapshot contains around 1,799 ZIP entries and roughly 1,501 files. It includes the live project structure plus runtime artifacts.

Important characteristics:

| Area | File count observed |
|---|---:|
| `.git/` | 643 |
| `docs/` | 278 |
| `ui/` | 132 |
| `core/` | 107 |
| `tools/` | 94 |
| `logs/` | 85 |
| `systemd/` | 60 |
| `mini_programs/` | 25 |
| `tests/` | 17 |
| `wrappers/` | 13 |

It also contains runtime databases:

```text
data/oroma.db
data/stats.db
data/knowledge.db
```

This is valuable for internal analysis because it shows real schema, runtime state and operational traces. It is not appropriate as a public software artifact because it includes private/runtime material.

### 4.2 Public Zenodo release ZIP

The public release package contains around 541 ZIP entries and roughly 521 files. It is a clean source distribution.

Included areas:

| Area | File count observed |
|---|---:|
| `ui/` | 132 |
| `core/` | 107 |
| `tools/` | 94 |
| `systemd/` | 58 |
| `docs/` | 56 |
| `mini_programs/` | 25 |
| `tests/` | 17 |
| `wrappers/` | 13 |
| `exports/` | 5 |

The public ZIP does **not** contain:

```text
.git/
logs/
data/*.db
data/state/
.cache/
.local/
__pycache__/
*.pyc
```

This means the clean-source publication path is already established. The earlier criticism that the public release needed DB/log cleanup applies to the internal system snapshot, not to the Zenodo public release package.

---

## 5. Runtime entry point

The production entry point is:

```text
run_oroma.py
```

Architecturally, this file acts as the runtime composition layer. It connects:

```text
Flask UI
AgentLoop
DeviceHub
Luma sampler
CircadianController
DreamWorker bridge
safe blueprint registration
```

It should not be understood as the place where all intelligence lives. It is the glue layer that starts and connects the main subsystems. The domain logic remains distributed across:

```text
core/
ui/
tools/
wrappers/
mini_programs/
```

This is appropriate for a long-running headless edge system.

---

## 6. Core memory architecture

The central cognitive layer is located in:

```text
core/
```

Important memory-related files include:

```text
core/snap.py
core/snapchain.py
core/snappattern.py
core/snaptoken.py
core/snap_indexer.py
core/meta_snap.py
core/episodic.py
```

### 6.1 Snap

A Snap is the smallest stable observation or experience unit. It can contain features, content, metadata, timestamps, privacy information, fingerprints and optional fusion information.

A Snap is therefore more than a log line. It is an atom of structured memory.

### 6.2 SnapChain

A SnapChain is a sequence of Snaps or SnapPatterns over time.

```text
Snap → Snap → Snap → Snap
        = episode / SnapChain
```

This is the architectural core of ORÓMA. It allows the system to store not only isolated events, but temporal experience.

The difference is important:

```text
Event logging:  "movement detected"
ORÓMA memory:   "in this situation, this sequence occurred and later led to this result"
```

That distinction is what makes Replay, Dream, Policy and Binding meaningful.

---

## 7. Database and persistence architecture

The internal system snapshot confirms three database roles:

```text
oroma.db      → main episodic and operational memory
stats.db      → energy, statistics and aggregated state
knowledge.db  → document/chunk/FTS knowledge layer
```

### 7.1 `oroma.db`

The main database contains core memory and learning structures, including:

```text
snapchains
snap_index
episodes
episode_events
episodic_metrics
policy_rules
rules
replay_log
quality_history
meta_snaps
object_nodes
object_relations
scenegraphs
metrics
dream_state
rewards_log
knowledge_gaps
hypotheses
```

Representative table roles:

| Table | Role |
|---|---|
| `snapchains` | stored experience chains |
| `snap_index` | Snap/feature/fingerprint index |
| `episodes` | higher-level episode structure |
| `episode_events` | event sequence inside episodes |
| `policy_rules` | learned policy rules |
| `replay_log` | Replay execution history |
| `quality_history` | quality evolution of SnapChains |
| `meta_snaps` | compressed or abstracted memories |
| `object_nodes` / `object_relations` | object and relation memory |
| `scenegraphs` | scene/object graph structures |
| `dream_state` | Dream execution state |
| `metrics` | runtime and learning metrics |

This confirms that ORÓMA uses SQLite not merely as a log store, but as a persistent memory substrate.

### 7.2 `stats.db`

`stats.db` separates aggregated energy and statistics from the main memory DB. Observed structures include:

```text
node_energy
relation_energy
energy_state
energy_top_cache
ptz_coverage_cells
stats_curve_day
stats_event_queue
stats_points
```

This separation is architecturally sound. Heavy dashboards and aggregate calculations should not constantly stress the main episodic memory.

### 7.3 `knowledge.db`

`knowledge.db` contains document and chunk structures such as:

```text
documents
chunks
chunk_meta
chunks_idx
chunks_content
chunks_data
```

This is the document/RAG-style knowledge layer. It is distinct from episodic memory and operational statistics.

---

## 8. DBWriter and single-writer discipline

Relevant files:

```text
core/sql_manager.py
core/db_writer.py
core/db_writer_client.py
systemd/oroma-db-writer.service
systemd/oroma-db-writer-watchdog.service
tools/db_writer_watchdog.py
```

SQLite is robust for local edge systems, but parallel writes from many workers can easily cause lock contention. ORÓMA addresses this through a DBWriter/single-writer design.

Target write path:

```text
Module / worker / UI
        ↓
DBWriter client
        ↓
Single writer queue
        ↓
SQLite DB
```

Avoided anti-pattern:

```text
Every module writes directly to SQLite
```

This design is essential because ORÓMA has many concurrent components:

```text
DreamWorker
Replay
Stats
Policy
PTZ
UI
Games
Forgetting
Indexer
Orchestrator
```

Without a disciplined write path, the architecture would be much more vulnerable to database locks and inconsistent runtime behavior.

---

## 9. Day/Dream architecture

Relevant files:

```text
core/circadian_controller.py
core/dream_worker.py
core/replay_system.py
core/replay_manager.py
systemd/oroma-dream.service
systemd/oroma-dream.timer
systemd/oroma-replay.service
systemd/oroma-replay.timer
ui/dream_ui.py
ui/replay_ui.py
ui/replay_api.py
```

The Day/Dream separation is one of ORÓMA's strongest design decisions.

### 9.1 Day

In Day mode, the system collects live experience:

```text
sensor values
frames
audio/events
game states
PTZ states
policy decisions
Snaps
SnapChains
```

### 9.2 Dream

In Dream mode, the system processes stored experience:

```text
Replay
consolidation
quality evaluation
Forgetting
MetaSnap creation
Policy improvement
Binding
compression
```

This is not a poetic metaphor in the technical core. It is an offline consolidation cycle.

A precise architectural sentence is:

> Day creates experience. Dream evaluates, compresses and reconnects experience.

This makes ORÓMA more than a recorder. It becomes a system that can revisit and reshape its own memory.

---

## 10. Replay layer

Relevant files:

```text
core/replay_system.py
core/replay_manager.py
tools/replay_auto.py
ui/replay_ui.py
ui/replay_api.py
systemd/oroma-replay.service
systemd/oroma-replay.timer
```

Replay is a learning primitive. It allows ORÓMA to reuse past SnapChains after the original event has passed.

Simplified flow:

```text
select old SnapChains
        ↓
replay / re-evaluate
        ↓
update quality
        ↓
influence policy, MetaSnaps and Bindings
```

This is central because it means ORÓMA can learn not only online, but also through offline reprocessing.

---

## 11. Forgetting, compression and MetaSnaps

Relevant files:

```text
core/forgetting.py
core/forgetting_worker.py
core/meta_snap.py
tools/compression_materializer.py
tools/compression_probe.py
tools/meta_snap_indexer_runner.py
ui/forgetting_ui.py
ui/meta_ui.py
```

A persistent memory system must not only accumulate. Without forgetting or compression, it eventually becomes slower, noisier and less useful.

ORÓMA's intended memory maintenance flow is:

```text
many Snaps / SnapChains
        ↓
quality evaluation
        ↓
compression
        ↓
MetaSnaps
        ↓
archive / downgrade / summarize / reuse
```

In this architecture, forgetting does not necessarily mean deletion. It can mean lowering priority, compressing, archiving or replacing many low-level events with higher-level MetaSnaps.

This is both technically necessary and biologically plausible.

---

## 12. Binding and synaptic layer

Relevant files and documents:

```text
docs/BINDING_ARCHITECTURE_v1.md
core/nmr_synaptic_plasticity.py
tools/synapses_bridge_materializer.py
tools/synapses_bridge_probe.py
tools/synapses_origin_probe.py
ui/synapses_ui.py
core/objectgraph_builder.py
core/fusion.py
```

Database structures also support this layer:

```text
object_nodes
object_relations
scenegraphs
```

The purpose of Binding is to connect events, objects, modalities and contexts.

Examples:

```text
object A relates to object B
event X appears together with event Y
sensor A and sensor B match within a time window
PTZ position belongs to motion, scene and SnapChain context
```

This is the transition from storage to meaning.

However, this is also the most important open research area in the current system. The infrastructure exists, but the key question is whether Binding already changes later decisions in a measurable way.

The decisive test is:

```text
Was a relation formed?
Was it reused later?
Did it influence a policy decision?
Was the decision better than without that relation?
```

Without this, Binding remains valuable infrastructure. With this, it becomes a true cognitive feedback channel.

---

## 13. Policy and learning layer

Relevant files:

```text
core/policy_engine.py
core/universal_policy.py
core/transfer_engine.py
core/reward.py
core/utility.py
core/mutation.py
tools/*_daily_runner.py
```

Supporting database tables include:

```text
policy_rules
rules
rewards_log
quality_history
```

Simplified learning flow:

```text
recognize state
        ↓
choose action
        ↓
observe result
        ↓
store reward / quality
        ↓
update policy rule
```

The policy layer is visible in the games and PTZ training domains:

```text
TicTacToe
Connect4
Snake
Pong
Flappybird
HideSeek
MemoryMaze
Chess / Chess2 / Chess3
PTZ Target
PTZ Arena
PTZ Coverage
```

These are not just demos. They are controlled testbeds for policy learning, replay effects and transfer behavior.

---

## 14. Games as learning laboratories

Relevant areas:

```text
mini_programs/
tools/*_daily_runner.py
ui/*_ui.py
```

Examples:

```text
mini_programs/tictactoe.py
mini_programs/connect4.py
mini_programs/snake.py
mini_programs/pong.py
mini_programs/flappybird.py
mini_programs/hide_seek.py
mini_programs/memorymaze_hybrid.py
mini_programs/ptz_target.py
mini_programs/ptz_arena.py
```

Daily runners include:

```text
tools/tictactoe_daily_runner.py
tools/connect4_daily_runner.py
tools/snake_daily_runner.py
tools/chess2_daily_runner.py
tools/chess3_daily_runner.py
tools/ptz_target_daily_runner.py
tools/ptz_coverage_daily_runner.py
```

This is a sound architecture choice. Real sensor input is noisy and hard to evaluate. Games provide bounded environments with clear states, actions and rewards.

They allow ORÓMA to test policy learning before applying similar mechanisms to messier real-world sensor domains.

---

## 15. Sensor, DeviceHub and wrapper layer

Relevant files:

```text
core/device_hub.py
core/camera_hub.py
core/sensor_channel.py
wrappers/vision_wrapper.py
wrappers/audio_wrapper.py
wrappers/hailo_wrapper.py
wrappers/degirum_wrapper.py
wrappers/gstreamer_wrapper.py
wrappers/ptz_controller.py
wrappers/text_wrapper.py
wrappers/tts_wrapper.py
```

The wrapper layer prevents the ORÓMA core from depending directly on every hardware backend.

Conceptually:

```text
ORÓMA core
        ↓
wrapper abstraction
        ↓
concrete hardware / runtime backend
```

This supports portability across:

```text
camera
PTZ
audio
vision
TTS
GStreamer
Hailo
DeGirum
```

This is appropriate for an edge AI system that may run on evolving hardware.

---

## 16. PTZ architecture

Relevant files:

```text
core/ptz_attention_loop.py
core/ptz_motor_state.py
tools/ptz_motor_worker.py
tools/ptz_motor_reward_collector.py
ui/ptz_coverage_ui.py
ui/ptz_target_ui.py
ui/ptz_arena_ui.py
ui/video_ui.py
systemd/oroma-ptz-motor-worker.service
systemd/oroma-ptz-motor-reward-collector.service
```

The PTZ layer is important because it gives ORÓMA a real sensor/actuator domain.

A strong design decision is the separation between:

```text
ptz_attention_loop.py
= slower attention / diagnosis / cognition path

ptz_motor_worker.py
= faster motor / reflex path
```

This separation matters because motor control must not be blocked by slow Dream, UI, DB or replay processes.

Correct architectural principle:

```text
fast actuator path
        separated from
slow cognitive consolidation path
```

The UI should observe PTZ state and health, but should not become the primary authority for unsafe or uncontrolled motor actions.

---

## 17. UI and observability layer

The `ui/` directory is large and functionally important. It is not just decoration; it is an observability and diagnosis layer.

Representative files:

```text
ui/flask_ui.py
ui/learning.py
ui/replay_ui.py
ui/dream_ui.py
ui/forgetting_ui.py
ui/synapses_ui.py
ui/video_ui.py
ui/ptz_coverage_ui.py
ui/health_ui.py
ui/models_ui.py
ui/export_ui.py
ui/import_ui.py
ui/research_ui.py
ui/selftest_ui.py
```

The UI exposes:

```text
learning state
Replay
Dream
Forgetting
Synapses / Binding
Video / PTZ
Health
Models
Import / Export
Research and diagnostics
Games
```

This is essential. An adaptive system that cannot be observed is difficult to trust, debug or publish scientifically.

---

## 18. Orchestrator and systemd layer

Relevant files:

```text
tools/oroma_orchestrator.py
systemd/oroma-orchestrator.service
systemd/oroma.service
systemd/*.service
systemd/*.timer
```

ORÓMA is designed as a long-running edge service, not as a one-shot script.

Typical service groups include:

```text
oroma.service
oroma-orchestrator.service
oroma-dream.service/timer
oroma-replay.service/timer
oroma-forgetting.service/timer
oroma-policy.service/timer
oroma-health.service/timer
oroma-stats.service/timer
oroma-archive.service/timer
oroma-db-writer.service
oroma-ptz-motor-worker.service
```

Layer responsibilities:

```text
run_oroma.py
    = UI and main runtime

tools/oroma_orchestrator.py
    = periodic job coordination

systemd
    = operating-system lifecycle integration
```

This is a realistic deployment model for Raspberry Pi / edge hardware.

---

## 19. Import, export and publication layer

Relevant files:

```text
core/export_gate.py
core/import_gate.py
exports/
ui/export_ui.py
ui/import_ui.py
exports/model_export.py
exports/model_import.py
exports/hailo_export.py
exports/degirum_export.py
```

The export path is policy-controlled. It is not simply a raw dump.

Important concepts:

```text
minimum age
quality threshold
non-destructive marking
bundle creation
KPI metrics
```

This is important because an adaptive memory system can contain private data, logs, internal states and low-quality intermediate artifacts.

Publication must therefore be filtered and intentional.

The clean Zenodo ZIP demonstrates that this distinction is already being applied at the public release level.

---

## 20. Documentation architecture

The documentation layer is broad and unusually strong for a solo experimental system.

Important documents include:

```text
docs/PROJECT_STRUCTURE.md
docs/BINDING_ARCHITECTURE_v1.md
docs/DB_SINGLE_WRITER.md
docs/OROMA_PATCH_GATE.md
docs/PTZ_MOTOR_WORKER.md
docs/NMR_SYNAPTIC_PLASTICITY.md
docs/OROMA_NAMING_AND_POSITIONING_GITHUB_DE.md
docs/zenodo_whitepaper_v1_release.md
docs/core/10_snap.md
docs/core/22_snapchain.md
docs/core/50_db_layer.md
docs/core/60_devicehub.md
docs/core/70_policy_transfer.md
docs/core/90_publication.md
```

The main documentation improvement should be navigation. The documentation is broad, but it should expose a clearer entry path:

```text
docs/README.md
    → architecture index
    → core concepts
    → runtime / ops
    → policy / learning
    → PTZ / sensor stack
    → release / publication
```

This is not a weakness of the architecture itself. It is a presentation and maintainability issue.

---

## 21. Architecture diagram

```text
┌──────────────────────────────────────────────┐
│                 UI / diagnosis                │
│ Flask blueprints, dashboards, APIs            │
│ ui/*                                          │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│              Runtime / orchestrator           │
│ run_oroma.py, tools/oroma_orchestrator.py      │
│ systemd services/timers                       │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│              Core cognition layer             │
│ Snap, SnapChain, Replay, Dream, Policy         │
│ core/snap.py, snapchain.py, dream_worker.py    │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│              Memory / persistence             │
│ oroma.db, stats.db, knowledge.db               │
│ DBWriter, sql_manager                          │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│              Sensor / actuator layer          │
│ DeviceHub, CameraHub, PTZ, Audio, Vision       │
│ core/device_hub.py, wrappers/*                 │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│              Hardware / edge runtime          │
│ Raspberry Pi, camera, PTZ, optional NPU        │
└──────────────────────────────────────────────┘
```

---

## 22. Learning loop

The most important architectural loop is:

```text
perceive
   ↓
create Snap
   ↓
form SnapChain
   ↓
store episode
   ↓
evaluate policy / reward
   ↓
Replay during Dream
   ↓
Binding / MetaSnap / Forgetting
   ↓
improve policy
   ↓
change future behavior
```

This loop is the central scientific claim of ORÓMA.

The current system strongly supports the first parts:

```text
perceive
store
index
replay
diagnose
```

The most important next proof is the last part:

```text
Dream / Replay / Binding
        ↓
measurably improved future behavior
```

---

## 23. Strengths

| Area | Assessment |
|---|---|
| Snap/SnapChain episodic memory | Very strong architectural foundation |
| Day/Dream separation | One of the strongest concepts in the system |
| Replay as learning primitive | Strong and distinctive |
| DBWriter/single-writer discipline | Essential and correctly prioritized |
| UI observability | Broad and valuable |
| systemd/orchestrator operation | Realistic for long-running edge deployment |
| PTZ attention vs motor separation | Architecturally correct |
| Games as policy testbeds | Useful and scientifically practical |
| Documentation breadth | Strong for a solo project |
| Clean Zenodo release package | Public distribution path is now credible |

---

## 24. Open risks and weak points

| Area | Risk / open point |
|---|---|
| Binding feedback | Infrastructure exists, but its measurable influence on decisions must be proven. |
| Generalization | The path from many Snaps to stable concepts is still the main research challenge. |
| Code homogeneity | Some modules are mature, others remain experimental or redundant. |
| System complexity | More than 100 core files and many services increase maintenance risk. |
| Parallel runtime behavior | Stability depends on strict DBWriter and orchestrator discipline. |
| Documentation navigation | The amount of documentation is strong but needs a clearer top-level path. |
| Ethics and shutdown | ResourceGuard, MutationGuard, Quarantine and HumanOverride should become explicit architectural gates. |

---

## 25. Learning evidence: the next decisive step

The most important next step is not adding more modules. It is proving the feedback loop.

For each domain, for example `ptz:coverage`, `game:tictactoe` or `game:chess3`, ORÓMA should track:

```text
explore_winrate_7d
policy_winrate_7d
delta_policy_vs_explore
binding_count
new_bridge_count
reused_bridge_count
policy_rule_q_shift
dream_replay_count
post_dream_improvement
```

The key research question:

```text
Is policy behavior after Dream measurably better than before Dream?
```

If yes, the system has a measurable cognitive feedback loop.

---

## 26. Binding evidence: from infrastructure to effect

The Binding layer should become measurable through decision impact.

Target evidence chain:

```text
relation created
        ↓
relation reused
        ↓
relation influences decision
        ↓
result improves compared with baseline
```

Useful metrics could include:

```text
binding_used_in_policy_decision
binding_reuse_count
bridge_reuse_success_rate
decision_source = policy + binding
post_binding_reward_delta
```

This would make Binding visibly real instead of merely stored.

---

## 27. Recommended next priorities

### Priority 1: Prove the Rückkanal / feedback loop

```text
Dream / Replay / Binding changes Policy in measurable ways.
```

### Priority 2: Make Binding decision-relevant

```text
object_relations / bridges / synapses influence actual decisions.
```

### Priority 3: Strengthen Learning Evidence Dashboard

```text
before/after Dream comparison
policy vs explore comparison
binding reuse metrics
post-dream improvement metrics
```

### Priority 4: Keep the public release clean

The Zenodo public release ZIP is already clean. Preserve this standard for future releases:

```text
no DBs
no logs
no .git
no caches
no runtime state
no private configs
```

### Priority 5: Formalize ethics and shutdown gates

Add or strengthen explicit gates:

```text
MutationGuard
ResourceGuard
ModuleQuarantine
SafeMode
HumanOverride
AuditTrail
```

This is necessary because an adaptive system must not only learn what should grow. It must also know what must be limited, isolated or stopped.

---

## 28. Publication-ready architecture description

### English

ORÓMA is an offline-first adaptive edge intelligence architecture built around persistent episodic memory. It represents observations as Snaps, connects them into SnapChains, stores them in a local SQLite-based memory layer, and improves behavior through replay-driven Dream consolidation, policy learning, binding mechanisms, forgetting and MetaSnap compression.

The system is designed for long-running headless edge deployment. Runtime services are coordinated through systemd and an ORÓMA orchestrator, while a Flask-based UI provides observability for learning, replay, memory, policy, sensor state, PTZ behavior and system health.

ORÓMA separates fast sensor/actuator paths from slower cognitive consolidation paths. This allows real-time components such as PTZ motor control to remain stable while memory, replay, policy and Dream workers operate asynchronously under resource and safety constraints.

### Deutsch

ORÓMA ist eine offline-first Edge-Intelligenzarchitektur mit persistentem episodischem Gedächtnis. Beobachtungen werden als Snaps modelliert, zu SnapChains verbunden, lokal in SQLite-basierten Gedächtnisschichten gespeichert und durch Replay-getriebene Dream-Konsolidierung, Policy-Lernen, Binding-Mechanismen, Forgetting und MetaSnap-Kompression weiterverarbeitet.

Das System ist für dauerhaften, headless Edge-Betrieb ausgelegt. systemd-Dienste und ein ORÓMA-Orchestrator koordinieren periodische Aufgaben, während eine Flask-basierte UI Lernzustand, Replay, Gedächtnis, Policies, Sensorik, PTZ-Verhalten und Systemgesundheit beobachtbar macht.

ORÓMA trennt schnelle Sensor-/Aktor-Pfade von langsamer kognitiver Konsolidierung. Dadurch kann z. B. PTZ-Motorik stabil und latenzarm laufen, während Speicher, Replay, Policy und Dream-Prozesse getrennt und ressourcenbegrenzt arbeiten.

---

## 29. Final assessment

ORÓMA is architecturally credible as an experimental offline-first edge AI system with persistent memory and replay-driven learning.

It is not merely a chatbot, not merely a game collection and not merely a logging system. Its real identity is a memory-centered adaptive runtime:

```text
persistent experience
+ replay
+ binding
+ policy learning
+ edge operation
+ observability
```

The foundation is strong. The clean public release path is now in place. The largest remaining scientific challenge is measurable cognitive integration:

```text
Do Dream, Replay and Binding measurably improve future behavior?
```

If ORÓMA can answer this with clear metrics, the project moves from an impressive architecture experiment toward a serious adaptive learning system.
