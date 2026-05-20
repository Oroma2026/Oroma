#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# Pfad:    /opt/ai/oroma/tools/__init__.py
# Projekt: ORÓMA
# Version: v3.7
# Zweck:
#   - Markiert 'tools' als Python-Package (Import & 'python -m tools.*' möglich).
#   - Gemeinsamer Platz für Tool-weite Konstanten/Helper (optional).
# Hinweise:
#   - Für reine CLI/systemd-Skripte ist __init__.py nicht zwingend erforderlich.
#   - Bei systemd-ExecStart entweder WorkingDirectory setzen ODER in Skripten
#     den Projekt-Root in sys.path einfügen (siehe Pfad-Fix in den Tools).
# =============================================================================
__all__ = []