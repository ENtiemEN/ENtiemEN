"""Build the GitHub profile's language cards from aggregate repository metadata."""
import base64
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
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
                if language not in {"HTML", "CSS", "PLpgSQL", "Jupyter Notebook"} and size > 0:
                    totals[language] += size
                    counts[language] += 1
        if len(repos) < 100:
            break
        page += 1
    print(f"Private repositories included: {private_seen}.")
    if not totals:
        sys.exit("No eligible language data returned.")
    return totals, counts


def render(names, shares, counts, mobile):
    w, header, step, margin = (343, 92, 64, 16) if mobile else (1000, 145, 62, 49)
    height = header + step*len(names) + (18 if mobile else 23)
    art = "mobile.png" if mobile else "desktop.png"
    encoded = base64.b64encode((ROOT/"scripts/art"/art).read_bytes()).decode()
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{height}" viewBox="0 0 {w} {height}">',
        '<title>Languages by code share and repository presence</title>',
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

    for i, name in enumerate(names):
        y = header + i*step + (26 if mobile else 33)
        value = shares[name]
        color = "#d3838e" if i == 0 else "#c6b9a5"
        label(margin, y, name, min(18 if mobile else 25, 270 / max(len(name), 1)), serif=True)
        label(300 if mobile else 890, y, str(counts[name]), 15 if mobile else 19, "middle")
        x, end = (162, 255) if mobile else (410, 680)
        by = y+20 if mobile else y
        span = (end-x)*value/100
        line(x, by, end, "#514a49")
        svg.append(f'<rect x="{x}" y="{by-4}" width="{span:.2f}" height="8" fill="{color}" mask="url(#cuts)"/>')
        svg.append(f'<path d="M{x+span:.2f} {by-4}v8" stroke="{color}"/>')
        label(162 if mobile else 708, y-3 if mobile else by, f"{value:.1f}%", 12 if mobile else 18)
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
    descriptions = []
    for name in names:
        unit = "repository" if counts[name] == 1 else "repositories"
        descriptions.append(f"{name}: {shares[name]:.1f}% in {counts[name]} {unit}")
    alt = "Languages by code share. " + "; ".join(descriptions)
    directory = root/"assets"/"languages"
    directory.mkdir(exist_ok=True)
    paths = {}
    for layout in ("desktop", "mobile"):
        data = render(names, shares, counts, layout == "mobile").encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()[:16]
        path = directory/f"{layout}-{digest}.svg"
        path.write_bytes(data)
        paths[layout] = path.relative_to(root).as_posix()
        for previous in directory.glob(f"{layout}-*.svg"):
            if previous != path:
                previous.unlink()
    picture = (f'<picture>\n  <source media="(max-width: 600px)" srcset="{paths["mobile"]}">\n'
               f'  <img src="{paths["desktop"]}" alt="{escape(alt, quote=True)}" width="100%">\n</picture>')
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps(history, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    readme.write_text(before+START+"\n"+picture+"\n"+END+after, encoding="utf-8")


if __name__ == "__main__":
    token = os.environ.get("LANG_STATS_TOKEN")
    if not token:
        sys.exit("Set the LANG_STATS_TOKEN repository secret.")
    totals, counts = collect(token, os.environ["GITHUB_REPOSITORY"])
    publish(ROOT, totals, counts, datetime.now(timezone.utc).date())
