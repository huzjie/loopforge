# HTTP API

Standard-library HTTP server, no external dependency.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | `{"ok": true, ...}` |
| GET | `/v1/info` | config summary (loops, dim, mhc, skill) |
| GET | `/v1/run` | full task run + aggregate summary |
| POST | `/v1/run` | `{"loops": N}` run at N loops |
| GET | `/v1/bench` | skill table + loop curve |
| GET | `/v1/stability` | mHC vs naive deep-recurrence stability |

Start with `python -m loopforge.cli serve --port 8431`.
