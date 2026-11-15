---
name: t3-wrapped
description: Build a Spotify-Wrapped-style "T3 Code Wrapped" from the user's local T3 Code history — a stats file plus a deck of share-ready PNG cards (sized for X/Twitter posts), with optional AI-generated art. Use when the user asks for their T3 Wrapped, coding-agent year in review, or a recap of their agent usage. Works with any provider T3 runs (Claude, Codex, Cursor, …).
---

# T3 Code Wrapped

Everything runs locally from T3's own database, so it covers every provider and model the user ran through T3. Scripts are Python 3.9+ standard library only. PNG export needs Chrome, Chromium, Edge or Brave.

Scripts live next to this file in `scripts/`. Default output folder: `~/t3-wrapped/<year>/`.

## 1. Collect

```sh
python3 scripts/collect.py --year 2026 --out ~/t3-wrapped/2026/wrapped.json
```

- Auto-detects `~/.t3/userdata/statev2.sqlite` (or `$T3_HOME`). If it is not found, ask the user where T3 keeps its data and pass `--db`.
- Opens the database read-only. The output holds counts, model names and the user's most-used prompt words (`you.top_words`). It never holds project names, thread titles or prompt text.
- By default, only threads **started in T3** count. History that T3 imported from other apps (e.g. Claude desktop, Codex) has messages but no commands, diffs, tokens or access mode, so mixing it in would skew the stats. The Day one card notes how much was imported. `--include-imported` adds that history to the prompt, calendar, word and project counts; activity cards stay T3-only either way.
- If the requested year has almost no data (e.g. it's early January), offer the previous year.

## 2. Words and handle (ask once, before rendering)

Show the user `you.top_words`, since common words can hint at what they're building. Ask whether to keep them or re-run collection with `--no-words` (the Words card then skips itself). Also ask whether they want a handle (e.g. `@name`) in the footer, and whether to drop any cards. Do not add any other names.

## 3. Captions (optional, recommended)

The default copy is generic. Write `captions.json` in the output folder to give the deck personality:

```json
{ "prompts": { "kicker": "You sent", "title": "prompts", "note": "That's 47 a day. Your agents have unionized." } }
```

Rules:
- Use only numbers that appear in `wrapped.json`. Never invent stats or context, and never add project, repo or people names.
- Keep `title` to 8 words or fewer and `note` to 16 or fewer. Cards have no room for more.
- Tease the user's habits, never the user. Being funny about the agents is fine.
- On hero cards (`prompts`, `agent_time`, `code`, `hands`) the `title` is the unit under the big number. Keep it short.
- Keys you omit keep the defaults. An empty string removes that line.

## 4. Art (optional)

The cover and persona cards already get generative art built from the user's own activity (a weekday × hour "fingerprint" dial). If you have an image-generation tool (for example, Codex's built-in image generation) and the user wants AI art:
- Generate **textless** portrait images (4:5, at least 1080×1350) for any card id, typically `cover` and `persona`. Save them as `art/<card id>.png` in the output folder; the renderer uses them as a darkened background.
- Never ask the image model to draw numbers, words, logos or UI. The renderer draws all text, so stats stay exact.
- Prompt idea for `persona`: an abstract, bold, poster-style illustration of "<persona name>": <persona blurb>, on a near-black background, using one or two saturated accents, with no text.
- Only the persona name and blurb go into the prompt; never send project names or stats.
- No image tool? Skip this step; the deck is complete without it.

## 5. Render

```sh
python3 scripts/render.py ~/t3-wrapped/2026 --handle @name
```

Output:
- `deck.html`: all cards; open it in a browser to scroll through.
- `cards/NN-<id>.png`: 2160×2700 (1080×1350 at 2×) each.

Options:
- `--cards a,b,c` picks which cards to include and their order.
- `--max N` caps the card count (default 20).
- `--html-only` writes the deck without exporting PNGs.

Cards with no data skip themselves.

`scripts/wrapped.sh [year] [render args]` runs collect + render in one go.

## 6. Check every card

Open every PNG and look at it. Watch for:
- clipped or overlapping text
- a card that reads as wrong or unflattering

Fix by shortening a caption or dropping the card, then re-render. Do not hand off without looking.

## 7. Hand off

Give the user:
- the `cards/` folder path, and the card order you recommend
- a suggested post (at most 280 characters) that leads with their persona and best stat

Point out that X's per-post image limit decides how many fit in one post. Never post or upload anything yourself.

## Cards

| id | Shows |
|---|---|
| cover | T3 Code Wrapped + year over the activity fingerprint |
| origin | Day one: the date the user downloaded T3 (their first T3 thread), a join-date title (`JOIN_TITLES` in render.py), and a note on imported history |
| prompts | Prompts sent, active days, longest streak |
| calendar | Prompts per month (per week when the history is under ~4 months), busiest day |
| clock | Weekday × hour heatmap, peak hour, late nights |
| agent_time | Agent hours, peak parallel agents, tokens, API-price estimate |
| models | Share of prompts per model, provider split |
| model_timeline | Model mix by month (or week) |
| projects | Project count and how prompts spread across them (no names) |
| code | Lines added/removed, files, languages |
| hands | Commands, tool calls, web searches, subagents, favorite command |
| apologies | "You're right" / "good catch" / "my mistake" count, per model |
| words | Most-used words, "please", "actually", "still broken" |
| habits | Full-access share, interrupts, compactions, tiny prompts, phone prompts |
| records | Longest thread, longest turn, biggest diff, busiest turn |
| persona | Archetype picked from the stats, plus runners-up |
| summary | Six-stat recap |

## Definitions (for explaining numbers)

- **Prompts**: messages the user typed, from desktop, web or mobile. Messages agents sent to other agents don't count.
- **Agent hours**: summed turn durations, each turn capped at 4 hours. **Peak parallel**: the most turns running at once.
- **API-price estimate**: token usage priced with T3's model rate table. Subscription users didn't pay this; label it as an API-price estimate. Token data only exists from newer T3 versions (`tokens.since`).
- **Receipts** (persona input only, no card): of the turns that edited files, the share that also ran a test, build, lint or typecheck, or took a screenshot or preview in the same turn. It misses checks done in a later turn, another thread, or by hand, so it is a floor; never present it as a grade.
- **Model per prompt**: the model of the run the prompt started. Imported history (only with `--include-imported`) uses the thread's last selected model.
- **Activity ratios** (interrupt rate, full-access share, receipts) use only T3-started threads, even with `--include-imported`. Prompt-based stats and the persona use whatever is included.
- **Persona**: the highest-scoring of 11 archetypes (`persona.scores`). You may reword the blurb in captions, but keep the archetype.
