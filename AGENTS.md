# t3-wrapped

- The distributable skill is `skills/t3-wrapped/`; its `SKILL.md` is the agent-facing spec, and `README.md` holds the paste-in prompt.
- Scripts are stdlib-only Python 3.9+ so they run on any T3 user's machine; keep it that way.
- `collect.py` must open T3's database read-only and keep transcripts, project names and thread titles out of `wrapped.json`.
- Smoke test: `skills/t3-wrapped/scripts/wrapped.sh` (writes to `~/t3-wrapped/<year>/`), then look at every PNG.
