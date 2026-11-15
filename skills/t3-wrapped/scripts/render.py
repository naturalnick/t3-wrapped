#!/usr/bin/env python3
"""Render wrapped.json into a deck of share cards (deck.html + one PNG per card).

Numbers are always drawn as text by this script, never by an image model, so they stay
exact. Optional inputs in the output folder:
  captions.json   {"<card id>": {"kicker": ..., "title": ..., "note": ...}} overrides copy
  art/<card id>.png  a textless background image for that card (e.g. art/cover.png)
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

W, H = 1080, 1350

ACCENTS = ["#c6f432", "#ff4f9a", "#4d8bff", "#ff7a2f", "#9d7bff", "#19d3a2", "#ffd23f"]
# Dark-surface categorical order from the dataviz reference palette (validated for CVD on dark).
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"]
OTHER = "#6b6a66"
HEAT = ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#b7d3f6"]

ORDER = ["cover", "origin", "prompts", "calendar", "clock", "agent_time", "models", "model_timeline", "projects", "code",
         "hands", "apologies", "words", "habits", "records", "persona", "summary"]

e = html.escape


def n(x):
    return f"{int(round(x)):,}"


def compact(x):
    x = float(x)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            v = x / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suf
    return n(x)


def pct(x):
    return "<1%" if 0 < x < 0.005 else f"{round(100 * x)}%"


def model_name(m):
    if not m or m == "unknown":
        return "Unknown"
    m = re.sub(r"-\d{8}$|-latest$", "", m.split("/")[-1])
    parts = m.split("-")
    if parts[0] == "claude":
        parts = parts[1:]
    if parts and parts[0] == "gpt":
        return " ".join(["GPT-" + parts[1]] + [p.capitalize() for p in parts[2:]]) if len(parts) > 1 else "GPT"
    words, nums = [], []
    for p in parts:
        if re.fullmatch(r"\d+(\.\d+)?", p):
            nums.append(p)
        else:
            if nums:
                words.append(".".join(nums))
                nums = []
            words.append(p.upper() if len(p) <= 2 else p.capitalize())
    if nums:
        words.append(".".join(nums))
    return " ".join(words)


def provider_name(p):
    return {"claudeAgent": "Claude", "codex": "Codex", "cursor": "Cursor", "opencode": "OpenCode",
            "gemini": "Gemini"}.get(p, (p or "Unknown")[:1].upper() + (p or "unknown")[1:])


def month_label(m):
    return "JFMAMJJASOND"[m - 1]


def nice_date(iso, long=False):
    d = date.fromisoformat(iso[:10])
    return d.strftime("%B %-d" if long else "%b %-d")


def weekly(d):
    """Use weeks when the history spans under ~4 months, so a late starter still gets a real chart."""
    return 2 <= len(d["calendar"].get("per_week", [])) <= 17



# ---------------------------------------------------------------------------
# building blocks


def bars(items, color=None, colors=None, fmt=n, max_items=6):
    items = items[:max_items]
    if not items:
        return ""
    top = max(i["value"] for i in items) or 1
    rows = []
    for idx, it in enumerate(items):
        c = (colors or {}).get(it["name"], color or SERIES[idx % len(SERIES)])
        w = max(1.5, 100 * it["value"] / top)
        rows.append(
            f'<div class="bar"><div class="bar-head"><span>{e(str(it.get("label", it["name"])))}</span>'
            f'<span class="num">{e(fmt(it["value"]))}</span></div>'
            f'<div class="track"><div class="fill" style="width:{w:.1f}%;background:{c}"></div></div></div>'
        )
    return '<div class="bars">' + "".join(rows) + "</div>"


def stat_row(pairs):
    cells = "".join(f'<div class="stat"><div class="stat-v num">{e(v)}</div><div class="stat-k">{e(k)}</div></div>'
                    for k, v in pairs if v not in (None, ""))
    return f'<div class="stats">{cells}</div>'


def model_colors(d):
    return {m["name"]: SERIES[i] for i, m in enumerate(d["models"]["by_model"][: len(SERIES) - 1])}


def blob(accent, variant):
    """Big off-canvas shape so every card has the same poster energy without an image model."""
    # Confined to the top-right corner (x > 850, y < 250); headlines are capped narrower than that.
    shapes = [
        f'<circle cx="1010" cy="70" r="170" fill="{accent}"/>',
        f'<rect x="880" y="-110" width="300" height="300" rx="80" transform="rotate(20 1030 40)" fill="{accent}"/>',
        f'<path d="M1080 0 L1080 250 Q900 230 850 0 Z" fill="{accent}"/>',
        f'<circle cx="1040" cy="40" r="150" fill="{accent}"/>',
        f'<g fill="{accent}">' + "".join(f'<rect x="{872 + i * 42}" y="-30" width="26" height="{270 - i * 38}" rx="13"/>' for i in range(5)) + "</g>",
    ]
    return f'<svg class="blob" viewBox="0 0 {W} {H}" aria-hidden="true">{shapes[variant % len(shapes)]}</svg>'


# ---------------------------------------------------------------------------
# cards — each returns (default copy, body html) or None to skip


def dial(d, cx, cy, inner, step, color=None):
    """A 24-hour clock of the user's week: one ring per weekday (Mon innermost), one spoke per hour."""
    heat = d["calendar"]["heatmap"]
    peak = max(max(r) for r in heat) or 1
    dots = []
    for day in range(7):
        r = inner + day * step
        # Cap dot size by the room this ring has, both around (24 slots) and outward (ring step).
        room = min(step, 2 * math.pi * r / 24) / 2 - 3
        for hour in range(24):
            v = heat[day][hour]
            if not v:
                continue
            a = hour / 24 * 2 * math.pi - math.pi / 2
            size = 4 + (room - 4) * math.sqrt(v / peak)
            dots.append(f'<circle cx="{cx + r * math.cos(a):.1f}" cy="{cy + r * math.sin(a):.1f}" r="{size:.1f}" '
                        f'fill="{color or ACCENTS[day % len(ACCENTS)]}"/>')
    return f'<svg class="art" viewBox="0 0 {W} {H}" aria-hidden="true">{"".join(dots)}</svg>'


def card_cover(d, ctx):
    art = dial(d, 540, 480, 140, 32)
    copy = dict(kicker="T3 Code", title="Wrapped", note="")
    body = (f'{art}<div class="cover-body"><div class="cover-brand">{{kicker}}</div><div class="cover-title">{{title}}</div>'
            f'<div class="cover-year num">{d["year"]}</div><div class="note">{{note}}</div></div>')
    return copy, body, "raw"


# Join-date titles. T3 Code's repo opened Feb 2026 and it launched publicly in March 2026.
JOIN_TITLES = [  # (joined before, title)
    ("2026-04-01", "Launch Crew"),  # includes pre-launch users
    ("2026-06-01", "Early Adopter"),
    ("2026-09-01", "Midyear Recruit"),
    ("2026-11-01", "Fashionably Late"),
    ("2027-01-01", "Just in Time"),
]


def join_title(first_day):
    return next((t for cutoff, t in JOIN_TITLES if first_day < cutoff), None)


def card_origin(d, ctx):
    o = d.get("origin") or {}
    if not o.get("first_day"):
        return None
    imp = o["imported"]
    started = date.fromisoformat(o["first_day"])
    year = "" if started.year == d["year"] else f" in {started.year}"
    fine = ""
    if imp["threads"]:
        apps = " & ".join(provider_name(k) for k, _ in sorted(imp["providers"].items(), key=lambda kv: -kv[1]))
        fine = (f"Includes {n(imp['threads'])} threads imported from {apps}" if imp["included"]
                else f"{n(imp['threads'])} threads imported from {apps} aren't tracked")
    copy = dict(kicker="Day one", title=f"downloaded T3 Code{year}", note="")
    tier = join_title(o["first_day"])
    return copy, (f'<div class="hero num">{nice_date(o["first_day"])}</div>'
                  + (f'<div class="tier">{e(tier)}</div>' if tier else "")
                  + (f'<div class="fine">{e(fine)}</div>' if fine else ""))


def card_prompts(d, ctx):
    t = d["totals"]
    copy = dict(kicker="You sent", title="prompts",
                note=f"across {n(t['threads'])} threads")
    body = (f'<div class="hero num">{n(t["prompts"])}</div>'
            + stat_row([("days active", n(t["active_days"])), ("longest streak", f"{t['longest_streak']}d"),
                        ("per active day", n(t["prompts"] / max(t["active_days"], 1)))]))
    return copy, body


def card_calendar(d, ctx):
    if weekly(d):
        wk = d["calendar"]["per_week"]
        top = max(w["prompts"] for w in wk) or 1
        cols = []
        for i, w in enumerate(wk):
            v = w["prompts"]
            h = 0 if not v else max(6, 270 * v / top)
            color = ctx["accent"] if v == top else "#3a3a37"
            label = nice_date(w["start"]) if len(wk) <= 8 or i % 2 == 0 else ""
            cols.append(f'<div class="col"><div class="col-v num">{compact(v) if v else ""}</div>'
                        f'<div class="col-bar" style="height:{h:.0f}px;background:{color}"></div>'
                        f'<div class="col-k wk">{label}</div></div>')
        best = d["calendar"]["busiest_day"]
        big = max(wk, key=lambda w: w["prompts"])
        copy = dict(kicker="Prompts per week", title="your biggest week",
                    note=f"{n(big['prompts'])} prompts that week · busiest day {nice_date(best['date'])} ({n(best['prompts'])})" if best else "")
        return copy, f'<div class="hero num">{nice_date(big["start"])}</div><div class="columns">{"".join(cols)}</div>'
    pm = d["calendar"]["per_month"]
    months = [i for i, v in enumerate(pm) if v]
    if len(months) < 2:
        return None
    lo, hi = months[0], 11
    top = max(pm) or 1
    cols = []
    for i in range(lo, hi + 1):
        v = pm[i]
        h = 0 if not v else max(6, 270 * v / top)
        color = ctx["accent"] if v == top else "#3a3a37"
        cols.append(f'<div class="col"><div class="col-v num">{compact(v) if v else ""}</div>'
                    f'<div class="col-bar" style="height:{h:.0f}px;background:{color}"></div>'
                    f'<div class="col-k">{month_label(i + 1)}</div></div>')
    best = d["calendar"]["busiest_day"]
    big_month = date(d["year"], pm.index(top) + 1, 1).strftime("%B")
    copy = dict(kicker="Prompts per month", title="your biggest month",
                note=f"{n(top)} prompts that month · busiest day {nice_date(best['date'])} ({n(best['prompts'])})" if best else "")
    return copy, f'<div class="hero num">{big_month}</div><div class="columns">{"".join(cols)}</div>'


def card_clock(d, ctx):
    heat = d["calendar"]["heatmap"]
    peak = max(max(r) for r in heat) or 1
    cells = []
    for day in range(7):
        cells.append(f'<div class="hm-day" style="grid-row:{day + 2};grid-column:1">{"MTWTFSS"[day]}</div>')
        for hour in range(24):
            v = heat[day][hour]
            c = "#1f1f1d" if not v else HEAT[min(len(HEAT) - 1, int(math.sqrt(v / peak) * (len(HEAT) - 1) + 0.5))]
            cells.append(f'<div class="hm-cell" style="grid-row:{day + 2};grid-column:{hour + 2};background:{c}"></div>')
    hours = "".join(f'<div class="hm-hour" style="grid-row:1;grid-column:{h + 2} / span 4">{lbl}</div>'
                    for h, lbl in ((0, "12a"), (6, "6a"), (12, "12p"), (18, "6p")))
    cal = d["calendar"]
    # Pick the most characterful headline from what the data supports.
    best_day, best_hour = max(((dy, hr) for dy in range(7) for hr in range(24)), key=lambda k: heat[k[0]][k[1]])
    dayname = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][best_day]
    hr12 = f"{(best_hour % 12) or 12}{'am' if best_hour < 12 else 'pm'}"
    title = "your peak hour"
    note = (f"Latest night: {cal['latest_night']} on {nice_date(cal['latest_night_date'])}" if cal.get("latest_night")
            else f"{pct(cal['weekend_share'])} of prompts on weekends")
    copy = dict(kicker="When you work", title=title, note=note)
    body = f'<div class="hero num">{dayname} {hr12}</div><div class="panel"><div class="heatmap">{hours}{"".join(cells)}</div></div>'
    return copy, body


def card_agent_time(d, ctx):
    t, tok = d["totals"], d["tokens"]
    if not t["agent_hours"]:
        return None
    pairs = [("peak agents at once", n(t["peak_parallel"])), ("tokens", compact(tok["total"]) if tok["total"] else None)]
    if tok.get("api_cost_usd"):
        pairs.append(("at API prices", f"${compact(tok['api_cost_usd'])}"))
    copy = dict(kicker="Your agents worked", title="hours",
                note=f"Token counts since {nice_date(tok['since'])}" if tok.get("since") and tok["since"] > d["range"]["first"] else "")
    return copy, f'<div class="hero num">{n(t["agent_hours"])}</div>' + stat_row(pairs)


def card_models(d, ctx):
    ms = d["models"]["by_model"]
    if not ms:
        return None
    total = sum(m["value"] for m in ms)
    shown = ms[:5]
    rest = total - sum(m["value"] for m in shown)
    items = [dict(name=m["name"], label=model_name(m["name"]), value=m["value"] / total) for m in shown]
    if rest > 0:
        items.append(dict(name="__other", label="Other", value=rest / total))
    colors = dict(model_colors(d), __other=OTHER)
    prov = d["models"]["by_provider"]
    ptotal = sum(p["value"] for p in prov) or 1
    split = " · ".join(f"{provider_name(p['name'])} {pct(p['value'] / ptotal)}" for p in prov[:4] if p["value"] / ptotal >= 0.01)
    copy = dict(kicker="Your top model", title="", note=split)
    return copy, f'<div class="hero num">{e(model_name(ms[0]["name"]))}</div><div class="panel">{bars(items, colors=colors, fmt=pct, max_items=6)}</div>'


def card_model_timeline(d, ctx):
    by_week = weekly(d) and d["models"].get("by_week")
    bm = ([dict(label=nice_date(w["start"]), models=w["models"]) for w in d["models"]["by_week"]] if by_week
          else [dict(label=month_label(m["month"]), models=m["models"]) for m in d["models"]["by_month"]])
    if len(bm) < 2 or len(d["models"]["by_model"]) < 2:
        return None
    colors = model_colors(d)
    cols = []
    for m in bm:
        tot = sum(m["models"].values()) or 1
        segs = sorted(m["models"].items(), key=lambda kv: list(colors).index(kv[0]) if kv[0] in colors else 99)
        seg_html = "".join(f'<div class="seg" style="flex:{v / tot:.4f};background:{colors.get(k, OTHER)}"></div>' for k, v in segs)
        cols.append(f'<div class="col"><div class="stack">{seg_html if m["models"] else ""}</div><div class="col-k{" wk" if by_week else ""}">{m["label"]}</div></div>')
    used = {k for m in bm for k in m["models"]}
    legend = "".join(f'<span class="lg"><i style="background:{c}"></i>{e(model_name(k))}</span>' for k, c in colors.items() if k in used)
    if any(k not in colors for k in used):
        legend += f'<span class="lg"><i style="background:{OTHER}"></i>Other</span>'
    ms = d["models"]["by_model"]
    loyal = ms[0]["value"] / sum(x["value"] for x in ms) > 0.8
    unit = "weeks" if by_week else "months"
    copy = dict(kicker=f"Model mix by {unit[:-1]}", title="You stayed loyal" if loyal else "You kept switching",
                note=f"{len(ms)} models over {len(bm)} {unit}")
    return copy, f'<div class="panel"><div class="columns stacked">{"".join(cols)}</div><div class="legend">{legend}</div></div>'


def card_projects(d, ctx):
    ps = [p["prompts"] for p in d["projects"]]
    if len(ps) < 2:
        return None
    total = sum(ps)
    top_n = 3 if len(ps) >= 5 else 1
    share = sum(ps[:top_n]) / total
    lead = "Your top 3 projects got" if top_n == 3 else "Your top project got"
    copy = dict(kicker="You worked on", title="projects", note=f"{lead} {pct(share)} of your prompts")
    shown = ps[:24]
    cols = []
    for i, v in enumerate(shown):
        top = i < top_n
        cols.append(f'<div class="col"><div class="col-v num">{pct(v / total) if top else ""}</div>'
                    f'<div class="col-bar" style="height:{max(6, 300 * v / ps[0]):.0f}px;'
                    f'background:{ctx["accent"] if top else "#3a3a37"}"></div></div>')
    gap = 12 if len(shown) <= 12 else 6
    return copy, (f'<div class="hero num">{n(len(ps))}</div>'
                  f'<div class="columns" style="gap:{gap}px">{"".join(cols)}</div>')


def card_code(d, ctx):
    c = d["code"]
    if not c["lines_added"]:
        return None
    copy = dict(kicker="Your agents wrote", title="lines of code",
                note=f"{n(c['lines_removed'])} deleted · {n(c['files_touched'])} files touched")
    langs = [dict(name=l["name"], value=l["value"]) for l in c["languages"][:5]]
    body = f'<div class="hero num">+{compact(c["lines_added"])}</div>'
    if langs:
        body += f'<div class="panel">{bars(langs, color=ctx["accent"], fmt=lambda v: compact(v) + " lines")}</div>'
    return copy, body


def card_hands(d, ctx):
    a = d["agent"]
    if not a["commands"] and not a["tool_calls"]:
        return None
    copy = dict(kicker="Your agents ran", title="commands", note="")
    body = (f'<div class="hero num">{compact(a["commands"])}</div>'
            + stat_row([("tool calls", compact(a["tool_calls"])), ("web searches", compact(a["web_searches"])),
                        ("subagents", compact(a["subagents"]))])
            + (f'<div class="panel">{bars(a["top_commands"][:3], color=ctx["accent"], fmt=compact)}</div>' if a["top_commands"] else ""))
    return copy, body


def card_apologies(d, ctx):
    ap = d["apologies"]
    if ap["total"] < 3:
        return None
    rows = [dict(name=k, label=model_name(k), value=v["per_100"]) for k, v in ap["by_model"].items()][:4]
    colors = model_colors(d)
    body = f'<div class="hero num">{n(ap["total"])}</div><div class="sub">times your agent said "you\'re right", "good catch" or "my mistake"</div>'
    if len(rows) >= 2:
        body += f'<div class="panel"><div class="panel-k">Per 100 replies</div>{bars(rows, colors=colors, fmt=lambda v: f"{v:g}")}</div>'
    top = rows[0]["label"] if rows and rows[0]["value"] > 0 else None
    copy = dict(kicker="You're absolutely right", title="", note=f"Most apologetic: {top}" if top else "")
    return copy, body


def card_words(d, ctx):
    words = d["you"]["top_words"][:10]
    ph = d["you"]["phrases"]
    if not words:
        return None
    sizes = [168, 132, 112, 96, 84, 76, 68, 62, 56, 52]
    cloud = "".join(f'<span style="font-size:{sizes[i]}px;color:{ACCENTS[i % len(ACCENTS)] if i < 3 else "#f0efec"}">{e(w["name"])}</span>'
                    for i, w in enumerate(words))
    copy = dict(kicker="Your words", title="", note="")
    body = (f'<div class="cloud">{cloud}</div>'
            + stat_row([('"please"', n(ph.get("please", 0))), ('"actually"', n(ph.get("actually", 0))),
                        ('"still broken"', n(ph.get("still_broken", 0)))]))
    return copy, body


def card_habits(d, ctx):
    y, a = d["you"], d["agent"]
    tiles = [
        (pct(y["full_access_share"]), "of threads in full-access mode"),
        (n(a["interrupts"]), "times you hit stop mid‑turn"),
        (n(a["compactions"]), "times your agent's memory got compacted"),
        (n(y["short_prompts"]), "prompts under 12 characters"),
    ]
    if y["sources"].get("mobile"):
        tiles.append((n(y["sources"]["mobile"]), "prompts sent from your phone"))
    if y["one_and_done_threads"]:
        tiles.append((n(y["one_and_done_threads"]), "one-prompt threads"))
    grid = "".join(f'<div class="tile"><div class="tile-v num">{e(v)}</div><div class="tile-k">{e(k)}</div></div>' for v, k in tiles[:6])
    copy = dict(kicker="Habits", title="The fine print", note="")
    return copy, f'<div class="tiles">{grid}</div>'


def card_records(d, ctx):
    r = d["records"]
    rows = []
    if r.get("longest_thread"):
        rows.append(("Longest thread", f"{n(r['longest_thread']['prompts'])} prompts"))
    if r.get("longest_turn") and r["longest_turn"]["minutes"] >= 5:
        rows.append(("Longest single turn", f"{n(r['longest_turn']['minutes'])} min"))
    if r.get("biggest_diff"):
        rows.append(("Biggest diff", f"{n(r['biggest_diff']['lines'])} lines"))
    if r.get("busiest_turn"):
        rows.append(("Busiest turn", f"{n(r['busiest_turn']['actions'])} actions"))
    if r.get("most_interrupted") and r["most_interrupted"]["times"] >= 3:
        rows.append(("Most interrupted", f"{n(r['most_interrupted']['times'])} stops"))
    if len(rows) < 2:
        return None
    items = "".join(f'<div class="rec"><div class="rec-k">{e(k)}</div><div class="rec-v num">{e(v)}</div></div>'
                    for k, v in rows[:5])
    return dict(kicker="Records", title="Personal bests", note=""), f'<div class="recs">{items}</div>'


def card_persona(d, ctx):
    p = d["persona"]
    copy = dict(kicker="Your coding-agent personality", title=p["name"], note=p["blurb"])
    runners = " · ".join(p["runners_up"])
    art = dial(d, 540, 440, 110, 28, ctx["accent"])
    return copy, art + (f'<div class="persona-runners">Also: {e(runners)}</div>' if runners else "")


def card_summary(d, ctx):
    t, c, tok = d["totals"], d["code"], d["tokens"]
    tiles = [
        (n(t["prompts"]), "prompts"),
        (n(t["agent_hours"]), "agent hours"),
        (model_name(d["models"]["by_model"][0]["name"]), "top model"),
        (n(t["projects"]), "projects"),
        ("+" + compact(c["lines_added"]), "lines"),
        (compact(tok["total"]) if tok["total"] else n(t["active_days"]), "tokens" if tok["total"] else "days active"),
    ]
    grid = "".join(f'<div class="tile"><div class="tile-v num fit">{e(v)}</div><div class="tile-k">{e(k)}</div></div>' for v, k in tiles)
    copy = dict(kicker=f"{d['year']} in T3 Code", title=d["persona"]["name"], note="")
    return copy, f'<div class="tiles">{grid}</div>'


CARDS = {k: globals()["card_" + k] for k in ORDER}


# ---------------------------------------------------------------------------

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--panel:#1a1a19;--track:#2c2c2a;
  --sans:-apple-system,"SF Pro Display","Segoe UI Variable Display","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
html,body{background:#0d0d0d;color:var(--ink);font-family:var(--sans)}
body.browse{padding:32px 16px;display:flex;flex-wrap:wrap;gap:24px;justify-content:center}
body.browse .card{zoom:.36;border-radius:40px}
body.single{width:1080px;height:1350px;overflow:hidden}
body.single .card:not(.on){display:none}
.card{position:relative;width:1080px;height:1350px;overflow:hidden;background:#0d0d0d;padding:150px 72px 150px;display:flex;flex-direction:column;gap:36px}
.blob,.art{position:absolute;inset:0;width:100%;height:100%;z-index:0}
.bg{position:absolute;inset:0;background-size:cover;background-position:center;z-index:0}
.bg::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(13,13,13,.25),rgba(13,13,13,.85) 70%)}
.card>*{position:relative;z-index:1}
.card>.blob,.card>.art,.card>.bg{position:absolute;z-index:0}
.card>.foot,.card>.cover-body{position:absolute}
.num{font-variant-numeric:tabular-nums;letter-spacing:-.03em}
.kicker{font-size:32px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--ink2)}
.title{font-size:96px;font-weight:850;line-height:1;letter-spacing:-.035em;max-width:800px;text-wrap:balance}
.note{font-size:40px;line-height:1.3;color:var(--ink2);max-width:880px;text-wrap:pretty}
.hero{font-size:190px;font-weight:900;line-height:.92;letter-spacing:-.05em;white-space:nowrap;max-width:780px}
.sub{font-size:36px;color:var(--ink2);margin-top:-12px;max-width:760px}
.spacer{flex:1}
.foot-r{display:flex;align-items:center;gap:20px}
.next{font-size:56px;line-height:1;color:var(--ink);font-weight:500}
.foot{position:absolute;left:72px;right:72px;bottom:48px;display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:600;color:var(--muted);z-index:1}
.panel{background:var(--panel);border-radius:32px;padding:40px}
.panel-k{font-size:30px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:24px}
.bars{display:flex;flex-direction:column;gap:26px}
.bar-head{display:flex;justify-content:space-between;font-size:38px;font-weight:650;margin-bottom:10px;gap:24px}
.bar-head span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-head .num{color:var(--ink2);font-weight:600}
.track{height:16px;background:var(--track);border-radius:8px;overflow:hidden}
.fill{height:100%;border-radius:8px}
.stats{display:flex;gap:56px}
.stat-v{font-size:96px;font-weight:850;line-height:1}
.stat-k{font-size:34px;color:var(--muted);margin-top:8px;font-weight:600}
.columns{display:flex;align-items:flex-end;gap:12px;height:380px}
.col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;gap:12px}
.col-bar{width:100%;border-radius:10px 10px 4px 4px}
.col-v{font-size:38px;color:var(--ink2);font-weight:650}
.col-k{font-size:34px;color:var(--muted);font-weight:700}
.columns.stacked{height:440px}
.stack{flex:1;width:100%;display:flex;flex-direction:column-reverse;gap:2px;border-radius:10px;overflow:hidden}
.seg{min-height:2px}
.legend{display:flex;flex-wrap:wrap;gap:14px 28px;margin-top:32px;font-size:34px;color:var(--ink2);font-weight:600}
.lg{display:flex;align-items:center;gap:10px}.lg i{width:20px;height:20px;border-radius:6px;display:inline-block}
.heatmap{display:grid;grid-template-columns:44px repeat(24,1fr);gap:5px;align-items:center}
.hm-cell{aspect-ratio:1;border-radius:6px}
.hm-day{font-size:32px;color:var(--muted);font-weight:700}
.hm-hour{font-size:32px;color:var(--muted);font-weight:700;grid-row:1}
.cloud{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px 36px;font-weight:850;letter-spacing:-.03em;line-height:1.05;flex:1;align-content:center}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.tile{background:var(--panel);border-radius:28px;padding:26px 32px;min-height:200px;display:flex;flex-direction:column;justify-content:space-between;gap:8px;overflow:hidden}
.tile-v{font-size:104px;font-weight:900;line-height:1;white-space:nowrap}
.tile-k{font-size:34px;color:var(--ink2);font-weight:600;line-height:1.25}
.recs{display:flex;flex-direction:column;gap:20px}
.rec{background:var(--panel);border-radius:28px;padding:26px 32px;display:flex;justify-content:space-between;align-items:center;gap:24px}
.rec-k{font-size:32px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.08em}
.rec-v{font-size:56px;font-weight:900}
.cover-body{left:72px;right:72px;bottom:150px}
.cover-title{font-size:140px;font-weight:900;letter-spacing:-.05em;line-height:.9}
.cover-year{font-size:140px;font-weight:900;line-height:.95;color:var(--accent)}
.cover-body .note{margin-top:20px}
.cover-body .note:empty{display:none}
.cover-brand{font-size:96px;font-weight:850;letter-spacing:-.03em;line-height:1;color:var(--ink2);margin-bottom:8px}
.col-k.wk{font-size:30px;white-space:nowrap}
.tier{font-size:104px;font-weight:900;line-height:.95;letter-spacing:-.045em;color:var(--accent);max-width:900px}
.fine{font-size:36px;color:var(--muted);font-weight:600}
.persona-runners{font-size:36px;color:var(--muted);font-weight:600}
.card.persona .title{font-size:110px}
"""

FIT_JS = """
for (const el of document.querySelectorAll('.fit, .tile-v, .title, .hero, .tier')) {
  let size = parseFloat(getComputedStyle(el).fontSize);
  const own = el.matches('.title, .hero, .tier');
  const limit = () => own ? el.clientWidth : el.parentElement.clientWidth - (el.classList.contains('tile-v') ? 64 : 0);
  while (el.scrollWidth > limit() && size > 28) {
    size -= 4; el.style.fontSize = size + 'px';
  }
}
const want = new URLSearchParams(location.search).get('card');
if (want) {
  document.body.className = 'single';
  for (const c of document.querySelectorAll('.card')) c.classList.toggle('on', c.id === want);
}
"""


def render_card(cid, d, idx, total, captions, art_dir, handle, accent):
    result = CARDS[cid](d, dict(accent=accent))
    if result is None:
        return None
    copy, body, *mode = result
    copy = {**copy, **{k: v for k, v in captions.get(cid, {}).items() if v is not None}}
    art = art_dir / f"{cid}.png" if art_dir else None
    bg = (f'<div class="bg" style="background-image:url(\'{art.resolve().as_uri()}\')"></div>' if art and art.exists()
          else blob(accent, idx) if cid not in ("cover", "persona") else "")
    # A swipe cue instead of page numbers; the last card has nowhere to swipe.
    arrow = '<span class="next" aria-label="Next">→</span>' if idx < total - 1 else ""
    foot = (f'<div class="foot"><span>{"" if cid == "cover" else f"T3 Code Wrapped {d['year']}"}</span>'
            f'<span class="foot-r">{e(handle or "")}{arrow}</span></div>')
    if mode and mode[0] == "raw":
        inner = body.format(**{k: e(v) for k, v in copy.items()})
    else:
        head = "".join(f'<div class="{k}">{e(copy[k])}</div>' for k in ("kicker", "title") if copy.get(k))
        tail = f'<div class="note">{e(copy["note"])}</div>' if copy.get("note") else ""
        if cid == "persona":
            art, _, rest = body.partition("</svg>")
            inner = f'{art}</svg><div class="spacer"></div>{head}{tail}{rest}'
        elif body.startswith('<div class="hero'):
            # Hero number reads as the subject; its unit ("prompts", "hours") sits under it.
            hero, _, rest = body.partition("</div>")
            title = f'<div class="title">{e(copy["title"])}</div>' if copy.get("title") else ""
            sub, rest = (rest.partition("</div>")[0] + "</div>", rest.partition("</div>")[2]) if rest.startswith('<div class="sub') else ("", rest)
            inner = f'<div class="kicker">{e(copy.get("kicker", ""))}</div>{hero}</div>{title}{sub}{tail}<div class="spacer"></div>{rest}'
        else:
            inner = f'{head}{tail}<div class="spacer"></div>{body}'
    return f'<section class="card {cid}" id="{cid}" style="--accent:{accent}">{bg}{inner}{foot}</section>'


def find_chrome():
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]:
        if Path(p).exists():
            return p
    for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]:
        if shutil.which(name):
            return shutil.which(name)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="Folder containing wrapped.json; output goes here too")
    ap.add_argument("--cards", help="Comma-separated card ids, in order (default: all with data)")
    ap.add_argument("--max", type=int, default=20, help="Maximum number of cards")
    ap.add_argument("--handle", help="Shown in the footer, e.g. @you")
    ap.add_argument("--scale", type=float, default=2, help="PNG pixel density (2 = 2160x2700)")
    ap.add_argument("--html-only", action="store_true")
    args = ap.parse_args()

    root = Path(args.dir).expanduser()
    d = json.loads((root / "wrapped.json").read_text())
    captions = json.loads((root / "captions.json").read_text()) if (root / "captions.json").exists() else {}
    ids = [c.strip() for c in args.cards.split(",")] if args.cards else ORDER
    unknown = [c for c in ids if c not in CARDS]
    if unknown:
        sys.exit(f"Unknown card ids: {', '.join(unknown)}. Known: {', '.join(ORDER)}")

    # Drop cards without data first so footers number only what ships.
    ids = [c for c in ids if CARDS[c](d, dict(accent=ACCENTS[0])) is not None][: args.max]
    cards = [render_card(c, d, i, len(ids), captions, root / "art", args.handle, ACCENTS[i % len(ACCENTS)])
             for i, c in enumerate(ids)]
    page = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>T3 Code Wrapped {d["year"]}</title><style>{CSS}</style></head><body class="browse">'
            f'{"".join(cards)}<script>{FIT_JS}</script></body></html>')
    deck = root / "deck.html"
    deck.write_text(page)
    print(f"Wrote {deck} ({len(ids)} cards: {', '.join(ids)})")
    if args.html_only:
        return

    chrome = find_chrome()
    if not chrome:
        sys.exit("No Chrome/Chromium/Edge/Brave found for PNG export. Set CHROME=/path/to/browser or open deck.html and screenshot each card at 1080x1350.")
    out = root / "cards"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    # A throwaway profile keeps headless runs from touching the user's real browser session.
    with tempfile.TemporaryDirectory() as profile:
        for i, cid in enumerate(ids):
            png = out / f"{i + 1:02d}-{cid}.png"
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                            f"--user-data-dir={profile}", f"--force-device-scale-factor={args.scale}",
                            f"--window-size={W},{H}", "--virtual-time-budget=1500", f"--screenshot={png}",
                            f"{deck.resolve().as_uri()}?card={cid}"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            print(png)


if __name__ == "__main__":
    main()
