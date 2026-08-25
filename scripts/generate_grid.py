#!/usr/bin/env python3
"""
Fetch real GitHub contribution + activity data from fully PUBLIC github.com
pages (contribution calendar page + user Atom feed) and render
assets/contribution.svg (arcade score bar + ship shooting animation + real
contribution grid + last active repo + recent commits).

No token/PAT is required at all. GH_TOKEN is accepted but purely optional
(only used, if ever re-added, to raise api.github.com rate limits).

Run: GH_USERNAME=hanscakrawangsa15 python3 scripts/generate_grid.py
"""

import os
import sys
import random
import json
import html
import urllib.request

GH_TOKEN = os.environ.get("GH_TOKEN")  # optional now — only raises API rate limits if provided
GH_USERNAME = os.environ.get("GH_USERNAME", "hanscakrawangsa15")

def _headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": GH_USERNAME}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return h


import re

def fetch_contributions():
    """Read the PUBLIC contribution calendar page (no auth needed at all —
    same technique used by the well-known 'snake' contribution action).
    Returns a list of weeks, each a list of 7 day-dicts compatible with the
    rest of this script (color/contributionCount/weekday/date)."""
    url = f"https://github.com/users/{GH_USERNAME}/contributions"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode()

    total_match = re.search(r'(\d[\d,]*)\s*\n\s*contributions', html)
    total = int(total_match.group(1).replace(",", "")) if total_match else 0

    cell_re = re.compile(
        r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*data-level="(\d)"'
    )
    cells = cell_re.findall(html)
    if not cells:
        # fallback: attribute order can vary, try the reverse order too
        cell_re2 = re.compile(
            r'data-level="(\d)"[^>]*data-date="(\d{4}-\d{2}-\d{2})"'
        )
        cells = [(d, l) for l, d in cell_re2.findall(html)]

    import datetime
    days_by_date = {}
    for date_str, level_str in cells:
        days_by_date[date_str] = int(level_str)

    if not days_by_date:
        print("ERROR: could not parse contribution calendar HTML.", file=sys.stderr)
        sys.exit(1)

    dates_sorted = sorted(days_by_date.keys())
    first_date = datetime.date.fromisoformat(dates_sorted[0])
    # align to the Sunday that starts that first date's week
    start = first_date - datetime.timedelta(days=(first_date.weekday() + 1) % 7)

    last_date = datetime.date.fromisoformat(dates_sorted[-1])
    total_days = (last_date - start).days + 1
    total_weeks = (total_days // 7) + 1

    PALETTE_HEX_BY_LEVEL = {0: "#161b22", 1: "#0e4429", 2: "#006d32", 3: "#26a641", 4: "#39d353"}

    weeks = []
    cur = start
    for w in range(total_weeks):
        days = []
        for d in range(7):
            date_str = cur.isoformat()
            level = days_by_date.get(date_str, 0)
            days.append({
                "color": PALETTE_HEX_BY_LEVEL[level],
                "contributionCount": level,  # approximate; exact count not exposed publicly
                "weekday": d,
                "date": date_str,
            })
            cur += datetime.timedelta(days=1)
        weeks.append({"contributionDays": days})

    return weeks, total


# GitHub's own 5-level color palette (light theme values); we re-map to our arcade palette
PALETTE = ["#0e1622", "#173226", "#1f6f45", "#2ee6a6", "#8fffcf"]
GH_LEVELS = {
    "#ebedf0": 0, "#9be9a8": 1, "#40c463": 2, "#30a14e": 3, "#216e39": 4,
    "#161b22": 0, "#0e4429": 1, "#006d32": 2, "#26a641": 3, "#39d353": 4,
}

def level_for(color_hex, count):
    if color_hex in GH_LEVELS:
        return GH_LEVELS[color_hex]
    # fallback heuristic based on raw count
    if count == 0: return 0
    if count <= 2: return 1
    if count <= 5: return 2
    if count <= 9: return 3
    return 4


def _fetch_public_atom_feed():
    """Fetch the fully-public GitHub activity feed for a user. This is the
    same kind of endpoint (github.com, not api.github.com) used for the
    contribution grid — it is NOT subject to the 60 req/hour unauthenticated
    REST API rate limit, so no token is ever required."""
    url = f"https://github.com/{GH_USERNAME}.atom"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode()
    except Exception as e:
        print(f"WARN: could not fetch activity feed ({e})", file=sys.stderr)
        return None


def fetch_last_active_repo():
    """Return (repo_name, pushed_at_iso) for the most recently pushed-to repo,
    excluding the profile repo itself (which always looks 'just pushed' because
    this very script commits to it). Uses the public Atom feed — no token."""
    xml = _fetch_public_atom_feed()
    if not xml:
        return None, None

    entries = re.findall(r'<entry>(.*?)</entry>', xml, re.S)
    for e in entries:
        title_m = re.search(r'<title type="html">(.*?)</title>', e)
        pub_m = re.search(r'<published>(.*?)</published>', e)
        if not title_m or not pub_m:
            continue
        title = html.unescape(title_m.group(1))
        # title looks like "<user> pushed <repo>" (or "created a branch", "opened a pull request", etc.)
        m = re.search(r'pushed\s+(\S+)', title)
        if not m:
            continue
        repo_name = m.group(1)
        if repo_name.lower() == GH_USERNAME.lower():
            continue  # skip the profile repo itself
        # normalise published "2026-08-25 12:59:34 UTC" -> ISO "2026-08-25T12:59:34Z"
        pub_raw = pub_m.group(1).replace(" UTC", "").replace(" ", "T") + "Z"
        return repo_name, pub_raw
    return None, None


def fetch_recent_commits(limit=3):
    """Return up to `limit` most recent individual commits (repo, message, date),
    parsed from the public Atom activity feed — no token, no api.github.com
    rate limit."""
    xml = _fetch_public_atom_feed()
    if not xml:
        return []

    entries = re.findall(r'<entry>(.*?)</entry>', xml, re.S)
    commits = []
    for e in entries:
        title_m = re.search(r'<title type="html">(.*?)</title>', e)
        pub_m = re.search(r'<published>(.*?)</published>', e)
        content_m = re.search(r'<content type="html">(.*?)</content>', e, re.S)
        if not (title_m and pub_m and content_m):
            continue
        title = html.unescape(title_m.group(1))
        m = re.search(r'pushed\s+(\S+)', title)
        if not m:
            continue
        repo_name = m.group(1)
        if repo_name.lower() == GH_USERNAME.lower():
            continue  # skip the profile repo itself — show real project commits
        pub_raw = pub_m.group(1).replace(" UTC", "").replace(" ", "T") + "Z"
        content = html.unescape(content_m.group(1))
        msgs = re.findall(r'<blockquote>\s*(.*?)\s*</blockquote>', content, re.S)
        for msg in msgs:
            msg_clean = re.sub(r'\s+', ' ', msg).strip()[:60]
            if not msg_clean:
                continue
            commits.append((repo_name, msg_clean, pub_raw))
            if len(commits) >= limit:
                return commits
    return commits


def relative_time(iso_str):
    if not iso_str:
        return "unknown"
    from datetime import datetime, timezone
    dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)} minutes ago"
    if secs < 86400:
        return f"{int(secs // 3600)} hours ago"
    if secs < 86400 * 30:
        return f"{int(secs // 86400)} days ago"
    return dt.strftime("%d %b %Y")


def build_svg(weeks, last_repo=None, last_pushed=None, recent_commits=None):
    cols = len(weeks)
    grid = {}
    for w, week in enumerate(weeks):
        for day in week["contributionDays"]:
            d = day["weekday"]
            lvl = level_for(day["color"], day["contributionCount"])
            grid[(w, d)] = PALETTE[lvl]

    W = 1400
    H_BOT = 500
    CYCLE = "9s"
    by = 0  # this file is standalone, starts at y=0

    svg = []
    svg.append(f'''<svg width="{W}" height="{H_BOT}" viewBox="0 0 {W} {H_BOT}" xmlns="http://www.w3.org/2000/svg">
<defs>
  <style>
    .term-bg {{ fill: #0b0f19; }}
    .scoreLabel {{ font-family: 'Courier New', monospace; font-size: 14px; fill: #f4c94c; font-weight: bold; }}
    .rank {{ font-family: 'Courier New', monospace; font-size: 14px; fill: #7ec8ff; font-weight: bold; }}
    .combo {{ font-family: 'Courier New', monospace; font-size: 14px; fill: #ff6b81; font-weight: bold; }}
    .month {{ font-family: 'Courier New', monospace; font-size: 10px; fill: #4a5568; }}
    .daylabel {{ font-family: 'Courier New', monospace; font-size: 10px; fill: #4a5568; }}
    .dim {{ font-family: 'Courier New', monospace; font-size: 12px; fill: #4a5568; }}
    .commitline {{ font-family: 'Courier New', monospace; font-size: 12px; fill: #d7e4ea; }}
    .commitrepo {{ font-family: 'Courier New', monospace; font-size: 12px; fill: #7ec8ff; font-weight: bold; }}
  </style>
  <radialGradient id="shipGlow" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#7ec8ff" stop-opacity="0.6"/>
    <stop offset="100%" stop-color="#7ec8ff" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="boomGrad" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#fff6d8" stop-opacity="1"/>
    <stop offset="40%" stop-color="#ffb020" stop-opacity="0.9"/>
    <stop offset="100%" stop-color="#ff5f56" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="flameGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#ffffff"/>
    <stop offset="50%" stop-color="#7ec8ff"/>
    <stop offset="100%" stop-color="#2ee6a6" stop-opacity="0"/>
  </linearGradient>
</defs>

<rect class="term-bg" x="0" y="0" width="{W}" height="{H_BOT}" rx="16"/>
<rect x="4" y="4" width="{W-8}" height="{H_BOT-8}" rx="16" fill="none" stroke="#2ee6a6" stroke-width="2"/>

<text x="40" y="42" class="scoreLabel">SCORE:</text>
<text x="420" y="42" class="rank">RANK: LVL 7 . SI ARCHITECT</text>
<text x="760" y="42" class="combo">COMBO: x3 SHIPPER</text>
<text x="1060" y="42" class="scoreLabel" fill="#7ec8ff">SHIELDS: 100%</text>
''')

    for i in range(10):
        x = 1230 + i * 12
        svg.append(f'<rect x="{x}" y="30" width="8" height="14" fill="#2ee6a6" opacity="0.85"/>\n')

    month_labels = []
    seen_months = set()
    for w, week in enumerate(weeks):
        first_day = week["contributionDays"][0]["date"]
        m = first_day[:7]  # YYYY-MM
        if m not in seen_months:
            seen_months.add(m)
            month_labels.append((w, first_day[5:7]))

    MONTH_NAMES = {"01":"JAN","02":"FEB","03":"MAR","04":"APR","05":"MAY","06":"JUN",
                   "07":"JUL","08":"AUG","09":"SEP","10":"OCT","11":"NOV","12":"DEC"}

    grid_x0, grid_y0 = 90, 110
    cell = 16
    for w, mm in month_labels:
        svg.append(f'<text x="{grid_x0 + w*cell}" y="{grid_y0-14}" class="month">{MONTH_NAMES.get(mm,"")}</text>\n')

    for i, d in enumerate(["MON", "WED", "FRI"]):
        svg.append(f'<text x="20" y="{grid_y0 + (i*2+1)*cell + 4}" class="daylabel">{d}</text>\n')

    bright_cells = [(w, d) for (w, d), c in grid.items() if c == PALETTE[4]]
    mid_cells = [(w, d) for (w, d), c in grid.items() if c == PALETTE[3]]
    random.seed()
    random.shuffle(bright_cells)
    random.shuffle(mid_cells)
    targets = (bright_cells + mid_cells)[:5]
    if len(targets) < 2:
        targets = list(grid.keys())[-5:]
    n = len(targets)
    hit_time_frac = [0.14 + i * (0.7 / max(n,1)) for i in range(n)]

    for w in range(cols):
        for d in range(7):
            if (w, d) not in grid:
                continue
            x = grid_x0 + w * cell
            y = grid_y0 + d * cell
            color = grid[(w, d)]
            if (w, d) in targets:
                idx = targets.index((w, d))
                hf = hit_time_frac[idx]
                eps = 0.012
                kt = f"0;{max(hf-0.001,0):.3f};{hf:.3f};{min(hf+eps,0.999):.3f};0.995;1"
                vals = f"{color};{color};#ffffff;#0a0f18;#0a0f18;{color}"
                svg.append(f'<rect x="{x}" y="{y}" width="12" height="12" rx="2" fill="{color}"><animate attributeName="fill" values="{vals}" keyTimes="{kt}" dur="{CYCLE}" repeatCount="indefinite"/></rect>\n')
            else:
                svg.append(f'<rect x="{x}" y="{y}" width="12" height="12" rx="2" fill="{color}"/>\n')

    for idx, (w, d) in enumerate(targets):
        x = grid_x0 + w * cell + 6
        y = grid_y0 + d * cell + 6
        hf = hit_time_frac[idx]
        kt = f"0;{max(hf-0.001,0):.3f};{hf:.3f};{min(hf+0.03,0.999):.3f};1"
        ov = "0;0;1;0;0"
        rv = "1;1;22;30;30"
        svg.append(f'''<circle cx="{x}" cy="{y}" r="1" fill="url(#boomGrad)">
  <animate attributeName="opacity" values="{ov}" keyTimes="{kt}" dur="{CYCLE}" repeatCount="indefinite"/>
  <animate attributeName="r" values="{rv}" keyTimes="{kt}" dur="{CYCLE}" repeatCount="indefinite"/>
</circle>\n''')

    if n > 0:
        ship_y = grid_y0 + 7 * cell + 55
        ship_xs = [grid_x0 + w * cell + 6 for (w, d) in targets]
        path_x = [ship_xs[0] - 40] + ship_xs + [ship_xs[0] - 40]
        path_kt = [0.0] + hit_time_frac + [1.0]
        kt_str = ";".join(f"{v:.3f}" for v in path_kt)

        svg.append(f'''
<g id="ship">
  <animateTransform attributeName="transform" type="translate"
    values="{';'.join(f'{v-path_x[0]:.1f} 0' for v in path_x)}"
    keyTimes="{kt_str}" dur="{CYCLE}" repeatCount="indefinite" calcMode="linear"/>
  <g transform="translate({path_x[0]} {ship_y})">
    <ellipse cx="0" cy="14" rx="20" ry="7" fill="url(#shipGlow)"/>
    <polygon points="-6,16 6,16 0,34" fill="url(#flameGrad)">
      <animate attributeName="points" values="-6,16 6,16 0,34; -5,16 5,16 0,40; -6,16 6,16 0,34" dur="0.25s" repeatCount="indefinite"/>
    </polygon>
    <path d="M 0 -26 C 8 -18 10 -4 8 10 L 4 16 L -4 16 L -8 10 C -10 -4 -8 -18 0 -26 Z" fill="#e7edf1" stroke="#7ec8ff" stroke-width="1.5"/>
    <path d="M -8 4 L -26 16 L -18 18 L -6 12 Z" fill="#7ec8ff" stroke="#ffffff" stroke-width="1"/>
    <path d="M 8 4 L 26 16 L 18 18 L 6 12 Z" fill="#7ec8ff" stroke="#ffffff" stroke-width="1"/>
    <ellipse cx="0" cy="-8" rx="4.5" ry="6.5" fill="#2ee6a6" stroke="#0b0f19" stroke-width="0.8"/>
    <circle cx="0" cy="-25" r="2" fill="#ffffff">
      <animate attributeName="opacity" values="1;0.3;1" dur="0.6s" repeatCount="indefinite"/>
    </circle>
  </g>
</g>
''')

        for idx, (w, d) in enumerate(targets):
            tx = grid_x0 + w * cell + 6
            ty = grid_y0 + d * cell + 6
            sx = ship_xs[idx]
            hf = hit_time_frac[idx]
            t0 = max(hf - 0.03, 0)
            t1 = hf
            kt = f"0;{t0:.3f};{t1:.3f};{min(t1+0.005,0.999):.3f};1"
            ov = "0;0;1;0;0"
            svg.append(f'''<line x1="{sx}" y1="{ship_y-10}" x2="{tx}" y2="{ty}" stroke="#8fffcf" stroke-width="2.5">
  <animate attributeName="opacity" values="{ov}" keyTimes="{kt}" dur="{CYCLE}" repeatCount="indefinite"/>
</line>
<line x1="{sx}" y1="{ship_y-10}" x2="{tx}" y2="{ty}" stroke="#ffffff" stroke-width="1">
  <animate attributeName="opacity" values="{ov}" keyTimes="{kt}" dur="{CYCLE}" repeatCount="indefinite"/>
</line>\n''')

    base_score = 24800
    score_vals = [base_score]
    for i in range(n):
        score_vals.append(score_vals[-1] + random.choice([40, 55, 65, 80]))
    bounds = [0.0] + hit_time_frac + [1.0]
    svg.append('<g>\n')
    for i, val in enumerate(score_vals):
        t_start = bounds[i]
        t_end = bounds[i + 1] if i + 1 < len(bounds) else 1.0
        txt = f"{val:,}"
        if i == 0:
            kt = f"0;{max(t_end-0.001,0):.3f};{t_end:.3f};1"
            ov = "1;1;0;0"
        elif i == len(score_vals) - 1:
            kt = f"0;{max(t_start-0.001,0):.3f};{t_start:.3f};1"
            ov = "0;0;1;1"
        else:
            kt = f"0;{max(t_start-0.001,0):.3f};{t_start:.3f};{max(t_end-0.001,0):.3f};{t_end:.3f};1"
            ov = "0;0;1;1;0;0"
        svg.append(f'<text x="105" y="42" class="scoreLabel" opacity="0">{txt}<animate attributeName="opacity" values="{ov}" keyTimes="{kt}" dur="{CYCLE}" repeatCount="indefinite"/></text>\n')
    svg.append('<text x="172" y="42" class="scoreLabel">PTS</text>\n')
    svg.append('</g>\n')

    if last_repo:
        when = relative_time(last_pushed)
        svg.append(f'<text x="{grid_x0}" y="352" class="scoreLabel" fill="#8fffcf">&gt; last active repo: {last_repo} ({when})</text>\n')

    if recent_commits:
        svg.append(f'<text x="{grid_x0}" y="378" class="dim">- recent commits</text>\n')
        for i, (repo_name, msg, created_at) in enumerate(recent_commits):
            y = 398 + i * 20
            when = relative_time(created_at) if created_at else "?"
            msg_esc = (msg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            svg.append(f'<text x="{grid_x0+8}" y="{y}" class="commitrepo">{repo_name}</text>'
                        f'<text x="{grid_x0+188}" y="{y}" class="commitline">{msg_esc}</text>'
                        f'<text x="{W-140}" y="{y}" class="dim">{when}</text>\n')

    svg.append(f'<text x="{grid_x0}" y="{H_BOT-16}" class="dim">[ CONTRIBUTION GRID // {GH_USERNAME} — live ]</text>\n')
    svg.append('</svg>')
    return ''.join(svg)


def main():
    weeks, total = fetch_contributions()
    last_repo, last_pushed = fetch_last_active_repo()
    recent_commits = fetch_recent_commits(limit=3)
    svg = build_svg(weeks, last_repo=last_repo, last_pushed=last_pushed, recent_commits=recent_commits)
    os.makedirs("assets", exist_ok=True)
    with open("assets/contribution.svg", "w") as f:
        f.write(svg)
    print(f"Generated assets/contribution.svg — {total} contributions in the last year. "
          f"Last active repo: {last_repo}. Recent commits: {len(recent_commits)}")


if __name__ == "__main__":
    main()
