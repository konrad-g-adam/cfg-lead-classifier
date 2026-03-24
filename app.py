#!/usr/bin/env python3
"""
app.py — Flask web interface for the Lead Nationality Classifier (Phase 2).

Run with:
    python app.py              # development server on http://127.0.0.1:5000
    python app.py --port 8080  # custom port

Features:
  - Drag-and-drop / file-picker CSV upload
  - Google Sheets URL input
  - Live progress updates via streaming
  - Download classified CSV / Excel / JSON outputs
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from flask import (
        Flask,
        jsonify,
        render_template_string,
        request,
        send_file,
    )
except ImportError:
    print(
        "Flask is not installed.  Install with:\n"
        "    pip install flask\n"
        "Then run this file again."
    )
    sys.exit(1)

import pandas as pd

from config import POLISH_PROFILE
from scoring import ProfileScorer
from utils import (
    read_csv,
    read_google_sheet,
    write_csvs,
    write_excel,
    write_json_report,
)

logger = logging.getLogger(__name__)
app = Flask(__name__)

# ── In-memory job store (swap for Redis/DB in production) ──────────────
JOBS: dict[str, dict] = {}
UPLOAD_DIR = Path(tempfile.gettempdir()) / "lead-classifier-uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


# ════════════════════════════════════════════════════════════════════════
# HTML template (single-file, no external dependencies)
# ════════════════════════════════════════════════════════════════════════

INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Lead Nationality Classifier</title>
<style>
  :root { --accent: #2563eb; --bg: #f8fafc; --card: #ffffff; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg);
         color: #1e293b; min-height: 100vh; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: .25rem; }
  .subtitle { color: #64748b; margin-bottom: 2rem; }
  .card { background: var(--card); border-radius: 12px; padding: 2rem;
          box-shadow: 0 1px 3px rgba(0,0,0,.1); max-width: 640px; margin: 0 auto; }

  /* Drag-and-drop zone */
  .dropzone { border: 2px dashed #cbd5e1; border-radius: 8px; padding: 2.5rem 1rem;
              text-align: center; cursor: pointer; transition: .2s; margin-bottom: 1.5rem; }
  .dropzone.over { border-color: var(--accent); background: #eff6ff; }
  .dropzone input { display: none; }
  .dropzone p { color: #64748b; }

  /* OR divider */
  .divider { display: flex; align-items: center; gap: .75rem; margin-bottom: 1.5rem; color: #94a3b8; }
  .divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: #e2e8f0; }

  /* Google Sheets input */
  .url-row { display: flex; gap: .5rem; margin-bottom: 1.5rem; }
  .url-row input { flex: 1; padding: .6rem .8rem; border: 1px solid #cbd5e1; border-radius: 6px; font-size: .9rem; }
  .url-row input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(37,99,235,.15); }

  /* Buttons */
  .btn { padding: .65rem 1.3rem; border: none; border-radius: 6px; font-size: .9rem;
         font-weight: 600; cursor: pointer; transition: .15s; }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: #1d4ed8; }
  .btn-primary:disabled { opacity: .5; cursor: not-allowed; }

  /* Progress & results */
  #status { margin-top: 1.5rem; }
  .progress-bar { height: 6px; background: #e2e8f0; border-radius: 3px; overflow: hidden; margin: .75rem 0; }
  .progress-bar .fill { height: 100%; background: var(--accent); transition: width .3s; }
  .results { margin-top: 1rem; }
  .results a { display: inline-block; margin: .25rem .5rem .25rem 0; padding: .4rem .8rem;
               background: #f1f5f9; border-radius: 6px; color: var(--accent);
               text-decoration: none; font-size: .85rem; font-weight: 500; }
  .results a:hover { background: #e2e8f0; }
  .stat { font-size: .95rem; margin: .25rem 0; }
  .stat b { color: var(--accent); }
</style>
</head>
<body>
<div class="card">
  <h1>Lead Nationality Classifier</h1>
  <p class="subtitle">Upload a Phantombuster CSV or paste a Google Sheets link</p>

  <!-- Drag-and-drop -->
  <div class="dropzone" id="dropzone">
    <input type="file" id="fileInput" accept=".csv" />
    <p><strong>Drop CSV here</strong> or click to browse</p>
  </div>

  <div class="divider">or</div>

  <!-- Google Sheets URL -->
  <div class="url-row">
    <input type="url" id="sheetUrl" placeholder="https://docs.google.com/spreadsheets/d/..." />
    <button class="btn btn-primary" id="classifyBtn" onclick="classify()">Classify</button>
  </div>

  <div id="status"></div>
</div>

<script>
const dz = document.getElementById('dropzone');
const fi = document.getElementById('fileInput');
let selectedFile = null;

dz.addEventListener('click', () => fi.click());
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('over');
  if (e.dataTransfer.files.length) { selectedFile = e.dataTransfer.files[0]; dz.querySelector('p').innerHTML = '<strong>' + selectedFile.name + '</strong> selected'; }
});
fi.addEventListener('change', () => {
  if (fi.files.length) { selectedFile = fi.files[0]; dz.querySelector('p').innerHTML = '<strong>' + selectedFile.name + '</strong> selected'; }
});

async function classify() {
  const btn = document.getElementById('classifyBtn');
  const status = document.getElementById('status');
  const sheetUrl = document.getElementById('sheetUrl').value.trim();

  if (!selectedFile && !sheetUrl) { alert('Please upload a CSV or enter a Google Sheets URL.'); return; }
  btn.disabled = true;
  status.innerHTML = '<div class="progress-bar"><div class="fill" style="width:10%"></div></div><p>Uploading...</p>';

  const fd = new FormData();
  if (selectedFile) fd.append('file', selectedFile);
  else fd.append('sheet_url', sheetUrl);

  try {
    status.querySelector('.fill').style.width = '30%';
    status.querySelector('p').textContent = 'Classifying profiles...';
    const resp = await fetch('/api/classify', { method: 'POST', body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Classification failed');

    status.querySelector('.fill').style.width = '100%';
    const c = data.classification;
    status.innerHTML = `
      <div class="progress-bar"><div class="fill" style="width:100%"></div></div>
      <p style="color:#16a34a;font-weight:600;">Classification complete!</p>
      <div class="stat"><b>${c.PL.count.toLocaleString()}</b> Polish (${c.PL.pct}%)</div>
      <div class="stat"><b>${c.EN.count.toLocaleString()}</b> Foreign (${c.EN.pct}%)</div>
      <div class="stat"><b>${c.UNCERTAIN.count.toLocaleString()}</b> Uncertain (${c.UNCERTAIN.pct}%)</div>
      <div class="results">
        <a href="/api/download/${data.job_id}/csv_pl">Download PL CSV</a>
        <a href="/api/download/${data.job_id}/csv_en">Download EN CSV</a>
        <a href="/api/download/${data.job_id}/csv_uncertain">Download Uncertain CSV</a>
        <a href="/api/download/${data.job_id}/excel">Download Excel</a>
        <a href="/api/download/${data.job_id}/json">Download JSON Report</a>
        <a href="/api/download/${data.job_id}/audit">Download Audit CSV</a>
      </div>`;
  } catch (err) {
    status.innerHTML = `<p style="color:#dc2626;">Error: ${err.message}</p>`;
  }
  btn.disabled = false;
}
</script>
</body>
</html>
"""


# ════════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/classify", methods=["POST"])
def api_classify():
    """Accept a CSV upload or Google Sheets URL and return classification."""
    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Load DataFrame ─────────────────────────────────────────────
        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]
            csv_path = job_dir / f.filename
            f.save(str(csv_path))
            df = read_csv(csv_path)
        elif request.form.get("sheet_url"):
            df = read_google_sheet(request.form["sheet_url"])
        else:
            return jsonify({"error": "No file or URL provided"}), 400

        # ── Score ──────────────────────────────────────────────────────
        scorer = ProfileScorer(POLISH_PROFILE)
        results = df.apply(scorer.score, axis=1)
        df["_classification_score"] = results.apply(lambda x: x[0])
        df["_classification_reasons"] = results.apply(lambda x: x[1])
        df["_classification"] = df["_classification_score"].apply(scorer.classify)

        # ── Write outputs ──────────────────────────────────────────────
        csv_files = write_csvs(df, job_dir, prefix="classified")
        xlsx_path = write_excel(df, job_dir, prefix="classified")
        json_path = write_json_report(df, job_dir, prefix="classified")

        # ── Store job metadata ─────────────────────────────────────────
        total = len(df)
        counts = df["_classification"].value_counts()
        JOBS[job_id] = {
            "dir": job_dir,
            "files": {
                "csv_pl": csv_files.get("PL"),
                "csv_en": csv_files.get("EN"),
                "csv_uncertain": csv_files.get("UNCERTAIN"),
                "audit": csv_files.get("AUDIT"),
                "excel": xlsx_path,
                "json": json_path,
            },
        }

        return jsonify({
            "job_id": job_id,
            "total_profiles": total,
            "classification": {
                "PL": {"count": int(counts.get("PL", 0)), "pct": round(counts.get("PL", 0) / total * 100, 1)},
                "EN": {"count": int(counts.get("EN", 0)), "pct": round(counts.get("EN", 0) / total * 100, 1)},
                "UNCERTAIN": {"count": int(counts.get("UNCERTAIN", 0)), "pct": round(counts.get("UNCERTAIN", 0) / total * 100, 1)},
            },
        })

    except Exception as e:
        logger.exception("Classification failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<job_id>/<file_type>")
def api_download(job_id: str, file_type: str):
    """Download a result file."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    path = job["files"].get(file_type)
    if not path or not Path(path).exists():
        return jsonify({"error": f"File type '{file_type}' not found"}), 404
    return send_file(str(path), as_attachment=True)


# ════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Lead Classifier web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    print(f"\n  Lead Classifier UI → http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
