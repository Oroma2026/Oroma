# ORÓMA Patch (A+B + PTZ Fix)

Enthaltene Fixes:

**A) UI-CPU-Fix**
- drosselt/entkoppelt eine zu aggressive DB-Polling-Schleife in `ui/forgetting_ui.py`, die beim Öffnen der UI sonst die CPU hochzieht.

**B) PiCar GPIO busy-wait Fix**
- verhindert einen GPIO/Deadman busy-loop (liblgpio / gpiochip0) in `wrappers/picar_wrapper.py`.

**C) PTZ Attention: "schaut an die Decke" / reagiert nicht**
- `core/ptz_attention_loop.py`
  - niedrigere Default-Motion-Schwellen (cam:motion ist in der Praxis oft viel kleiner als 0.015/0.06).
  - `ptz:mode` Mapping erweitert (threat/speech/probe/luma_recover), damit nicht ständig `-1` geloggt wird.
  - **Idle-Home**: wenn Mode=fixate und die Kamera deutlich vom Horizont abweicht, nudgt sie sanft zurück (verhindert "kleben" an der Decke).

Hinweis:
- ENV-Overrides bleiben unverändert möglich (die neuen Werte sind nur Defaults).
- Alle Python-Files sind mit `python3 -m py_compile` geprüft.
