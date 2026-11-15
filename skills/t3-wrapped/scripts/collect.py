#!/usr/bin/env python3
"""Aggregate a year of T3 Code activity into wrapped.json.

Reads T3's local state database read-only. The output holds only counts, model
names, a few fixed phrases and the user's most-used words; no project names,
thread titles or prompt text.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

# A single stuck turn should not count as days of agent time.
MAX_TURN_SECONDS = 4 * 3600

VERIFY_CMD = re.compile(
    r"""(?x)
    \b(pytest|jest|vitest|mocha|rspec|phpunit|playwright|cypress|ctest|xcodebuild
      |tsc|typecheck|type-check|eslint|ruff|mypy|pyright|flake8|biome|clippy|shellcheck
      |swiftlint|golangci-lint|prettier\s+--check)\b
    | \b(swift|cargo|go|deno|bun|dotnet|mix|zig)\s+(test|build|check|vet)\b
    | \b(npm|pnpm|yarn|bun)\s+(run\s+)?[\w:-]*(test|build|lint|check|verify)\b
    | \b(make|just|task)\s+[\w-]*(test|check|lint|build|verify)\b
    | \b(gradlew?|mvn)\b.*\b(test|check|build|verify)\b
    | \bgodot\b.*--headless
    | \b(unittest|bazel\s+test|python3?\s+-m\s+(pytest|unittest))\b
    | (^|/)(test|tests|check|verify|ci)[\w-]*\.sh\b
    """
)
VERIFY_TOOL = re.compile(r"screenshot|snapshot|preview_|test|lint|diagnostic", re.I)

APOLOGY = re.compile(
    r"you'?re (absolutely |totally |completely |quite )?right|good catch|my (mistake|bad|apologies)"
    r"|i apologi[sz]e|sorry about that|you caught",
    re.I,
)

PHRASES = {
    "please": r"\b(please|pls|plz)\b",
    "thanks": r"\b(thanks|thank you|thx|ty)\b",
    "actually": r"\bactually\b",
    "still_broken": r"\bstill (broken|not|doesn'?t|failing|wrong|the same|happening)\b|\bnot working\b|\b(doesn'?t|didn'?t|does not|did not) work\b",
    "why": r"^\s*why\b|\bwhy (is|does|did|are|isn'?t|doesn'?t|would)\b",
    "wtf": r"\b(wtf|ffs|omg)\b|\bwhat the (hell|heck|fuck)\b",
    "go_on": r"^\s*(continue|go|go ahead|keep going|proceed|do it|yes|yep|yeah|y|ok|okay|sure|lgtm|ship it|sounds good)[\s.!]*$",
}
PHRASES = {k: re.compile(v, re.I | re.M) for k, v in PHRASES.items()}

STOPWORDS = set(
    """a about above after again against all also am an and any are aren't as at be because been before being below
    between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for
    from further get got had hadn't has hasn't have haven't having he her here hers herself him himself his how i i'd
    i'll i'm i've if in into is isn't it it's its itself just let's like make me more most much must my myself need no
    nor not now of off on once one only or other ought our ours ourselves out over own same she should shouldn't so some
    such than that that's the their theirs them themselves then there there's these they they're this those through to
    too under until up us use used using very want was wasn't way we we're were weren't what what's when where which
    while who whom why will with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
    also can just thing things something anything everything really still even think know see look looks see seems
    maybe sure ok okay yes yeah well right now new way lot bit go going gonna want wanted try trying able make made
    sure let lets etc via per instead already actually please thanks thank fix it's i'm don't can't won't there's
    should could would might will shall here there where when what which who whom whose how why
    add added change changed update updated remove removed work works working file files code good great
    feel feels done next first last also another other better new nice keep still back away into onto
    true false null none name names value values type types set sets get gets run runs check checked
    """.split()
)

LANGS = {
    "py": "Python", "ts": "TypeScript", "tsx": "TypeScript", "js": "JavaScript", "jsx": "JavaScript",
    "mjs": "JavaScript", "cjs": "JavaScript", "swift": "Swift", "kt": "Kotlin", "kts": "Kotlin", "java": "Java",
    "go": "Go", "rs": "Rust", "rb": "Ruby", "php": "PHP", "c": "C", "h": "C/C++", "cc": "C++", "cpp": "C++",
    "hpp": "C++", "m": "Objective-C", "mm": "Objective-C", "cs": "C#", "fs": "F#", "gd": "GDScript",
    "gdshader": "Godot shaders", "tscn": "Godot scenes", "lua": "Lua", "dart": "Dart", "scala": "Scala",
    "ex": "Elixir", "exs": "Elixir", "erl": "Erlang", "hs": "Haskell", "ml": "OCaml", "clj": "Clojure",
    "zig": "Zig", "nim": "Nim", "jl": "Julia", "r": "R", "sql": "SQL", "sh": "Shell", "bash": "Shell",
    "zsh": "Shell", "fish": "Shell", "ps1": "PowerShell", "html": "HTML", "css": "CSS", "scss": "CSS",
    "sass": "CSS", "less": "CSS", "vue": "Vue", "svelte": "Svelte", "astro": "Astro", "md": "Markdown",
    "mdx": "Markdown", "json": "JSON", "yaml": "YAML", "yml": "YAML", "toml": "TOML", "xml": "XML",
    "glsl": "Shaders", "metal": "Shaders", "wgsl": "Shaders", "hlsl": "Shaders", "sol": "Solidity",
    "tf": "Terraform", "proto": "Protobuf", "graphql": "GraphQL", "ipynb": "Notebooks", "tex": "LaTeX",
}

ARCHETYPES = {
    "night_shift": ("The Night Shift", "Your best ideas show up after midnight, and so do you."),
    "conductor": ("The Conductor", "One agent is a conversation. You ran an orchestra."),
    "polyglot": ("The Model Polyglot", "No favorites. Every model got an audition."),
    "loyalist": ("The Loyalist", "You found your model and never looked back."),
    "inspector": ("The Inspector", "Trust, but run the tests. Then run them again."),
    "cowboy": ("The Cowboy", "Full access, no tests, straight to prod. Yeehaw."),
    "backseat": ("The Backseat Driver", "You hit stop the moment it turned the wrong way."),
    "novelist": ("The Novelist", "Your prompts have chapters."),
    "minimalist": ("The Minimalist", "'y'. 'go'. 'continue'. Why use many word."),
    "diplomat": ("The Diplomat", "Please and thank you, every single time. The robots will remember."),
    "commuter": ("The Commuter", "You shipped from your phone more than most people ship from a desk."),
}


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def find_db(explicit):
    if explicit:
        return Path(explicit).expanduser()
    roots = []
    if os.environ.get("T3_HOME"):
        roots.append(Path(os.environ["T3_HOME"]).expanduser() / "userdata")
    roots.append(Path.home() / ".t3" / "userdata")
    for root in roots:
        if (root / "statev2.sqlite").exists():
            return root / "statev2.sqlite"
        # Future schema versions may rename the file; take the newest state*.sqlite.
        found = sorted(root.glob("state*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if found:
            return found[0]
    sys.exit("Could not find T3's state database. Pass --db /path/to/statev2.sqlite.")


class DB:
    def __init__(self, path: Path):
        self.con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.tables = {r[0] for r in self.con.execute("select name from sqlite_master where type='table'")}

    def has(self, table):
        return table in self.tables

    def rows(self, sql):
        return self.con.execute(sql)


def short_tool(name):
    if not name:
        return None
    name = str(name)
    if name.startswith("mcp__"):
        parts = name.split("__")
        name = parts[-1] if len(parts) >= 3 else name
    return name.rsplit(".", 1)[-1]


def command_verb(cmd):
    if not cmd:
        return None
    cmd = re.sub(r"^\s*(/bin/)?(ba|z)?sh\s+-l?c\s+['\"]?", "", cmd)
    for segment in re.split(r"&&|;|\|\||\n", cmd):
        tokens = segment.strip().split()
        while tokens and (re.match(r"^\w+=", tokens[0]) or tokens[0] in {"sudo", "env", "time", "exec", "command"}):
            tokens.pop(0)
        if not tokens or tokens[0] in {"cd", "export", "set", "source", ".", "echo", "true", "("}:
            continue
        verb = os.path.basename(tokens[0].strip("'\"()"))
        if re.match(r"^[\w.+-]{1,24}$", verb):
            return verb
    return None


def diff_counts(diff):
    added = removed = 0
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def lang_of(path):
    if not path:
        return None
    name = os.path.basename(str(path)).lower()
    if name in {"dockerfile", "makefile", "justfile"}:
        return name.capitalize()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return LANGS.get(ext)


def load_rates():
    path = Path.home() / ".t3" / "userdata" / "usage-model-rates.json"
    try:
        doc = json.loads(path.read_text())
        return doc.get("document", doc)
    except (OSError, ValueError):
        return {}


def rate_for(rates, model):
    if not model or not rates:
        return None
    for key in (model, model.split("/")[-1]):
        r = rates.get(key)
        if r and r.get("input_cost_per_token") is not None:
            return r
    return None


def longest_streak(days):
    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="Path to T3's statev2.sqlite (auto-detected by default)")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--out", required=True, help="Where to write wrapped.json")
    ap.add_argument("--no-words", action="store_true", help="Skip the most-used-words list")
    ap.add_argument("--include-imported", action="store_true",
                    help="Also count history T3 imported from other apps (Claude/Codex). It has messages only, no tool or token detail")
    args = ap.parse_args()

    db = DB(find_db(args.db))
    tz = datetime.now().astimezone().tzinfo
    start = datetime(args.year, 1, 1, tzinfo=tz)
    end = datetime(args.year + 1, 1, 1, tzinfo=tz)
    in_range = lambda t: t is not None and start <= t < end  # noqa: E731
    warnings = []

    # ---- projects & threads -------------------------------------------------

    threads = {}
    for tid, pid, rmode, created, rel, model, provider, origin in db.rows(
        """select thread_id, project_id, runtime_mode, created_at,
                  json_extract(payload_json, '$.lineage.relationshipToParent'),
                  json_extract(payload_json, '$.modelSelection.model'),
                  coalesce(provider_instance_id, default_provider),
                  json_extract(payload_json, '$.historyOrigin')
           from orchestration_v2_projection_threads"""
    ):
        # historyOrigin is set on threads T3 imported from other apps rather than ones started in T3.
        threads[tid] = dict(project=pid, mode=rmode, created=parse_ts(created),
                            child=bool(rel), model=model, provider=provider, imported=bool(origin))
    skip = lambda tid: threads.get(tid, {}).get("imported") and not args.include_imported  # noqa: E731

    # ---- runs (one per prompt sent to an agent) -----------------------------
    runs = {}
    for rid, tid, provider, req, opts, model in db.rows(
        """select run_id, thread_id, provider, requested_at,
                  json_extract(payload_json, '$.modelSelection.options'),
                  json_extract(payload_json, '$.modelSelection.model')
           from orchestration_v2_projection_runs"""
    ):
        t = parse_ts(req)
        effort = None
        try:
            for o in json.loads(opts or "[]"):
                if re.search(r"effort|reasoning", str(o.get("id", "")), re.I):
                    effort = str(o.get("value"))
        except (ValueError, AttributeError):
            pass
        runs[rid] = dict(thread=tid, provider=provider, model=model or provider, at=t, effort=effort)


    # ---- provider turns: time + tokens --------------------------------------
    intervals = []
    tokens = Counter()
    cost = 0.0
    cost_covered = cost_total_turns = 0
    rates = load_rates()
    longest_turn = None
    token_since = None
    for tid, attempt, s, e, model, driver, inp, out, cached, created, reasoning in db.rows(
        """select thread_id, run_attempt_id, started_at, completed_at,
                  json_extract(payload_json, '$.model'),
                  json_extract(payload_json, '$.nativeTurnRef.driver'),
                  json_extract(payload_json, '$.turnTokenUsage.inputTokens'),
                  json_extract(payload_json, '$.turnTokenUsage.outputTokens'),
                  json_extract(payload_json, '$.turnTokenUsage.cachedInputTokens'),
                  json_extract(payload_json, '$.turnTokenUsage.cacheCreationTokens'),
                  json_extract(payload_json, '$.turnTokenUsage.reasoningTokens')
           from orchestration_v2_projection_provider_turns"""
    ):
        st, et = parse_ts(s), parse_ts(e)
        if not in_range(st) or skip(tid):
            continue
        if not model and attempt:
            m = re.match(r"run-attempt:run:(.+):attempt:\d+$", attempt)
            model = runs.get(unquote(m.group(1)), {}).get("model") if m else None
        model = model or threads.get(tid, {}).get("model")
        if et and et > st:
            secs = min((et - st).total_seconds(), MAX_TURN_SECONDS)
            intervals.append((st, st + timedelta(seconds=secs)))
            if not longest_turn or secs > longest_turn["seconds"]:
                longest_turn = dict(seconds=round(secs), thread=tid, model=model)
        inp, out, cached, created = (int(x or 0) for x in (inp, out, cached, created))
        if inp or out:
            tokens["input"] += inp
            tokens["output"] += out
            tokens["cached"] += cached
            tokens["reasoning"] += int(reasoning or 0)
            token_since = min(token_since, st) if token_since else st
            cost_total_turns += 1
            r = rate_for(rates, model)
            if r:
                fresh = max(inp - cached - created, 0)
                cost += (
                    fresh * r.get("input_cost_per_token", 0)
                    + cached * (r.get("cache_read_input_token_cost") or r.get("input_cost_per_token", 0))
                    + created * (r.get("cache_creation_input_token_cost") or r.get("input_cost_per_token", 0))
                    + out * r.get("output_cost_per_token", 0)
                )
                cost_covered += 1

    agent_seconds = sum((b - a).total_seconds() for a, b in intervals)
    # Sweep for wall-clock coverage and peak parallelism.
    events = sorted([(a, 1) for a, _ in intervals] + [(b, -1) for _, b in intervals], key=lambda x: (x[0], x[1]))
    live = peak = 0
    peak_at = None
    wall = 0.0
    last = None
    for t, delta in events:
        if live > 0 and last:
            wall += (t - last).total_seconds()
        live += delta
        last = t
        if live > peak:
            peak, peak_at = live, t

    # ---- messages -----------------------------------------------------------
    prompts = []  # (time, text, thread, source, model, provider)
    apologies = Counter()
    assistant_msgs = Counter()
    first_native = None  # (time, thread) of the first prompt typed in a T3-started thread, any year
    imported = dict(threads=set(), prompts=0, first=None, last=None, providers=Counter())
    for role, created, tid, rid, text, by, source in db.rows(
        """select role, created_at, thread_id, run_id,
                  json_extract(payload_json, '$.text'),
                  json_extract(payload_json, '$.createdBy'),
                  json_extract(payload_json, '$.creationSource')
           from orchestration_v2_projection_messages where streaming = 0"""
    ):
        t = parse_ts(created)
        th = threads.get(tid, {})
        if role == "user" and by == "user" and t:
            if th.get("imported"):
                imported["threads"].add(tid)
                imported["prompts"] += 1
                imported["first"] = min(imported["first"] or t, t)
                imported["last"] = max(imported["last"] or t, t)
            elif not th.get("child") and (first_native is None or t < first_native[0]):
                first_native = (t, tid)
        if not in_range(t) or skip(tid):
            continue
        run = runs.get(rid, {})
        model = run.get("model") or th.get("model") or "unknown"
        if role == "user" and by == "user":
            prompts.append((t, text or "", tid, source, model, run.get("provider") or th.get("provider") or "unknown"))
        elif role == "assistant":
            assistant_msgs[model] += 1
            if text and APOLOGY.search(text):
                apologies[model] += 1

    # ---- turn items: tools, commands, diffs ---------------------------------
    item_counts = Counter()
    tool_counts = Counter()
    verbs = Counter()
    run_edits = Counter()
    run_lines = Counter()
    run_actions = Counter()
    verified_runs = set()
    interrupts_by_thread = Counter()
    lang_lines = Counter()
    files = set()
    lines_added = lines_removed = 0
    for tid, rid, typ, upd, start_s, cmd, tool, fname, changes, diff in db.rows(
        """select thread_id, run_id, type, updated_at,
                  json_extract(payload_json, '$.startedAt'),
                  case when type = 'command_execution' then substr(json_extract(payload_json, '$.input'), 1, 800) end,
                  case when type = 'dynamic_tool' then json_extract(payload_json, '$.toolName') end,
                  case when type = 'file_change' then json_extract(payload_json, '$.fileName') end,
                  case when type = 'file_change' then json_extract(payload_json, '$.changes') end,
                  case when type = 'file_change' then json_extract(payload_json, '$.diffStr') end
           from orchestration_v2_projection_turn_items"""
    ):
        if not in_range(parse_ts(start_s) or parse_ts(upd)) or skip(tid):
            continue
        item_counts[typ] += 1
        if typ == "command_execution":
            run_actions[rid] += 1
            v = command_verb(cmd)
            if v:
                verbs[v] += 1
            if cmd and VERIFY_CMD.search(cmd):
                verified_runs.add(rid)
        elif typ == "dynamic_tool":
            run_actions[rid] += 1
            name = short_tool(tool)
            if name:
                tool_counts[name] += 1
                if VERIFY_TOOL.search(name):
                    verified_runs.add(rid)
        elif typ == "file_change":
            run_actions[rid] += 1
            run_edits[rid] += 1
            paths = []
            try:
                paths = [c.get("path") for c in json.loads(changes or "[]") if c.get("path")]
            except (ValueError, AttributeError):
                pass
            paths = paths or ([fname] if fname else [])
            files.update(paths)
            a, r = diff_counts(diff)
            lines_added += a
            lines_removed += r
            run_lines[rid] += a + r
            if paths:
                share = (a + r) / len(paths)
                for p in paths:
                    lang = lang_of(p)
                    if lang:
                        lang_lines[lang] += share
        elif typ == "run_interrupt_request":
            interrupts_by_thread[tid] += 1

    # ---- derived ------------------------------------------------------------
    n_prompts = len(prompts)
    days = {t.date() for t, *_ in prompts}
    per_month = Counter(t.month for t, *_ in prompts)
    week_of = lambda t: t.date() - timedelta(days=t.weekday())  # noqa: E731
    per_week = Counter(week_of(t) for t, *_ in prompts)
    model_week = defaultdict(Counter)
    for p in prompts:
        model_week[week_of(p[0])][p[4]] += 1
    weeks = []
    if per_week:
        w = min(per_week)
        while w <= max(per_week):
            weeks.append(w)
            w += timedelta(days=7)
    per_day = Counter(t.date() for t, *_ in prompts)
    heat = [[0] * 24 for _ in range(7)]
    for t, *_ in prompts:
        heat[t.weekday()][t.hour] += 1
    night = [t for t, *_ in prompts if t.hour < 5]
    latest_night = max(night, key=lambda t: (t.hour, t.minute)) if night else None
    night_share = sum(1 for t, *_ in prompts if t.hour >= 22 or t.hour < 5) / max(n_prompts, 1)

    if not prompts:
        sys.exit(f"No T3 prompts found in {args.year}.")
    model_runs = Counter(p[4] for p in prompts)
    provider_runs = Counter(p[5] for p in prompts)
    model_month = defaultdict(Counter)
    for p in prompts:
        model_month[p[0].month][p[4]] += 1
    effort = Counter()
    effort_model = defaultdict(Counter)
    for r in runs.values():
        if r["effort"] and in_range(r["at"]) and not skip(r["thread"]):
            effort[r["effort"].lower()] += 1
            effort_model[r["model"]][r["effort"].lower()] += 1

    proj_runs = Counter()
    proj_days = defaultdict(set)
    thread_runs = Counter()
    for t, _, tid, *_ in prompts:
        th = threads.get(tid, {})
        if th.get("child"):
            continue
        thread_runs[tid] += 1
        pid = th.get("project")
        if pid:
            proj_runs[pid] += 1
            proj_days[pid].add(t.date())

    top_threads = [tid for tid, _ in thread_runs.most_common()]
    lengths = [len(text) for _, text, *_ in prompts]
    phrase_counts = {k: sum(1 for _, text, *_ in prompts if rx.search(text)) for k, rx in PHRASES.items()}
    caps = sum(1 for _, text, *_ in prompts if len(re.findall(r"[A-Za-z]", text)) >= 12
               and sum(c.isupper() for c in text) / max(len(re.findall(r"[A-Za-z]", text)), 1) > 0.7)
    words = Counter()
    if not args.no_words:
        for _, text, *_ in prompts:
            text = re.sub(r"```.*?```|<pasted_content.*?</pasted_content>", " ", text, flags=re.S)
            # Pasted logs, JSON and stack traces would swamp the words a person actually typed.
            text = "\n".join(line for line in text.splitlines()[:40] if len(line) < 400 and not re.search(r"[{}<>=;]|\d{3,}|\w+\(\)|^\s*(at|File|Error|\[)", line))
            text = re.sub(r"https?://\S+|\S*[/\\]\S*|`[^`]*`|\S+\.\w{1,5}\b|\b\w*[A-Z]\w*[A-Z]\w*\b|\b\w+_\w+\b", " ", text)
            for w in re.findall(r"[a-z][a-z']{2,}", text.lower()):
                w = w.strip("'")
                if w not in STOPWORDS and len(w) > 2:
                    words[w] += 1
    sources = Counter(p[3] for p in prompts)

    edit_runs = [rid for rid in run_edits]
    verified_edit_runs = [rid for rid in edit_runs if rid in verified_runs]
    receipts = len(verified_edit_runs) / len(edit_runs) if edit_runs else None

    top_threads_set = [tid for tid in thread_runs if not threads.get(tid, {}).get("child")]
    one_and_done = sum(1 for tid in top_threads_set if thread_runs[tid] == 1)

    biggest_run = max(run_lines.items(), key=lambda kv: kv[1], default=(None, 0))
    busiest_run = max(run_actions.items(), key=lambda kv: kv[1], default=(None, 0))
    most_interrupted = max(interrupts_by_thread.items(), key=lambda kv: kv[1], default=(None, 0))

    apology_rates = {
        m: dict(count=apologies[m], replies=assistant_msgs[m], per_100=round(100 * apologies[m] / assistant_msgs[m], 1))
        for m in assistant_msgs
        if assistant_msgs[m] >= 50 and m != "unknown"
    }

    # ---- persona ------------------------------------------------------------
    top_model_share = model_runs.most_common(1)[0][1] / n_prompts
    real_models = sum(1 for c in model_runs.values() if c / n_prompts >= 0.05)
    median_len = statistics.median(lengths) if lengths else 0
    short_share = sum(1 for n in lengths if n <= 12) / max(len(lengths), 1)
    polite = (phrase_counts["please"] + phrase_counts["thanks"]) / max(n_prompts, 1)
    native_threads = [tid for tid in top_threads_set if not threads.get(tid, {}).get("imported")]
    yolo_share = sum(1 for tid in native_threads if threads[tid]["mode"] == "full-access") / max(len(native_threads), 1)
    n_native = sum(1 for p in prompts if not threads.get(p[2], {}).get("imported"))
    interrupt_rate = item_counts["run_interrupt_request"] / max(n_native, 1)
    mobile_share = sources.get("mobile", 0) / max(n_prompts, 1)
    scores = {
        "night_shift": night_share / 0.35,
        "conductor": max(peak / 6, (agent_seconds / wall if wall else 0) / 2.5),
        "polyglot": real_models / 4,
        "loyalist": (top_model_share - 0.6) / 0.35,
        "inspector": (receipts or 0) / 0.75,
        "cowboy": (1 - (receipts if receipts is not None else 1)) * yolo_share / 0.7,
        "backseat": interrupt_rate / 0.08,
        "novelist": median_len / 450,
        "minimalist": short_share / 0.25,
        "diplomat": polite / 0.3,
        "commuter": mobile_share / 0.12,
    }
    ranked = sorted(scores, key=scores.get, reverse=True)
    persona_key = ranked[0]

    def top_list(counter, n, name=lambda k: k):
        return [dict(name=name(k), value=round(v, 1) if isinstance(v, float) else v) for k, v in counter.most_common(n)]

    out = {
        "schema": 1,
        "year": args.year,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "range": dict(first=min(days).isoformat(), last=max(days).isoformat()),
        "totals": dict(
            prompts=n_prompts,
            threads=len(top_threads_set),
            projects=len(proj_runs),
            active_days=len(days),
            longest_streak=longest_streak(days),
            agent_hours=round(agent_seconds / 3600, 1),
            wall_hours=round(wall / 3600, 1),
            parallel_factor=round(agent_seconds / wall, 2) if wall else None,
            peak_parallel=peak,
            peak_parallel_at=peak_at.isoformat(timespec="minutes") if peak_at else None,
        ),
        "tokens": dict(
            input=tokens["input"], output=tokens["output"], cached=tokens["cached"], reasoning=tokens["reasoning"],
            total=tokens["input"] + tokens["output"],
            since=token_since.date().isoformat() if token_since else None,
            api_cost_usd=round(cost, 2) if cost_covered else None,
            cost_coverage=round(cost_covered / cost_total_turns, 2) if cost_total_turns else None,
        ),
        "calendar": dict(
            per_month=[per_month.get(m, 0) for m in range(1, 13)],
            per_week=[dict(start=w.isoformat(), prompts=per_week.get(w, 0)) for w in weeks],
            busiest_day=dict(date=max(per_day, key=per_day.get).isoformat(), prompts=max(per_day.values())) if per_day else None,
            heatmap=heat,
            night_share=round(night_share, 3),
            latest_night=latest_night.strftime("%-I:%M %p") if latest_night else None,
            latest_night_date=latest_night.date().isoformat() if latest_night else None,
            weekend_share=round(sum(1 for t, *_ in prompts if t.weekday() >= 5) / max(n_prompts, 1), 3),
        ),
        "models": dict(
            by_model=top_list(model_runs, 12),
            by_provider=top_list(provider_runs, 8),
            by_month=[dict(month=m, models=dict(model_month[m])) for m in range(1, 13) if model_month[m]],
            by_week=[dict(start=w.isoformat(), models=dict(model_week[w])) for w in weeks],
            effort=dict(effort),
            effort_by_model={m: dict(c) for m, c in effort_model.items()},
            first=min(prompts)[4],
            last=max(prompts)[4],
        ),
        # Names are left out on purpose: the deck shows how work was spread, not what it was.
        "projects": [dict(prompts=c, days=len(proj_days[pid])) for pid, c in proj_runs.most_common()],
        "code": dict(
            lines_added=lines_added,
            lines_removed=lines_removed,
            files_touched=len(files),
            languages=top_list(Counter({k: round(v) for k, v in lang_lines.items()}), 6),
        ),
        "agent": dict(
            commands=item_counts["command_execution"],
            tool_calls=item_counts["dynamic_tool"],
            file_edits=item_counts["file_change"],
            web_searches=item_counts["web_search"],
            subagents=item_counts["subagent"] + sum(1 for t in threads.values() if t["child"] and in_range(t["created"])),
            compactions=item_counts["compaction"],
            interrupts=item_counts["run_interrupt_request"],
            errors=item_counts["error"],
            questions_asked=item_counts["user_input_request"],
            top_commands=top_list(verbs, 6),
            top_tools=top_list(tool_counts, 6),
        ),
        "receipts": dict(
            edit_runs=len(edit_runs),
            verified=len(verified_edit_runs),
            rate=round(receipts, 3) if receipts is not None else None,
            blind_ships=len(edit_runs) - len(verified_edit_runs),
        ),
        "apologies": dict(
            total=sum(apologies.values()),
            by_model=dict(sorted(apology_rates.items(), key=lambda kv: -kv[1]["per_100"])),
        ),
        "you": dict(
            median_prompt_chars=round(median_len),
            longest_prompt_chars=max(lengths) if lengths else 0,
            short_prompts=sum(1 for n in lengths if n <= 12),
            caps_prompts=caps,
            phrases=phrase_counts,
            top_words=[] if args.no_words else top_list(words, 12),
            sources=dict(sources),
            full_access_share=round(yolo_share, 3),
            one_and_done_threads=one_and_done,
        ),
        "records": dict(
            longest_thread=dict(prompts=thread_runs[top_threads[0]]) if top_threads else None,
            longest_turn=dict(minutes=round(longest_turn["seconds"] / 60), model=longest_turn["model"]) if longest_turn else None,
            biggest_diff=dict(lines=biggest_run[1]) if biggest_run[0] else None,
            busiest_turn=dict(actions=busiest_run[1]) if busiest_run[0] else None,
            most_interrupted=dict(times=most_interrupted[1]) if most_interrupted[0] else None,
        ),
        "persona": dict(
            key=persona_key,
            name=ARCHETYPES[persona_key][0],
            blurb=ARCHETYPES[persona_key][1],
            runners_up=[ARCHETYPES[k][0] for k in ranked[1:3]],
            scores={k: round(v, 2) for k, v in scores.items()},
        ),
        "origin": dict(
            first_day=first_native[0].date().isoformat() if first_native else None,
            tracked=dict(threads=len(native_threads), prompts=n_native),
            imported=dict(
                threads=len(imported["threads"]),
                prompts=imported["prompts"],
                first=imported["first"].date().isoformat() if imported["first"] else None,
                last=imported["last"].date().isoformat() if imported["last"] else None,
                providers=dict(Counter(threads[t]["provider"] for t in imported["threads"])),
                included=args.include_imported,
            ),
        ),
        "warnings": warnings,
    }
    Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).expanduser().write_text(json.dumps(out, indent=2))
    t = out["totals"]
    print(f"Wrote {args.out}: {t['prompts']} prompts, {t['threads']} threads, {t['projects']} projects, "
          f"{t['agent_hours']} agent-hours, persona {out['persona']['name']}")


if __name__ == "__main__":
    main()
