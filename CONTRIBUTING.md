<!--
Path: CONTRIBUTING.md
Project: ORÓMA – Offline-Realtime-Organic-Memory-AI
Version: v1.0-public-community
Date: 2026-05-21
Author: Jörg Werner / ORÓMA project
Purpose:
  Public contribution guidance for the ORÓMA source repository.
  This document defines the safe and productive way to report issues, suggest
  changes, and prepare contributions without exposing private runtime data.
Scope:
  Applies to the public Codeberg/GitHub source repositories.
  Does not apply to private live-system data, SQLite databases, logs, model
  weights, environment files, camera/audio captures, or local runtime state.
License context:
  Software source code in this repository is released under the MIT License.
  Whitepapers and scientific documentation may use separate citation/licensing
  terms, especially when referenced through Zenodo DOI records.
-->

# Contributing to ORÓMA

Thank you for your interest in ORÓMA.

ORÓMA stands for **Offline-Realtime-Organic-Memory-AI** and is an offline-first adaptive edge intelligence architecture. The public repository is intended to document and share source code, architecture notes, tests, service units, and reproducible development material.

## Project status

ORÓMA is an active research and engineering project. The public repository may contain experimental modules, incomplete research paths, and hardware-specific integrations. Contributions should therefore be conservative, reviewable, and explicit.

## What is welcome

Useful contributions include:

- Bug reports with reproducible steps.
- Documentation improvements.
- Headless/runtime stability improvements.
- Test coverage improvements.
- Security hardening suggestions.
- Compatibility fixes for Debian/Raspberry Pi/Linux edge systems.
- Small, focused pull requests with clear motivation.

## What should not be contributed

Do not submit or attach:

- SQLite databases such as `oroma.db`, `stats.db`, or `knowledge.db`.
- WAL/SHM sidecars such as `*.db-wal` or `*.db-shm`.
- Logs from live systems unless carefully redacted.
- `.env`, `.env.systemd`, tokens, passwords, API keys, SSH keys, TLS keys, or certificates.
- Camera/audio captures, snapshots, sensor dumps, or personal data.
- Runtime state folders such as `data/`, `state/`, `logs/`, `archives/`, `models/`, or `third_party/build` outputs.
- Large model weights or generated binaries.
- Unreviewed automated rewrites of large parts of the codebase.

## Development principles

Please keep changes aligned with the following principles:

1. **Headless-first operation**  
   ORÓMA must remain usable without Qt, Wayland, X11, or desktop-only dependencies.

2. **No destructive behavior**  
   Do not delete live data, learning state, rules, memories, or runtime artifacts as part of ordinary code paths. Prefer disabling, archiving, or explicitly gated cleanup.

3. **SQLite discipline**  
   Keep database connections short-lived and always closed. Use context managers or `try/finally`. Avoid long transactions and avoid direct write fallbacks when DBWriter-compatible paths are required.

4. **Visible errors**  
   Do not hide relevant failures silently. Log or expose errors through status/debug output where appropriate.

5. **Small pull requests**  
   Prefer focused changes that are easy to review and test.

6. **Preserve architecture vocabulary**  
   ORÓMA uses specific concepts such as Snap, SnapChain, SnapToken, Day/Dream, replay consolidation, binding, policy/explore split, DBWriter, and edge runtime. Please keep terminology consistent.

## Before opening a pull request

Please run the relevant checks when possible:

```bash
python3 -m py_compile core/*.py ui/*.py tools/*.py wrappers/*.py
```

For focused changes, compiling only the touched Python files is acceptable.

Also verify that no private/runtime files are staged:

```bash
git status --short
```

and, if available:

```bash
git ls-files | egrep '(^data/|^logs/|^log/|^state/|^third_party/|^models/|\.db$|\.db-wal$|\.db-shm$|\.log$|\.wav$|\.jpg$|^\.env|^docs/history/|^tree\.txt$|\.diff$|\.patch$|^\.use_orchestrator$)' && echo "STOP: private/runtime artifact tracked" || echo "OK: tracked files look public-safe"
```

## Issue reports

A good issue should include:

- ORÓMA version or commit hash.
- Operating system and hardware, if relevant.
- Exact command or UI path.
- Expected behavior.
- Actual behavior.
- Minimal redacted logs only when necessary.

## Pull request expectations

A good pull request should include:

- A concise description of the change.
- The reason for the change.
- Files touched.
- Tests or manual checks performed.
- Any runtime or configuration impact.
- Confirmation that no DBs, logs, secrets, or runtime artifacts are included.

## Licensing

By contributing code, you agree that your contribution may be distributed under the repository's MIT License unless explicitly stated otherwise in a separate file.

