"""Loopback-only audit reviewing UI (stdlib HTTP server, no frontend build)."""

from __future__ import annotations

import asyncio
import html
import json
import math
import secrets
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .orchestrator import audit_many, model_slug
from .reports import parse_report, validate_locations
from .scoring import leaderboard
from .storage import load_json, save_json
from .workspace import (
    DEFAULT_FOCUS,
    FOCUS_PRIORITY_LABELS,
    MAX_CUSTOM_FOCUS_LENGTH,
    MAX_FOCUS_LENGTH,
    REVIEW_FOCUS_CRITERIA,
    format_review_focus,
    initialize_workspace,
    validate_workspace,
)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RepoReview Arena</title>
<style>
:root{color-scheme:dark;--bg:#0b1120;--panel:#121d30;--line:#28384e;--muted:#91a4b9;--ink:#eaf1fa;--accent:#67e8f9;--gold:#f5c773;--red:#fb7185}
*{box-sizing:border-box}body{font:15px/1.5 ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--ink);margin:0}main{max-width:1250px;margin:auto;padding:36px 24px 70px}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;padding-bottom:28px;border-bottom:1px solid var(--line)}.header-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.language-label{font-size:12px;color:var(--muted)}.language-toggle{display:inline-flex;gap:2px;padding:3px;border:1px solid #48627d;border-radius:9px;background:#0b1526}.language-toggle button{min-width:42px;padding:5px 9px;border:0;background:transparent;color:var(--muted)}.language-toggle button[aria-pressed="true"]{background:#155b67;color:var(--ink);box-shadow:inset 0 0 0 1px #237e8e}.language-toggle button:focus-visible{position:relative;z-index:1;outline:2px solid var(--accent);outline-offset:2px}.field-browse{margin-left:auto;padding:3px 8px;font-size:11px;white-space:nowrap}.folder-dialog{width:min(680px,calc(100vw - 32px));max-width:calc(100vw - 32px);max-height:82vh;padding:20px;border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--ink)}.folder-dialog::backdrop{background:#020617bb;backdrop-filter:blur(2px)}.folder-dialog p{margin:10px 0}.folder-current-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:12px 0 6px}.folder-current-row code{flex:1;min-width:180px;overflow-wrap:anywhere;background:#0a1220;border:1px solid var(--line);border-radius:7px;padding:8px;color:#8bd6e8}.folder-list{list-style:none;margin:12px 0;padding:0;max-height:38vh;overflow:auto;border:1px solid var(--line);border-radius:8px}.folder-entry{width:100%;display:flex;justify-content:space-between;align-items:center;gap:12px;text-align:left;border:0;border-bottom:1px solid var(--line);border-radius:0;background:transparent;padding:10px}.folder-entry:hover{background:#1b2b42;border-color:var(--line)}.folder-badge{font-size:11px;color:var(--muted);white-space:nowrap}.folder-dialog-actions{display:flex;justify-content:flex-end;gap:8px}h1{font-size:29px;letter-spacing:-.04em;margin:0}h2{font-size:19px;margin:0 0 14px}h3{font-size:16px;margin:0 0 6px}small,.muted{color:var(--muted)}.eyebrow{font-size:11px;letter-spacing:.19em;color:var(--accent);font-weight:800;text-transform:uppercase}
section{margin-top:32px}.meta{font-size:12px;color:var(--muted)}.meta b{color:var(--ink)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(205px,1fr));gap:14px}
.panel,.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}.card h3{font-size:22px}.total{font-size:37px;font-weight:780;letter-spacing:-.06em}.gold{color:var(--gold)}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px, 1.15fr);gap:20px}.row{display:flex;flex-wrap:wrap;align-items:center;gap:10px;justify-content:space-between}.pill{padding:3px 8px;border-radius:6px;font-size:11px;background:#25364e;color:#bad5ea}.p0{color:#ff697c}.p1{color:#ffb376}.p2{color:#e4d58b}.p3{color:#9cc6dd}
button,select,input,textarea{border:1px solid #48627d;background:#16263c;color:var(--ink);border-radius:7px;font:inherit;padding:7px 10px}button{cursor:pointer}button:hover{border-color:var(--accent)}button:disabled{opacity:.5;cursor:not-allowed}button.primary{background:#155b67;border-color:#237e8e}input[type=number]{width:78px}label{font-size:12px;color:var(--muted)}.finding{padding:15px 0;border-top:1px solid var(--line)}.finding:first-child{border-top:0}p{margin:6px 0 12px}.code{font-family:ui-monospace,monospace;color:#8bd6e8;font-size:12px;overflow-wrap:anywhere}.detail{font-size:13px;color:#c5d1df;white-space:pre-wrap}.formrow{display:flex;align-items:end;flex-wrap:wrap;gap:10px;margin-top:12px}.formrow>div{display:flex;flex-direction:column;gap:4px}.field{display:flex;flex-direction:column;gap:5px;min-width:220px;flex:1}.pathinput{width:100%}.field textarea{min-height:74px;resize:vertical}.model-choice{display:flex;align-items:center;gap:8px;margin:7px 0}.model-choice input{margin:0}.job-outcomes{padding-left:20px;color:var(--muted)}.promptbox{max-height:420px;overflow:auto;background:#0a1220;border:1px solid var(--line);border-radius:8px;padding:14px}
table{border-collapse:collapse;width:100%}td,th{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left}th{font-size:11px;color:var(--muted);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}tr:first-child td{font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}#message{margin-top:12px;color:var(--accent)}
@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:22px 13px}.panel{padding:14px}}
.field-heading{display:flex;align-items:center;gap:7px}.help-wrap{position:relative;display:inline-flex;align-items:center}.help-tip{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;padding:0;border-radius:50%;font-size:11px;font-weight:700;line-height:1}.tooltip-content{display:none;position:absolute;left:0;top:calc(100% + 8px);z-index:30;width:min(280px,calc(100vw - 48px));padding:9px 11px;border:1px solid #48627d;border-radius:8px;background:#09111f;color:var(--ink);font-size:12px;font-weight:400;line-height:1.45;text-align:left;white-space:normal;box-shadow:0 8px 24px #0008}.help-wrap:hover .tooltip-content,.help-wrap:focus-within .tooltip-content{display:block}.help-wrap.tip-right .tooltip-content{left:auto;right:0}.field-description{font-size:12px;color:var(--muted);line-height:1.4}
.focus-config{grid-column:1/-1;width:100%;min-width:0;border:1px solid var(--line);border-radius:10px;padding:12px;margin:16px 0 12px}.focus-config legend{padding:0 6px;font-weight:700}.focus-config legend .help-wrap{margin-left:4px}.focus-criteria-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:10px 0 14px}.focus-criterion{min-width:0;border:1px solid var(--line);border-radius:8px;background:#101a2b;padding:10px}.focus-criterion .field-heading{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:5px 6px}.focus-criterion label{grid-column:1;grid-row:1;color:var(--ink);line-height:1.35}.focus-criterion .help-wrap{grid-column:2;grid-row:1}.focus-criterion .tooltip-content{left:auto;right:0}.focus-priority-level{grid-column:1/-1;grid-row:2;color:var(--accent);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}.priority-slider{display:block;width:100%;height:26px;margin:7px 0 0;padding:0;border:0;background:transparent;accent-color:var(--accent);cursor:pointer}.priority-slider:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}.custom-focus-field{max-width:100%}.focus-submit{grid-column:1/-1;display:flex;justify-content:flex-end}
@media(max-width:1050px){.focus-criteria-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:700px){.focus-criteria-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:460px){.focus-criteria-grid{grid-template-columns:1fr}}
@media(min-width:801px) and (max-width:1100px){.help-wrap.tip-right .tooltip-content{left:0;right:auto}}
@media(max-width:800px){.tooltip-content{width:min(220px,calc(100vw - 48px))}.help-wrap.tip-right .tooltip-content{left:0;right:auto}}
</style></head><body><main>
<header><div><div class="eyebrow" data-i18n="independentAudits">Independent LLM code audits</div><h1>RepoReview <span class="gold">Arena</span></h1><div id="project" class="meta" data-i18n="loadingCompetition">Loading competition…</div></div><div class="header-actions"><span id="language-label" class="language-label" data-i18n="languageLabel">Language</span><div id="language-toggle" class="language-toggle" role="group" aria-labelledby="language-label"><button type="button" class="language-option" data-language="en" aria-pressed="true">EN</button><button type="button" class="language-option" data-language="pt-BR" aria-pressed="false">PT-BR</button></div><button data-i18n="refresh" onclick="reload()">Refresh</button><button data-i18n="exportScorecard" onclick="exportJson()">Export scorecard</button></div></header>
<section class="panel" id="workspace-panel">
<div class="row"><div><div class="eyebrow" data-i18n="localWorkspace">Local workspace</div><h2 data-i18n="openOrCreateArena">Open or create an arena</h2></div><span id="workspace-active" class="meta" data-i18n="noArenaSelected">No arena selected</span></div>
<p class="muted" data-i18n="workspaceIntro">Open an existing arena to continue, or create one from a clean local Git checkout.</p>
<div class="grid">
<div><h3 data-i18n="openExistingArena">Open an existing arena</h3><div class="formrow">
<div class="field"><div class="field-heading"><label for="workspace-path" data-i18n="workspacePath">Workspace path</label><span class="help-wrap"><button class="help-tip" type="button" data-i18n-aria-label="helpWorkspacePath" aria-describedby="workspace-path-help">?</button><span class="tooltip-content" id="workspace-path-help" role="tooltip" data-i18n="workspacePathHelp">Path to an existing RepoReview Arena folder containing manifest.json. Reports, the frozen snapshot, prompt, and judgments are stored here. Relative paths use the directory where you started reporeview web.</span></span><button class="field-browse" type="button" data-i18n="browseFolder" data-i18n-title="browseFolderTitle" title="Browse for an existing arena" onclick="openFolderPicker()">Browse</button></div><input id="workspace-path" class="pathinput" aria-describedby="workspace-path-help" data-i18n-placeholder="workspacePathPlaceholder" placeholder="Path to arena folder"><small class="field-description" data-i18n="workspacePathDescription">Existing arena folder; it must contain manifest.json.</small></div>
<button class="primary" data-i18n="openArena" data-i18n-title="openArenaTitle" title="Load the existing arena at this path." onclick="openWorkspace()">Open arena</button></div></div>
<div><h3 data-i18n="createNewArena">Create a new arena</h3><div class="formrow">
<div class="field"><div class="field-heading"><label for="repo-path" data-i18n="gitRepositoryRoot">Git repository root</label><span class="help-wrap"><button class="help-tip" type="button" data-i18n-aria-label="helpGitRepositoryRoot" aria-describedby="repo-path-help">?</button><span class="tooltip-content" id="repo-path-help" role="tooltip" data-i18n="gitRepositoryRootHelp">Path to the root of the local Git checkout you want to audit. The checkout must be clean; the Arena snapshots committed files from this repository.</span></span></div><input id="repo-path" class="pathinput" aria-describedby="repo-path-help" data-i18n-placeholder="gitRepositoryPlaceholder" placeholder="Path to local Git repository"><small class="field-description" data-i18n="gitRepositoryRootDescription">Root folder of a clean local Git checkout.</small></div>
<div class="field"><div class="field-heading"><label for="out-path" data-i18n="newWorkspacePath">New workspace path</label><span class="help-wrap tip-right"><button class="help-tip" type="button" data-i18n-aria-label="helpNewWorkspacePath" aria-describedby="out-path-help">?</button><span class="tooltip-content" id="out-path-help" role="tooltip" data-i18n="newWorkspacePathHelp">Path to a new folder for this arena. It must not already exist. The Arena writes the manifest, frozen source snapshot, generated prompt, reports, and judgments here. Relative paths use the directory where you started reporeview web.</span></span></div><input id="out-path" class="pathinput" aria-describedby="out-path-help" data-i18n-placeholder="newWorkspacePathPlaceholder" placeholder="Path to new arena folder"><small class="field-description" data-i18n="newWorkspacePathDescription">A new folder where this arena's files will be saved.</small></div>
</div>
</div>
<fieldset class="focus-config" aria-describedby="review-focus-guidance">
<legend><span data-i18n="reviewFocus">Review focus</span><span class="help-wrap"><button class="help-tip" type="button" data-i18n-aria-label="helpReviewFocus" aria-describedby="review-focus-help">?</button><span class="tooltip-content" id="review-focus-help" role="tooltip" data-i18n="reviewFocusHelp">Set how much each criterion should guide the audit. A value of 0 omits that criterion.</span></span></legend>
<p id="review-focus-guidance" class="field-description" data-i18n="reviewFocusDescription">Set each priority from 0 (omit) to 5 (critical). Priorities guide attention; evidence standards stay the same.</p>
<div id="focus-criteria" class="focus-criteria-grid">__FOCUS_CRITERIA__</div>
<div class="field custom-focus-field"><div class="field-heading"><label for="focus-custom-text" data-i18n="customCriteria">Custom criteria</label><span class="help-wrap tip-right"><button class="help-tip" type="button" data-i18n-aria-label="helpCustomCriteria" aria-describedby="focus-custom-help">?</button><span class="tooltip-content" id="focus-custom-help" role="tooltip" data-i18n="customCriteriaHelp">Add additional review criteria, one per line. This text is included as written in the generated prompt.</span></span></div><textarea id="focus-custom-text" maxlength="__MAX_CUSTOM_FOCUS_LENGTH__" data-i18n-placeholder="customCriteriaPlaceholder" placeholder="One custom criterion per line" aria-describedby="focus-custom-help review-focus-guidance"></textarea><small class="field-description" data-i18n="customCriteriaDescription">Optional, up to 4,000 characters. Use one criterion per line.</small></div>
</fieldset>
<div class="focus-submit"><button class="primary" data-i18n="createArena" data-i18n-title="createArenaTitle" title="Create the snapshot and review prompt in the new workspace folder." onclick="initializeWorkspace()">Create arena</button></div>
</div><div id="workspace-message" role="status"></div></section>
<div id="arena-content" hidden>
<section><div class="eyebrow" data-i18n="leaderboard">Leaderboard</div><h2 data-i18n="verifiedResultsOnly">Verified results only</h2><div id="summary" class="cards"></div></section>
<section class="panel"><div class="row"><h2 data-i18n="scoreboard">Scoreboard</h2><button data-i18n="downloadScoreJson" onclick="downloadScore()">Download score JSON</button></div><div style="overflow:auto"><table id="board"></table></div><p class="muted" data-i18n="scoreboardExplanation">Unreviewed findings score zero. Confirmed issues score by reviewed severity. Penalties apply for false positives.</p></section>
<section><div class="eyebrow" data-i18n="valueProposals">Value proposals</div><h2 data-i18n="goldenFeatureContenders">Golden feature contenders</h2><div id="goldpodium" class="cards"></div></section>
<section class="grid"><div class="panel"><h2 data-i18n="runAudit">Run an audit</h2><p class="muted" data-i18n="auditDisclosure">Selected providers receive source fragments from the frozen snapshot. Review the snapshot before authorizing a run.</p><div id="run-models"></div><div id="models-message" class="meta"></div><div class="formrow"><div><label for="concurrency" data-i18n="concurrency">Concurrency</label><input type="number" id="concurrency" min="1" max="16" value="2"></div><div><label for="max-steps" data-i18n="maxSteps">Max steps</label><input type="number" id="max-steps" min="1" value="35"></div><div><label for="retries" data-i18n="retries">Retries</label><input type="number" id="retries" min="0" max="5" value="2"></div><div><label for="retry-delay" data-i18n="retryDelay">Retry delay (s)</label><input type="number" id="retry-delay" min="0" max="60" step="0.5" value="5"></div></div><label class="model-choice"><input id="confirm-send-code" type="checkbox"><span data-i18n="authorizeCodeSending">I authorize sending approved source fragments to the selected providers for this run.</span></label><button id="run-button" class="primary" data-i18n="startAudit" onclick="startRun()">Start audit</button><div id="run-message" role="status"></div><div id="job-details"></div></div>
<div class="panel"><div class="row"><h2 data-i18n="generatedReviewPrompt">Generated review prompt</h2><button data-i18n="showPrompt" onclick="loadPrompt()">Show prompt</button></div><p class="muted" data-i18n="promptIntro">Copy this prompt into an IDE or another coding agent.</p><div id="prompt-actions" hidden><button data-i18n="copy" onclick="copyPrompt()">Copy</button> <button data-i18n="downloadPrompt" onclick="downloadPrompt()">Download prompt</button></div><pre id="prompt-text" class="promptbox" hidden></pre><div id="prompt-message" role="status"></div></div></section>
<section class="grid"><div><div class="panel"><h2 data-i18n="reviewContestant">Review a contestant</h2><div class="formrow"><div><label for="model" data-i18n="model">Model</label><select id="model" onchange="showReport()"></select></div></div><p id="abstract" class="muted"></p><h3 data-i18n="goldenFeature">Golden feature</h3><div id="gold"></div><h3 style="margin-top:22px" data-i18n="rubricScores">Rubric scores</h3><div class="formrow"><div><label for="improvements" data-i18n="improvements15">Improvements /15</label><input type="number" id="improvements" min="0" max="15"></div><div><label for="goldscore" data-i18n="gold20">Gold /20</label><input type="number" id="goldscore" min="0" max="20"></div><div><label for="rigor" data-i18n="rigor5">Rigor /5</label><input type="number" id="rigor" min="0" max="5"></div><button class="primary" data-i18n="saveScores" onclick="saveRubric()">Save scores</button></div></div>
<div class="panel" style="margin-top:20px"><h2 data-i18n="importManualReport">Import manual report</h2><p class="muted" data-i18n="importExplanation">JSON report produced in any IDE or imported from another system. The model ID may contain only letters, digits, underscores and hyphens.</p><div class="formrow"><input id="importmodel" data-i18n-placeholder="modelIdPlaceholder" data-i18n-aria-label="modelIdLabel" placeholder="model-id" aria-label="Model ID"><input type="file" id="importfile" accept=".json,.md,.txt" data-i18n-aria-label="reportFile"><button data-i18n="import" onclick="importReport()">Import</button></div><div id="message"></div></div></div>
<div class="panel"><div class="row"><h2 data-i18n="findingsAdjudication">Findings &amp; adjudication</h2><span id="count" class="pill"></span></div><div id="findings"></div></div></section>
</div>
<dialog id="folder-dialog" class="folder-dialog" aria-labelledby="folder-dialog-title">
<div class="row"><h2 id="folder-dialog-title" data-i18n="folderDialogTitle">Choose an arena folder</h2><button type="button" data-i18n="close" onclick="closeFolderPicker()">Close</button></div>
<p class="muted" data-i18n="folderDialogIntro">Navigate to a folder containing manifest.json and select it. Relative paths start from the directory where reporeview web was launched.</p>
<div class="folder-current-row"><button id="folder-parent" type="button" data-i18n="folderUp" onclick="browseParentFolder()">Parent folder</button><button type="button" data-i18n="startAtLaunchFolder" onclick="browseFolder('')">Start at launch folder</button><code id="folder-current"></code></div>
<p id="folder-current-kind" class="meta"></p><div id="folder-message" role="status"></div>
<ul id="folder-list" class="folder-list" data-i18n-aria-label="folderListLabel" aria-label="Folders in this directory"></ul>
<div class="folder-dialog-actions"><button id="folder-select" class="primary" type="button" data-i18n="chooseCurrentFolder" onclick="chooseFolder()" disabled>Use this folder</button></div>
</dialog>
</main><script>
let state = {};
let dashboardStatus = {};
let pollTimer = null;
let folderPickerTarget = null;
let folderPickerData = null;
let folderRequestId = 0;
const token = __TOKEN__;

const translations = {
  en: {
    languageLabel: "Language",
    independentAudits: "Independent LLM code audits",
    loadingCompetition: "Loading competition…",
    selectWorkspaceSubtitle: "Select or create a local audit workspace",
    projectSummary: "{repo} | {commit} | {count} reviewed files",
    refresh: "Refresh",
    exportScorecard: "Export scorecard",
    localWorkspace: "Local workspace",
    openOrCreateArena: "Open or create an arena",
    noArenaSelected: "No arena selected",
    workspaceIntro: "Open an existing arena to continue, or create one from a clean local Git checkout.",
    openExistingArena: "Open an existing arena",
    workspacePath: "Workspace path",
    helpWorkspacePath: "Help for Workspace path",
    browseFolder: "Browse",
    browseFolderTitle: "Browse for an existing arena",
    folderDialogTitle: "Choose an arena folder",
    folderDialogIntro: "Navigate to a folder containing manifest.json and select it. Relative paths start from the directory where reporeview web was launched.",
    folderListLabel: "Folders in this directory",
    close: "Close",
    folderUp: "Parent folder",
    chooseCurrentFolder: "Use this folder",
    startAtLaunchFolder: "Start at launch folder",
    loadingFolders: "Loading folders…",
    folderPathNotDirectory: "The selected path is not an existing folder.",
    folderReadFailed: "This folder cannot be read.",
    currentArenaFolder: "This folder contains manifest.json.",
    notArenaFolder: "This folder is not an arena; manifest.json is missing.",
    noSubfolders: "No subfolders.",
    arenaFolder: "Arena",
    regularFolder: "Folder",
    moreFolders: "Only the first {count} folders are shown. Open a folder to continue browsing.",
    openFolder: "Open folder {name}",
    workspacePathHelp: "Path to an existing RepoReview Arena folder containing manifest.json. Reports, the frozen snapshot, prompt, and judgments are stored here. Relative paths use the directory where you started reporeview web.",
    workspacePathPlaceholder: "Path to arena folder",
    workspacePathDescription: "Existing arena folder; it must contain manifest.json.",
    openArena: "Open arena",
    openArenaTitle: "Load the existing arena at this path.",
    createNewArena: "Create a new arena",
    gitRepositoryRoot: "Git repository root",
    helpGitRepositoryRoot: "Help for Git repository root",
    gitRepositoryRootHelp: "Path to the root of the local Git checkout you want to audit. The checkout must be clean; the Arena snapshots committed files from this repository.",
    gitRepositoryPlaceholder: "Path to local Git repository",
    gitRepositoryRootDescription: "Root folder of a clean local Git checkout.",
    newWorkspacePath: "New workspace path",
    helpNewWorkspacePath: "Help for New workspace path",
    newWorkspacePathHelp: "Path to a new folder for this arena. It must not already exist. The Arena writes the manifest, frozen source snapshot, generated prompt, reports, and judgments here. Relative paths use the directory where you started reporeview web.",
    newWorkspacePathPlaceholder: "Path to new arena folder",
    newWorkspacePathDescription: "A new folder where this arena's files will be saved.",
    reviewFocus: "Review focus",
    helpReviewFocus: "Help for Review focus",
    reviewFocusHelp: "Set how much each criterion should guide the audit. A value of 0 omits that criterion. Higher values guide attention without changing evidence standards.",
    reviewFocusDescription: "Choose a priority from 0 (omit) to 5 (critical). Criteria with value 0 are left out of the generated prompt.",
    priorityAriaValue: "Priority {value} of 5: {label}.",
    priorityOff: "Omitted",
    priorityLow: "Low",
    priorityMedium: "Medium",
    priorityHigh: "High",
    priorityVeryHigh: "Very high",
    priorityCritical: "Critical",
    customCriteria: "Custom criteria",
    helpCustomCriteria: "Help for Custom criteria",
    customCriteriaHelp: "Add additional review criteria, one per line. This text is included as written in the generated prompt.",
    customCriteriaPlaceholder: "One custom criterion per line",
    customCriteriaDescription: "Optional, up to 4,000 characters. Use one criterion per line.",
    criterionCriticalFlows: "Critical flows and impact",
    helpCriterionCriticalFlows: "Help for Critical flows and impact",
    criterionCriticalFlowsHelp: "Trace the most consequential user, business, or operational paths and what happens if they fail.",
    criterionCorrectness: "Correctness and edge cases",
    helpCriterionCorrectness: "Help for Correctness and edge cases",
    criterionCorrectnessHelp: "Check expected behavior, invariants, boundary values, and unusual inputs.",
    criterionSecurity: "Security and privacy",
    helpCriterionSecurity: "Help for Security and privacy",
    criterionSecurityHelp: "Look for trust boundaries, access control, secret handling, privacy, and misuse.",
    criterionFailureRecovery: "Failure handling and recovery",
    helpCriterionFailureRecovery: "Help for Failure handling and recovery",
    criterionFailureRecoveryHelp: "Examine timeouts, retries, partial failures, rollback, and recovery behavior.",
    criterionDataIntegrity: "Data integrity and consistency",
    helpCriterionDataIntegrity: "Help for Data integrity and consistency",
    criterionDataIntegrityHelp: "Check consistency, atomicity, duplication, loss, and concurrent updates.",
    criterionArchitecture: "Architecture and maintainability",
    helpCriterionArchitecture: "Help for Architecture and maintainability",
    criterionArchitectureHelp: "Look for risky coupling, fragile boundaries, and complexity that makes changes unsafe.",
    criterionPerformance: "Performance and resource use",
    helpCriterionPerformance: "Help for Performance and resource use",
    criterionPerformanceHelp: "Look for latency, resource use, unbounded work, and scaling bottlenecks.",
    criterionApiCompatibility: "APIs and compatibility",
    helpCriterionApiCompatibility: "Help for APIs and compatibility",
    criterionApiCompatibilityHelp: "Check contracts, integrations, version changes, and backward compatibility.",
    criterionTestingObservability: "Testing and observability",
    helpCriterionTestingObservability: "Help for Testing and observability",
    criterionTestingObservabilityHelp: "Assess whether important behavior is covered and failures can be diagnosed.",
    createArena: "Create arena",
    createArenaTitle: "Create the snapshot and review prompt in the new workspace folder.",
    leaderboard: "Leaderboard",
    verifiedResultsOnly: "Verified results only",
    scoreboard: "Scoreboard",
    downloadScoreJson: "Download score JSON",
    scoreboardExplanation: "Unreviewed findings score zero. Confirmed issues score by reviewed severity. Penalties apply for false positives.",
    valueProposals: "Value proposals",
    goldenFeatureContenders: "Golden feature contenders",
    runAudit: "Run an audit",
    auditDisclosure: "Selected providers receive source fragments from the frozen snapshot. Review the snapshot before authorizing a run.",
    concurrency: "Concurrency",
    maxSteps: "Max steps",
    retries: "Retries",
    retryDelay: "Retry delay (s)",
    authorizeCodeSending: "I authorize sending approved source fragments to the selected providers for this run.",
    startAudit: "Start audit",
    generatedReviewPrompt: "Generated review prompt",
    showPrompt: "Show prompt",
    promptIntro: "Copy this prompt into an IDE or another coding agent.",
    copy: "Copy",
    downloadPrompt: "Download prompt",
    reviewContestant: "Review a contestant",
    model: "Model",
    goldenFeature: "Golden feature",
    rubricScores: "Rubric scores",
    improvements15: "Improvements /15",
    gold20: "Gold /20",
    rigor5: "Rigor /5",
    saveScores: "Save scores",
    importManualReport: "Import manual report",
    importExplanation: "JSON report produced in any IDE or imported from another system. The model ID may contain only letters, digits, underscores and hyphens.",
    modelIdPlaceholder: "model-id",
    modelIdLabel: "Model ID",
    reportFile: "Report file",
    import: "Import",
    findingsAdjudication: "Findings & adjudication",
    chooseModels: "Choose one or more enabled models.",
    reportAlreadyExists: " - report already exists",
    status_queued: "queued",
    status_running: "running",
    status_succeeded: "succeeded",
    status_failed: "failed",
    status_partial: "partially completed",
    status_partially_failed: "partially failed",
    status_partial_failure: "partially failed",
    status_completed_with_errors: "completed with errors",
    auditStatus: "Audit {status}{location}{error}",
    jobInWorkspace: " in {workspace}",
    findingsSavedOne: "{count} finding saved",
    findingsSavedMany: "{count} findings saved",
    contestants: "Contestants",
    validatedFindings: "Validated findings",
    pendingReview: "Pending review",
    topScore: "Top score",
    rank: "Rank",
    bugs: "Bugs",
    improve: "Improve",
    gold: "Gold",
    rigor: "Rigor",
    penalties: "Penalties",
    total: "Total",
    pending: "Pending",
    tokens: "Tokens",
    noSummary: "No summary",
    findingOne: "{count} finding",
    findingMany: "{count} findings",
    evidence: "Evidence",
    fix: "Fix",
    evidence_confirmed: "confirmed",
    evidence_strong_evidence: "strong evidence",
    evidence_hypothesis: "hypothesis",
    judgment: "Judgment",
    verdict_pending: "pending",
    verdict_confirmed: "confirmed",
    verdict_false_positive: "false positive",
    reviewedSeverity: "Reviewed severity",
    issueKey: "Issue key (dedupe)",
    optionalCanonicalId: "optional canonical ID",
    fabricatedEvidence: "Fabricated evidence",
    save: "Save",
    arenaOpened: "Arena opened",
    arenaCreated: "Arena created and selected",
    promptReady: "Prompt ready",
    promptCopied: "Prompt copied",
    clipboardUnavailable: "Clipboard unavailable; select and copy the prompt text.",
    reportImported: "Report imported",
  },
  "pt-BR": {
    languageLabel: "Idioma",
    independentAudits: "Auditorias independentes de código com LLMs",
    loadingCompetition: "Carregando arena…",
    selectWorkspaceSubtitle: "Selecione ou crie uma arena local para começar",
    projectSummary: "{repo} | {commit} | {count} arquivos analisados",
    refresh: "Atualizar",
    exportScorecard: "Exportar quadro de pontuação",
    localWorkspace: "Arena local",
    openOrCreateArena: "Abrir ou criar uma arena",
    noArenaSelected: "Nenhuma arena selecionada",
    workspaceIntro: "Abra uma arena existente ou crie uma a partir de um repositório Git local sem alterações pendentes.",
    openExistingArena: "Abrir arena existente",
    workspacePath: "Caminho da arena",
    helpWorkspacePath: "Ajuda sobre o caminho da arena",
    browseFolder: "Procurar",
    browseFolderTitle: "Procurar uma arena existente",
    folderDialogTitle: "Escolha a pasta da arena",
    folderDialogIntro: "Navegue até uma pasta com manifest.json e selecione-a. Caminhos relativos partem da pasta em que reporeview web foi iniciado.",
    folderListLabel: "Pastas neste diretório",
    close: "Fechar",
    folderUp: "Pasta acima",
    chooseCurrentFolder: "Usar esta pasta",
    startAtLaunchFolder: "Ir para a pasta inicial",
    loadingFolders: "Carregando pastas…",
    folderPathNotDirectory: "O caminho escolhido não corresponde a uma pasta existente.",
    folderReadFailed: "Não foi possível ler esta pasta.",
    currentArenaFolder: "Esta pasta contém manifest.json.",
    notArenaFolder: "Esta pasta não é uma arena; falta o arquivo manifest.json.",
    noSubfolders: "Não há subpastas.",
    arenaFolder: "Arena",
    regularFolder: "Pasta",
    moreFolders: "Exibindo apenas as primeiras {count} pastas. Abra uma pasta para continuar navegando.",
    openFolder: "Abrir a pasta {name}",
    workspacePathHelp: "Caminho para uma pasta de arena RepoReview existente com o arquivo manifest.json. Os relatórios, o snapshot congelado, a instrução e as avaliações ficam aqui. Caminhos relativos usam a pasta em que você iniciou reporeview web.",
    workspacePathPlaceholder: "Caminho da pasta da arena",
    workspacePathDescription: "Pasta de uma arena existente; precisa conter manifest.json.",
    openArena: "Abrir arena",
    openArenaTitle: "Carrega a arena existente neste caminho.",
    createNewArena: "Criar nova arena",
    gitRepositoryRoot: "Raiz do repositório Git",
    helpGitRepositoryRoot: "Ajuda sobre a raiz do repositório Git",
    gitRepositoryRootHelp: "Caminho para a raiz do checkout Git local que será auditado. O checkout precisa estar limpo; a arena cria um snapshot dos arquivos commitados.",
    gitRepositoryPlaceholder: "Caminho do repositório Git local",
    gitRepositoryRootDescription: "Pasta raiz de um checkout Git local sem alterações pendentes.",
    newWorkspacePath: "Caminho da nova arena",
    helpNewWorkspacePath: "Ajuda sobre o caminho da nova arena",
    newWorkspacePathHelp: "Caminho de uma pasta nova para esta arena. Ela ainda não pode existir. A arena salvará aqui o manifesto, o snapshot do código, a instrução gerada, os relatórios e as avaliações. Caminhos relativos usam a pasta em que você iniciou reporeview web.",
    newWorkspacePathPlaceholder: "Caminho para a nova pasta da arena",
    newWorkspacePathDescription: "Pasta nova onde os arquivos da arena serão salvos.",
    reviewFocus: "Foco da revisão",
    helpReviewFocus: "Ajuda sobre o foco da revisão",
    reviewFocusHelp: "Defina quanto cada critério deve orientar a auditoria. O valor 0 omite o critério. Valores maiores aumentam a atenção sem alterar o padrão de evidências.",
    reviewFocusDescription: "Escolha uma prioridade de 0 (omitir) a 5 (crítica). Critérios com valor 0 não entram na instrução gerada.",
    priorityAriaValue: "Prioridade {value} de 5: {label}.",
    priorityOff: "Omitido",
    priorityLow: "Baixa",
    priorityMedium: "Média",
    priorityHigh: "Alta",
    priorityVeryHigh: "Muito alta",
    priorityCritical: "Crítica",
    customCriteria: "Critérios personalizados",
    helpCustomCriteria: "Ajuda sobre critérios personalizados",
    customCriteriaHelp: "Adicione critérios extras de revisão, um por linha. O texto será incluído como escrito na instrução gerada.",
    customCriteriaPlaceholder: "Um critério personalizado por linha",
    customCriteriaDescription: "Opcional, até 4.000 caracteres. Use um critério por linha.",
    criterionCriticalFlows: "Fluxos críticos e impacto",
    helpCriterionCriticalFlows: "Ajuda sobre fluxos críticos e impacto",
    criterionCriticalFlowsHelp: "Investigue os fluxos de usuário, negócio ou operação de maior consequência e o que acontece quando falham.",
    criterionCorrectness: "Correção e casos extremos",
    helpCriterionCorrectness: "Ajuda sobre correção e casos extremos",
    criterionCorrectnessHelp: "Verifique o comportamento esperado, invariantes, valores de limite e entradas incomuns.",
    criterionSecurity: "Segurança e privacidade",
    helpCriterionSecurity: "Ajuda sobre segurança e privacidade",
    criterionSecurityHelp: "Procure limites de confiança, controle de acesso, tratamento de segredos, privacidade e uso indevido.",
    criterionFailureRecovery: "Tratamento e recuperação de falhas",
    helpCriterionFailureRecovery: "Ajuda sobre tratamento e recuperação de falhas",
    criterionFailureRecoveryHelp: "Examine timeouts, novas tentativas, falhas parciais, rollback e mecanismos de recuperação.",
    criterionDataIntegrity: "Integridade e consistência dos dados",
    helpCriterionDataIntegrity: "Ajuda sobre integridade e consistência dos dados",
    criterionDataIntegrityHelp: "Verifique consistência, atomicidade, duplicação, perda e atualizações concorrentes.",
    criterionArchitecture: "Arquitetura e manutenção",
    helpCriterionArchitecture: "Ajuda sobre arquitetura e manutenção",
    criterionArchitectureHelp: "Procure acoplamento arriscado, limites frágeis e complexidade que torne mudanças inseguras.",
    criterionPerformance: "Desempenho e uso de recursos",
    helpCriterionPerformance: "Ajuda sobre desempenho e uso de recursos",
    criterionPerformanceHelp: "Procure latência, consumo de recursos, trabalho sem limite e gargalos de escala.",
    criterionApiCompatibility: "APIs e compatibilidade",
    helpCriterionApiCompatibility: "Ajuda sobre APIs e compatibilidade",
    criterionApiCompatibilityHelp: "Verifique contratos, integrações, mudanças de versão e compatibilidade com versões anteriores.",
    criterionTestingObservability: "Testes e observabilidade",
    helpCriterionTestingObservability: "Ajuda sobre testes e observabilidade",
    criterionTestingObservabilityHelp: "Avalie se os comportamentos importantes estão cobertos e se falhas podem ser diagnosticadas.",
    createArena: "Criar arena",
    createArenaTitle: "Cria o snapshot e a instrução de revisão na nova pasta da arena.",
    leaderboard: "Classificação",
    verifiedResultsOnly: "Somente resultados verificados",
    scoreboard: "Placar",
    downloadScoreJson: "Baixar placar em JSON",
    scoreboardExplanation: "Achados não revisados valem zero. Problemas confirmados pontuam conforme a severidade revisada. Falsos positivos geram penalidades.",
    valueProposals: "Propostas de valor",
    goldenFeatureContenders: "Destaques das soluções",
    runAudit: "Executar auditoria",
    auditDisclosure: "Os provedores selecionados receberão trechos do snapshot congelado. Revise o snapshot antes de autorizar a execução.",
    concurrency: "Execuções simultâneas",
    maxSteps: "Máximo de etapas",
    retries: "Novas tentativas",
    retryDelay: "Intervalo entre tentativas (s)",
    authorizeCodeSending: "Autorizo o envio de trechos aprovados do código aos provedores selecionados nesta execução.",
    startAudit: "Iniciar auditoria",
    generatedReviewPrompt: "Instrução de revisão gerada",
    showPrompt: "Exibir instrução",
    promptIntro: "Copie esta instrução para uma IDE ou outro agente de programação.",
    copy: "Copiar",
    downloadPrompt: "Baixar instrução",
    reviewContestant: "Avaliar um modelo",
    model: "Modelo",
    goldenFeature: "Destaque da solução",
    rubricScores: "Notas da rubrica",
    improvements15: "Melhorias /15",
    gold20: "Destaque /20",
    rigor5: "Rigor /5",
    saveScores: "Salvar notas",
    importManualReport: "Importar relatório manual",
    importExplanation: "Relatório JSON produzido em uma IDE ou importado de outro sistema. O ID do modelo pode conter apenas letras, números, sublinhados e hífens.",
    modelIdPlaceholder: "id-do-modelo",
    modelIdLabel: "ID do modelo",
    reportFile: "Arquivo do relatório",
    import: "Importar",
    findingsAdjudication: "Achados e validação",
    chooseModels: "Escolha um ou mais modelos habilitados.",
    reportAlreadyExists: " - já existe um relatório",
    status_queued: "na fila",
    status_running: "em andamento",
    status_succeeded: "concluída",
    status_failed: "falhou",
    status_partial: "concluída parcialmente",
    status_partially_failed: "falhou parcialmente",
    status_partial_failure: "com falha parcial",
    status_completed_with_errors: "concluída com erros",
    auditStatus: "Auditoria {status}{location}{error}",
    jobInWorkspace: " em {workspace}",
    findingsSavedOne: "{count} achado salvo",
    findingsSavedMany: "{count} achados salvos",
    contestants: "Modelos participantes",
    validatedFindings: "Achados validados",
    pendingReview: "Aguardando revisão",
    topScore: "Maior pontuação",
    rank: "Posição",
    bugs: "Bugs",
    improve: "Melhorias",
    gold: "Destaque",
    rigor: "Rigor",
    penalties: "Penalidades",
    total: "Total",
    pending: "Pendentes",
    tokens: "Tokens",
    noSummary: "Sem resumo",
    findingOne: "{count} achado",
    findingMany: "{count} achados",
    evidence: "Evidência",
    fix: "Correção",
    evidence_confirmed: "confirmada",
    evidence_strong_evidence: "evidência forte",
    evidence_hypothesis: "hipótese",
    judgment: "Avaliação",
    verdict_pending: "pendente",
    verdict_confirmed: "confirmado",
    verdict_false_positive: "falso positivo",
    reviewedSeverity: "Severidade revisada",
    issueKey: "Chave do problema (deduplicação)",
    optionalCanonicalId: "ID canônico opcional",
    fabricatedEvidence: "Evidência fabricada",
    save: "Salvar",
    arenaOpened: "Arena aberta",
    arenaCreated: "Arena criada e selecionada",
    promptReady: "Instrução pronta",
    promptCopied: "Instrução copiada",
    clipboardUnavailable: "Área de transferência indisponível; selecione e copie o texto da instrução.",
    reportImported: "Relatório importado",
  },
};

let language = "en";

function t(key, values) {
  const dictionary = translations[language] || translations.en;
  let message = dictionary[key] || translations.en[key] || key;
  Object.keys(values || {}).forEach(function (name) {
    message = message.replaceAll("{" + name + "}", String(values[name]));
  });
  return message;
}

const priorityLabelKeys = [
  "priorityOff", "priorityLow", "priorityMedium", "priorityHigh",
  "priorityVeryHigh", "priorityCritical",
];

function updatePriorityLabel(slider) {
  const value = Number(slider.value);
  const label = t(priorityLabelKeys[value] || "priorityOff");
  const output = document.getElementById("priority-" + slider.dataset.priorityId + "-level");
  if (output) output.textContent = value + " - " + label;
  slider.setAttribute("aria-valuetext", t("priorityAriaValue", {value: value, label: label}));
}

function refreshPriorityLabels() {
  document.querySelectorAll(".priority-slider").forEach(updatePriorityLabel);
}

function localizedNode(tag, klass, key) {
  const element = node(tag, klass, t(key));
  element.dataset.i18n = key;
  return element;
}

function localizedPrefixNode(tag, klass, key, value) {
  const element = node(tag, klass);
  element.dataset.i18nPrefix = key;
  element.dataset.value = String(value);
  element.textContent = t(key) + ": " + String(value);
  return element;
}

function statusText(status) {
  const key = "status_" + String(status || "").replace(/[^a-z0-9]+/gi, "_");
  return translations.en[key] ? t(key) : String(status || "").replaceAll("_", " ");
}

function evidenceText(status) {
  const key = "evidence_" + String(status || "");
  return translations.en[key] ? t(key) : String(status || "").replaceAll("_", " ");
}

function setLocalizedMessage(id, key, values) {
  const element = document.getElementById(id);
  element.dataset.translationKey = key;
  element.dataset.translationValues = JSON.stringify(values || {});
  element.textContent = t(key, values);
}

function refreshLocalizedMessages() {
  document.querySelectorAll("[data-translation-key]").forEach(function (element) {
    let values = {};
    try { values = JSON.parse(element.dataset.translationValues || "{}"); }
    catch (error) { values = {}; }
    element.textContent = t(element.dataset.translationKey, values);
  });
}

function renderProjectLabel() {
  const project = document.getElementById("project");
  if (dashboardStatus.workspace && state.manifest) {
    project.textContent = t("projectSummary", {
      repo: state.manifest.repo_name,
      commit: state.manifest.commit.slice(0, 12),
      count: state.manifest.file_count,
    });
  } else if (!dashboardStatus.workspace) {
    project.textContent = t("selectWorkspaceSubtitle");
  }
}

function applyLanguage() {
  document.documentElement.lang = language;
  document.querySelectorAll("[data-i18n]").forEach(function (element) {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(function (element) {
    element.title = t(element.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach(function (element) {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
  });
  document.querySelectorAll("[data-i18n-prefix]").forEach(function (element) {
    element.textContent = t(element.dataset.i18nPrefix) + ": " + element.dataset.value;
  });
  document.querySelectorAll("[data-evidence-status]").forEach(function (element) {
    element.textContent = element.dataset.findingSeverity + " | " +
      evidenceText(element.dataset.evidenceStatus);
  });
  document.querySelectorAll("#language-toggle [data-language]").forEach(function (button) {
    button.setAttribute("aria-pressed", String(button.dataset.language === language));
  });
  refreshPriorityLabels();
  const active = document.getElementById("workspace-active");
  active.textContent = dashboardStatus.workspace || t("noArenaSelected");
  renderProjectLabel();
  const currentReport = state.reports && state.reports[document.getElementById("model").value];
  if (currentReport && !currentReport.summary) {
    document.getElementById("abstract").textContent = t("noSummary");
  }
  refreshLocalizedMessages();
  if (dashboardStatus.models) renderModels();
  if (state.leaderboard) renderBoard();
  if (dashboardStatus.job) renderJob(dashboardStatus.job);
  if (folderPickerData && document.getElementById("folder-dialog").open) {
    renderFolderPicker(folderPickerData);
  }
}

function node(tag, klass, text) {
  const element = document.createElement(tag);
  if (klass) element.className = klass;
  if (text !== undefined) element.textContent = String(text);
  return element;
}
function clear(id) {
  const element = document.getElementById(id);
  element.replaceChildren();
  return element;
}
function option(select, value, text, translationKey) {
  const item = node("option", "", text);
  item.value = value;
  if (translationKey) item.dataset.i18n = translationKey;
  select.append(item);
}
function setMessage(id, text) {
  const element = document.getElementById(id);
  delete element.dataset.translationKey;
  delete element.dataset.translationValues;
  element.textContent = text || "";
}
async function api(path, payload) {
  const hasPayload = payload !== undefined;
  const response = await fetch(path, {
    method: hasPayload ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Arena-Token": token },
    body: hasPayload ? JSON.stringify(payload) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed");
  return data;
}
function saveFile(name, content, type) {
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(new Blob([content], { type: type }));
  anchor.download = name;
  anchor.click();
  setTimeout(function () { URL.revokeObjectURL(anchor.href); }, 3000);
}

function openFolderPicker() {
  folderPickerTarget = "workspace-path";
  const dialog = document.getElementById("folder-dialog");
  if (!dialog.open) dialog.showModal();
  browseFolder(document.getElementById(folderPickerTarget).value.trim());
}

async function browseFolder(path) {
  const requestId = ++folderRequestId;
  folderPickerData = null;
  document.getElementById("folder-current").textContent = path || "";
  document.getElementById("folder-current-kind").textContent = "";
  document.getElementById("folder-parent").disabled = true;
  document.getElementById("folder-select").disabled = true;
  clear("folder-list");
  setLocalizedMessage("folder-message", "loadingFolders");
  try {
    const result = await api("/api/folders", { path: path || "" });
    if (requestId !== folderRequestId || !document.getElementById("folder-dialog").open) return;
    folderPickerData = result;
    renderFolderPicker(folderPickerData);
  } catch (error) {
    if (requestId !== folderRequestId) return;
    const message = error.message === "path must be an existing directory"
      ? t("folderPathNotDirectory")
      : error.message === "This folder cannot be read"
        ? t("folderReadFailed")
        : error.message;
    setMessage("folder-message", message);
  }
}

function renderFolderPicker(data) {
  document.getElementById("folder-current").textContent = data.path;
  document.getElementById("folder-current-kind").textContent = t(
    data.is_workspace ? "currentArenaFolder" : "notArenaFolder"
  );
  document.getElementById("folder-parent").disabled = !data.parent;
  document.getElementById("folder-select").disabled = !data.is_workspace;
  const list = clear("folder-list");
  const directories = data.directories || [];
  if (!directories.length) {
    setLocalizedMessage("folder-message", "noSubfolders");
  } else {
    setMessage("folder-message", data.truncated
      ? t("moreFolders", { count: directories.length })
      : "");
  }
  directories.forEach(function (directory) {
    const item = node("li");
    const button = node("button", "folder-entry");
    button.type = "button";
    button.setAttribute("aria-label", t("openFolder", { name: directory.name }));
    button.append(
      node("span", "", directory.name),
      node("span", "folder-badge", t(directory.is_workspace ? "arenaFolder" : "regularFolder")),
    );
    button.onclick = function () { browseFolder(directory.path); };
    item.append(button);
    list.append(item);
  });
}

function browseParentFolder() {
  if (folderPickerData && folderPickerData.parent) {
    browseFolder(folderPickerData.parent);
  }
}

function chooseFolder() {
  if (!folderPickerData || !folderPickerData.is_workspace || !folderPickerTarget) return;
  const input = document.getElementById(folderPickerTarget);
  input.value = folderPickerData.path;
  document.getElementById("folder-dialog").close();
  folderPickerData = null;
  folderPickerTarget = null;
  input.focus();
}

function closeFolderPicker() {
  const dialog = document.getElementById("folder-dialog");
  folderRequestId += 1;
  if (dialog.open) dialog.close();
  folderPickerData = null;
  folderPickerTarget = null;
}

async function reload() {
  try {
    const previousWorkspace = dashboardStatus.workspace;
    dashboardStatus = await api("/api/status");
    if (previousWorkspace !== dashboardStatus.workspace) {
      const prompt = document.getElementById("prompt-text");
      prompt.textContent = "";
      prompt.hidden = true;
      document.getElementById("prompt-actions").hidden = true;
      setMessage("prompt-message", "");
    }
    const hasWorkspace = !!dashboardStatus.workspace;
    const exportButton = document.querySelector('button[onclick="exportJson()"]');
    if (exportButton) exportButton.disabled = !hasWorkspace;
    document.getElementById("arena-content").hidden = !hasWorkspace;
    document.getElementById("workspace-active").textContent =
      dashboardStatus.workspace || t("noArenaSelected");
    if (hasWorkspace) {
      state = await api("/api/data");
      renderProjectLabel();
      const previousModel = document.getElementById("model").value;
      const models = clear("model");
      Object.keys(state.reports).sort().forEach(function (id) { option(models, id, id); });
      if (previousModel && state.reports[previousModel]) models.value = previousModel;
      renderBoard();
      showReport();
      renderModels();
    } else {
      state = { reports: {}, judgments: {}, leaderboard: [] };
      renderProjectLabel();
      clear("model");
      clear("summary");
      clear("board");
      clear("goldpodium");
      clear("run-models");
      clear("findings");
      showReport();
      setMessage("models-message", "");
    }
    renderJob(dashboardStatus.job);
    clearTimeout(pollTimer);
    if (dashboardStatus.job && ["queued", "running"].includes(dashboardStatus.job.status)) {
      pollTimer = setTimeout(reload, 1200);
    }
  } catch (error) {
    document.getElementById("project").textContent = error.message;
  }
}

function renderModels() {
  const root = document.getElementById("run-models");
  const preserveSelection = root.dataset.initialized === "true";
  const selected = new Set(Array.from(
    root.querySelectorAll("input:checked:not(:disabled)")
  ).map(function (checkbox) { return checkbox.value; }));
  root.replaceChildren();
  const models = dashboardStatus.models || [];
  const reports = state.reports || {};
  if (dashboardStatus.models_error) {
    setMessage("models-message", dashboardStatus.models_error);
  } else {
    setLocalizedMessage("models-message", "chooseModels");
  }
  models.forEach(function (model) {
    const row = node("label", "model-choice");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.value = model.id;
    checkbox.checked = preserveSelection ? selected.has(model.id) : !reports[model.id];
    checkbox.disabled = !!reports[model.id];
    const label = node("span", "", model.id +
      (reports[model.id] ? t("reportAlreadyExists") : ""));
    label.dataset.modelId = model.id;
    label.dataset.reportExists = reports[model.id] ? "true" : "false";
    row.append(checkbox, label);
    root.append(row);
  });
  root.dataset.initialized = "true";
  document.getElementById("run-button").disabled =
    !models.length || (dashboardStatus.job &&
      ["queued", "running"].includes(dashboardStatus.job.status));
}
function renderJob(job) {
  const root = clear("job-details");
  if (!job) {
    setMessage("run-message", "");
    return;
  }
  setLocalizedMessage("run-message", "auditStatus", {
    status: statusText(job.status),
    location: job.workspace ? t("jobInWorkspace", { workspace: job.workspace }) : "",
    error: job.error ? ": " + job.error : "",
  });
  const outcomes = new Map((job.outcomes || []).map(function (result) {
    return [result.model, result];
  }));
  if (job.models && job.models.length) {
    const list = node("ul", "job-outcomes");
    job.models.forEach(function (model) {
      const result = outcomes.get(model.id);
      let detail = model.id + ": " + statusText(model.status);
      if (result && result.error) detail += " - " + result.error;
      else if (result && result.findings !== undefined) {
        detail += " - " + t(result.findings === 1 ? "findingsSavedOne" : "findingsSavedMany", {
          count: result.findings,
        });
      }
      list.append(node("li", "", detail));
    });
    root.append(list);
  }
  document.getElementById("run-button").disabled =
    ["queued", "running"].includes(job.status) || !(dashboardStatus.models || []).length;
}
function renderBoard() {
  const rows = state.leaderboard || [];
  const summary = clear("summary");
  [
    ["contestants", rows.length],
    ["validatedFindings", rows.reduce(function (total, row) { return total + row.confirmed; }, 0)],
    ["pendingReview", rows.reduce(function (total, row) { return total + row.pending; }, 0)],
    ["topScore", rows[0] ? rows[0].total : 0],
  ].forEach(function (item) {
    const card = node("div", "card");
    card.append(node("div", "eyebrow", t(item[0])), node("div", "total", item[1]));
    summary.append(card);
  });
  const board = clear("board");
  const headings = node("tr");
  ["rank", "model", "bugs", "improve", "gold", "rigor", "penalties",
   "total", "pending", "tokens"].forEach(function (key) {
    headings.append(node("th", "", t(key)));
  });
  board.append(headings);
  rows.forEach(function (row, index) {
    const line = node("tr");
    const report = state.reports[row.model];
    const tokens = (report.audit_metadata && report.audit_metadata.prompt_tokens || 0) +
      (report.audit_metadata && report.audit_metadata.completion_tokens || 0);
    [index + 1, row.model, row.bugs, row.improvements, row.gold, row.rigor,
     row.penalty, row.total, row.pending, tokens].forEach(function (value, column) {
      line.append(node("td", column > 1 ? "num" : "", value));
    });
    board.append(line);
  });
  const podium = clear("goldpodium");
  rows.slice().sort(function (left, right) { return right.gold - left.gold; })
    .forEach(function (row) {
      const card = node("div", "card");
      const feature = state.reports[row.model].golden_feature;
      card.append(
        node("div", "eyebrow", row.model + " | " + row.gold + "/20"),
        node("h3", "gold", row.golden_feature),
        node("p", "muted", feature.mechanism || ""),
      );
      podium.append(card);
    });
}
function showReport() {
  const id = document.getElementById("model").value;
  const report = state.reports[id];
  if (!report) {
    document.getElementById("abstract").textContent = "";
    clear("gold");
    setMessage("count", "");
    ["improvements", "goldscore", "rigor"].forEach(function (field) {
      document.getElementById(field).value = 0;
    });
    clear("findings");
    return;
  }
  const judgment = state.judgments[id] || {};
  document.getElementById("abstract").textContent = report.summary || t("noSummary");
  const gold = clear("gold");
  gold.append(node("h3", "gold", report.golden_feature.name));
  gold.append(node("p", "muted", report.golden_feature.mechanism || ""));
  ["improvements", "goldscore", "rigor"].forEach(function (field) {
    const rubricField = field === "goldscore" ? "gold" : field;
    document.getElementById(field).value =
      (judgment.rubric && judgment.rubric[rubricField]) || 0;
  });
  setLocalizedMessage("count", report.findings.length === 1 ? "findingOne" : "findingMany", {
    count: report.findings.length,
  });
  const root = clear("findings");
  report.findings.forEach(function (finding) {
    const article = node("article", "finding");
    const heading = node("div", "row");
    const badge = node("span", "pill " + finding.severity.toLowerCase(),
      finding.severity + " | " + evidenceText(finding.evidence_status));
    badge.dataset.findingSeverity = finding.severity;
    badge.dataset.evidenceStatus = finding.evidence_status;
    heading.append(
      node("h3", "", finding.id + " - " + finding.title),
      badge,
    );
    article.append(
      heading,
      node("div", "code", finding.file + ":" + finding.line),
      node("p", "detail", finding.description),
      localizedPrefixNode("p", "detail", "evidence", finding.evidence),
      localizedPrefixNode("p", "detail", "fix", finding.recommendation),
    );
    const controls = node("div", "formrow");
    const verdictBox = node("div");
    verdictBox.append(localizedNode("label", "", "judgment"));
    const verdict = node("select");
    ["pending", "confirmed", "false_positive"].forEach(function (value) {
      option(verdict, value, t("verdict_" + value), "verdict_" + value);
    });
    const previous = (judgment.findings && judgment.findings[finding.id]) || {};
    verdict.value = previous.verdict || "pending";
    verdictBox.append(verdict);
    const severityBox = node("div");
    severityBox.append(localizedNode("label", "", "reviewedSeverity"));
    const severity = node("select");
    ["P0", "P1", "P2", "P3"].forEach(function (value) { option(severity, value, value); });
    severity.value = previous.severity || finding.severity;
    severityBox.append(severity);
    const keyBox = node("div");
    keyBox.append(localizedNode("label", "", "issueKey"));
    const key = node("input");
    key.dataset.i18nPlaceholder = "optionalCanonicalId";
    key.placeholder = t("optionalCanonicalId");
    key.value = previous.issue_key || "";
    keyBox.append(key);
    const fabricatedBox = node("div");
    fabricatedBox.append(localizedNode("label", "", "fabricatedEvidence"));
    const fabricated = node("input");
    fabricated.type = "checkbox";
    fabricated.checked = !!previous.fabricated_evidence;
    fabricatedBox.append(fabricated);
    const save = localizedNode("button", "primary", "save");
    save.onclick = async function () {
      try {
        await api("/api/adjudicate", {
          model: id, finding: finding.id, verdict: verdict.value,
          severity: severity.value, issue_key: key.value,
          fabricated_evidence: fabricated.checked,
        });
        await reload();
      } catch (error) {
        alert(error.message);
      }
    };
    controls.append(verdictBox, severityBox, keyBox, fabricatedBox, save);
    article.append(controls);
    root.append(article);
  });
}
async function openWorkspace() {
  try {
    await api("/api/workspace/open", {
      path: document.getElementById("workspace-path").value,
    });
    setLocalizedMessage("workspace-message", "arenaOpened");
    await reload();
  } catch (error) {
    setMessage("workspace-message", error.message);
  }
}
async function initializeWorkspace() {
  try {
    const criteria = {};
    document.querySelectorAll(".priority-slider").forEach(function (slider) {
      criteria[slider.dataset.priorityId] = Number(slider.value);
    });
    await api("/api/workspace/init", {
      repo: document.getElementById("repo-path").value,
      out: document.getElementById("out-path").value,
      review_focus: {
        criteria: criteria,
        custom_text: document.getElementById("focus-custom-text").value,
      },
    });
    setLocalizedMessage("workspace-message", "arenaCreated");
    await reload();
  } catch (error) {
    setMessage("workspace-message", error.message);
  }
}
async function startRun() {
  const selected = Array.from(
    document.querySelectorAll("#run-models input:checked:not(:disabled)")
  ).map(function (checkbox) { return checkbox.value; });
  try {
    const result = await api("/api/run", {
      models: selected,
      confirm_send_code: document.getElementById("confirm-send-code").checked,
      concurrency: +document.getElementById("concurrency").value,
      max_steps: +document.getElementById("max-steps").value,
      retries: +document.getElementById("retries").value,
      retry_delay: +document.getElementById("retry-delay").value,
    });
    setLocalizedMessage("run-message", "auditStatus", {
      status: statusText(result.job.status), location: "", error: "",
    });
    await reload();
  } catch (error) {
    setMessage("run-message", error.message);
  }
}
async function loadPrompt() {
  try {
    const result = await api("/api/prompt");
    const prompt = document.getElementById("prompt-text");
    prompt.textContent = result.prompt;
    prompt.hidden = false;
    document.getElementById("prompt-actions").hidden = false;
    setLocalizedMessage("prompt-message", "promptReady");
  } catch (error) {
    setMessage("prompt-message", error.message);
  }
}
async function copyPrompt() {
  try {
    await navigator.clipboard.writeText(document.getElementById("prompt-text").textContent);
    setLocalizedMessage("prompt-message", "promptCopied");
  } catch (error) {
    setLocalizedMessage("prompt-message", "clipboardUnavailable");
  }
}
async function saveRubric() {
  try {
    await api("/api/rubric", {
      model: document.getElementById("model").value,
      improvements: +document.getElementById("improvements").value,
      gold: +document.getElementById("goldscore").value,
      rigor: +document.getElementById("rigor").value,
    });
    await reload();
  } catch (error) {
    alert(error.message);
  }
}
async function importReport() {
  const file = document.getElementById("importfile").files[0];
  const id = document.getElementById("importmodel").value;
  if (!file) return;
  try {
    await api("/api/import", { model: id, content: await file.text() });
    setLocalizedMessage("message", "reportImported");
    await reload();
  } catch (error) {
    document.getElementById("message").textContent = error.message;
  }
}
function downloadScore() {
  saveFile("arena-score.json", JSON.stringify(state.leaderboard || [], null, 2),
    "application/json");
}
function exportJson() {
  saveFile("arena-scoreboard.json", JSON.stringify(state, null, 2), "application/json");
}
function downloadPrompt() {
  saveFile("audit-prompt.md", document.getElementById("prompt-text").textContent,
    "text/markdown");
}
document.querySelectorAll(".priority-slider").forEach(function (slider) {
  slider.addEventListener("input", function () {
    updatePriorityLabel(slider);
  });
});
const languageToggle = document.getElementById("language-toggle");
document.getElementById("folder-dialog").addEventListener("close", function () {
  folderRequestId += 1;
  folderPickerData = null;
  folderPickerTarget = null;
});
try {
  const storedLanguage = localStorage.getItem("reporeview-language");
  if (storedLanguage && translations[storedLanguage]) language = storedLanguage;
} catch (error) {
  // The dashboard still works if browser storage is unavailable.
}
languageToggle.addEventListener("click", function (event) {
  const button = event.target.closest("[data-language]");
  if (!button) return;
  language = translations[button.dataset.language] ? button.dataset.language : "en";
  try { localStorage.setItem("reporeview-language", language); }
  catch (error) { /* Keep the selected language for this page session. */ }
  applyLanguage();
});
applyLanguage();
reload();
</script></body></html>"""


class NoActiveWorkspace(RuntimeError):
    """Raised when an operation requires an arena but none is selected."""


def _data(workspace: Path) -> dict:
    reports = {p.stem: load_json(p) for p in sorted((workspace / "reports").glob("*.json"))}
    judgments = {p.stem: load_json(p) for p in sorted((workspace / "judgments").glob("*.json"))}
    return {
        "manifest": load_json(workspace / "manifest.json"),
        "reports": reports,
        "judgments": judgments,
        "leaderboard": leaderboard(reports, judgments),
    }


def _configured_models(models_path: Path | None) -> tuple[list[dict[str, str]], str | None]:
    if models_path is None:
        return [], "Start the server with --models <path> to enable provider audits."
    try:
        config = load_json(models_path)
        models = config.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("models.json must contain a nonempty models array")
        available: list[dict[str, str]] = []
        seen: set[str] = set()
        for entry in models:
            if not isinstance(entry, dict):
                raise ValueError("Each model entry must be an object")
            if entry.get("enabled", True) is False:
                continue
            model_id = model_slug(entry.get("id"))
            if model_id in seen:
                raise ValueError("Model IDs must be unique")
            if not isinstance(entry.get("model"), str) or not entry["model"].strip():
                raise ValueError("Each enabled model must specify a LiteLLM model name")
            seen.add(model_id)
            available.append({"id": model_id})
        if not available:
            raise ValueError("models.json has no enabled models")
        return available, None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return [], "Could not read a valid models.json configuration."


def _render_review_focus_criteria() -> str:
    """Render the fixed priority controls from the server-side criterion list."""

    controls = []
    for criterion_id, (label, default_value) in REVIEW_FOCUS_CRITERIA.items():
        suffix = "".join(part.title() for part in criterion_id.split("_"))
        label_key = f"criterion{suffix}"
        help_key = f"criterion{suffix}Help"
        help_label_key = f"helpCriterion{suffix}"
        level = FOCUS_PRIORITY_LABELS.get(default_value, "off")
        value_text = f"{default_value} · {level.title()}"
        controls.append(
            f'<div class="focus-criterion">'
            f'<div class="field-heading">'
            f'<label for="priority-{criterion_id}" data-i18n="{label_key}">'
            f'{html.escape(label)}</label>'
            f'<span class="help-wrap"><button class="help-tip" type="button" '
            f'data-i18n-aria-label="{help_label_key}" '
            f'aria-describedby="priority-{criterion_id}-help">?</button>'
            f'<span class="tooltip-content" id="priority-{criterion_id}-help" '
            f'role="tooltip" data-i18n="{help_key}">{html.escape(label)}</span></span>'
            f'<output id="priority-{criterion_id}-level" class="focus-priority-level" '
            f'for="priority-{criterion_id}" aria-live="polite">{value_text}</output>'
            f'</div>'
            f'<input id="priority-{criterion_id}" class="priority-slider" type="range" '
            f'min="0" max="5" step="1" value="{default_value}" '
            f'data-priority-id="{criterion_id}" '
            f'aria-describedby="priority-{criterion_id}-help '
            f'priority-{criterion_id}-level review-focus-guidance" '
            f'aria-valuetext="{default_value} of 5, {level} priority">'
            f'</div>'
        )
    return "\n".join(controls)


def _path_from_input(raw: Any, base: Path, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        raise ValueError(f"{field} must be a nonempty local path")
    path = Path(raw.strip())
    return (path if path.is_absolute() else base / path).resolve()


def _validate_run_options(payload: dict[str, Any]) -> dict[str, int | float]:
    integer_options = {
        "concurrency": (2, 1, 16),
        "max_steps": (35, 1, None),
        "retries": (2, 0, 5),
    }
    options: dict[str, int | float] = {}
    for key, (default, minimum, maximum) in integer_options.items():
        value = payload.get(key, default)
        if (type(value) is not int or value < minimum
                or (maximum is not None and value > maximum)):
            limit = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
            raise ValueError(f"{key} must be an integer {limit}")
        options[key] = value
    delay = payload.get("retry_delay", 5)
    if (type(delay) not in (int, float) or not math.isfinite(delay)
            or not 0 <= delay <= 60):
        raise ValueError("retry_delay must be a number between 0 and 60")
    options["retry_delay"] = float(delay)
    return options


def _start_audit(server: ThreadingHTTPServer, workspace: Path,
                 models: list[dict[str, Any]], options: dict[str, int | float]) -> dict:
    with server.state_lock:
        current = server.jobs.get(server.current_job_id)
        if current and current["status"] in {"queued", "running"}:
            raise RuntimeError("An audit is already running")
        server.jobs.clear()
        job_id = secrets.token_urlsafe(12)
        job = {
            "id": job_id, "workspace": str(workspace), "status": "queued",
            "created_at": time.time(),
            "finished_at": None, "outcomes": [], "error": None,
            "models": [{"id": model["id"], "status": "queued"} for model in models],
        }
        server.jobs[job_id] = job
        server.current_job_id = job_id

    def run() -> None:
        with server.state_lock:
            job["status"] = "running"
        try:
            def track_model(update: dict[str, Any]) -> None:
                with server.state_lock:
                    for item in job["models"]:
                        if item["id"] == update["model"]:
                            item["status"] = update["status"]
                            break

            outcomes = asyncio.run(audit_many(
                workspace, models, progress=track_model, **options,
            ))
            failures = sum("error" in outcome for outcome in outcomes)
            if failures == len(outcomes):
                state = "failed"
            elif failures:
                state = "partial_failure"
            else:
                state = "succeeded"
            with server.state_lock:
                job.update(status=state, outcomes=outcomes, finished_at=time.time())
        except Exception:
            with server.state_lock:
                job.update(
                    status="failed", error="Audit could not complete. Check the local server output.",
                    finished_at=time.time(),
                )

    threading.Thread(target=run, name=f"arena-audit-{job_id}", daemon=True).start()
    with server.state_lock:
        return deepcopy(job)


def create_server(workspace: Path | None = None, port: int = 8765,
                  models_path: Path | None = None) -> ThreadingHTTPServer:
    """Create the loopback HTTP server; exposed separately to support API tests."""

    initial_workspace = validate_workspace(workspace) if workspace is not None else None
    starting_directory = Path.cwd().resolve()
    configured_models = models_path.resolve() if models_path is not None else None
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, content: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def _json(self, status: int, payload: dict) -> None:
            self._reply(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def _host_allowed(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def _authorized(self) -> bool:
            origin = self.headers.get("Origin", "")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            return self.headers.get("X-Arena-Token") == token and (not origin or origin == expected)

        def _read_payload(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid content length") from None
            if length <= 0 or length > 2_000_000:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            return payload

        def _active_workspace(self) -> Path:
            workspace_path = self.server.active_workspace
            if workspace_path is None:
                raise NoActiveWorkspace("No active workspace. Open an arena or create one first.")
            return workspace_path

        def _status(self) -> dict:
            models, model_error = _configured_models(self.server.models_path)
            with self.server.state_lock:
                current_job = self.server.jobs.get(self.server.current_job_id)
                job = deepcopy(current_job) if current_job else None
                workspace_path = self.server.active_workspace
            return {
                "workspace": str(workspace_path) if workspace_path else None,
                "models": models,
                "models_error": model_error,
                "job": job,
            }

        def do_GET(self):  # noqa: N802
            if not self._host_allowed():
                self._json(403, {"error": "Invalid host"})
                return
            if self.path == "/":
                page = (
                    PAGE.replace("__TOKEN__", json.dumps(token))
                    .replace("__FOCUS_CRITERIA__", _render_review_focus_criteria())
                    .replace("__MAX_CUSTOM_FOCUS_LENGTH__", str(MAX_CUSTOM_FOCUS_LENGTH))
                )
                self._reply(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self._reply(204, b"", "image/x-icon")
            elif self.path == "/api/status":
                self._json(200, self._status())
            elif self.path == "/api/data":
                try:
                    self._json(200, _data(self._active_workspace()))
                except NoActiveWorkspace as exc:
                    self._json(409, {"error": str(exc)})
                except (OSError, ValueError, KeyError, TypeError):
                    self._json(400, {"error": "Could not load the selected workspace."})
            elif self.path == "/api/prompt":
                try:
                    prompt = (self._active_workspace() / "prompt.md").read_text(encoding="utf-8")
                    self._json(200, {"prompt": prompt})
                except NoActiveWorkspace as exc:
                    self._json(409, {"error": str(exc)})
                except OSError:
                    self._json(404, {"error": "Generated prompt is missing from this workspace."})
            elif self.path.startswith("/api/jobs/"):
                job_id = unquote(self.path.removeprefix("/api/jobs/"))
                with self.server.state_lock:
                    job = self.server.jobs.get(job_id)
                    snapshot = deepcopy(job) if job else None
                if snapshot is None:
                    self._json(404, {"error": "Audit job not found"})
                else:
                    self._json(200, {"job": snapshot})
            else:
                self._json(404, {"error": "Not found"})

        def do_POST(self):  # noqa: N802
            if not self._host_allowed():
                self._json(403, {"error": "Invalid host"})
                return
            if not self._authorized():
                self._json(403, {"error": "Forbidden"})
                return
            allowed = {
                "/api/folders", "/api/workspace/open", "/api/workspace/init", "/api/run",
                "/api/rubric", "/api/adjudicate", "/api/import",
            }
            if self.path not in allowed:
                self._json(404, {"error": "Not found"})
                return
            try:
                payload = self._read_payload()
                if self.path == "/api/folders":
                    raw_path = payload.get("path", "")
                    current = (self.server.starting_directory if raw_path == "" else
                               _path_from_input(raw_path, self.server.starting_directory, "path"))
                    if not current.is_dir():
                        raise ValueError("path must be an existing directory")
                    try:
                        children = sorted(
                            (child for child in current.iterdir() if child.is_dir()),
                            key=lambda child: child.name.casefold(),
                        )
                    except OSError:
                        raise ValueError("This folder cannot be read") from None
                    limit = 200
                    visible_children = children[:limit]
                    self._json(200, {
                        "path": str(current),
                        "parent": str(current.parent) if current.parent != current else None,
                        "is_workspace": (current / "manifest.json").is_file(),
                        "directories": [{
                            "name": child.name,
                            "path": str(child),
                            "is_workspace": (child / "manifest.json").is_file(),
                        } for child in visible_children],
                        "truncated": len(children) > limit,
                    })
                    return
                if self.path == "/api/workspace/open":
                    candidate = _path_from_input(payload.get("path"), self.server.starting_directory, "path")
                    workspace_path = validate_workspace(candidate)
                    data = _data(workspace_path)
                    with self.server.state_lock:
                        self.server.active_workspace = workspace_path
                    self._json(200, data)
                    return
                if self.path == "/api/workspace/init":
                    repo_path = _path_from_input(payload.get("repo"), self.server.starting_directory, "repo")
                    output_path = _path_from_input(payload.get("out"), self.server.starting_directory, "out")
                    if "review_focus" in payload:
                        if "focus" in payload:
                            raise ValueError("Use either review_focus or focus, not both")
                        review_focus = payload["review_focus"]
                        if (not isinstance(review_focus, dict)
                                or set(review_focus) != {"criteria", "custom_text"}):
                            raise ValueError(
                                "review_focus must contain criteria and custom_text"
                            )
                        focus = format_review_focus(
                            review_focus["criteria"], review_focus["custom_text"],
                        )
                        if len(focus) > MAX_FOCUS_LENGTH:
                            raise ValueError("combined review focus must be no longer than 6000 characters")
                    else:
                        focus = payload.get("focus", DEFAULT_FOCUS)
                        if not isinstance(focus, str) or len(focus) > 4000:
                            raise ValueError("focus must be text no longer than 4000 characters")
                    initialize_workspace(repo_path, output_path, focus)
                    workspace_path = validate_workspace(output_path)
                    with self.server.state_lock:
                        self.server.active_workspace = workspace_path
                    self._json(201, _data(workspace_path))
                    return
                if self.path == "/api/run":
                    workspace_path = self._active_workspace()
                    if payload.get("confirm_send_code") is not True:
                        raise ValueError("Confirm that source fragments may be sent to the selected providers")
                    if self.server.models_path is None:
                        raise ValueError("Start the server with --models <path> to enable provider audits")
                    config = load_json(self.server.models_path)
                    configured = config.get("models")
                    if not isinstance(configured, list) or not configured:
                        raise ValueError("models.json must contain a nonempty models array")
                    available: dict[str, dict[str, Any]] = {}
                    for entry in configured:
                        if not isinstance(entry, dict):
                            raise ValueError("Each model entry must be an object")
                        if entry.get("enabled", True) is False:
                            continue
                        model_id = model_slug(entry.get("id"))
                        model_name = entry.get("model")
                        if not isinstance(model_name, str) or not model_name.strip():
                            raise ValueError("Each enabled model must specify a LiteLLM model name")
                        if model_id in available:
                            raise ValueError("Model IDs must be unique")
                        available[model_id] = entry
                    if not available:
                        raise ValueError("models.json has no enabled models")
                    selected = payload.get("models")
                    if not isinstance(selected, list) or not selected:
                        raise ValueError("Select at least one enabled model")
                    if any(not isinstance(item, str) for item in selected):
                        raise ValueError("Model selections must be model IDs")
                    selected_ids = [model_slug(item) for item in selected]
                    if len(set(selected_ids)) != len(selected_ids):
                        raise ValueError("Model selections must be unique")
                    unknown = set(selected_ids) - available.keys()
                    if unknown:
                        raise ValueError("Select only enabled models from models.json")
                    models = [available[model_id] for model_id in selected_ids]
                    existing = [model_id for model_id in selected_ids
                                if (workspace_path / "reports" / f"{model_id}.json").exists()]
                    if existing:
                        raise ValueError(f"Refusing to overwrite existing audits: {', '.join(existing)}")
                    reports = _data(workspace_path)["reports"]
                    existing = [model_id for model_id in selected_ids if model_id in reports]
                    if existing:
                        raise ValueError(f"Refusing to overwrite existing audits: {', '.join(existing)}")
                    options = _validate_run_options(payload)
                    job = _start_audit(self.server, workspace_path, models, options)
                    self._json(202, {"job": job})
                    return

                workspace_path = self._active_workspace()
                model = model_slug(payload.get("model"))
                reports = _data(workspace_path)["reports"]
                if self.path == "/api/import":
                    if model in reports:
                        raise ValueError("Model already has a report; use a new model ID")
                    content = payload.get("content")
                    if not isinstance(content, str):
                        raise ValueError("Report content must be text")
                    manifest = load_json(workspace_path / "manifest.json")
                    approved = {f["path"] for f in manifest["files"]}
                    parsed = parse_report(content, approved)
                    validate_locations(parsed, workspace_path / "snapshot")
                    parsed["audit_metadata"] = {"model_id": model, "source": "manual"}
                    save_json(workspace_path / "reports" / f"{model}.json", parsed)
                else:
                    if model not in reports:
                        raise ValueError("Unknown model")
                    path = workspace_path / "judgments" / f"{model}.json"
                    decision = load_json(path) if path.exists() else {"findings": {}, "rubric": {}}
                    if self.path == "/api/rubric":
                        scores = {key: payload[key] for key in ("improvements", "gold", "rigor")}
                        for key, maximum in (("improvements", 15), ("gold", 20), ("rigor", 5)):
                            if type(scores[key]) is not int or not 0 <= scores[key] <= maximum:
                                raise ValueError(f"Invalid {key} score")
                        decision["rubric"] = scores
                    else:
                        finding = payload.get("finding")
                        if finding not in {item["id"] for item in reports[model]["findings"]}:
                            raise ValueError("Unknown finding")
                        if payload.get("verdict") not in ("pending", "confirmed", "false_positive"):
                            raise ValueError("Invalid verdict")
                        if payload.get("severity") not in ("P0", "P1", "P2", "P3"):
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
            except NoActiveWorkspace as exc:
                self._json(409, {"error": html.escape(str(exc))})
            except RuntimeError as exc:
                self._json(409, {"error": html.escape(str(exc))})
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": html.escape(str(exc))})
            except Exception:
                self._json(500, {"error": "Operation failed. Check the local server output."})

        def log_message(self, fmt, *args):
            pass  # Avoid persisting file paths or report content in server logs.

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.active_workspace = initial_workspace
    server.models_path = configured_models
    server.starting_directory = starting_directory
    server.state_lock = threading.RLock()
    server.jobs = {}
    server.current_job_id = None
    return server


def serve(workspace: Path | None = None, port: int = 8765,
          models: Path | None = None) -> None:
    httpd = create_server(workspace, port, models)
    with httpd:
        print(f"RepoReview Arena: http://127.0.0.1:{httpd.server_port}")
        print("Loopback-only dashboard. Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
