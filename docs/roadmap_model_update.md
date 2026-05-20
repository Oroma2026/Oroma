<!--
================================================================================
FILE:        docs/roadmap_model_update.md
PROJECT:     ORÓMA (OROMA) – Offline-First, Headless, Edge Cognitive System
VERSION:     v1.0
DATE:        2026-01-09 (Europe/Berlin)
AUTHOR:      Jörg + ChatGPT (based on project ZIP baseline)

PURPOSE:
  Roadmap to "bring learning into models" in a way that fits ORÓMA:
  - keep auditability (DB as ground truth)
  - keep headless/edge stability
  - enable continuous improvement via safe batch retraining + promotion
  - optional ONNX/NPU deployment (Hailo/DeGirum) without rewriting the system

SCOPE:
  This roadmap targets two first practical models:
  1) Gate Model (Quality/Token Accept/Skip)  -> immediate benefit for live system efficiency
  2) Policy Student Model (distilled from policy_rules / teacher policy) -> speed + stable behavior

NON-GOALS (explicit):
  - Not "everything in one huge model"
  - Not replacing Orchestrator/systemd scheduling
  - Not storing "memory only in weights" (ORÓMA DB remains the source of truth)

BASELINE (for traceability):
  Project ZIP: /mnt/data/oroma_20260108_170011_with_db.zip
  Working tree: /mnt/data/oroma_zip_20260108/
  DBs: /mnt/data/oroma_zip_20260108/data/oroma.db and data/stats.db

SAFETY PRINCIPLES (must remain true):
  - Non-destructive: never delete models/data; only deactivate/rollback
  - Promote model only if evaluation improves over baseline (gated promotion)
  - Keep explainability: teacher rules remain inspectable even if student model runs
  - Headless: no GUI deps; all tools run via CLI/orchestrator
================================================================================
-->

# ORÓMA Roadmap – Model Creation + Continuous Updating (Edge/Headless)

## 0) Target Outcomes (what "done" looks like)
- A reproducible pipeline that:
  - extracts datasets from `oroma.db` / `stats.db`
  - trains small models offline (Dream/Orchestrator window)
  - evaluates against baselines
  - exports ONNX
  - registers/promotes model versions safely (rollbackable)
- Two shipped models:
  1) **GateModel v1** (token/quality accept/skip)
  2) **PolicyStudent v1** (distilled from `policy_rules`)
- A clear “experiment harness” to prove improvements with A/B settings.

---

## 1) Architecture Decision: Hybrid Learning (recommended)
ORÓMA stays the **Cognitive OS**:
- episodic memory (snapchains/meta_snaps)
- consolidation (dream worker)
- graph projection (scene/object graphs)
- explainable rules (policy_rules/rules)
- telemetry separation (stats.db)

Models become **specialized accelerators**:
- fast gating/classification
- fast policy inference (distilled from rules)
- optional embeddings for retrieval later

This preserves:
- auditability
- reproducibility
- headless stability
- rollback safety

---

## 2) Phases and Milestones

### Phase A — Baseline + Evaluation Harness (Foundation)
**Goal:** Create a consistent evaluation protocol so “model updates” are measurable and safe.

**Deliverables**
- `tools/eval_run_report.py` (or equivalent):
  - outputs a single JSON report for a defined time window
  - reads:
    - rewards summary (`rewards_log`)
    - key metrics (`metrics`)
    - optional energy snapshot (`stats.db energy_top_cache`)
- `docs/eval_protocol.md`:
  - fixed time windows
  - fixed baselines
  - required run metadata (Run-ID, setting flags, duration, environment notes)

**Acceptance Criteria**
- Running evaluation twice on the same DB snapshot yields identical results.
- Report includes:
  - reward/min by source
  - candidate vs accepted/skip ratios
  - heartbeat sanity
  - (optional) top energy keys

**Suggested SQL building blocks**
```sql
-- oroma.db: reward summary last 60 minutes relative to max(created_at)
WITH mx AS (SELECT MAX(created_at) tmax FROM rewards_log)
SELECT source, COUNT(*) n, ROUND(SUM(reward),3) sum_r, ROUND(AVG(reward),6) avg_r
FROM rewards_log, mx
WHERE created_at >= mx.tmax - 3600
GROUP BY source
ORDER BY n DESC;


⸻

Phase B — Dataset Export Pipeline (DB → features)

Goal: Standardize how training data is created from ORÓMA DBs.

Deliverables
	•	tools/dataset_export_gate.py
	•	exports a tabular dataset for GateModel
	•	tools/dataset_export_policy_student.py
	•	exports (state_features, action_label) from policy_rules / policy source

Data Formats
	•	CSV (simple) + optional Parquet (faster on PC)
	•	Include a dataset_manifest.json:
	•	DB paths + timestamps
	•	row counts per source table
	•	feature schema hash (to avoid silent drift)

Acceptance Criteria
	•	Export runs headless on Pi.
	•	Dataset contains:
	•	no NULL surprises (or explicit defaults)
	•	deterministic column order

⸻

Phase C — GateModel v1 (Quality/Token accept/skip)

Goal: Replace or augment heuristic gating with a tiny supervised model.

Problem Definition
	•	Input: recent metrics + context features
	•	Output: accept vs skip (and confidence)
	•	Target: improve efficiency and quality by reducing useless candidates.

Training Candidates
	•	Start with scikit-learn:
	•	Logistic Regression / SGDClassifier
	•	GradientBoosting / RandomForest (if still fast)
	•	Export to ONNX via skl2onnx

Features (example set)
	•	last N values of:
	•	cam:token:candidate
	•	cam:token:skip_quality
	•	audio candidate/accepted
	•	brightness/motion if available
	•	error flags / last_error_ts (if present)
	•	time-of-day phase (Day/Dream state, if logged)

Baselines
	•	Current heuristic gate (existing behavior)
	•	Always-accept baseline (for reference)

Acceptance Criteria
	•	Offline eval shows:
	•	reduced skip_noise (or improved accepted-quality proxy)
	•	no drop in stable runtime (heartbeat stays stable)
	•	Promotion gate: only activate if metrics improve.

⸻

Phase D — PolicyStudent v1 (distillation from rules)

Goal: Faster inference while keeping teacher explainability intact.

Teacher
	•	policy_rules (and/or rules) remain the authoritative explainable source.

Student
	•	Small NN or even linear model:
	•	input: canonical state features
	•	output: action distribution / top action

Workflow
	1.	Export dataset from policy_rules
	2.	Train student
	3.	Validate:
	•	agreement with teacher on train states
	•	generalization on held-out states (test split)
	4.	Deploy:
	•	ONNX model + small runtime wrapper
	•	fallback to teacher when confidence low or state unseen

Acceptance Criteria
	•	Student matches teacher ≥ target threshold (e.g. 95% on test states)
	•	Inference latency improved measurably (e.g. 2–10× faster depending on domain)
	•	Behavior stable (no “random drift”) due to fixed trained snapshot
	•	Full rollback support (switch back to teacher-only instantly)

⸻

Phase E — Safe Model Registry + Promotion + Rollback (production)

Goal: Operationalize model updates inside ORÓMA safely.

Deliverables
	•	A consistent model directory layout (example):
	•	models/gate_model/<timestamp>/model.onnx
	•	models/policy_student/<timestamp>/model.onnx
	•	models/.../meta.json (schema, training window, eval results)
	•	tools/model_promote.py:
	•	checks eval results
	•	marks a model “active” in registry DB/table
	•	never deletes older versions
	•	tools/model_rollback.py:
	•	switches active model to previous version

Promotion Rules (must be explicit)
	•	Promote only if:
	•	primary metric improves by threshold (configured)
	•	no regression in stability/error metrics
	•	Always keep last N versions (N>=5 recommended)

⸻

Phase F — Automated Retraining Schedule (Orchestrator)

Goal: Continuous improvement without human babysitting.

Plan
	•	GateModel: retrain weekly (or daily if stable + fast)
	•	PolicyStudent: retrain weekly or after policy_rules grows by X%

Deliverables
	•	Orchestrator tasks:
	•	oroma-train-gate (scheduled)
	•	oroma-train-policy-student (scheduled)
	•	Each task writes:
	•	dataset manifest
	•	eval report
	•	promotion decision log line

Acceptance Criteria
	•	Can run on Pi without impacting Day-loop (runs in Dream/off-peak).
	•	If DB is locked, task fails gracefully and retries later (no crashes, no corruption).

⸻

Phase G — Optional: Embedding Model (MetaSnap/SnapChain embeddings)

Goal: Improve retrieval/dedupe/search beyond heuristics.

Approach
	•	Small encoder that maps SnapChain/MetaSnap features → vector
	•	Use vectors in:
	•	future vector index
	•	cluster/stability metrics

Note
	•	This is optional and should come after Gate/Policy, because it’s more complex to evaluate properly.

⸻

Phase H — Optional: NPU Acceleration (Hailo / DeGirum)

Goal: Deploy inference to NPU where beneficial.

Flow
	•	Train on CPU (PC or Pi)
	•	Export ONNX
	•	Compile/convert for target runtime
	•	Validate outputs match CPU within tolerance

Acceptance Criteria
	•	Same API contract as CPU inference
	•	Failover to CPU if NPU unavailable
	•	No new GUI deps

⸻

3) Suggested Implementation Order (fast wins first)
	1.	Phase A (Evaluation Harness)
	2.	Phase B (Dataset Export)
	3.	Phase C (GateModel v1)
	4.	Phase E (Registry + Promote/Rollback)
	5.	Phase F (Automated schedule)
	6.	Phase D (PolicyStudent v1)
	7.	Optional G/H

Reason: GateModel gives immediate system-wide benefit and is simplest to validate.

⸻

4) Risks and Mitigations

Risk: “Good curves, no real learning”
	•	Mitigation:
	•	fixed train/test splits
	•	baselines
	•	promotion gate requires improvement on test, not only train

Risk: Drift / instability from online training
	•	Mitigation:
	•	prefer batch retraining in Dream/Orchestrator windows
	•	keep weights frozen during Day-loop

Risk: SQLite contention / locks
	•	Mitigation:
	•	dataset export uses read-only, short transactions
	•	retries and backoff
	•	stats.db used for long-window aggregates

Risk: Explainability loss
	•	Mitigation:
	•	teacher rules remain first-class
	•	student is optional accelerator with fallback

⸻

5) “Definition of Done” Checklist
	•	eval_run_report produces deterministic JSON from DB snapshots
	•	dataset exports are stable + schema versioned
	•	GateModel v1 trains and exports ONNX
	•	Promotion gate only activates improved models
	•	Rollback is one command and works instantly
	•	Orchestrator schedules retrain safely in Dream/off-peak
	•	Documentation includes protocol + run template

⸻

6) Run Template (copy/paste into docs/logbook)

Run-ID:
Setting: Dream= | Graph= | Policy= | GateModel= | PolicyStudent=
Start/End (Berlin):
DB Snapshot: oroma.db / stats.db versions
Primary: reward/min (by source), token ratios, energy-top summary
Secondary: heartbeat, errors, notes (restarts, light changes, device changes)