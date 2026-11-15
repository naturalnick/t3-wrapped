# T3 Code Wrapped

Your year with coding agents, as a deck of share cards. It reads T3 Code's local database, so it covers every provider you ran through T3. Nothing leaves your machine unless you post the images.

## Paste this into T3

```
Make my T3 Code Wrapped. Clone https://github.com/naturalnick/t3-wrapped into a temporary folder, read skills/t3-wrapped/SKILL.md, and follow it step by step. Run everything locally.
```

## Or install it as a skill

```sh
cp -R skills/t3-wrapped ~/.claude/skills/   # Claude
cp -R skills/t3-wrapped ~/.codex/skills/    # Codex
```

Then ask for "my T3 Wrapped".

## Run it by hand

```sh
skills/t3-wrapped/scripts/wrapped.sh 2026 --handle @you   # → ~/t3-wrapped/2026/cards/*.png
```

Requires Python 3.9+ and Chrome, Chromium, Edge or Brave for the PNG export.
