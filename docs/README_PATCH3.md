# ORÓMA PTZ Motor Worker Video-Integration Patch

Baseline ZIP: `/mnt/data/oroma_20260516_235906_with_db.zip`

Patched files:
- `/opt/ai/oroma/ui/video_ui.py`
- `/opt/ai/oroma/ui/templates/video.html`
- `/opt/ai/oroma/docs/PTZ_MOTOR_WORKER.md`
- `/opt/ai/oroma/docs/README_PATCH3.md`

Purpose:
- Keeps PTZ Motor Worker status directly inside `/video/`.
- Makes the PTZ Motor Worker block read-only: status, heartbeat, tuning, state and logs only.
- Removes Start/Restart/Stop buttons from the Video UI.
- Keeps `oroma-ptz-motor-worker.service` disabled by default.
- Preserves the legacy control endpoint as a JSON 403 guard so old browser tabs/tests fail visibly instead of causing 404/500.
- Ensures Flask/UI does not call sudo or run systemd write actions.
- Documents the v1.1/v1.2/v1.4b/v1.4c/v1.4d/v1.5b/v1.5c PTZ line from the shared Claude/ORÓMA calibration: upper-body bias, signal-based down-hold, target hold, axis lock, and Eye-Pair / face-like salience as a soft candidate score.
- Keeps Eye-Pair explicitly non-identifying: no person recognition, no hard face detection, no model requirement.

Validation performed in sandbox:
- Full target-file read before patching.
- `python3 -m py_compile ui/video_ui.py tools/ptz_motor_worker.py`.
- Jinja parse of `ui/templates/video.html` using installed Flask/Jinja dependencies.
- Full re-read of patched target files.
- Diff generated and reviewed.

Runtime control remains manual:

```bash
sudo systemctl start oroma-ptz-motor-worker.service
sudo systemctl stop oroma-ptz-motor-worker.service
sudo systemctl restart oroma-ptz-motor-worker.service
```

Additional v1.4a patch files:
- `/opt/ai/oroma/core/ptz_motor_state.py`
- `/opt/ai/oroma/tools/ptz_motor_worker.py`

New v1.4a behavior:
- Adds optional Eye-Pair / face-like salience candidate extraction.
- Keeps motion as fallback and, by default, requires motion confirmation before Eye-Pair can influence servo decisions.
- Adds `candidates[]`, `attention.eye_pair`, and `eye_pair_*` tuning/status fields.


Additional v1.4b behavior:
- Adds local-motion gating for Eye-Pair candidates in normalized (-1..+1) attention coordinates.
- Adds temporal Eye-Pair stability (`EYE_PAIR_MIN_FRAMES_STABLE`) before Eye-Pair can be selected.
- Adds separated counters: `eye_pair_raw`, `eye_pair_geom_ok`, `eye_pair_motion_gated`, `eye_pair_temporal_gated`, `eye_pair_selected`, and rejection counters.
- Keeps Motion as fallback and keeps UI/systemd runtime control read-only/manual-only.


Additional v1.4c behavior:
- Adds Eye/Head-Hold-Bias after a real `eye_pair_salience` follow command.
- Lets weak non-eye Motion/Edge candidates decay instead of immediately pulling the target away.
- Allows a short active hold command window via `OROMA_PTZ_MOTOR_EYE_HOLD_COMMAND=1`, bounded by confidence and tick decay.
- Adds state/counter fields for `eye_hold_bias`, `eye_hold_commands`, and target last-qualified candidate kind/source.
- Keeps the worker manual-only and the Video UI read-only.


Additional v1.4d/v1.4d1 behavior:
- Adds Servo-Damping / Calm-Follow without changing the read-only UI model.
- Changes the default `OROMA_PTZ_MOTOR_AMOUNT_MAX` from 5 to 3 for calmer normal follow commands.
- Adds tick-based move cooldown via `OROMA_PTZ_MOTOR_MOVE_COOLDOWN_TICKS=3`; no sleep/blocking is introduced in the worker loop.
- Allows cooldown bypass for strong signals and `eye_pair_salience` candidates.
- Adds Micro-Move-Guard before Axis-Lock, so tiny weak deviations cannot be converted back into axis commands.
- Adds API/state visibility for `move_cooldown_*`, `micro_guard_*`, and `attention.servo`.
- Keeps the worker manual-only and `oroma-ptz-motor-worker.service` disabled by default.

Additional v1.4d1 correction:
- Aligns the systemd reference units with the v1.4d calm-follow default by changing `OROMA_PTZ_MOTOR_AMOUNT_MAX` from `5` to `3` in `systemd/oroma-ptz-motor-worker.service` and `systemd/oroma-orchestrator.service.d/40-ptz-attention.conf`.
- This fixes the live-start mismatch where the Python default was already `3`, but systemd still forced the worker to start with `amount=2-5`. Expected start log after deploying the corrected unit is `amount=2-3`, unless a local `.env` or external drop-in overrides it again.


Additional v1.5b behavior:
- Replaces the too-soft v1.5a Face-Region context with a lightweight vertical-gradient score on the existing downsampled grayscale ROI.
- Adds `OROMA_PTZ_MOTOR_FACE_REGION_GRAD_MIN=0.80` and `OROMA_PTZ_MOTOR_EYE_FACE_RANK_THRESHOLD=0.85`.
- Adds additive Eye/Face soft-ranking: temporal Eye-Pair candidates with plausible Face-Region score can win as `candidate_winner=eye_face_salience`, while Motion remains the fallback.
- Keeps Raspberry Pi 5 performance constraints: no model, no cascade, no ellipse-fit, no full-resolution scan; only small ROI mean/diff/variance operations.
- Adds state/API visibility for `candidate_winner`, `face_region.score`, `face_region.score_norm`, and `face_region.reason` in addition to the existing Face-Region counters.
- Keeps Local-Motion/Temporal Eye-Pair Gates, Servo-Damping, manual-only systemd operation, and read-only Video UI unchanged.

Additional v1.5c behavior:
- Adds Face-assisted Motion-Radius for Eye-Pair gating.
- Keeps the conservative base radius `OROMA_PTZ_MOTOR_EYE_PAIR_MOTION_RADIUS=0.35`.
- Adds `OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST=1.55` and `OROMA_PTZ_MOTOR_EYE_FACE_RADIUS_BOOST_MIN=0.40`.
- The effective Eye-Pair motion gate radius is widened only when the Eye-Pair candidate already has a plausible Face-Region score (`face_region.ok` and sufficient `score_norm`).
- This targets the observed `gate_reason=motion_too_far` bottleneck where a real head/eye candidate was close to, but not exactly on, the current Motion-Centroid.
- No Servo-Damping, Amount, Cooldown, systemd, UI-control, DB, or schema behavior is changed.

