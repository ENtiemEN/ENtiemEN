"""Build the GitHub profile's language cards from aggregate repository metadata."""
import base64
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- language-card:start -->"
END = "<!-- language-card:end -->"


def api(path, token):
    request = Request("https://api.github.com" + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "profile-language-stats",
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                sys.exit(f"GitHub language metadata request failed (HTTP {error.code}).")
        except (URLError, TimeoutError):
            if attempt == 2:
                sys.exit("GitHub language metadata request timed out or was unavailable.")
        sleep(2 ** (attempt + 1))


def collect(token, profile):
    totals, counts = Counter(), Counter()
    private_seen = False
    page = 1
    while True:
        repos = api(f"/user/repos?affiliation=owner&visibility=all&per_page=100&page={page}", token)
        for repo in repos:
            owner = repo["owner"]["login"]
            if repo["fork"] or owner.lower() != profile.split("/")[0].lower():
                continue
            if repo["full_name"].lower() == profile.lower():
                continue  # Generated profile assets should never influence the ranking.
            languages = api(f'/repos/{quote(owner)}/{quote(repo["name"])}/languages', token)
            private_seen |= repo["private"]
            for language, size in languages.items():
                if language not in {"HTML", "CSS"} and size > 0:
                    totals[language] += size
                    counts[language] += 1
        if len(repos) < 100:
            break
        page += 1
    print(f"Private repositories included: {private_seen}.")
    if not totals:
        sys.exit("No eligible language data returned.")
    return totals, counts


def render(names, shares, counts, history, today, mobile):
    w, header, step, margin = (343, 92, 64, 16) if mobile else (1000, 145, 62, 49)
    height = header + step*len(names) + (18 if mobile else 23)
    art = "mobile.png" if mobile else "desktop.png"
    encoded = base64.b64encode((ROOT/"scripts/art"/art).read_bytes()).decode()
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{height}" viewBox="0 0 {w} {height}">',
        '<title>Languages by code share, repository presence and 30-day trend</title>',
        '<defs><pattern id="hatch" width="4" height="8" patternUnits="userSpaceOnUse"><path d="M-2 8l5-8m-1 8l5-8" fill="none" stroke="white" stroke-width="1.2"/></pattern>'
        '<mask id="cuts"><rect width="100%" height="100%" fill="url(#hatch)"/></mask></defs>',
        '<rect width="100%" height="100%" fill="#19191c"/>',
        f'<image width="{w}" height="{header}" xlink:href="data:image/png;base64,{encoded}"/>',
    ]

    def label(x, y, text, size, anchor="start", serif=False):
        face = "Georgia,serif" if serif else "Consolas,monospace"
        svg.append(f'<text x="{x}" y="{y}" dy=".34em" font-family="{face}" font-size="{size}" fill="#ede4d3" text-anchor="{anchor}">{escape(text)}</text>')

    def line(x, y, end, color):
        svg.append(f'<path d="M{x} {y}H{end}" stroke="{color}"/>')

    days = sorted(history)
    window_start = today - timedelta(days=29)
    series = {name: [history[day].get(name, 0) for day in days] for name in names}
    amplitude = max((abs(v-values[0]) for values in series.values() for v in values), default=0)
    scale = min(2.4 if mobile else 3.2, 18 / max(amplitude, 0.01))
    for i, name in enumerate(names):
        y = header + i*step + (26 if mobile else 33)
        value = shares[name]
        color = "#d3838e" if i == 0 else "#c6b9a5"
        label(margin, y, name, min(18 if mobile else 25, 270 / max(len(name), 1)), serif=True)
        label(207 if mobile else 621, y, str(counts[name]), 15 if mobile else 19, "middle")
        x, end = (margin, 112) if mobile else (290, 465)
        by = y+20 if mobile else y
        span = (end-x)*value/100
        line(x, by, end, "#514a49")
        svg.append(f'<rect x="{x}" y="{by-4}" width="{span:.2f}" height="8" fill="{color}" mask="url(#cuts)"/>')
        svg.append(f'<path d="M{x+span:.2f} {by-4}v8" stroke="{color}"/>')
        label(126 if mobile else 493, by, f"{value:.1f}%", 12 if mobile else 18)
        left, right = (248, 322) if mobile else (727, 875)
        baseline = y+4
        line(left, baseline, right, "#413d3e")
        values = series[name]
        points = []
        previous = None
        for day, v in zip(days, values):
            observed = date.fromisoformat(day)
            px = left+(right-left)*(observed-window_start).days/29
            py = baseline-(v-values[0])*scale
            joined = previous is not None and (observed-previous).days == 1
            points.append(f'{"L" if joined else "M"}{px:.2f} {py:.2f}')
            svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1" fill="{color}"/>')
            previous = observed
        svg.append(f'<path d="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.7"/>')
        svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="2.7" fill="{color}"/>')
        if not mobile and len(days)>1:
            label(w-margin, y, f"{values[-1]-values[0]:+.1f} pp", 14, "end")
        line(margin, header+(i+1)*step+2, w-margin, "#413d3e")
    return "\n".join(svg+["</svg>", ""])


def publish(root, totals, counts, today):
    names = sorted(totals, key=lambda name: (-totals[name], name))[:5]
    total = sum(totals.values())
    shares = {name: round(size*100/total, 2) for name,size in totals.items()}
    state = root/"data/languages.json"
    history = json.loads(state.read_text()) if state.exists() else {}
    history = {day: sample for day,sample in history.items() if 0 <= (today-date.fromisoformat(day)).days < 30}
    history[today.isoformat()] = shares
    readme = root/"README.md"
    original = readme.read_text(encoding="utf-8")
    before, opening, remainder = original.partition(START)
    _, closing, after = remainder.partition(END)
    if not opening or not closing:
        raise ValueError("Missing language card markers in README.")
    alt = "Languages by code share. " + "; ".join(f"{n}: {shares[n]:.1f}% in {counts[n]} repositories" for n in names)
    picture = ('<picture>\n  <source media="(max-width: 600px)" srcset="assets/languages-mobile.svg">\n'
               f'  <img src="assets/languages.svg" alt="{escape(alt, quote=True)}" width="100%">\n</picture>')
    for mobile, filename in [(False,"languages.svg"), (True,"languages-mobile.svg")]:
        (root/"assets"/filename).write_text(render(names, shares, counts, history, today, mobile), encoding="utf-8")
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps(history, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    readme.write_text(before+START+"\n"+picture+"\n"+END+after, encoding="utf-8")


if __name__ == "__main__":
    token = os.environ.get("LANG_STATS_TOKEN")
    if not token:
        sys.exit("Set the LANG_STATS_TOKEN repository secret.")
    totals, counts = collect(token, os.environ["GITHUB_REPOSITORY"])
    publish(ROOT, totals, counts, datetime.now(timezone.utc).date())
