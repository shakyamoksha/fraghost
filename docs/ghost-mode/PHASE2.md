# Ghost Mode phase 2

Foundation only. No tunnel, no new route, and no new network dependency.

## Modified files

- `shakya/ghost.py` — state machine. `PROTECTED` is refused.
- `shakya/secrets.py` — Windows DPAPI for the proxy password.
- `shakya/settings.py` — `ghost_enabled`, migration, public settings view.
- `shakya/engine.py` — controller wiring. The proxy switch is unchanged.
- `shakya/server.py` — settings responses omit the password. `GET /api/ghost`.
- `shakya/selftest.py` — password redaction and Ghost Mode alongside the existing torrent checks.
- `shakya/test_ghost.py` — state machine and plaintext migration.
- `ui/src/types.ts`, `ui/src/api.ts`, `ui/src/App.tsx` — separate Ghost Mode control and status.

## Tests

- `python -m shakya.test_ghost` — passed (`ghost tests ok`).
- `python -m shakya.selftest` — passed (`selftest ok`).
- `npm run build` in `ui` — passed.

## Limitations

Ghost Mode cannot become `PROTECTED`. With the switch on and no trusted endpoint it stays `BLOCKED`. The existing proxy can still be on or off independently. UDP is still not a sealed tunnel. RSS and update downloads still use the normal route.
