"""Loopback-only audit reviewing UI (stdlib HTTP server, no frontend build)."""

from __future__ import annotations

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .orchestrator import model_slug
from .reports import parse_report, validate_locations
from .scoring import leaderboard
from .storage import load_json, save_json


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RepoReview Arena</title>
<style>
:root{color-scheme:dark;--bg:#0b1120;--panel:#121d30;--line:#28384e;--muted:#91a4b9;--ink:#eaf1fa;--accent:#67e8f9;--gold:#f5c773;--red:#fb7185}
*{box-sizing:border-box}body{font:15px/1.5 ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--ink);margin:0}main{max-width:1250px;margin:auto;padding:36px 24px 70px}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;padding-bottom:28px;border-bottom:1px solid var(--line)}h1{font-size:29px;letter-spacing:-.04em;margin:0}h2{font-size:19px;margin:0 0 14px}h3{font-size:16px;margin:0 0 6px}small,.muted{color:var(--muted)}.eyebrow{font-size:11px;letter-spacing:.19em;color:var(--accent);font-weight:800;text-transform:uppercase}
section{margin-top:32px}.meta{font-size:12px;color:var(--muted)}.meta b{color:var(--ink)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(205px,1fr));gap:14px}
.panel,.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}.card h3{font-size:22px}.total{font-size:37px;font-weight:780;letter-spacing:-.06em}.gold{color:var(--gold)}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px, 1.15fr);gap:20px}.row{display:flex;flex-wrap:wrap;align-items:center;gap:10px;justify-content:space-between}.pill{padding:3px 8px;border-radius:6px;font-size:11px;background:#25364e;color:#bad5ea}.p0{color:#ff697c}.p1{color:#ffb376}.p2{color:#e4d58b}.p3{color:#9cc6dd}
button,select,input,textarea{border:1px solid #48627d;background:#16263c;color:var(--ink);border-radius:7px;font:inherit;padding:7px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}button.primary{background:#155b67;border-color:#237e8e}input[type=number]{width:70px}label{font-size:12px;color:var(--muted)}.finding{padding:15px 0;border-top:1px solid var(--line)}.finding:first-child{border-top:0}p{margin:6px 0 12px}.code{font-family:ui-monospace,monospace;color:#8bd6e8;font-size:12px;overflow-wrap:anywhere}.detail{font-size:13px;color:#c5d1df;white-space:pre-wrap}.formrow{display:flex;align-items:end;flex-wrap:wrap;gap:10px;margin-top:12px}.formrow>div{display:flex;flex-direction:column;gap:4px}
table{border-collapse:collapse;width:100%}td,th{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left}th{font-size:11px;color:var(--muted);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}tr:first-child td{font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}#message{margin-top:12px;color:var(--accent)}
@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:22px 13px}.panel{padding:14px}}
</style></head><body><main>
<header><div><div class="eyebrow">Independent LLM code audits</div><h1>RepoReview <span class="gold">Arena</span></h1><div id="project" class="meta">Loading competition…</div></div><div><button onclick="reload()">Refresh</button> <button onclick="exportJson()">Export scorecard</button></div></header>
<section><div class="eyebrow">Leaderboard</div><h2>Verified results only</h2><div id="summary" class="cards"></div></section>
<section class="panel"><h2>Scoreboard</h2><div style="overflow:auto"><table id="board"></table></div><p class="muted">Unreviewed findings score zero. Confirmed issues score by reviewed severity. Penalties apply for false positives.</p></section>
<section><div class="eyebrow">Value proposals</div><h2>Golden feature contenders</h2><div id="goldpodium" class="cards"></div></section>
<section class="grid"><div><div class="panel"><h2>Review a contestant</h2><div class="formrow"><div><label>Model</label><select id="model" onchange="showReport()"></select></div></div><p id="abstract" class="muted"></p><h3>Golden feature</h3><div id="gold"></div><h3 style="margin-top:22px">Rubric scores</h3><div class="formrow"><div><label>Improvements /15</label><input type="number" id="improvements" min="0" max="15"></div><div><label>Gold /20</label><input type="number" id="goldscore" min="0" max="20"></div><div><label>Rigor /5</label><input type="number" id="rigor" min="0" max="5"></div><button class="primary" onclick="saveRubric()">Save scores</button></div></div>
<div class="panel" style="margin-top:20px"><h2>Import manual report</h2><p class="muted">JSON report produced in any IDE or imported from another system. The model ID may contain only letters, digits, underscores and hyphens.</p><div class="formrow"><input id="importmodel" placeholder="model-id" aria-label="Model ID"><input type="file" id="importfile" accept=".json,.md,.txt"><button onclick="importReport()">Import</button></div><div id="message"></div></div></div>
<div class="panel"><div class="row"><h2>Findings & adjudication</h2><span id="count" class="pill"></span></div><div id="findings"></div></div></section>
</main><script>
let state={};const token=__TOKEN__;
function node(t,klass,text){const e=document.createElement(t);if(klass)e.className=klass;if(text!==undefined)e.textContent=String(text);return e}
function clear(id){const x=document.getElementById(id);x.replaceChildren();return x}
function option(sel,v,t){const x=node('option','',t);x.value=v;sel.append(x)}
async function api(path,payload){const r=await fetch(path,{method:payload?'POST':'GET',headers:{'Content-Type':'application/json','X-Arena-Token':token},body:payload?JSON.stringify(payload):undefined});const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed');return data}
async function reload(){state=await api('/api/data');document.getElementById('project').textContent=state.manifest.repo_name+' · '+state.manifest.commit.slice(0,12)+' · '+state.manifest.file_count+' reviewed files';const old=document.getElementById('model').value;const s=clear('model');Object.keys(state.reports).sort().forEach(k=>option(s,k,k));if(old&&state.reports[old])s.value=old;renderBoard();showReport()}
function renderBoard(){const rows=state.leaderboard;const top=clear('summary');for(const [title,val] of [['Contestants',rows.length],['Validated findings',rows.reduce((n,r)=>n+r.confirmed,0)],['Pending review',rows.reduce((n,r)=>n+r.pending,0)],['Top score',rows[0]?.total??0]]){const d=node('div','card');d.append(node('div','eyebrow',title),node('div','total',val));top.append(d)}let t=clear('board');const head=node('tr');for(const x of ['Rank','Model','Bugs','Improve','Gold','Rigor','Penalties','Total','Pending','Tokens'])head.append(node('th','',x));t.append(head);rows.forEach((r,i)=>{const row=node('tr');for(const [j,x] of [i+1,r.model,r.bugs,r.improvements,r.gold,r.rigor,r.penalty,r.total,r.pending,(state.reports[r.model].audit_metadata?.prompt_tokens||0)+(state.reports[r.model].audit_metadata?.completion_tokens||0)].entries())row.append(node('td',j>1?'num':'',x));t.append(row)});const g=clear('goldpodium');for(const r of [...rows].sort((a,b)=>b.gold-a.gold)){const d=node('div','card');d.append(node('div','eyebrow',r.model+' · '+r.gold+'/20'),node('h3','gold',r.golden_feature),node('p','muted',state.reports[r.model].golden_feature.mechanism||''));g.append(d)}}
function showReport(){const id=document.getElementById('model').value;const rep=state.reports[id];if(!rep){clear('findings');return}const j=state.judgments[id]||{};document.getElementById('abstract').textContent=rep.summary||'No summary';const gold=clear('gold');gold.append(node('h3','gold',rep.golden_feature.name));gold.append(node('p','muted',rep.golden_feature.mechanism||''));for(const field of ['improvements','goldscore','rigor'])document.getElementById(field).value=j.rubric?.[field==='goldscore'?'gold':field]||0;document.getElementById('count').textContent=rep.findings.length+' findings';const root=clear('findings');for(const f of rep.findings){const el=node('article','finding');const head=node('div','row');head.append(node('h3','',f.id+' — '+f.title),node('span','pill '+f.severity.toLowerCase(),f.severity+' · '+f.evidence_status));el.append(head,node('div','code',f.file+':'+f.line),node('p','detail',f.description),node('p','detail','Evidence: '+f.evidence),node('p','detail','Fix: '+f.recommendation));const controls=node('div','formrow');const status=node('div');status.append(node('label','','Judgment'));const sel=node('select');for(const x of ['pending','confirmed','false_positive'])option(sel,x,x.replace('_',' '));const prev=j.findings?.[f.id]||{};sel.value=prev.verdict||'pending';status.append(sel);const severity=node('div');severity.append(node('label','','Reviewed severity'));const sv=node('select');for(const x of ['P0','P1','P2','P3'])option(sv,x,x);sv.value=prev.severity||f.severity;severity.append(sv);const keybox=node('div');keybox.append(node('label','','Issue key (dedupe)'));const key=node('input');key.placeholder='optional canonical ID';key.value=prev.issue_key||'';keybox.append(key);const fab=node('div');fab.append(node('label','','Fabricated evidence'));const fabricated=node('input');fabricated.type='checkbox';fabricated.checked=!!prev.fabricated_evidence;fab.append(fabricated);const save=node('button','primary','Save');save.onclick=async()=>{try{await api('/api/adjudicate',{model:id,finding:f.id,verdict:sel.value,severity:sv.value,issue_key:key.value,fabricated_evidence:fabricated.checked});await reload()}catch(e){alert(e.message)}};controls.append(status,severity,keybox,fab,save);el.append(controls);root.append(el)}}
async function saveRubric(){try{await api('/api/rubric',{model:document.getElementById('model').value,improvements:+document.getElementById('improvements').value,gold:+document.getElementById('goldscore').value,rigor:+document.getElementById('rigor').value});await reload()}catch(e){alert(e.message)}}
async function importReport(){const file=document.getElementById('importfile').files[0];const id=document.getElementById('importmodel').value;if(!file)return;try{await api('/api/import',{model:id,content:await file.text()});document.getElementById('message').textContent='Report imported';await reload()}catch(e){document.getElementById('message').textContent=e.message}}
function exportJson(){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));a.download='arena-scoreboard.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),3000)}
reload().catch(e=>document.getElementById('project').textContent=e.message);
</script></body></html>"""


def _data(workspace: Path) -> dict:
    reports = {p.stem: load_json(p) for p in sorted((workspace / "reports").glob("*.json"))}
    judgments = {p.stem: load_json(p) for p in sorted((workspace / "judgments").glob("*.json"))}
    return {
        "manifest": load_json(workspace / "manifest.json"),
        "reports": reports,
        "judgments": judgments,
        "leaderboard": leaderboard(reports, judgments),
    }


def serve(workspace: Path, port: int = 8765) -> None:
    workspace = workspace.resolve()
    if not (workspace / "manifest.json").exists():
        raise FileNotFoundError("Workspace missing manifest.json")
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, content: bytes, mime: str):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def _json(self, status: int, payload: dict):
            self._reply(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def _host_allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{port}"

        def do_GET(self):  # noqa: N802
            if not self._host_allowed():
                self._json(403, {"error": "Invalid host"})
                return
            if self.path == "/":
                page = PAGE.replace("__TOKEN__", json.dumps(token))
                self._reply(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/data":
                self._json(200, _data(workspace))
            else:
                self._json(404, {"error": "Not found"})

        def do_POST(self):  # noqa: N802
            if not self._host_allowed():
                self._json(403, {"error": "Invalid host"})
                return
            origin = self.headers.get("Origin", "")
            if self.headers.get("X-Arena-Token") != token or (
                origin and origin != f"http://127.0.0.1:{port}"
            ):
                self._json(403, {"error": "Forbidden"})
                return
            if self.path not in ("/api/rubric", "/api/adjudicate", "/api/import"):
                self._json(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "Invalid content length"})
                return
            if length <= 0 or length > 2_000_000:
                self._json(413, {"error": "Invalid upload size"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
                model = model_slug(payload["model"])
                reports = _data(workspace)["reports"]
                if self.path == "/api/import":
                    if model in reports:
                        raise ValueError("Model already has a report; use a new model ID")
                    manifest = load_json(workspace / "manifest.json")
                    approved = {f["path"] for f in manifest["files"]}
                    parsed = parse_report(payload["content"], approved)
                    validate_locations(parsed, workspace / "snapshot")
                    parsed["audit_metadata"] = {"model_id": model, "source": "manual"}
                    save_json(workspace / "reports" / f"{model}.json", parsed)
                else:
                    if model not in reports:
                        raise ValueError("Unknown model")
                    path = workspace / "judgments" / f"{model}.json"
                    decision = load_json(path) if path.exists() else {"findings": {}, "rubric": {}}
                    if self.path == "/api/rubric":
                        scores = {k: payload[k] for k in ("improvements", "gold", "rigor")}
                        for key, maximum in (("improvements", 15), ("gold", 20), ("rigor", 5)):
                            if type(scores[key]) is not int or not 0 <= scores[key] <= maximum:
                                raise ValueError(f"Invalid {key} score")
                        decision["rubric"] = scores
                    else:
                        finding = payload["finding"]
                        if finding not in {f["id"] for f in reports[model]["findings"]}:
                            raise ValueError("Unknown finding")
                        if payload["verdict"] not in ("pending", "confirmed", "false_positive"):
                            raise ValueError("Invalid verdict")
                        if payload["severity"] not in ("P0", "P1", "P2", "P3"):
                            raise ValueError("Invalid severity")
                        issue_key = str(payload.get("issue_key", ""))
                        if len(issue_key) > 100:
                            raise ValueError("Issue key too long")
                        decision.setdefault("findings", {})[finding] = {
                            "verdict": payload["verdict"], "severity": payload["severity"],
                            "issue_key": issue_key,
                            "fabricated_evidence": payload.get("fabricated_evidence") is True,
                        }
                    save_json(path, decision)
                self._json(200, {"ok": True})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": html.escape(str(exc))})

        def log_message(self, fmt, *args):
            pass  # avoid persisting file paths or report content in server logs

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"RepoReview Arena: http://127.0.0.1:{port}")
        print("Loopback-only dashboard. Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
