"""
Local web dashboard for job search automation.

Run with:
  python dashboard.py          # opens http://localhost:5050 in your browser
  python dashboard.py --port=8080

Two tabs:
  Applications — live table from SQLite (same data as the HTML report)
  Settings     — edit all config options and save to user_config.json
                 (no need to touch config.py)

The Settings tab writes to user_config.json which is automatically
read by config.py on every script run.
"""

import json
import os
import sqlite3
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import (
    AI_CONFIG,
    BROWSER_CONFIG,
    COMPANY_BLACKLIST,
    PATHS,
    PLATFORMS,
    SCHEDULER_CONFIG,
    SEARCH_CONFIG,
)

PORT = int(os.getenv("DASHBOARD_PORT", "5050"))
USER_CONFIG_FILE = Path("user_config.json")


# ── HTML template ──────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Search Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1a1a2e}}
  .header{{background:linear-gradient(135deg,#1a1a2e,#4a90d9);color:white;padding:24px 40px;display:flex;align-items:center;justify-content:space-between}}
  .header h1{{font-size:22px;font-weight:700}}
  .header small{{opacity:.7;font-size:13px}}
  .tabs{{display:flex;gap:0;border-bottom:2px solid #e0e0e0;padding:0 40px;background:white}}
  .tab{{padding:14px 28px;cursor:pointer;font-size:14px;font-weight:600;color:#666;border-bottom:3px solid transparent;margin-bottom:-2px;transition:.15s}}
  .tab.active{{color:#4a90d9;border-bottom-color:#4a90d9}}
  .tab:hover{{color:#4a90d9}}
  .pane{{display:none;padding:24px 40px 40px}}
  .pane.active{{display:block}}
  /* Stats */
  .stats{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px}}
  .stat{{background:white;border-radius:10px;padding:18px 24px;flex:1;min-width:120px;box-shadow:0 2px 6px rgba(0,0,0,.07);text-align:center}}
  .stat .n{{font-size:32px;font-weight:700;color:#4a90d9}}
  .stat .l{{font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-top:3px}}
  .stat.green .n{{color:#27ae60}}
  .stat.red .n{{color:#e74c3c}}
  .stat.orange .n{{color:#f39c12}}
  /* Table */
  table{{width:100%;border-collapse:collapse;background:white;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,.07);overflow:hidden}}
  thead{{background:#1a1a2e;color:white}}
  th{{padding:12px 14px;text-align:left;font-size:12px;font-weight:600;letter-spacing:.3px}}
  td{{padding:11px 14px;border-bottom:1px solid #f0f0f0;font-size:13px;vertical-align:top}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#f8f9ff}}
  .badge{{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600}}
  .badge-applied{{background:#d4edda;color:#155724}}
  .badge-skipped{{background:#f0f0f0;color:#666}}
  .badge-rejected{{background:#f8d7da;color:#721c24}}
  .badge-found{{background:#cce5ff;color:#004085}}
  .badge-error{{background:#fff3cd;color:#856404}}
  .badge-reviewing{{background:#e2d9f3;color:#432874}}
  .score-high{{color:#27ae60;font-weight:700}}
  .score-mid{{color:#f39c12;font-weight:700}}
  .score-low{{color:#e74c3c;font-weight:700}}
  /* Settings */
  .settings-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
  @media(max-width:900px){{.settings-grid{{grid-template-columns:1fr}}}}
  .card{{background:white;border-radius:10px;padding:24px;box-shadow:0 2px 6px rgba(0,0,0,.07)}}
  .card h3{{font-size:15px;font-weight:700;color:#1a1a2e;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid #f0f0f0}}
  .field{{margin-bottom:14px}}
  .field label{{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;text-transform:uppercase;letter-spacing:.4px}}
  .field input[type=text],.field input[type=number],.field input[type=time],.field select,.field textarea{{
    width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px;
    font-family:inherit;background:white;transition:.15s}}
  .field input:focus,.field select:focus,.field textarea:focus{{outline:none;border-color:#4a90d9;box-shadow:0 0 0 3px rgba(74,144,217,.1)}}
  .field input[type=checkbox]{{width:16px;height:16px;cursor:pointer}}
  .checkbox-row{{display:flex;align-items:center;gap:8px}}
  .checkbox-row label{{font-size:14px;font-weight:400;text-transform:none;letter-spacing:0;cursor:pointer}}
  .platform-row{{display:flex;align-items:center;gap:12px;margin-bottom:10px;padding:10px;background:#f8f9fa;border-radius:6px}}
  .platform-row label{{font-weight:600;min-width:130px}}
  .platform-row input[type=number]{{width:80px}}
  .btn{{padding:10px 24px;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;transition:.15s}}
  .btn-primary{{background:#4a90d9;color:white}}
  .btn-primary:hover{{background:#357abd}}
  .btn-danger{{background:#e74c3c;color:white}}
  .btn-danger:hover{{background:#c0392b}}
  .btn-secondary{{background:#f0f0f0;color:#333}}
  .btn-secondary:hover{{background:#e0e0e0}}
  .save-bar{{display:flex;align-items:center;gap:16px;margin-top:24px;padding:16px;background:white;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,.07)}}
  .save-msg{{font-size:13px;color:#27ae60;display:none}}
  /* Blacklist */
  .bl-group{{margin-bottom:16px;padding:12px;background:#fff8f8;border-radius:8px;border-left:3px solid #e74c3c}}
  .bl-group h4{{font-size:13px;font-weight:700;color:#c0392b;margin-bottom:8px}}
  .bl-tags{{display:flex;flex-wrap:wrap;gap:6px}}
  .bl-tag{{display:inline-flex;align-items:center;gap:6px;background:white;border:1px solid #ddd;border-radius:20px;padding:3px 10px;font-size:12px}}
  .bl-tag button{{background:none;border:none;cursor:pointer;color:#999;font-size:14px;line-height:1;padding:0}}
  .bl-tag button:hover{{color:#e74c3c}}
  .bl-add{{display:flex;gap:8px;margin-top:8px}}
  .bl-add input{{flex:1;padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px}}
  .bl-add button{{padding:6px 12px;border:none;background:#e74c3c;color:white;border-radius:6px;cursor:pointer;font-size:12px}}
  .section-title{{font-size:16px;font-weight:700;color:#1a1a2e;margin:24px 0 12px}}
  .refresh-btn{{font-size:12px;padding:6px 14px}}
  .note{{font-size:12px;color:#888;margin-top:4px}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>Job Search Dashboard</h1>
    <small>Local control panel — runs at http://localhost:{port}</small>
  </div>
  <div style="font-size:13px;opacity:.8">{generated_at}</div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('applications')">Applications</div>
  <div class="tab" onclick="showTab('settings')">Settings</div>
</div>

<!-- APPLICATIONS TAB -->
<div id="tab-applications" class="pane active">
  <div class="stats">
    <div class="stat green"><div class="n">{total_applied}</div><div class="l">Applied</div></div>
    <div class="stat"><div class="n">{total_found}</div><div class="l">Found</div></div>
    <div class="stat orange"><div class="n">{total_skipped}</div><div class="l">Skipped</div></div>
    <div class="stat red"><div class="n">{total_rejected}</div><div class="l">Rejected</div></div>
    <div class="stat"><div class="n">{avg_score}</div><div class="l">Avg Score (applied)</div></div>
  </div>

  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <div class="section-title" style="margin:0">All Jobs</div>
    <button class="btn btn-secondary refresh-btn" onclick="window.location.reload()">Refresh</button>
  </div>

  <table>
    <thead>
      <tr>
        <th>#</th><th>Platform</th><th>Title / Company</th><th>Score</th>
        <th>Status</th><th>Applied</th><th>Notes</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>

<!-- SETTINGS TAB -->
<div id="tab-settings" class="pane">
  <form id="settings-form" onsubmit="saveSettings(event)">

  <div class="settings-grid">

    <!-- Search config -->
    <div class="card">
      <h3>Search</h3>
      <div class="field">
        <label>Min relevance score (0-100)</label>
        <input type="number" name="min_relevance_score" min="0" max="100"
               value="{min_relevance_score}">
        <div class="note">Jobs below this score are skipped automatically</div>
      </div>
      <div class="field">
        <label>Max applications per run</label>
        <input type="number" name="max_applications_per_run" min="1" max="100"
               value="{max_applications_per_run}">
      </div>
      <div class="field">
        <label>Posted within (days)</label>
        <input type="number" name="posted_within_days" min="1" max="30"
               value="{posted_within_days}">
        <div class="note">1 = last 24 hours (matches daily schedule)</div>
      </div>
      <div class="field">
        <div class="checkbox-row">
          <input type="checkbox" id="require_review" name="require_review"
                 {require_review_checked}>
          <label for="require_review">Require manual review before submitting</label>
        </div>
      </div>
      <div class="field">
        <div class="checkbox-row">
          <input type="checkbox" id="skip_duplicate_companies" name="skip_duplicate_companies"
                 {skip_duplicate_companies_checked}>
          <label for="skip_duplicate_companies">Skip companies already applied to</label>
        </div>
      </div>
      <div class="field">
        <div class="checkbox-row">
          <input type="checkbox" id="remote_only" name="remote_only"
                 {remote_only_checked}>
          <label for="remote_only">Remote only</label>
        </div>
      </div>
      <div class="field">
        <label>Location</label>
        <input type="text" name="location" value="{location}">
      </div>
      <div class="field">
        <label>Job titles (one per line)</label>
        <textarea name="job_titles" rows="8">{job_titles}</textarea>
      </div>
    </div>

    <!-- Platforms -->
    <div class="card">
      <h3>Platforms</h3>
      {platform_rows}
    </div>

    <!-- Scheduler -->
    <div class="card">
      <h3>Scheduler</h3>
      <div class="field">
        <label>Daily run time (24h)</label>
        <input type="time" name="run_at" value="{run_at}">
      </div>
    </div>

    <!-- Browser -->
    <div class="card">
      <h3>Browser</h3>
      <div class="field">
        <div class="checkbox-row">
          <input type="checkbox" id="headless" name="headless"
                 {headless_checked}>
          <label for="headless">Headless mode (no visible window)</label>
        </div>
        <div class="note">Disable headless to handle CAPTCHAs manually</div>
      </div>
    </div>

  </div>

  <!-- Blacklist -->
  <div class="section-title">Company Blacklist</div>
  <div class="card">
    <p style="font-size:13px;color:#666;margin-bottom:16px">
      Jobs from these companies are automatically skipped. Matching is case-insensitive substring.
    </p>
    <div id="blacklist-container">
      {blacklist_html}
    </div>
    <div style="margin-top:16px">
      <div class="bl-add" id="new-group-row">
        <input type="text" id="new-group-name" placeholder="New group name (e.g. Google)">
        <button type="button" onclick="addGroup()">+ Add Group</button>
      </div>
    </div>
  </div>

  <div class="save-bar">
    <button type="submit" class="btn btn-primary">Save Settings</button>
    <button type="button" class="btn btn-secondary" onclick="window.location.reload()">Reset</button>
    <span class="save-msg" id="save-msg">Saved! Settings will apply on next run.</span>
  </div>

  </form>
</div>

<script>
function showTab(name) {{
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}}

// ── Blacklist management ──────────────────────────────────────────────────────
function removeTag(groupEl, tag) {{
  tag.closest('.bl-tag').remove();
}}

function addTag(groupEl) {{
  const inp = groupEl.querySelector('.add-name-input');
  const val = inp.value.trim();
  if (!val) return;
  const tagsDiv = groupEl.querySelector('.bl-tags');
  tagsDiv.insertAdjacentHTML('beforeend', makeTag(val));
  inp.value = '';
}}

function makeTag(name) {{
  return `<span class="bl-tag"><span class="tag-name">${{name}}</span>
    <button type="button" onclick="this.closest('.bl-tag').remove()" title="Remove">×</button></span>`;
}}

function addGroup() {{
  const nameInp = document.getElementById('new-group-name');
  const gname = nameInp.value.trim();
  if (!gname) return;
  const container = document.getElementById('blacklist-container');
  container.insertAdjacentHTML('beforeend', makeGroupHtml(gname));
  nameInp.value = '';
}}

function makeGroupHtml(gname) {{
  return `<div class="bl-group" data-group="${{gname}}">
    <h4>${{gname}}</h4>
    <div class="bl-tags"></div>
    <div class="bl-add">
      <input type="text" class="add-name-input" placeholder="Add company / subsidiary name">
      <button type="button" onclick="addTag(this.closest('.bl-group'))">+ Add</button>
    </div>
  </div>`;
}}

// ── Save settings ─────────────────────────────────────────────────────────────
function saveSettings(e) {{
  e.preventDefault();
  const form = document.getElementById('settings-form');
  const fd = new FormData(form);

  // Collect blacklist from DOM
  const blacklist = {{}};
  document.querySelectorAll('.bl-group').forEach(g => {{
    const group = g.dataset.group;
    const names = Array.from(g.querySelectorAll('.tag-name')).map(el => el.textContent.trim());
    blacklist[group] = names;
  }});

  // Collect platforms
  const platforms = {{}};
  document.querySelectorAll('.platform-row').forEach(row => {{
    const name = row.dataset.platform;
    const enabled = row.querySelector('.plat-enabled').checked;
    const max = parseInt(row.querySelector('.plat-max').value) || 20;
    platforms[name] = {{enabled, max_jobs_to_scrape: max}};
  }});

  const payload = {{
    search: {{
      min_relevance_score: parseInt(fd.get('min_relevance_score')) || 70,
      max_applications_per_run: parseInt(fd.get('max_applications_per_run')) || 20,
      posted_within_days: parseInt(fd.get('posted_within_days')) || 1,
      require_review: !!fd.get('require_review'),
      skip_duplicate_companies: !!fd.get('skip_duplicate_companies'),
      remote_only: !!fd.get('remote_only'),
      location: fd.get('location') || 'United States',
      job_titles: fd.get('job_titles').split('\\n').map(s => s.trim()).filter(Boolean),
    }},
    platforms,
    scheduler: {{run_at: fd.get('run_at') || '09:00'}},
    browser: {{headless: !!fd.get('headless')}},
    blacklist,
  }};

  fetch('/api/config', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload),
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.ok) {{
      const msg = document.getElementById('save-msg');
      msg.style.display = 'inline';
      setTimeout(() => msg.style.display = 'none', 4000);
    }}
  }})
  .catch(err => alert('Save failed: ' + err));
}}
</script>

</body>
</html>"""


# ── Helper builders ────────────────────────────────────────────────────────────

def _build_rows() -> tuple[str, dict]:
    db_path = PATHS["database"]
    if not Path(db_path).exists():
        return "<tr><td colspan='7' style='text-align:center;color:#999;padding:24px'>No jobs yet. Run the script first.</td></tr>", {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        jobs = conn.execute("""
            SELECT * FROM jobs ORDER BY
              CASE status WHEN 'applied' THEN 0 WHEN 'reviewing' THEN 1 WHEN 'found' THEN 2 ELSE 3 END,
              relevance_score DESC
        """).fetchall()
        stats_raw = conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        stats = {r[0]: r[1] for r in stats_raw}
        avg_row = conn.execute("SELECT AVG(relevance_score) FROM jobs WHERE status='applied'").fetchone()

    stats["avg_score"] = f"{avg_row[0]:.0f}" if avg_row and avg_row[0] else "—"

    rows_html = ""
    for i, job in enumerate(jobs, 1):
        score = job["relevance_score"] or 0
        sc = "score-high" if score >= 80 else "score-mid" if score >= 60 else "score-low"
        score_s = f"{score:.0f}" if score else "—"
        applied = (job["applied_at"] or "")[:16]
        notes = (job["notes"] or "")[:60]
        title_link = f'<a href="{job["url"] or "#"}" target="_blank" style="color:#1a1a2e;font-weight:600">{job["title"]}</a>'
        rows_html += f"""<tr>
          <td>{i}</td>
          <td>{job["platform"]}</td>
          <td>{title_link}<div style="font-size:12px;color:#666;margin-top:2px">{job["company"]}</div></td>
          <td><span class="{sc}">{score_s}</span></td>
          <td><span class="badge badge-{job["status"]}">{job["status"]}</span></td>
          <td style="font-size:12px">{applied}</td>
          <td style="font-size:12px;color:#888">{notes}</td>
        </tr>"""

    return rows_html, stats


def _build_platform_rows() -> str:
    html = ""
    for pname, pcfg in PLATFORMS.items():
        checked = "checked" if pcfg["enabled"] else ""
        html += f"""<div class="platform-row" data-platform="{pname}">
          <label>{pname}</label>
          <div class="checkbox-row">
            <input type="checkbox" class="plat-enabled" {checked}>
            <span style="font-size:13px">enabled</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            <input type="number" class="plat-max" value="{pcfg['max_jobs_to_scrape']}" min="1" max="100" style="width:70px">
            <span style="font-size:12px;color:#888">max jobs</span>
          </div>
        </div>"""
    return html


def _build_blacklist_html() -> str:
    html = ""
    for group, names in COMPANY_BLACKLIST.items():
        tags = "".join(
            f'<span class="bl-tag"><span class="tag-name">{n}</span>'
            f'<button type="button" onclick="this.closest(\'.bl-tag\').remove()" title="Remove">×</button></span>'
            for n in names
        )
        html += f"""<div class="bl-group" data-group="{group}">
          <h4>{group}</h4>
          <div class="bl-tags">{tags}</div>
          <div class="bl-add">
            <input type="text" class="add-name-input" placeholder="Add company / subsidiary name">
            <button type="button" onclick="addTag(this.closest('.bl-group'))">+ Add</button>
          </div>
        </div>"""
    return html


def _render_dashboard() -> str:
    rows_html, stats = _build_rows()
    titles = "\n".join(SEARCH_CONFIG.get("job_titles", []))

    return HTML.format(
        port=PORT,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total_applied=stats.get("applied", 0),
        total_found=stats.get("found", 0),
        total_skipped=stats.get("skipped", 0),
        total_rejected=stats.get("rejected", 0),
        avg_score=stats.get("avg_score", "—"),
        rows=rows_html,
        # Search fields
        min_relevance_score=SEARCH_CONFIG.get("min_relevance_score", 70),
        max_applications_per_run=SEARCH_CONFIG.get("max_applications_per_run", 20),
        posted_within_days=SEARCH_CONFIG.get("posted_within_days", 1),
        require_review_checked="checked" if SEARCH_CONFIG.get("require_review", True) else "",
        skip_duplicate_companies_checked="checked" if SEARCH_CONFIG.get("skip_duplicate_companies", True) else "",
        remote_only_checked="checked" if SEARCH_CONFIG.get("remote_only", True) else "",
        location=SEARCH_CONFIG.get("location", "United States"),
        job_titles=titles,
        # Platform rows
        platform_rows=_build_platform_rows(),
        # Scheduler
        run_at=SCHEDULER_CONFIG.get("run_at", "09:00"),
        # Browser
        headless_checked="checked" if BROWSER_CONFIG.get("headless", False) else "",
        # Blacklist
        blacklist_html=_build_blacklist_html(),
    )


# ── HTTP handler ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default request logging

    def _send(self, body: str | bytes, content_type: str = "text/html", status: int = 200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(_render_dashboard())
        elif parsed.path == "/api/config":
            # Return current user_config.json (or empty dict)
            data = json.loads(USER_CONFIG_FILE.read_text()) if USER_CONFIG_FILE.exists() else {}
            self._send(json.dumps(data), "application/json")
        else:
            self._send("Not found", status=404)

    def do_POST(self):
        if self.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                USER_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                self._send(json.dumps({"ok": True}), "application/json")
                print(f"[Dashboard] Settings saved to {USER_CONFIG_FILE}")
            except Exception as e:
                self._send(json.dumps({"ok": False, "error": str(e)}), "application/json", 400)
        else:
            self._send("Not found", status=404)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    global PORT
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            try:
                PORT = int(arg.split("=", 1)[1])
            except ValueError:
                pass

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"[Dashboard] Serving at {url}")
    print(f"[Dashboard] Press Ctrl+C to stop\n")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] Stopped.")


if __name__ == "__main__":
    main()
