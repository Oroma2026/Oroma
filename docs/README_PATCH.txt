ORÓMA PTZ Motor Worker Patch
============================
Patch: PTZ Phase 5a – learned policy bias feedback
File:  tools/ptz_motor_worker.py
Base:  oroma_20260529_081410_with_db.zip

Live deployment expectation:
- Copy tools/ptz_motor_worker.py into /opt/ai/oroma/tools/ptz_motor_worker.py
- Do not enable OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE before compile/start validation.
- The default remains disabled: OROMA_PTZ_MOTOR_POLICY_BIAS_ENABLE=0

Validation performed here:
- python3 -m py_compile tools/ptz_motor_worker.py
- read-only helper smoke-test with temporary policy_rules DB
- disabled-worker --once smoke-test
- diff generated against original ZIP file

Documentation update is intentionally not included yet. It should be applied only after the live worker starts and the state JSON shows policy_bias_* fields without cmd_fail regressions.
