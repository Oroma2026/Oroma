# ORÓMA Pull Request

## Summary

Describe the change clearly.

## Scope

- [ ] Core logic
- [ ] UI
- [ ] Tools
- [ ] Docs
- [ ] Tests
- [ ] Systemd/runtime
- [ ] Other

## Public safety checklist

- [ ] No databases are included.
- [ ] No logs are included.
- [ ] No `.env` or secrets are included.
- [ ] No runtime state is included.
- [ ] No camera/audio/private sensor data is included.
- [ ] No generated cache/build artifacts are included.

## Runtime checklist

- [ ] Headless operation preserved.
- [ ] No Qt/Wayland/X11 dependency introduced.
- [ ] SQLite/DBWriter discipline preserved.
- [ ] Errors are visible via logs/status/UI where relevant.

## Notes

Add any relevant implementation notes here.
