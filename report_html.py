#!/usr/bin/env python3
"""Standalone HTML report rendering for the PMR/PSH CLI (test.py).

The web service (service.py) does not use this module: the static page
renders the same report JSON client-side with static/app.js, reusing the
CSS in static/style.css (kept in sync with the styles below).
"""

from datetime import datetime
import html
import os

from pmr_core import WEEKDAYS_FR, MIN_TRANSFER_MINUTES, Verdict

MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

ROLE_ICON = {"départ": "🚉", "correspondance": "🔄", "arrivée": "🏁"}
ROLE_LABEL = {"départ": "Départ", "correspondance": "Correspondance", "arrivée": "Arrivée"}
LINE_ICON = {"success": "✓", "info": "ℹ", "warning": "⚠", "error": "✗"}
VERDICT_META = {
    Verdict.OK: ("OK", "ok"),
    Verdict.MEH: ("Vigilance", "meh"),
    Verdict.KO: ("Bloquant", "ko"),
}


def default_report_path(day):
    return os.path.abspath(f"itineraire_pmr_{day}.html")


def format_date_fr(date_str):
    d = datetime.strptime(date_str, "%Y%m%d").date()
    return f"{WEEKDAYS_FR[d.weekday()].capitalize()} {d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def _esc(value):
    return html.escape(str(value), quote=True)


def _render_times(entry):
    chips = []
    if entry["arrival_time"]:
        chips.append(f'<span class="chip">↘ arrivée <b>{_esc(entry["arrival_time"])}</b></span>')
    if entry["departure_time"]:
        chips.append(f'<span class="chip">↗ départ <b>{_esc(entry["departure_time"])}</b></span>')
    return "".join(chips)


def _render_pmr_lines(lines):
    items = []
    for line in lines:
        kind = line["kind"]
        icon = LINE_ICON.get(kind, "ℹ")
        items.append(
            f'<li class="pmr-line {kind}"><span class="pmr-icon">{icon}</span>'
            f'<span>{_esc(line["text"])}</span></li>'
        )
    return "".join(items)


def _render_station(station):
    role = station["role"]
    icon = ROLE_ICON.get(role, "•")
    label, cls = VERDICT_META[station["verdict"]]

    transfer_html = ""
    if station["transfer_minutes"] is not None:
        warn = station["transfer_warning"]
        badge_cls = "meh" if warn else "ok"
        transfer_html = (
            f'<div class="transfer {badge_cls}">'
            f'{"⚠" if warn else "✓"} Correspondance : {station["transfer_minutes"]} min'
            + (f" (&lt; {MIN_TRANSFER_MINUTES} min)" if warn else "")
            + "</div>"
        )

    return f"""
      <li class="station">
        <div class="marker {cls}">{icon}</div>
        <div class="card">
          <div class="card-head">
            <div class="card-title">
              <span class="idx">{station["n"]}</span>
              <span class="name">{_esc(station["name"])}</span>
              <span class="uic">UIC {_esc(station["uic"])}</span>
            </div>
            <span class="pill {cls}">{label}</span>
          </div>
          <div class="role-row">
            <span class="role-tag">{ROLE_LABEL.get(role, role)}</span>
            {_render_times(station)}
          </div>
          {transfer_html}
          <ul class="pmr-lines">
            {_render_pmr_lines(station["lines"])}
          </ul>
        </div>
      </li>"""


def render_html_report(report):
    day = report["day"]
    verdict = report["verdict"]
    label, cls = VERDICT_META[verdict]
    stations_html = "".join(_render_station(s) for s in report["stations"])

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Itinéraire PMR/PSH — {_esc(day)}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f5f6f8;
    --surface: #ffffff;
    --text: #1a1d23;
    --muted: #6b7280;
    --border: #e5e7eb;
    --line: #d1d5db;
    --ok-fg: #15803d; --ok-bg: #dcfce7; --ok-line: #86efac;
    --meh-fg: #b45309; --meh-bg: #fef3c7; --meh-line: #fcd34d;
    --ko-fg: #b91c1c; --ko-bg: #fee2e2; --ko-line: #fca5a5;
    --info-fg: #374151; --info-bg: #f3f4f6;
    --accent: #2563eb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a;
      --surface: #1e2127;
      --text: #e8eaed;
      --muted: #9aa1ac;
      --border: #2d313a;
      --line: #3a3f4a;
      --ok-fg: #4ade80; --ok-bg: #16321f; --ok-line: #1f6b3a;
      --meh-fg: #fbbf24; --meh-bg: #3a2c0d; --meh-line: #7c5a12;
      --ko-fg: #f87171; --ko-bg: #3a1414; --ko-line: #7a2323;
      --info-fg: #cbd0d8; --info-bg: #262a31;
      --accent: #60a5fa;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding-block: 32px;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .wrap {{
    max-width: 760px;
    margin: 0 auto;
    padding-inline: 20px;
  }}
  header {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 28px;
  }}
  h1 {{
    font-size: 20px;
    margin: 0 0 4px;
  }}
  .subtitle {{
    color: var(--muted);
    font-size: 14px;
    margin: 0;
  }}
  .pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
    font-size: 13px;
    padding: 4px 12px;
    border-radius: 999px;
    white-space: nowrap;
  }}
  .pill.ok {{ color: var(--ok-fg); background: var(--ok-bg); border: 1px solid var(--ok-line); }}
  .pill.meh {{ color: var(--meh-fg); background: var(--meh-bg); border: 1px solid var(--meh-line); }}
  .pill.ko {{ color: var(--ko-fg); background: var(--ko-bg); border: 1px solid var(--ko-line); }}
  .pill.big {{ font-size: 15px; padding: 8px 18px; }}
  ol.timeline {{
    list-style: none;
    margin: 0;
    padding: 0;
    position: relative;
  }}
  li.station {{
    position: relative;
    display: flex;
    gap: 16px;
    padding-bottom: 28px;
  }}
  li.station:last-child {{ padding-bottom: 0; }}
  li.station::before {{
    content: "";
    position: absolute;
    left: 19px;
    top: 40px;
    bottom: -4px;
    width: 2px;
    background: var(--line);
  }}
  li.station:last-child::before {{ display: none; }}
  .marker {{
    flex: 0 0 auto;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    background: var(--surface);
    border: 2px solid var(--line);
    z-index: 1;
  }}
  .marker.ok {{ border-color: var(--ok-line); }}
  .marker.meh {{ border-color: var(--meh-line); }}
  .marker.ko {{ border-color: var(--ko-line); }}
  .card {{
    flex: 1 1 auto;
    min-width: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
  }}
  .card-head {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }}
  .card-title {{
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
    min-width: 0;
  }}
  .card-title .idx {{
    color: var(--muted);
    font-size: 13px;
  }}
  .card-title .name {{
    font-weight: 600;
    font-size: 16px;
  }}
  .card-title .uic {{
    color: var(--muted);
    font-size: 12px;
  }}
  .role-row {{
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }}
  .role-tag {{
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: .04em;
    color: var(--accent);
    font-weight: 600;
  }}
  .chip {{
    font-size: 13px;
    color: var(--muted);
    background: var(--info-bg);
    padding: 2px 9px;
    border-radius: 8px;
  }}
  .chip b {{ color: var(--text); }}
  .transfer {{
    margin-top: 10px;
    font-size: 13px;
    font-weight: 600;
    padding: 6px 10px;
    border-radius: 8px;
    display: inline-block;
  }}
  .transfer.ok {{ color: var(--ok-fg); background: var(--ok-bg); }}
  .transfer.meh {{ color: var(--meh-fg); background: var(--meh-bg); }}
  ul.pmr-lines {{
    list-style: none;
    margin: 12px 0 0;
    padding: 10px 0 0;
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 7px;
  }}
  li.pmr-line {{
    display: flex;
    gap: 8px;
    font-size: 13.5px;
    align-items: flex-start;
  }}
  .pmr-icon {{
    flex: 0 0 auto;
    width: 18px;
    text-align: center;
    font-weight: 700;
  }}
  li.pmr-line.success .pmr-icon {{ color: var(--ok-fg); }}
  li.pmr-line.error .pmr-icon {{ color: var(--ko-fg); }}
  li.pmr-line.warning .pmr-icon {{ color: var(--meh-fg); }}
  li.pmr-line.info .pmr-icon {{ color: var(--muted); }}
  li.pmr-line.warning span:last-child {{ color: var(--meh-fg); }}
  li.pmr-line.error span:last-child {{ color: var(--ko-fg); }}
  footer {{
    margin-top: 28px;
    color: var(--muted);
    font-size: 12px;
    text-align: center;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Itinéraire PMR/PSH</h1>
        <p class="subtitle">{_esc(format_date_fr(day))}</p>
      </div>
      <span class="pill big {cls}">{label}</span>
    </header>
    <ol class="timeline">{stations_html}
    </ol>
    <footer>Généré automatiquement — seuil de correspondance courte : {MIN_TRANSFER_MINUTES} min.</footer>
  </div>
</body>
</html>
"""
