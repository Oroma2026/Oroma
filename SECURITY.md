<!--
Path: SECURITY.md
Project: ORÓMA – Offline-Realtime-Organic-Memory-AI
Version: v1.0-public-community
Date: 2026-05-21
Author: Jörg Werner / ORÓMA project
Purpose:
  Security disclosure and safe-reporting policy for the public ORÓMA repository.
Scope:
  Applies to source-code issues, service/unit configuration, local web UI,
  DBWriter/runtime access paths, edge-device integration, and documentation that
  may affect safe deployment. It does not request public disclosure of secrets,
  live databases, logs, camera/audio captures, tokens, or private system data.
-->

# Security Policy

ORÓMA is an offline-first adaptive edge intelligence architecture. It may run on local Linux edge devices, use local web interfaces, systemd services, SQLite databases, camera/audio components, PTZ control, and model/runtime integrations.

Security reports are welcome, but please do not disclose exploitable details publicly before they can be reviewed.

## Supported public scope

Security feedback is relevant for:

- Local Flask/UI routes and API endpoints.
- Authentication and token handling.
- Systemd service configuration.
- File permissions and runtime paths.
- SQLite/DBWriter access patterns.
- Import/export boundaries.
- Camera/audio/PTZ safety boundaries.
- Unsafe defaults in public configuration examples.
- Documentation that could lead users to expose secrets or private data.

## Do not post private data publicly

Please do not include the following in GitHub/Codeberg issues or pull requests:

- `.env`, `.env.systemd`, tokens, passwords, API keys, SSH keys, TLS keys, certificates.
- SQLite databases such as `oroma.db`, `stats.db`, `knowledge.db`.
- `*.db-wal`, `*.db-shm`, backups, archives, or live runtime state.
- Logs containing hostnames, IPs, personal data, tokens, camera paths, or private environment values.
- Camera snapshots, audio recordings, sensor dumps, or personal data.
- Exploit payloads that can be copied and used against live systems.

## How to report a vulnerability

Preferred reporting path:

1. Open a minimal public issue saying that you have a security concern, without exploit details.
2. Provide a safe, high-level description of the affected component.
3. Wait for maintainer guidance on how to share details privately.

If a private contact address is later published in the repository metadata, use that channel for sensitive details.

## Report content

A useful report should include:

- Affected file, route, service, or component.
- Expected security boundary.
- Observed behavior.
- Minimal reproduction steps using dummy data.
- Impact assessment.
- Suggested mitigation, if known.

## Response expectations

This is a small independent research project. Response times may vary. Security issues that can expose secrets, permit unintended remote access, bypass local UI protection, or cause unsafe PTZ/device behavior are treated as high priority.

## Safe defaults

Public examples should assume:

- Local-first deployment.
- No public exposure of the UI without explicit hardening.
- No publishing of DBs, logs, runtime state, or model weights.
- No default trust in uploaded/imported files.
- No silent fallback to unsafe write paths.

