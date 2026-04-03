"""
HTML report generator — creates a detailed, browsable report of all applications.
The report has two tabs: Applications and Settings (read-only config snapshot).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from config import (
    BROWSER_CONFIG,
    COMPANY_BLACKLIST,
    PATHS,
    PLATFORMS,
    SCHEDULER_CONFIG,
    SEARCH_CONFIG,
)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Job Applications Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; color: #1a1a2e; }}
  .header {{ background: linear-gradient(135deg, #1a1a2e, #4a90d9); color: white; padding: 28px 40px; }}
  .header h1 {{ font-size: 26px; font-weight: 700; }}
  .header p {{ opacity: 0.8; margin-top: 5px; font-size: 13px; }}
  .tabs {{ display: flex; border-bottom: 2px solid #e0e0e0; padding: 0 40px; background: white; }}
  .tab {{ padding: 14px 26px; cursor: pointer; font-size: 14px; font-weight: 600; color: #666;
           border-bottom: 3px solid transparent; margin-bottom: -2px; }}
  .tab.active {{ color: #4a90d9; border-bottom-color: #4a90d9; }}
  .pane {{ display: none; }}
  .pane.active {{ display: block; }}
  .stats {{ display: flex; gap: 16px; padding: 24px 40px; flex-wrap: wrap; }}
  .stat-card {{ background: white; border-radius: 12px; padding: 20px 28px; flex: 1; min-width: 140px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }}
  .stat-card .num {{ font-size: 36px; font-weight: 700; color: #4a90d9; }}
  .stat-card .label {{ font-size: 13px; color: #666; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-card.green .num {{ color: #27ae60; }}
  .stat-card.red .num {{ color: #e74c3c; }}
  .stat-card.orange .num {{ color: #f39c12; }}
  .content {{ padding: 0 40px 40px; }}
  .section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; color: #1a1a2e; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px;
           box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }}
  thead {{ background: #1a1a2e; color: white; }}
  th {{ padding: 14px 16px; text-align: left; font-size: 13px; font-weight: 600; letter-spacing: 0.3px; }}
  td {{ padding: 12px 16px; border-bottom: 1px solid #f0f0f0; font-size: 14px; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8f9ff; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
  .badge-applied {{ background: #d4edda; color: #155724; }}
  .badge-skipped {{ background: #f0f0f0; color: #666; }}
  .badge-rejected {{ background: #f8d7da; color: #721c24; }}
  .badge-found {{ background: #cce5ff; color: #004085; }}
  .badge-error {{ background: #fff3cd; color: #856404; }}
  .score {{ font-weight: 700; }}
  .score-high {{ color: #27ae60; }}
  .score-mid {{ color: #f39c12; }}
  .score-low {{ color: #e74c3c; }}
  .file-link {{ color: #4a90d9; text-decoration: none; font-size: 12px; }}
  .file-link:hover {{ text-decoration: underline; }}
  .reason {{ font-size: 12px; color: #666; margin-top: 4px; max-width: 300px; }}
  .generated {{ text-align: center; padding: 20px; color: #999; font-size: 13px; }}
  /* Settings tab */
  .settings-content {{ padding: 24px 40px 40px; }}
  .settings-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .card {{ background: white; border-radius: 12px; padding: 22px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .card h3 {{ font-size: 14px; font-weight: 700; color: #1a1a2e; margin-bottom: 14px;
              padding-bottom: 10px; border-bottom: 1px solid #f0f0f0; text-transform: uppercase;
              letter-spacing: .5px; }}
  .cfg-row {{ display: flex; justify-content: space-between; align-items: flex-start;
              padding: 8px 0; border-bottom: 1px solid #f9f9f9; font-size: 13px; }}
  .cfg-row:last-child {{ border-bottom: none; }}
  .cfg-key {{ color: #555; flex: 1; }}
  .cfg-val {{ font-weight: 600; color: #1a1a2e; flex: 1; text-align: right; word-break: break-word; }}
  .cfg-val.yes {{ color: #27ae60; }}
  .cfg-val.no {{ color: #e74c3c; }}
  .bl-group {{ margin-bottom: 12px; padding: 10px; background: #fff8f8;
               border-radius: 8px; border-left: 3px solid #e74c3c; }}
  .bl-group h4 {{ font-size: 12px; font-weight: 700; color: #c0392b; margin-bottom: 6px; }}
  .bl-tags {{ display: flex; flex-wrap: wrap; gap: 5px; }}
  .bl-tag {{ background: white; border: 1px solid #ddd; border-radius: 20px;
             padding: 2px 9px; font-size: 11px; color: #555; }}
  .dashboard-link {{ display: inline-block; margin-bottom: 20px; padding: 10px 20px;
                     background: #4a90d9; color: white; border-radius: 8px;
                     text-decoration: none; font-size: 14px; font-weight: 600; }}
  .dashboard-link:hover {{ background: #357abd; }}
  .settings-note {{ font-size: 13px; color: #666; margin-bottom: 20px; padding: 12px 16px;
                    background: #fffbea; border-radius: 8px; border-left: 3px solid #f39c12; }}
</style>
</head>
<body>

<div class="header">
  <h1>Job Applications Report</h1>
  <p>Generated: {generated_at} | Database: {db_path}</p>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('applications', this)">Applications</div>
  <div class="tab" onclick="showTab('settings', this)">Settings</div>
</div>

<!-- APPLICATIONS TAB -->
<div id="tab-applications" class="pane active">
  <div class="stats">
    <div class="stat-card green">
      <div class="num">{total_applied}</div>
      <div class="label">Applied</div>
    </div>
    <div class="stat-card">
      <div class="num">{total_found}</div>
      <div class="label">Found</div>
    </div>
    <div class="stat-card orange">
      <div class="num">{total_skipped}</div>
      <div class="label">Skipped</div>
    </div>
    <div class="stat-card red">
      <div class="num">{total_rejected}</div>
      <div class="label">Rejected</div>
    </div>
    <div class="stat-card">
      <div class="num">{avg_score}</div>
      <div class="label">Avg Score (applied)</div>
    </div>
  </div>

  <div class="content">
    <div class="section-title">All Applications</div>
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Platform</th>
          <th>Title</th>
          <th>Company</th>
          <th>Score</th>
          <th>Status</th>
          <th>Applied At</th>
          <th>Files</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</div>

<!-- SETTINGS TAB -->
<div id="tab-settings" class="pane">
  <div class="settings-content">
    <div class="settings-note">
      This is a read-only snapshot of your current settings.
      To edit settings without touching code, run <code>python dashboard.py</code>.
    </div>
    <a class="dashboard-link" href="http://localhost:5050" target="_blank">Open Dashboard (Settings)</a>

    <div class="settings-grid">
      <div class="card">
        <h3>Search</h3>
        {cfg_search_rows}
      </div>
      <div class="card">
        <h3>Platforms</h3>
        {cfg_platform_rows}
      </div>
      <div class="card">
        <h3>Scheduler &amp; Browser</h3>
        {cfg_scheduler_rows}
      </div>
    </div>

    <div class="section-title" style="margin-top:28px">Company Blacklist</div>
    <div class="card">
      {cfg_blacklist_html}
    </div>
  </div>
</div>

<div class="generated">Auto-generated by Job Search Automation</div>

<script>
function showTab(name, el) {{
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  el.classList.add('active');
}}
</script>

</body>
</html>"""

def _cfg_row(key: str, val) -> str:
    if isinstance(val, bool):
        cls = "yes" if val else "no"
        display = "Yes" if val else "No"
    else:
        cls = ""
        display = str(val)
    return f'<div class="cfg-row"><span class="cfg-key">{key}</span><span class="cfg-val {cls}">{display}</span></div>'


def _build_cfg_search_rows() -> str:
    cfg = SEARCH_CONFIG
    rows = [
        _cfg_row("Min relevance score", cfg.get("min_relevance_score", 70)),
        _cfg_row("Max applications / run", cfg.get("max_applications_per_run", 20)),
        _cfg_row("Posted within (days)", cfg.get("posted_within_days", 1)),
        _cfg_row("Require review", cfg.get("require_review", True)),
        _cfg_row("Skip duplicate companies", cfg.get("skip_duplicate_companies", True)),
        _cfg_row("Remote only", cfg.get("remote_only", True)),
        _cfg_row("Location", cfg.get("location", "United States")),
    ]
    titles = cfg.get("job_titles", [])
    rows.append(f'<div class="cfg-row"><span class="cfg-key">Job titles</span><span class="cfg-val" style="font-size:11px;text-align:right">{", ".join(titles)}</span></div>')
    return "\n".join(rows)


def _build_cfg_platform_rows() -> str:
    rows = []
    for pname, pcfg in PLATFORMS.items():
        enabled = pcfg.get("enabled", False)
        cls = "yes" if enabled else "no"
        status = "On" if enabled else "Off"
        max_j = pcfg.get("max_jobs_to_scrape", 0)
        rows.append(
            f'<div class="cfg-row"><span class="cfg-key">{pname}</span>'
            f'<span class="cfg-val {cls}">{status} (max {max_j})</span></div>'
        )
    return "\n".join(rows)


def _build_cfg_scheduler_rows() -> str:
    rows = [
        _cfg_row("Daily run time", SCHEDULER_CONFIG.get("run_at", "09:00")),
        _cfg_row("Headless browser", BROWSER_CONFIG.get("headless", False)),
    ]
    return "\n".join(rows)


def _build_cfg_blacklist_html() -> str:
    if not COMPANY_BLACKLIST:
        return '<p style="color:#888;font-size:13px">No blacklist entries.</p>'
    html = ""
    for group, names in COMPANY_BLACKLIST.items():
        tags = "".join(f'<span class="bl-tag">{n}</span>' for n in names)
        html += f'<div class="bl-group"><h4>{group}</h4><div class="bl-tags">{tags}</div></div>'
    return html


ROW_TEMPLATE = """<tr>
  <td>{i}</td>
  <td>{platform}</td>
  <td>
    <a href="{url}" target="_blank" style="color:#1a1a2e;font-weight:600;">{title}</a>
    <div class="reason">{reason}</div>
  </td>
  <td>{company}</td>
  <td><span class="score {score_class}">{score}</span></td>
  <td><span class="badge badge-{status}">{status}</span></td>
  <td>{applied_at}</td>
  <td>
    {resume_link}
    {cl_link}
  </td>
  <td style="font-size:12px;color:#666;">{notes}</td>
</tr>"""


def generate_report(db_path: str = None, output_path: str = None) -> str:
    db_path = db_path or PATHS["database"]
    report_dir = Path(PATHS["report_dir"])
    report_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_path or str(report_dir / f"report_{ts}.html")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        jobs = conn.execute("""
            SELECT * FROM jobs ORDER BY
              CASE status WHEN 'applied' THEN 0 WHEN 'reviewing' THEN 1 WHEN 'found' THEN 2 ELSE 3 END,
              relevance_score DESC
        """).fetchall()

        stats_raw = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in stats_raw}

        avg_row = conn.execute(
            "SELECT AVG(relevance_score) FROM jobs WHERE status = 'applied'"
        ).fetchone()
        avg_score = f"{avg_row[0]:.0f}" if avg_row[0] else "—"

    rows_html = ""
    for i, job in enumerate(jobs, 1):
        score = job["relevance_score"] or 0
        score_class = "score-high" if score >= 80 else "score-mid" if score >= 60 else "score-low"
        score_str = f"{score:.0f}" if score else "—"

        resume_link = ""
        if job["resume_path"] and Path(job["resume_path"]).exists():
            resume_link = f'<a class="file-link" href="file://{job["resume_path"]}">📄 Resume</a><br>'

        cl_link = ""
        if job["cover_letter_path"] and Path(job["cover_letter_path"]).exists():
            cl_link = f'<a class="file-link" href="file://{job["cover_letter_path"]}">✉ Cover Letter</a>'

        applied_at = (job["applied_at"] or "")[:16]
        reason = (job["relevance_reason"] or "")[:120]

        rows_html += ROW_TEMPLATE.format(
            i=i,
            platform=job["platform"],
            title=job["title"],
            company=job["company"],
            url=job["url"] or "#",
            score=score_str,
            score_class=score_class,
            status=job["status"],
            applied_at=applied_at,
            resume_link=resume_link,
            cl_link=cl_link,
            notes=job["notes"] or "",
            reason=reason,
        )

    html = HTML_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        db_path=db_path,
        total_applied=stats.get("applied", 0),
        total_found=stats.get("found", 0),
        total_skipped=stats.get("skipped", 0),
        total_rejected=stats.get("rejected", 0),
        avg_score=avg_score,
        rows=rows_html,
        cfg_search_rows=_build_cfg_search_rows(),
        cfg_platform_rows=_build_cfg_platform_rows(),
        cfg_scheduler_rows=_build_cfg_scheduler_rows(),
        cfg_blacklist_html=_build_cfg_blacklist_html(),
    )

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
