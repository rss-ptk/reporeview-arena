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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .local_data import (
    app_data_directory,
    create_review_id,
    list_reviews,
    load_settings,
    review_directory,
    save_repository_path,
)
from .modes import normalize_review_modes, rubric_fields_for_modes
from .orchestrator import audit_many, model_slug, provider_preferences, reasoning_preferences
from .reports import parse_report, validate_locations
from .repository import validate_repository_root
from .scoring import InvalidJudgmentSeverity, leaderboard, leaderboards
from .storage import load_json, save_json
from .usage import read_usage
from .workspace import (
    DEFAULT_FOCUS,
    FOCUS_PRIORITY_LABELS,
    GOLDEN_FEATURE_ASPECTS,
    MAX_CUSTOM_FOCUS_LENGTH,
    MAX_FOCUS_LENGTH,
    REVIEW_FOCUS_CRITERIA,
    format_golden_feature_direction,
    format_review_focus,
    initialize_workspace,
    normalize_review_configuration,
    update_workspace_review_configuration,
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
table{border-collapse:collapse;width:100%}td,th{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left}th{font-size:11px;color:var(--muted);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}tr:first-child td{font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}#message{margin-top:12px;color:var(--accent)}.save-feedback{margin:8px 0 0;font-size:13px;color:var(--muted)}.save-feedback:empty{display:none}.save-feedback[data-state="success"]{color:var(--accent)}.save-feedback[data-state="error"]{color:var(--red)}
@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:22px 13px}.panel{padding:14px}}
.server-alert{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin:16px 0 0;padding:12px 14px;border:1px solid var(--red);border-left-width:4px;border-radius:8px;background:#2a1622;color:var(--ink)}.server-alert[hidden]{display:none}.server-alert-body{min-width:0}.server-alert p{margin:4px 0;color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere}.server-alert small{color:var(--muted);overflow-wrap:anywhere}.server-alert button{flex:0 0 auto}
@media(max-width:460px){.server-alert{flex-direction:column}.server-alert button{align-self:flex-end}}
.field-heading{display:flex;align-items:center;gap:7px}.help-wrap{position:relative;display:inline-flex;align-items:center}.help-tip{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;padding:0;border-radius:50%;font-size:11px;font-weight:700;line-height:1}.tooltip-content{display:none;position:absolute;left:0;top:calc(100% + 8px);z-index:30;width:min(280px,calc(100vw - 48px));padding:9px 11px;border:1px solid #48627d;border-radius:8px;background:#09111f;color:var(--ink);font-size:12px;font-weight:400;line-height:1.45;text-align:left;white-space:normal;box-shadow:0 8px 24px #0008}.help-wrap:hover .tooltip-content,.help-wrap:focus-within .tooltip-content{display:block}.help-wrap.tip-right .tooltip-content{left:auto;right:0}.field-description{font-size:12px;color:var(--muted);line-height:1.4}
.workspace-accordions{display:grid;gap:10px}.workspace-accordion{border:1px solid var(--line);border-radius:10px;background:#101a2b}.workspace-accordion>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;font-weight:700;cursor:pointer;list-style:none}.workspace-accordion>summary::-webkit-details-marker,.workspace-accordion>summary::marker{display:none;content:""}.workspace-accordion>summary::after{content:"+";color:var(--accent);font-size:20px;line-height:1}.workspace-accordion[open]>summary::after{content:"−"}.workspace-accordion>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:7px}.workspace-accordion-body{border-top:1px solid var(--line);padding:14px}.workspace-accordion-body>.formrow:first-child{margin-top:0}.workspace-accordion .focus-config{grid-column:auto;margin:16px 0 12px}.workspace-accordion .focus-submit{grid-column:auto;margin-top:12px}
.focus-config{grid-column:1/-1;width:100%;min-width:0;border:1px solid var(--line);border-radius:10px;padding:12px;margin:16px 0 12px}.focus-config legend{padding:0 6px;font-weight:700}.focus-config legend .help-wrap{margin-left:4px}.focus-criteria-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:10px 0 14px}.focus-criterion{min-width:0;border:1px solid var(--line);border-radius:8px;background:#101a2b;padding:10px}.focus-criterion .field-heading{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:5px 6px}.focus-criterion label{grid-column:1;grid-row:1;color:var(--ink);line-height:1.35}.focus-criterion .help-wrap{grid-column:2;grid-row:1}.focus-criterion .tooltip-content{left:auto;right:0}.focus-priority-level{grid-column:1/-1;grid-row:2;color:var(--accent);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}.priority-slider{display:block;width:100%;height:26px;margin:7px 0 0;padding:0;border:0;background:transparent;accent-color:var(--accent);cursor:pointer}.priority-slider:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}.custom-focus-field{max-width:100%}.focus-submit{grid-column:1/-1;display:flex;justify-content:flex-end}
@media(max-width:1050px){.focus-criteria-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:700px){.focus-criteria-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:460px){.focus-criteria-grid{grid-template-columns:1fr}}
@media(min-width:801px) and (max-width:1100px){.help-wrap.tip-right .tooltip-content{left:0;right:auto}}
@media(max-width:800px){.tooltip-content{width:min(220px,calc(100vw - 48px))}.help-wrap.tip-right .tooltip-content{left:0;right:auto}}
.contestants{min-width:0;border:1px solid var(--line);border-radius:10px;padding:12px;margin:14px 0 10px}.contestants legend{padding:0 6px;font-weight:700}.contestants:disabled{opacity:.65}.contestants .model-choice{font-size:13px;color:var(--ink)}.model-choice.is-locked{opacity:.7}.contestant-tools{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:10px 0 6px}.contestant-tools button{padding:3px 10px;font-size:12px}
.wizard-shell{margin-top:28px}.wizard-heading{display:grid;grid-template-columns:minmax(0,1fr) minmax(150px,260px);align-items:end;gap:20px}.wizard-progress{height:8px;overflow:hidden;border-radius:99px;background:#25364e}.wizard-progress span{display:block;width:0;height:100%;border-radius:inherit;background:var(--accent);transition:width .2s ease}.wizard-nav{margin-top:18px}.wizard-nav ol{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:0;padding:0;list-style:none}.wizard-step-button{display:grid;grid-template-columns:28px minmax(0,1fr);align-items:center;gap:2px 8px;width:100%;min-height:74px;padding:9px 10px;text-align:left;background:#101a2b;border-color:var(--line)}.wizard-step-button:focus-visible,.wizard-actions button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}.wizard-step-number{grid-row:1 / span 2;display:grid;place-items:center;width:26px;height:26px;border:1px solid #48627d;border-radius:50%;font-size:12px;color:var(--muted)}.wizard-step-button[aria-current="step"]{border-color:#237e8e;background:#13283a}.wizard-step-button[aria-current="step"] .wizard-step-number,.wizard-step-button.is-complete .wizard-step-number{border-color:var(--accent);color:var(--accent)}.wizard-step-button[aria-disabled="true"]{opacity:.68;cursor:not-allowed}.wizard-step-label{font-size:12px;font-weight:700;overflow-wrap:anywhere}.wizard-step-status,.wizard-step-hint{grid-column:2;font-size:10px;line-height:1.3;color:var(--muted);overflow-wrap:anywhere}.wizard-step-status{color:var(--accent)}.wizard-step-hint:empty{display:none}.wizard-panel{margin-top:18px}.wizard-panel[hidden]{display:none}.wizard-actions{display:flex;justify-content:space-between;gap:10px;margin-top:20px;padding-top:14px;border-top:1px solid var(--line)}.wizard-actions button{min-width:104px}.wizard-progress-summary{font-size:12px;color:var(--muted)}#wizard-message{margin-top:10px;color:var(--muted)}#wizard-message:empty{display:none}#partial-scores .card{padding:14px}.partial-score-total{font-size:28px;font-weight:760;letter-spacing:-.04em;color:var(--accent)}
@media(max-width:800px){.wizard-heading{grid-template-columns:1fr}.wizard-progress{max-width:none}.wizard-nav ol{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:460px){.wizard-nav ol{grid-template-columns:1fr}.wizard-step-button{min-height:58px}.wizard-actions button{min-width:0;flex:1}}
@media(prefers-reduced-motion:reduce){.wizard-progress span{transition:none}}
.golden-feature-config{min-width:0;border:1px solid var(--line);border-radius:10px;padding:12px;margin:16px 0 12px}
.golden-feature-config legend{padding:0 6px;font-weight:700}
.golden-feature-aspect-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:8px;margin-top:10px}
.golden-feature-aspect{display:flex;align-items:flex-start;gap:9px;min-width:0;padding:10px;border:1px solid var(--line);border-radius:8px;background:#101a2b;color:var(--ink);cursor:pointer;line-height:1.4}
.golden-feature-aspect input{flex:0 0 auto;margin:2px 0 0;padding:0;accent-color:var(--accent)}
.golden-feature-aspect:focus-within{outline:2px solid var(--accent);outline-offset:2px}
</style></head><body><main>
<header><div><div class="eyebrow" data-i18n="independentAudits">Independent LLM code audits</div><h1>RepoReview <span class="gold">Arena</span></h1><div id="project" class="meta" data-i18n="loadingCompetition">Loading competition…</div></div><div class="header-actions"><span id="language-label" class="language-label" data-i18n="languageLabel">Language</span><div id="language-toggle" class="language-toggle" role="group" aria-labelledby="language-label"><button type="button" class="language-option" data-language="en" aria-pressed="true">EN</button><button type="button" class="language-option" data-language="pt-BR" aria-pressed="false">PT-BR</button></div><button data-i18n="refresh" onclick="reload()">Refresh</button></div></header>
<div id="server-alert" class="server-alert" role="alert" aria-atomic="true" hidden><div class="server-alert-body"><strong data-i18n="serverAlertTitle">Server request failed</strong><p id="server-alert-message"></p><small id="server-alert-context"></small></div><button id="server-alert-dismiss" type="button" data-i18n="serverAlertDismiss">Dismiss</button></div>
<section class="wizard-shell" aria-labelledby="wizard-title">
<div class="wizard-heading"><div><div class="eyebrow" data-i18n="localWorkspace">Review setup</div><h2 id="wizard-title" data-i18n="wizardTitle">Review workflow</h2></div><div id="wizard-progress" class="wizard-progress" role="progressbar" aria-label="Workflow progress" data-i18n-aria-label="wizardProgress" aria-valuemin="1" aria-valuemax="5" aria-valuenow="1" aria-valuetext="Step 1 of 5"><span id="wizard-progress-fill"></span></div></div>
<nav id="wizard-nav" class="wizard-nav" aria-label="Workflow progress" data-i18n-aria-label="wizardProgress"><ol>
<li><button type="button" class="wizard-step-button" data-wizard-step="0" aria-controls="wizard-panel-0" aria-current="step" aria-describedby="wizard-hint-0"><span class="wizard-step-number">1</span><span class="wizard-step-label" data-i18n="wizardStepRepository">Repository</span><span id="wizard-status-0" class="wizard-step-status" aria-live="polite"></span><small id="wizard-hint-0" class="wizard-step-hint"></small></button></li>
<li><button type="button" class="wizard-step-button" data-wizard-step="1" aria-controls="wizard-panel-1" aria-disabled="true" aria-describedby="wizard-hint-1"><span class="wizard-step-number">2</span><span class="wizard-step-label" data-i18n="wizardStepReview">Set up review</span><span id="wizard-status-1" class="wizard-step-status"></span><small id="wizard-hint-1" class="wizard-step-hint"></small></button></li>
<li><button type="button" class="wizard-step-button" data-wizard-step="2" aria-controls="wizard-panel-2" aria-disabled="true" aria-describedby="wizard-hint-2"><span class="wizard-step-number">3</span><span class="wizard-step-label" data-i18n="wizardStepReports">Collect reports</span><span id="wizard-status-2" class="wizard-step-status"></span><small id="wizard-hint-2" class="wizard-step-hint"></small></button></li>
<li><button type="button" class="wizard-step-button" data-wizard-step="3" aria-controls="wizard-panel-3" aria-disabled="true" aria-describedby="wizard-hint-3"><span class="wizard-step-number">4</span><span class="wizard-step-label" data-i18n="wizardStepAdjudication">Validate findings</span><span id="wizard-status-3" class="wizard-step-status"></span><small id="wizard-hint-3" class="wizard-step-hint"></small></button></li>
<li><button type="button" class="wizard-step-button" data-wizard-step="4" aria-controls="wizard-panel-4" aria-disabled="true" aria-describedby="wizard-hint-4"><span class="wizard-step-number">5</span><span class="wizard-step-label" data-i18n="wizardStepResults">Scorecard</span><span id="wizard-status-4" class="wizard-step-status"></span><small id="wizard-hint-4" class="wizard-step-hint"></small></button></li>
</ol></nav>
<div id="wizard-message" role="status" data-i18n="wizardLoading">Loading workflow...</div>
<section class="panel wizard-panel" id="workspace-panel" data-wizard-panel="0" aria-labelledby="wizard-heading-0" hidden>
<div class="row"><div><div class="eyebrow" data-i18n="wizardStepRepository">Repository</div><h2 id="wizard-heading-0" tabindex="-1" data-i18n="repositorySettings">Git repository</h2></div></div>
<p class="muted" data-i18n="repositoryIntro">Choose the Git checkout to review. Review folders are managed automatically.</p>
<div class="formrow">
<div class="field"><div class="field-heading"><label for="repo-path" data-i18n="gitRepositoryRoot">Git repository root</label><span class="help-wrap"><button class="help-tip" type="button" data-i18n-aria-label="helpGitRepositoryRoot" aria-describedby="repo-path-help">?</button><span class="tooltip-content" id="repo-path-help" role="tooltip" data-i18n="gitRepositoryRootHelp">Path to the root of the local Git checkout you want to review.</span></span><button class="field-browse" type="button" data-i18n="browseFolder" data-i18n-title="browseFolderTitle" title="Browse for a Git repository" onclick="openFolderPicker()">Browse</button></div><input id="repo-path" class="pathinput" aria-describedby="repo-path-help" data-i18n-placeholder="gitRepositoryPlaceholder" placeholder="Path to local Git repository"><small class="field-description" data-i18n="gitRepositoryRootDescription">Root folder of a local Git checkout.</small></div>
<button id="save-repository-button" type="button" data-i18n="saveRepository" onclick="saveRepository()" disabled>Save repository</button>
</div>
<div id="repository-message" role="status"></div>
<div class="wizard-actions"><span></span><button type="button" class="primary wizard-next" data-wizard-next="0" data-i18n="wizardContinue">Continue</button></div>
</section>
<div id="arena-content">
<section class="panel wizard-panel" id="wizard-panel-1" data-wizard-panel="1" aria-labelledby="wizard-heading-1" hidden>
<div class="row"><div><div class="eyebrow" data-i18n="wizardStepReview">Set up review</div><h2 id="wizard-heading-1" tabindex="-1" data-i18n="openOrCreateArena">Continue a review or start a new one</h2></div><span class="meta"><span data-i18n="activeReview">Active review</span>: <span id="workspace-active" data-i18n="noArenaSelected">No review loaded</span></span></div>
<p class="muted" data-i18n="wizardReviewIntro">Choose a saved review or create one and set its review priorities.</p>
<div class="formrow">
<div class="field"><label for="review-history" data-i18n="reviewHistory">Previous reviews for this repository</label><select id="review-history" data-i18n-aria-label="reviewHistory" aria-label="Previous reviews"><option value="" data-i18n="noPreviousReviews">No saved reviews</option></select></div>
<button id="open-review-button" type="button" data-i18n="openReview" onclick="openReview()" disabled>Open review</button>
<button id="create-review-button" class="primary" type="button" data-i18n="createReview" onclick="createReview()" disabled>Create review</button>
</div>
<fieldset id="review-mode-picker" class="focus-config" aria-describedby="review-modes-description review-mode-message">
<legend data-i18n="reviewModes">Review outputs</legend>
<p id="review-modes-description" class="field-description" data-i18n="reviewModesDescription">Choose one or both independent review outputs. Both are selected by default.</p>
<label class="model-choice"><input type="checkbox" data-review-mode value="code_audit" checked><span data-i18n="codeAuditMode">Code audit</span></label>
<label class="model-choice"><input type="checkbox" data-review-mode value="golden_feature" checked><span data-i18n="goldenFeatureMode">Golden Feature</span></label>
<div id="review-mode-message" role="status"></div>
</fieldset>
<fieldset id="audit-focus-config" class="focus-config" aria-describedby="review-focus-guidance">
<legend><span data-i18n="reviewFocus">Review focus</span><span class="help-wrap"><button class="help-tip" type="button" data-i18n-aria-label="helpReviewFocus" aria-describedby="review-focus-help">?</button><span class="tooltip-content" id="review-focus-help" role="tooltip" data-i18n="reviewFocusHelp">Set how much each criterion should guide the review. A value of 0 omits that criterion.</span></span></legend>
<p id="review-focus-guidance" class="field-description" data-i18n="reviewFocusDescription">Set each priority from 0 (omit) to 5 (critical). Priorities guide attention; evidence standards stay the same.</p>
<div id="focus-criteria" class="focus-criteria-grid">__FOCUS_CRITERIA__</div>
<div class="field custom-focus-field"><div class="field-heading"><label for="focus-custom-text" data-i18n="customCriteria">Custom criteria</label><span class="help-wrap tip-right"><button class="help-tip" type="button" data-i18n-aria-label="helpCustomCriteria" aria-describedby="focus-custom-help">?</button><span class="tooltip-content" id="focus-custom-help" role="tooltip" data-i18n="customCriteriaHelp">Add additional review criteria, one per line. This text is included as written in the generated prompt.</span></span></div><textarea id="focus-custom-text" maxlength="__MAX_CUSTOM_FOCUS_LENGTH__" data-i18n-placeholder="customCriteriaPlaceholder" placeholder="One custom criterion per line" aria-describedby="focus-custom-help review-focus-guidance"></textarea><small class="field-description" data-i18n="customCriteriaDescription">Optional, up to 4,000 characters. Use one criterion per line.</small></div>
</fieldset>
<fieldset id="golden-feature-aspects" class="golden-feature-config" aria-describedby="golden-feature-direction-description">
<legend data-i18n="goldenFeatureDirection">Golden feature direction</legend>
<p id="golden-feature-direction-description" class="field-description" data-i18n="goldenFeatureDirectionDescription">Select one or more aspects for the single golden feature. The proposal should coherently address every selected aspect; leave all unchecked for no specific direction.</p>
<div class="golden-feature-aspect-list">__GOLDEN_FEATURE_ASPECTS__</div>
</fieldset>
<div id="review-config-message" role="status"></div>
<div id="workspace-message" role="status"></div>
<div class="wizard-actions"><button type="button" class="wizard-back" data-wizard-back="1" data-i18n="wizardBack">Back</button><button type="button" class="primary wizard-next" data-wizard-next="1" data-i18n="wizardContinue">Continue</button></div>
</section>
<section class="wizard-panel" id="wizard-panel-2" data-wizard-panel="2" aria-labelledby="wizard-heading-2" hidden>
<div class="row"><div><div class="eyebrow" data-i18n="wizardStepReports">Collect reports</div><h2 id="wizard-heading-2" tabindex="-1" data-i18n="runAudit">Run an audit</h2></div><button id="new-review-button" type="button" aria-describedby="new-review-help" data-i18n="newReview" onclick="prepareNewReview()" disabled>New review</button></div>
<p id="new-review-help" class="field-description" data-i18n="newReviewHelp">Set up a fresh review with the default priorities. This review will remain in history.</p>
<p class="muted" data-i18n="wizardReportsIntro">Run selected providers or import a report produced elsewhere. Provider audits require fresh consent.</p>
<section class="grid">
<div class="panel"><h3 data-i18n="runAudit">Run an audit</h3><p class="muted" data-i18n="auditDisclosure">Selected providers receive source fragments from the frozen snapshot. Review the snapshot before authorizing a run.</p><fieldset id="contestants-fieldset" class="contestants" aria-describedby="contestants-description contestants-count models-message"><legend data-i18n="contestantsToAudit">Contestants to audit</legend><p id="contestants-description" class="field-description" data-i18n="contestantsDescription">Only enabled models from models.json are listed. Contestants that already have a report are locked, so existing results are never overwritten.</p><div class="contestant-tools"><button type="button" id="contestants-select-all" data-i18n="selectAllContestants" onclick="setAllContestants(true)" disabled>Select all</button><button type="button" id="contestants-clear" data-i18n="clearContestants" onclick="setAllContestants(false)" disabled>Clear selection</button><span id="contestants-count" class="meta" role="status"></span></div><div id="run-models"></div><div id="models-message" class="meta"></div></fieldset><div class="formrow"><div><label for="concurrency" data-i18n="concurrency">Concurrency</label><input type="number" id="concurrency" min="1" max="16" value="2"></div><div><label for="max-steps" data-i18n="maxSteps">Max steps</label><input type="number" id="max-steps" min="1" value="35"></div><div><label for="retries" data-i18n="retries">Retries</label><input type="number" id="retries" min="0" max="5" value="2"></div><div><label for="retry-delay" data-i18n="retryDelay">Retry delay (s)</label><input type="number" id="retry-delay" min="0" max="60" step="0.5" value="5"></div></div><label class="model-choice"><input id="confirm-send-code" type="checkbox"><span data-i18n="authorizeCodeSending">I authorize sending approved source fragments to the selected providers for this run.</span></label><button id="run-button" class="primary" data-i18n="startAudit" onclick="startRun()">Start audit</button><div id="run-message" role="status"></div><div id="job-details"></div></div>
<div class="panel"><div class="row"><h3 data-i18n="generatedReviewPrompt">Generated review prompt</h3><button data-i18n="showPrompt" onclick="loadPrompt()">Show prompt</button></div><p class="muted" data-i18n="promptIntro">Copy this prompt into an IDE or another coding agent.</p><div id="prompt-actions" hidden><button data-i18n="copy" onclick="copyPrompt()">Copy</button> <button data-i18n="downloadPrompt" onclick="downloadPrompt()">Download prompt</button></div><pre id="prompt-text" class="promptbox" hidden></pre><div id="prompt-message" role="status"></div></div>
</section>
<section class="panel"><h3 data-i18n="importManualReport">Import manual report</h3><p class="muted" data-i18n="importExplanation">JSON report produced in any IDE or imported from another system. The model ID may contain only letters, digits, underscores and hyphens.</p><div class="formrow"><input id="importmodel" data-i18n-placeholder="modelIdPlaceholder" data-i18n-aria-label="modelIdLabel" placeholder="model-id"><input type="file" id="importfile" accept=".json,.md,.txt" data-i18n-aria-label="reportFile"><button data-i18n="import" onclick="importReport()">Import</button></div><div id="message" role="status"></div></section>
<section class="panel"><h3 data-i18n="usageTitle">Usage and cost</h3><p class="muted" data-i18n="usageExplanation">Reported OpenRouter credits; cache effect is positive for reported savings and negative for cache-write surcharges. Missing values remain unknown.</p><div id="usage-history"></div></section>
<div class="wizard-actions"><button type="button" class="wizard-back" data-wizard-back="2" data-i18n="wizardBack">Back</button><button type="button" class="primary wizard-next" data-wizard-next="2" data-i18n="wizardContinue">Continue</button></div>
</section>
<section class="wizard-panel" id="wizard-panel-3" data-wizard-panel="3" aria-labelledby="wizard-heading-3" hidden>
<div><div class="eyebrow" data-i18n="wizardStepAdjudication">Validate findings</div><h2 id="wizard-heading-3" tabindex="-1" data-i18n="findingsAdjudication">Findings &amp; adjudication</h2></div>
<p class="muted" data-i18n="wizardAdjudicationIntro">Assign a verdict to every finding, then save the rubric for each report.</p>
<section class="grid">
<div id="review-result-panel" class="panel">
<h3 data-i18n="reviewContestant">Review a contestant</h3>
<div class="formrow"><div><label for="model" data-i18n="model">Model</label><select id="model" onchange="showReport()"></select></div></div>
<p id="abstract" class="muted"></p>
<section id="gold-feature-result"><h3 data-i18n="goldenFeature">Golden Feature</h3><div id="gold"></div></section>
<section id="audit-rubric-fields"><h3 style="margin-top:22px" data-i18n="rubricScores">Rubric scores</h3>
<div class="formrow"><div><label for="improvements" data-i18n="improvements20">Improvements /20</label><input type="number" id="improvements" min="0" max="20"></div></div></section>
<div id="gold-rubric-field" class="formrow"><div><label for="goldscore" data-i18n="gold20">Golden Feature /20</label><input type="number" id="goldscore" min="0" max="20"></div></div>
<div class="formrow"><button id="save-rubric-button" class="primary" data-i18n="saveScores" onclick="saveRubric()">Save scores</button></div>
<p id="rubric-save-message" class="save-feedback" role="status" aria-live="polite" aria-atomic="true"></p>
</div>
<div class="panel" id="audit-findings-panel"><div class="row"><h3 data-i18n="findingsAdjudication">Findings &amp; adjudication</h3><span id="count" class="pill"></span></div><p id="finding-save-message" class="save-feedback" role="status" aria-live="polite" aria-atomic="true"></p><div id="findings"></div></div>
</section>
<section class="panel"><div class="row"><h3 data-i18n="partialScoreboard">Live score preview</h3><span id="partial-score-status" class="meta"></span></div><div id="partial-scores" class="cards" aria-live="polite"></div></section>
<div class="wizard-actions"><button type="button" class="wizard-back" data-wizard-back="3" data-i18n="wizardBack">Back</button><button type="button" class="primary wizard-next" data-wizard-next="3" data-i18n="wizardContinue">Continue</button></div>
</section>
<section class="wizard-panel" id="wizard-panel-4" data-wizard-panel="4" aria-labelledby="wizard-heading-4" hidden>
<div><div class="eyebrow" data-i18n="wizardStepResults">Scorecard</div><h2 id="wizard-heading-4" tabindex="-1" data-i18n="verifiedResultsOnly">Verified results only</h2></div>
<p class="muted" data-i18n="wizardResultsIntro">All current reports have been reviewed. View and export the scorecard.</p>
<section id="audit-score-section" class="panel"><div class="row"><h3 data-i18n="codeAuditScoreboard">Code audit scoreboard (/80)</h3><div class="header-actions"><button data-i18n="downloadScoreJson" onclick="downloadScore()">Download score JSON</button><button data-i18n="exportScorecard" onclick="exportJson()">Export scorecard</button></div></div><div id="summary" class="cards"></div><div style="overflow:auto"><table id="board"></table></div><p class="muted" data-i18n="scoreboardExplanation">Unreviewed findings score zero. Confirmed issues score by reviewed severity. Penalties apply for false positives.</p></section>
<section id="gold-feature-score-section" class="panel"><div class="row"><h3 data-i18n="goldenFeatureScoreboard">Golden Feature scoreboard (/20)</h3><div class="header-actions"><button data-i18n="downloadScoreJson" onclick="downloadScore()">Download score JSON</button><button data-i18n="exportScorecard" onclick="exportJson()">Export scorecard</button></div></div><div style="overflow:auto"><table id="golden-feature-board"></table></div><div class="eyebrow" data-i18n="valueProposals">Value proposals</div><h3 data-i18n="goldenFeatureContenders">Golden Feature proposals</h3><div id="goldpodium" class="cards"></div></section>
<div class="wizard-actions"><button type="button" class="wizard-back" data-wizard-back="4" data-i18n="wizardBack">Back</button></div>
</section>
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
let contestantSelection = null;
let initialRepositoryLoaded = false;
let wizardCurrentStep = null;
let wizardDataReady = false;
let wizardDataError = false;
let wizardProgressState = null;
let folderPickerTarget = null;
let folderPickerData = null;
let folderRequestId = 0;
const token = __TOKEN__;

const translations = {
  en: {
    languageLabel: "Language",
    wizardTitle: "Review workflow",
    wizardLoading: "Loading workflow...",
    wizardProgress: "Workflow progress",
    wizardStepRepository: "Repository",
    wizardStepReview: "Set up review",
    wizardStepReports: "Collect reports",
    wizardStepAdjudication: "Evaluate results",
    wizardStepResults: "Scorecard",
    wizardBack: "Back",
    wizardContinue: "Continue",
    wizardDone: "Complete",
    wizardCurrent: "Current",
    wizardReady: "Ready",
    wizardLocked: "Locked",
    wizardProgressLabel: "Step {current} of 5",
    wizardRepoGate: "Save a valid Git repository to unlock this step.",
    wizardReviewGate: "Create or open a valid review to unlock this step.",
    wizardUnsavedReviewSettings: "Click Continue to apply these settings before collecting reports.",
    wizardReportsGate: "Add a valid report and finish any audit for this review to unlock this step.",
    wizardAuditRunning: "This review's audit is still running.",
    wizardFindingsGate: "Resolve every finding to unlock the scorecard.",
    wizardRubricGate: "Save rubric scores for every report to unlock the scorecard.",
    wizardRefreshFailed: "Workflow state could not be refreshed. Refresh the page to continue.",
    wizardReviewIntro: "Choose a saved review or create one and set its review priorities.",
    reviewSettingsUnsaved: "Changes are pending. Click Continue to update this review's prompt.",
    reviewCreateBeforeContinue: "Create the new review before continuing.",
    savingReviewSettings: "Saving review settings and updating the prompt…",
    reviewSettingsSaved: "Review settings saved and prompt updated.",
    reviewSettingsLocked: "This review already has reports. Create a new review to change its prompt.",
    reviewSettingsLockedByAudit: "Wait for the current audit to finish before changing review settings.",
    wizardReportsIntro: "Run selected providers or import a report produced elsewhere. Provider audits require fresh consent.",
    wizardAdjudicationIntro: "Review findings and score only the deliverables selected for this review.",
    wizardResultsIntro: "All current reports have been reviewed. View and export the scorecard.",
    partialScoreboard: "Live score preview",
    independentAudits: "Independent LLM code audits",
    loadingCompetition: "Loading competition…",
    selectWorkspaceSubtitle: "Select a Git repository to create or open a review",
    projectSummary: "{repo} | {commit} | {count} reviewed files",
    refresh: "Refresh",
    exportScorecard: "Export scorecard",
    localWorkspace: "Review setup",
    repositorySettings: "Git repository",
    repositoryIntro: "Choose the Git checkout to review. Review folders are managed automatically.",
    saveRepository: "Save repository",
    repositorySaved: "Repository saved",
    repositoryNotConfigured: "Choose and save the root of a Git checkout before creating a review.",
    repositorySettingsInvalid: "The saved repository settings could not be read. Choose and save the repository again.",
    repositoryUnavailable: "The saved Git repository is not available at this path. Choose its current root and save again.",
    repositoryDirectoryRequired: "Choose an existing directory.",
    repositoryGitRequired: "Choose the root of a valid Git repository.",
    repositoryRootRequired: "Choose the Git repository root, not a subdirectory.",
    repositoryDirty: "This Git checkout has tracked changes. Commit or stash them before creating a review.",
    serverAlertTitle: "Server request failed",
    serverAlertDismiss: "Dismiss",
    serverAlertContext: "Request {path} · HTTP {status}",
    serverAlertContextNoStatus: "Request {path}",
    serverRequestFailed: "The server returned an error.",
    serverUnreadableResponse: "The server returned a response the app could not read.",
    reviewHistory: "Previous reviews for this repository",
    noPreviousReviews: "No saved reviews",
    openReview: "Open review",
    createReview: "Create new review",
    creatingReview: "Creating review\u2026",
    creatingReviewStatus: "Creating a new review. Please wait\u2026",
    reviewCreated: "Review created and selected",
    reviewOpened: "Review opened",
    newReview: "New review",
    newReviewHelp: "Choose review outputs and priorities for a fresh review. It will remain in history.",
    reviewShort: "Review {id}",
    openOrCreateArena: "Continue a review or start a new one",
    activeReview: "Active review",
    noArenaSelected: "No review loaded",
    workspaceIntro: "Load a saved review folder, or create a new review from a clean Git repository.",
    openExistingArena: "Continue a saved review",
    workspacePath: "Saved review folder",
    helpWorkspacePath: "Help for saved review folder",
    browseFolder: "Browse",
    browseFolderTitle: "Browse for a Git repository",
    folderDialogTitle: "Choose a Git repository root",
    folderDialogIntro: "Navigate to the root of a local Git checkout and select it. Relative paths start from the directory where reporeview web was launched.",
    folderListLabel: "Folders in this directory",
    close: "Close",
    folderUp: "Parent folder",
    chooseCurrentFolder: "Use this repository",
    startAtLaunchFolder: "Start at launch folder",
    loadingFolders: "Loading folders…",
    folderPathNotDirectory: "The selected path is not an existing folder.",
    folderReadFailed: "This folder cannot be read.",
    currentArenaFolder: "This is the root of a Git repository.",
    notArenaFolder: "This folder is not the root of a Git repository.",
    noSubfolders: "No subfolders.",
    arenaFolder: "Git repository",
    regularFolder: "Folder",
    moreFolders: "Only the first {count} folders are shown. Open a folder to continue browsing.",
    openFolder: "Open folder {name}",
    workspacePathHelp: "Select a folder containing a saved review's manifest, snapshot, prompt, reports, and judgments. Relative paths use the directory where you started reporeview web.",
    workspacePathPlaceholder: "Path to saved review folder",
    workspacePathDescription: "Folder with this review's snapshot, prompt, reports, and judgments.",
    openArena: "Load saved review",
    openArenaTitle: "Load the saved review from this folder.",
    createNewArena: "Start a review from Git",
    gitRepositoryRoot: "Git repository root",
    helpGitRepositoryRoot: "Help for Git repository root",
    gitRepositoryRootHelp: "Path to the root of the local Git checkout you want to audit. The checkout must be clean; the Arena snapshots committed files from this repository.",
    gitRepositoryPlaceholder: "Path to local Git repository",
    gitRepositoryRootDescription: "Root folder of a clean local Git checkout.",
    newWorkspacePath: "New review folder",
    helpNewWorkspacePath: "Help for new review folder",
    newWorkspacePathHelp: "Choose a folder that does not exist yet. The new review's snapshot, prompt, reports, and judgments will be saved there. Relative paths use the directory where you started reporeview web.",
    newWorkspacePathPlaceholder: "Path to new review folder",
    newWorkspacePathDescription: "A new folder where this review's files will be saved.",
    reviewFocus: "Priorities for this review",
    helpReviewFocus: "Help for Review focus",
    reviewFocusHelp: "Set how much each criterion should guide the audit. A value of 0 omits that criterion. Higher values guide attention without changing evidence standards.",
    reviewFocusDescription: "Choose priorities from 0 (omit) to 5 (critical). Click Continue to save them and update this review's prompt.",
    reviewModes: "Review outputs",
    reviewModesDescription: "Choose one or both outputs for this review. Both are selected by default.",
    codeAuditMode: "Code audit",
    goldenFeatureMode: "Golden Feature",
    selectReviewMode: "Select Code audit, Golden Feature, or both before continuing.",
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
    goldenFeatureDirection: "Golden Feature direction",
    goldenFeatureDirectionDescription: "Select one or more aspects for the single golden feature. The proposal should coherently address every selected aspect; leave all unchecked for no specific direction.",
    goldenFeatureAspectArchitecture: "Architecture and maintainability",
    goldenFeatureAspectThirdPartyIntegrations: "Third-party integrations",
    goldenFeatureAspectUserFacingFeatures: "User-facing functionality",
    goldenFeatureAspectDeveloperExperience: "Developer experience and tooling",
    goldenFeatureAspectSecurityPrivacy: "Security and privacy",
    goldenFeatureAspectPerformanceScalability: "Performance and scalability",
    goldenFeatureAspectReliabilityAutomation: "Reliability and automation",
    goldenFeatureAspectDataReporting: "Data and reporting",
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
    createArena: "Create review from Git",
    createArenaTitle: "Snapshot this repository and save a new review in the new folder.",
    leaderboard: "Leaderboard",
    verifiedResultsOnly: "Verified results only",
    scoreboard: "Scoreboard",
    downloadScoreJson: "Download score JSON",
    scoreboardExplanation: "Unreviewed findings score zero. Confirmed issues score by reviewed severity. Penalties apply for false positives.",
    valueProposals: "Value proposals",
    goldenFeatureContenders: "Golden Feature proposals",
    runAudit: "Run the selected review",
    auditDisclosure: "Selected providers receive source fragments from the frozen snapshot. Review the snapshot before authorizing a run.",
    concurrency: "Concurrency",
    maxSteps: "Max steps",
    retries: "Retries",
    retryDelay: "Retry delay (s)",
    authorizeCodeSending: "I authorize sending approved source fragments to the selected providers for this run.",
    startAudit: "Start review",
    generatedReviewPrompt: "Generated review prompt",
    showPrompt: "Show prompt",
    promptIntro: "Copy this prompt into an IDE or another coding agent.",
    copy: "Copy",
    downloadPrompt: "Download prompt",
    reviewContestant: "Review a contestant",
    model: "Model",
    goldenFeature: "Golden Feature",
    rubricScores: "Rubric scores",
    improvements20: "Improvements /20",
    gold20: "Golden Feature /20",
    saveScores: "Save scores",
    savingFinding: "Saving finding…",
    findingSaved: "Finding {finding} saved.",
    savingScores: "Saving scores…",
    rubricSaved: "Rubric scores saved.",
    saveFailed: "Could not save: {error}",
    importManualReport: "Import manual report",
    importExplanation: "JSON report produced in any IDE or imported from another system. The model ID may contain only letters, digits, underscores and hyphens.",
    modelIdPlaceholder: "model-id",
    modelIdLabel: "Model ID",
    reportFile: "Report file",
    import: "Import",
    findingsAdjudication: "Evaluate selected results",
    codeAuditScoreboard: "Code audit scoreboard (/80)",
    goldenFeatureScoreboard: "Golden Feature scoreboard (/20)",
    auditScore: "Code audit score /80",
    featureScore: "Golden Feature score /20",
    proposal: "Proposal",
    chooseModels: "Choose one or more contestants to audit.",
    contestantsToAudit: "Models for this review",
    contestantsDescription: "Only enabled models from models.json are listed. A report in this review locks that model; reports in other reviews do not. Create a new review to run the same model again.",
    selectAllContestants: "Select all",
    clearContestants: "Clear selection",
    contestantsSelected: "Selected: {selected} of {available}",
    contestantHasReport: "Report in this review",
    noRunnableContestants: "Every enabled model already has a report in this review. Start a new review to run them again.",
    selectAtLeastOneContestant: "Select at least one contestant to audit.",
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
    reportSaved: "report saved",
    contestants: "Contestants",
    validatedFindings: "Validated findings",
    pendingReview: "Pending review",
    topScore: "Top score",
    rank: "Rank",
    bugs: "Bugs",
    improve: "Improve",
    gold: "Gold",
    penalties: "Penalties",
    total: "Total",
    pending: "Pending",
    tokens: "Tokens",
    usageTitle: "Usage and cost",
    usageExplanation: "Reported OpenRouter credits; cache effect is positive for reported savings and negative for cache-write surcharges. Missing values remain unknown.",
    usageEmpty: "No usage records. Costs for older or imported reports are unavailable.",
    usageRun: "Run",
    usageAttempts: "Attempts",
    usageInput: "Input",
    usageOutput: "Output",
    usageCache: "Cache read",
    usageCacheWrite: "Cache written",
    usageCacheRate: "Cache rate",
    usageCacheEffect: "Cache cost effect",
    usageDiscountCoverage: "{known} of {total} attempts with reported cache effect",
    usageSession: "OpenRouter session",
    usageReasoning: "Reasoning",
    usageCost: "OpenRouter cost",
    usageUpstream: "Provider cost",
    usageUnknown: "Unknown",
    usageCoverage: "{known} of {total} attempts with cost",
    usage_complete: "complete",
    usage_partial: "partial",
    usage_unavailable: "unavailable",
    usageStep: "Step {step}, attempt {attempt}",
    usageGeneration: "Generation",
    usageRunning: "in progress",
    usageFailed: "failed",
    usageReceived: "response received",
    usageProvider: "Provider",
    usageError: "Error",
    usageModel: "Resolved model",
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
    errorMissingPageToken: "The request did not include the dashboard security token. Reload the page from {url}, then try again.",
    errorStalePageToken: "This page's security token is out of date, usually because the server restarted. Reload from {url}, then try again.",
    errorOriginMismatch: "The request came from a different page origin. Open the dashboard at {url}, then try again.",
    errorInvalidHost: "The dashboard only accepts requests through {url}. Open that exact address, then try again.",
  },
  "pt-BR": {
    languageLabel: "Idioma",
    errorMissingPageToken: "A requisi\u00e7\u00e3o n\u00e3o incluiu o token de seguran\u00e7a do painel. Recarregue a p\u00e1gina em {url} e tente novamente.",
    errorStalePageToken: "O token de seguran\u00e7a desta p\u00e1gina est\u00e1 desatualizado, geralmente porque o servidor foi reiniciado. Recarregue em {url} e tente novamente.",
    errorOriginMismatch: "A requisi\u00e7\u00e3o veio de outra origem. Abra o painel em {url} e tente novamente.",
    errorInvalidHost: "O painel aceita requisi\u00e7\u00f5es somente pelo endere\u00e7o {url}. Abra exatamente esse endere\u00e7o e tente novamente.",
    wizardTitle: "Fluxo da revis\u00e3o",
    wizardLoading: "Carregando o fluxo...",
    wizardProgress: "Progresso do fluxo",
    wizardStepRepository: "Reposit\u00f3rio",
    wizardStepReview: "Configurar revis\u00e3o",
    wizardStepReports: "Coletar relat\u00f3rios",
    wizardStepAdjudication: "Avaliar resultados",
    wizardStepResults: "Placar",
    wizardBack: "Voltar",
    wizardContinue: "Continuar",
    wizardDone: "Conclu\u00edda",
    wizardCurrent: "Atual",
    wizardReady: "Liberada",
    wizardLocked: "Bloqueada",
    wizardProgressLabel: "Etapa {current} de 5",
    wizardRepoGate: "Salve um reposit\u00f3rio Git v\u00e1lido para liberar esta etapa.",
    wizardReviewGate: "Crie ou abra uma revis\u00e3o v\u00e1lida para liberar esta etapa.",
    wizardUnsavedReviewSettings: "Clique em Continuar para aplicar estas configura\u00e7\u00f5es antes de coletar relat\u00f3rios.",
    wizardReportsGate: "Adicione um relat\u00f3rio v\u00e1lido e aguarde a auditoria desta revis\u00e3o para liberar esta etapa.",
    wizardAuditRunning: "A auditoria desta revis\u00e3o ainda est\u00e1 em andamento.",
    wizardFindingsGate: "Defina um veredito para cada achado para liberar o placar.",
    wizardRubricGate: "Salve as notas da rubrica de cada relat\u00f3rio para liberar o placar.",
    wizardRefreshFailed: "N\u00e3o foi poss\u00edvel atualizar o fluxo. Atualize a p\u00e1gina para continuar.",
    wizardReviewIntro: "Escolha uma revis\u00e3o salva ou crie uma e defina suas prioridades.",
    reviewSettingsUnsaved: "H\u00e1 altera\u00e7\u00f5es pendentes. Clique em Continuar para atualizar o prompt desta revis\u00e3o.",
    reviewCreateBeforeContinue: "Crie a nova revis\u00e3o antes de continuar.",
    savingReviewSettings: "Salvando as configura\u00e7\u00f5es e atualizando o prompt\u2026",
    reviewSettingsSaved: "Configura\u00e7\u00f5es salvas e prompt atualizado.",
    reviewSettingsLocked: "Esta revis\u00e3o j\u00e1 tem relat\u00f3rios. Crie uma nova revis\u00e3o para alterar o prompt.",
    reviewSettingsLockedByAudit: "Aguarde a auditoria atual terminar antes de alterar as configura\u00e7\u00f5es.",
    wizardReportsIntro: "Execute auditorias nos provedores selecionados ou importe um relat\u00f3rio. Cada auditoria exige novo consentimento.",
    wizardAdjudicationIntro: "Revise os achados e dê notas apenas aos resultados selecionados nesta revisão.",
    wizardResultsIntro: "Todos os relat\u00f3rios atuais foram revisados. Consulte e exporte o placar.",
    partialScoreboard: "Pr\u00e9via do placar",
    independentAudits: "Auditorias independentes de código com LLMs",
    loadingCompetition: "Carregando arena…",
    selectWorkspaceSubtitle: "Selecione um repositório Git para criar ou abrir uma revisão",
    projectSummary: "{repo} | {commit} | {count} arquivos analisados",
    refresh: "Atualizar",
    exportScorecard: "Exportar quadro de pontuação",
    localWorkspace: "Configuração da revisão",
    repositorySettings: "Reposit\u00f3rio Git",
    repositoryIntro: "Escolha o checkout Git para revisar. As pastas das revis\u00f5es ser\u00e3o gerenciadas automaticamente.",
    saveRepository: "Salvar reposit\u00f3rio",
    repositorySaved: "Reposit\u00f3rio salvo",
    repositoryNotConfigured: "Escolha e salve a raiz de um checkout Git antes de criar uma revis\u00e3o.",
    repositorySettingsInvalid: "N\u00e3o foi poss\u00edvel ler as configura\u00e7\u00f5es salvas do reposit\u00f3rio. Escolha e salve o reposit\u00f3rio novamente.",
    repositoryUnavailable: "O reposit\u00f3rio Git salvo n\u00e3o est\u00e1 dispon\u00edvel neste caminho. Escolha a raiz atual e salve novamente.",
    repositoryDirectoryRequired: "Escolha uma pasta existente.",
    repositoryGitRequired: "Escolha a raiz de um reposit\u00f3rio Git v\u00e1lido.",
    repositoryRootRequired: "Escolha a raiz do reposit\u00f3rio Git, n\u00e3o uma subpasta.",
    repositoryDirty: "Este checkout Git tem altera\u00e7\u00f5es em arquivos controlados. Fa\u00e7a commit ou stash antes de criar uma revis\u00e3o.",
    serverAlertTitle: "Falha na solicita\u00e7\u00e3o ao servidor",
    serverAlertDismiss: "Fechar",
    serverAlertContext: "Requisi\u00e7\u00e3o {path} \u00b7 HTTP {status}",
    serverAlertContextNoStatus: "Requisi\u00e7\u00e3o {path}",
    serverRequestFailed: "O servidor retornou um erro.",
    serverUnreadableResponse: "O app n\u00e3o conseguiu interpretar a resposta do servidor.",
    reviewHistory: "Revis\u00f5es anteriores deste reposit\u00f3rio",
    noPreviousReviews: "Nenhuma revis\u00e3o salva",
    openReview: "Abrir revis\u00e3o",
    createReview: "Criar nova revis\u00e3o",
    creatingReview: "Criando revis\u00e3o\u2026",
    creatingReviewStatus: "Criando uma nova revis\u00e3o. Aguarde\u2026",
    reviewCreated: "Revis\u00e3o criada e selecionada",
    reviewOpened: "Revis\u00e3o aberta",
    newReview: "Nova revis\u00e3o",
    newReviewHelp: "Escolha os resultados e as prioridades de uma nova revisão. Ela continuará no histórico.",
    reviewShort: "Revis\u00e3o {id}",
    openOrCreateArena: "Continuar uma revisão ou começar outra",
    activeReview: "Revisão ativa",
    noArenaSelected: "Nenhuma revisão carregada",
    workspaceIntro: "Carregue uma pasta de revisão salva ou crie uma revisão a partir de um repositório Git sem alterações pendentes.",
    openExistingArena: "Continuar uma revisão salva",
    workspacePath: "Pasta da revisão salva",
    helpWorkspacePath: "Ajuda sobre a pasta de revisão salva",
    browseFolder: "Procurar",
    browseFolderTitle: "Procurar um repositório Git",
    folderDialogTitle: "Escolha a raiz de um repositório Git",
    folderDialogIntro: "Navegue até a raiz de um checkout Git local e selecione-a. Caminhos relativos partem da pasta em que reporeview web foi iniciado.",
    folderListLabel: "Pastas neste diretório",
    close: "Fechar",
    folderUp: "Pasta acima",
    chooseCurrentFolder: "Usar este repositório",
    startAtLaunchFolder: "Ir para a pasta inicial",
    loadingFolders: "Carregando pastas…",
    folderPathNotDirectory: "O caminho escolhido não corresponde a uma pasta existente.",
    folderReadFailed: "Não foi possível ler esta pasta.",
    currentArenaFolder: "Esta pasta é a raiz de um repositório Git.",
    notArenaFolder: "Esta pasta não é a raiz de um repositório Git.",
    noSubfolders: "Não há subpastas.",
    arenaFolder: "Repositório Git",
    regularFolder: "Pasta",
    moreFolders: "Exibindo apenas as primeiras {count} pastas. Abra uma pasta para continuar navegando.",
    openFolder: "Abrir a pasta {name}",
    workspacePathHelp: "Selecione uma pasta com o manifesto, snapshot, instrução, relatórios e avaliações de uma revisão salva. Caminhos relativos usam a pasta em que você iniciou reporeview web.",
    workspacePathPlaceholder: "Caminho da pasta de revisão salva",
    workspacePathDescription: "Pasta com o snapshot, a instrução, os relatórios e as avaliações desta revisão.",
    openArena: "Carregar revisão salva",
    openArenaTitle: "Carrega a revisão salva nesta pasta.",
    createNewArena: "Iniciar revisão a partir do Git",
    gitRepositoryRoot: "Raiz do repositório Git",
    helpGitRepositoryRoot: "Ajuda sobre a raiz do repositório Git",
    gitRepositoryRootHelp: "Caminho para a raiz do checkout Git local que será auditado. O checkout precisa estar limpo; a arena cria um snapshot dos arquivos commitados.",
    gitRepositoryPlaceholder: "Caminho do repositório Git local",
    gitRepositoryRootDescription: "Pasta raiz de um checkout Git local sem alterações pendentes.",
    newWorkspacePath: "Pasta da nova revisão",
    helpNewWorkspacePath: "Ajuda sobre a nova pasta de revisão",
    newWorkspacePathHelp: "Escolha uma pasta que ainda não exista. O snapshot, a instrução, os relatórios e as avaliações da nova revisão serão salvos nela. Caminhos relativos usam a pasta em que você iniciou reporeview web.",
    newWorkspacePathPlaceholder: "Caminho da nova pasta de revisão",
    newWorkspacePathDescription: "Pasta nova onde os arquivos desta revisão serão salvos.",
    reviewFocus: "Prioridades desta revis\u00e3o",
    helpReviewFocus: "Ajuda sobre o foco da revisão",
    reviewFocusHelp: "Defina quanto cada critério deve orientar a auditoria. O valor 0 omite o critério. Valores maiores aumentam a atenção sem alterar o padrão de evidências.",
    reviewFocusDescription: "Escolha prioridades de 0 (omitir) a 5 (cr\u00edtica). Clique em Continuar para salv\u00e1-las e atualizar o prompt desta revis\u00e3o.",
    reviewModes: "Resultados da revisão",
    reviewModesDescription: "Escolha um ou os dois resultados desta revis\u00e3o. Ambos come\u00e7am selecionados.",
    codeAuditMode: "Auditoria de código",
    goldenFeatureMode: "Golden Feature",
    selectReviewMode: "Selecione Auditoria de c\u00f3digo, Golden Feature ou ambas antes de continuar.",
    priorityAriaValue: "Prioridade {value} de 5: {label}.",
    goldenFeatureDirection: "Direcionamento da Golden Feature",
    goldenFeatureDirectionDescription: "Selecione um ou mais aspectos para a única golden feature. A proposta deve atender de forma coerente a todos os aspectos marcados; deixe tudo desmarcado para não direcionar a proposta.",
    goldenFeatureAspectArchitecture: "Arquitetura e manutenção",
    goldenFeatureAspectThirdPartyIntegrations: "Integrações de terceiros",
    goldenFeatureAspectUserFacingFeatures: "Funcionalidades para usuários",
    goldenFeatureAspectDeveloperExperience: "Experiência de desenvolvimento e ferramentas",
    goldenFeatureAspectSecurityPrivacy: "Segurança e privacidade",
    goldenFeatureAspectPerformanceScalability: "Desempenho e escala",
    goldenFeatureAspectReliabilityAutomation: "Confiabilidade e automação",
    goldenFeatureAspectDataReporting: "Dados e relatórios",
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
    createArena: "Criar revisão a partir do Git",
    createArenaTitle: "Cria um snapshot deste repositório e salva a nova revisão na pasta indicada.",
    leaderboard: "Classificação",
    verifiedResultsOnly: "Somente resultados verificados",
    scoreboard: "Placar",
    downloadScoreJson: "Baixar placar em JSON",
    scoreboardExplanation: "Achados não revisados valem zero. Problemas confirmados pontuam conforme a severidade revisada. Falsos positivos geram penalidades.",
    valueProposals: "Propostas de valor",
    goldenFeatureContenders: "Propostas de Golden Feature",
    runAudit: "Executar revisão selecionada",
    auditDisclosure: "Os provedores selecionados receberão trechos do snapshot congelado. Revise o snapshot antes de autorizar a execução.",
    concurrency: "Execuções simultâneas",
    maxSteps: "Máximo de etapas",
    retries: "Novas tentativas",
    retryDelay: "Intervalo entre tentativas (s)",
    authorizeCodeSending: "Autorizo o envio de trechos aprovados do código aos provedores selecionados nesta execução.",
    startAudit: "Iniciar revisão",
    generatedReviewPrompt: "Instrução de revisão gerada",
    showPrompt: "Exibir instrução",
    promptIntro: "Copie esta instrução para uma IDE ou outro agente de programação.",
    copy: "Copiar",
    downloadPrompt: "Baixar instrução",
    reviewContestant: "Avaliar um modelo",
    model: "Modelo",
    goldenFeature: "Golden Feature",
    rubricScores: "Notas da rubrica",
    improvements20: "Melhorias /20",
    gold20: "Golden Feature /20",
    saveScores: "Salvar notas",
    savingFinding: "Salvando achado…",
    findingSaved: "Achado {finding} salvo.",
    savingScores: "Salvando notas…",
    rubricSaved: "Notas da rubrica salvas.",
    saveFailed: "Não foi possível salvar: {error}",
    importManualReport: "Importar relatório manual",
    importExplanation: "Relatório JSON produzido em uma IDE ou importado de outro sistema. O ID do modelo pode conter apenas letras, números, sublinhados e hífens.",
    modelIdPlaceholder: "id-do-modelo",
    modelIdLabel: "ID do modelo",
    reportFile: "Arquivo do relatório",
    import: "Importar",
    findingsAdjudication: "Avaliar resultados selecionados",
    codeAuditScoreboard: "Placar da auditoria de código (/80)",
    goldenFeatureScoreboard: "Placar da Golden Feature (/20)",
    auditScore: "Nota da auditoria de código /80",
    featureScore: "Nota da Golden Feature /20",
    proposal: "Proposta",
    chooseModels: "Escolha um ou mais modelos participantes para auditar.",
    contestantsToAudit: "Modelos desta revisão",
    contestantsDescription: "Somente modelos habilitados em models.json aparecem aqui. Um relatório nesta revisão bloqueia o modelo; relatórios de outras revisões não bloqueiam. Crie uma nova revisão para usar o mesmo modelo novamente.",
    selectAllContestants: "Selecionar todos",
    clearContestants: "Limpar seleção",
    contestantsSelected: "Selecionados: {selected} de {available}",
    contestantHasReport: "Relatório nesta revisão",
    noRunnableContestants: "Todos os modelos habilitados já têm relatório nesta revisão. Inicie uma nova revisão para usá-los novamente.",
    selectAtLeastOneContestant: "Selecione ao menos um modelo participante para auditar.",
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
    reportSaved: "relatório salvo",
    contestants: "Modelos participantes",
    validatedFindings: "Achados validados",
    pendingReview: "Aguardando revisão",
    topScore: "Maior pontuação",
    rank: "Posição",
    bugs: "Bugs",
    improve: "Melhorias",
    gold: "Destaque",
    penalties: "Penalidades",
    total: "Total",
    pending: "Pendentes",
    tokens: "Tokens",
    usageTitle: "Uso e custo",
    usageExplanation: "Créditos reportados pela OpenRouter; o efeito do cache é positivo para economias reportadas e negativo para custos adicionais de gravação. Valores ausentes permanecem indisponíveis.",
    usageEmpty: "Sem registros de uso. Custos de relatórios antigos ou importados não estão disponíveis.",
    usageRun: "Execução",
    usageAttempts: "Tentativas",
    usageInput: "Entrada",
    usageOutput: "Saída",
    usageCache: "Cache lido",
    usageCacheWrite: "Cache gravado",
    usageCacheRate: "Taxa de cache",
    usageCacheEffect: "Efeito financeiro do cache",
    usageDiscountCoverage: "{known} de {total} tentativas com efeito do cache reportado",
    usageSession: "Sessão OpenRouter",
    usageReasoning: "Raciocínio",
    usageCost: "Custo OpenRouter",
    usageUpstream: "Custo do provedor",
    usageUnknown: "Indisponível",
    usageCoverage: "{known} de {total} tentativas com custo",
    usage_complete: "completo",
    usage_partial: "parcial",
    usage_unavailable: "indisponível",
    usageStep: "Rodada {step}, tentativa {attempt}",
    usageGeneration: "Geração",
    usageRunning: "em andamento",
    usageFailed: "falhou",
    usageReceived: "resposta recebida",
    usageProvider: "Provedor",
    usageError: "Erro",
    usageModel: "Modelo usado",
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
let reviewCreationInProgress = false;
let reviewSetupSaving = false;
let reviewSetupBaseline = null;
let reviewSetupPending = false;
let reviewSetupBaselineResetRequested = false;
let reviewSetupNeedsSave = false;

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
  const text = t(key, values);
  element.dataset.translationKey = key;
  element.dataset.translationValues = JSON.stringify(values || {});
  // Skip identical writes so the polling re-render does not re-announce live regions.
  if (element.textContent !== text) element.textContent = text;
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
  if (reviewCreationInProgress) {
    document.getElementById("create-review-button").textContent = t("creatingReview");
  }
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
  updateActiveReviewLabel();
  renderProjectLabel();
  const currentReport = state.reports && state.reports[document.getElementById("model").value];
  if (currentReport && !currentReport.summary) {
    document.getElementById("abstract").textContent = t("noSummary");
  }
  const modeMessage = document.getElementById("review-mode-message");
  if (modeMessage) {
    setMessage(modeMessage.id, selectedReviewModes().length ? "" : t("selectReviewMode"));
  }
  refreshLocalizedMessages();
  if (dashboardStatus.models) renderModels();
  if (state.leaderboard) renderBoard();
  renderUsage();
  if (dashboardStatus.job) renderJob(dashboardStatus.job);
  renderWizard();
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
  delete element.dataset.state;
  element.textContent = text || "";
}
function setSaveFeedback(id, key, values, state) {
  document.getElementById(id).dataset.state = state;
  setLocalizedMessage(id, key, values);
}
function setSaveError(id, error) {
  setSaveFeedback(id, "saveFailed", {
    error: error && error.message ? error.message : String(error),
  }, "error");
}
function setRepositoryError(id, error) {
  const message = String(error.message || "");
  if (message.includes("Git tracked files are dirty")) {
    setLocalizedMessage(id, "repositoryDirty");
    return;
  }
  if (message === "--repo must be an existing directory") {
    setLocalizedMessage(id, "repositoryDirectoryRequired");
    return;
  }
  if (message === "--repo must be a valid Git repository") {
    setLocalizedMessage(id, "repositoryGitRequired");
    return;
  }
  if (message === "Pass the Git repository root, not a subdirectory") {
    setLocalizedMessage(id, "repositoryRootRequired");
    return;
  }
  setMessage(id, message);
}
function dismissServerAlert() {
  document.getElementById("server-alert").hidden = true;
}
function showServerAlert(message, path, status) {
  const alert = document.getElementById("server-alert");
  const messageElement = document.getElementById("server-alert-message");
  const context = document.getElementById("server-alert-context");
  const text = String(message || t("serverRequestFailed"));
  const endpoint = String(path || "");
  const hasStatus = status !== undefined && status !== null;
  const contextKey = hasStatus ? "serverAlertContext" : "serverAlertContextNoStatus";
  const contextValues = hasStatus
    ? {path: endpoint, status: status}
    : {path: endpoint};
  const unchanged = !alert.hidden && messageElement.textContent === text &&
    context.dataset.translationKey === contextKey &&
    context.dataset.translationValues === JSON.stringify(contextValues);
  if (unchanged) return;
  messageElement.textContent = text;
  setLocalizedMessage("server-alert-context", contextKey, contextValues);
  alert.hidden = false;
}
async function api(path, payload) {
  const hasPayload = payload !== undefined;
  let response;
  try {
    response = await fetch(path, {
      method: hasPayload ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-Arena-Token": token },
      body: hasPayload ? JSON.stringify(payload) : undefined,
    });
  } catch (error) {
    const message = error && error.message ? error.message : t("serverRequestFailed");
    showServerAlert(message, path);
    throw error;
  }
  const responseText = await response.text();
  let data;
  try {
    data = responseText ? JSON.parse(responseText) : {};
  } catch (error) {
    if (response.ok) {
      const message = t("serverUnreadableResponse");
      showServerAlert(message, path, response.status);
      throw Error(message);
    }
    data = {error: responseText.trim()};
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    data = {error: String(data || "")};
  }
  if (!response.ok) {
    const errorKeys = {
      missing_page_token: "errorMissingPageToken",
      stale_page_token: "errorStalePageToken",
      origin_mismatch: "errorOriginMismatch",
      invalid_host: "errorInvalidHost",
    };
    const errorKey = errorKeys[data.error_code];
    const message = errorKey
      ? t(errorKey, {url: data.dashboard_url || location.origin})
      : String(data.error || data.message || t("serverRequestFailed"));
    showServerAlert(message, path, response.status);
    throw Error(message);
  }
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
  folderPickerTarget = "repo-path";
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
    data.is_git_repository ? "currentArenaFolder" : "notArenaFolder"
  );
  document.getElementById("folder-parent").disabled = !data.parent;
  document.getElementById("folder-select").disabled = !data.is_git_repository;
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
      node("span", "folder-badge", t(directory.is_git_repository ? "arenaFolder" : "regularFolder")),
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
  if (!folderPickerData || !folderPickerData.is_git_repository || !folderPickerTarget) return;
  const input = document.getElementById(folderPickerTarget);
  input.value = folderPickerData.path;
  document.getElementById("folder-dialog").close();
  folderPickerData = null;
  folderPickerTarget = null;
  input.focus();
  updateWorkspaceActions();
}

function closeFolderPicker() {
  const dialog = document.getElementById("folder-dialog");
  folderRequestId += 1;
  if (dialog.open) dialog.close();
  folderPickerData = null;
  folderPickerTarget = null;
}

// BEGIN review-modes
function selectedReviewModes() {
  return Array.from(document.querySelectorAll("[data-review-mode]"))
    .filter(function (input) { return input.checked; })
    .map(function (input) { return input.value; });
}
// BEGIN review-setup
function reviewSetupSignature() {
  return JSON.stringify({
    modes: selectedReviewModes(),
    criteria: Array.from(document.querySelectorAll(".priority-slider"))
      .map(function (slider) {
        return [slider.dataset.priorityId, Number(slider.value)];
      }),
    customText: document.getElementById("focus-custom-text").value,
    goldenFeatureAspects: Array.from(
      document.querySelectorAll("[data-golden-feature-aspect]"),
    ).filter(function (aspect) { return aspect.checked; })
      .map(function (aspect) { return aspect.value; }),
  });
}
function reviewSetupHasUnsavedChanges() {
  return reviewSetupPending || reviewSetupNeedsSave ||
    (reviewSetupBaseline !== null && reviewSetupSignature() !== reviewSetupBaseline);
}
function rememberReviewSetupBaseline() {
  reviewSetupBaseline = reviewSetupSignature();
  reviewSetupPending = false;
  reviewSetupNeedsSave = false;
}
// END review-setup
function setReviewModeSelection(modes) {
  const selected = new Set(modes || ["code_audit", "golden_feature"]);
  document.querySelectorAll("[data-review-mode]").forEach(function (input) {
    input.checked = selected.has(input.value);
  });
  updateReviewModeControls();
}
function updateReviewModeControls() {
  const modes = selectedReviewModes();
  document.getElementById("audit-focus-config").hidden = !modes.includes("code_audit");
  document.getElementById("golden-feature-aspects").hidden = !modes.includes("golden_feature");
  setMessage("review-mode-message", modes.length ? "" : t("selectReviewMode"));
  updateWorkspaceActions();
}
// END review-modes

function updateWorkspaceActions() {
  const repositoryInput = document.getElementById("repo-path");
  const selectedPath = repositoryInput.value.trim();
  const savedPath = dashboardStatus.repository_path || "";
  const hasReports = Object.keys(state.reports || {}).length > 0;
  const settingsLockedByReports = hasReports && !reviewSetupPending;
  const settingsLockedByAudit = jobIsActive(dashboardStatus.job);
  const settingsDisabled = reviewCreationInProgress || reviewSetupSaving || settingsLockedByAudit ||
    settingsLockedByReports;
  document.getElementById("save-repository-button").disabled =
    !selectedPath || (selectedPath === savedPath && dashboardStatus.repository_available);
  document.getElementById("create-review-button").disabled =
    reviewCreationInProgress || reviewSetupSaving || !selectedReviewModes().length ||
    !dashboardStatus.repository_available || selectedPath !== savedPath;
  document.getElementById("open-review-button").disabled =
    reviewSetupSaving || !document.getElementById("review-history").value;
  document.getElementById("new-review-button").disabled =
    reviewCreationInProgress || reviewSetupSaving || !dashboardStatus.repository_available ||
    selectedPath !== savedPath || !dashboardStatus.workspace ||
    jobIsActive(dashboardStatus.job);
  ["review-mode-picker", "audit-focus-config", "golden-feature-aspects"]
    .forEach(function (id) {
      document.getElementById(id).disabled = settingsDisabled;
    });
  const reviewConfigMessage = document.getElementById("review-config-message");
  if (!reviewCreationInProgress && reviewSetupPending) {
    setLocalizedMessage("review-config-message", "reviewCreateBeforeContinue");
  } else if (!reviewCreationInProgress && settingsLockedByReports) {
    setLocalizedMessage("review-config-message", "reviewSettingsLocked");
  } else if (!reviewCreationInProgress && settingsLockedByAudit) {
    setLocalizedMessage("review-config-message", "reviewSettingsLockedByAudit");
  } else if (!reviewCreationInProgress && reviewSetupHasUnsavedChanges()) {
    clearGeneratedPrompt();
    setLocalizedMessage("review-config-message", "reviewSettingsUnsaved");
  } else if (["reviewSettingsUnsaved", "reviewCreateBeforeContinue",
      "reviewSettingsLocked", "reviewSettingsLockedByAudit"].includes(
      reviewConfigMessage.dataset.translationKey)) {
    setMessage("review-config-message", "");
  }
  renderWizard();
}

async function renderReviewHistory() {
  const data = await api("/api/reviews");
  const select = clear("review-history");
  const reviews = data.reviews || [];
  if (!reviews.length) {
    option(select, "", t("noPreviousReviews"), "noPreviousReviews");
    select.disabled = true;
    return;
  }
  reviews.forEach(function (review) {
    const date = new Date(review.created_at);
    const dateText = Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
    const label = [review.repo_name, review.commit.slice(0, 12), dateText]
      .filter(Boolean).join(" | ");
    option(select, review.id, label);
  });
  select.disabled = false;
  if (dashboardStatus.review_id && reviews.some(function (item) {
    return item.id === dashboardStatus.review_id;
  })) {
    select.value = dashboardStatus.review_id;
  }
}

function updateActiveReviewLabel() {
  const label = document.getElementById("workspace-active");
  if (!state.manifest) {
    label.textContent = t("noArenaSelected");
    return;
  }
  const reviewId = state.manifest.review_id || dashboardStatus.review_id;
  const parts = [state.manifest.repo_name, state.manifest.commit.slice(0, 12)];
  if (reviewId) parts.push(t("reviewShort", {id: reviewId.slice(0, 8)}));
  label.textContent = parts.join(" | ");
}

async function reload() {
  try {
    const previousWorkspace = dashboardStatus.workspace;
    dashboardStatus = await api("/api/status");
    const workspaceChanged = previousWorkspace !== dashboardStatus.workspace;
    if (workspaceChanged) {
      const prompt = document.getElementById("prompt-text");
      prompt.textContent = "";
      prompt.hidden = true;
      document.getElementById("prompt-actions").hidden = true;
      setMessage("prompt-message", "");
    }
    const hasWorkspace = !!dashboardStatus.workspace;
    if (!initialRepositoryLoaded) {
      document.getElementById("repo-path").value = dashboardStatus.repository_path || "";
      initialRepositoryLoaded = true;
    }
    const repositoryMessage = document.getElementById("repository-message");
    const repositoryStateMessages = [
      "repositoryNotConfigured", "repositorySettingsInvalid", "repositoryUnavailable",
    ];
    if (dashboardStatus.settings_error) {
      setLocalizedMessage("repository-message", "repositorySettingsInvalid");
    } else if (!dashboardStatus.repository_path) {
      setLocalizedMessage("repository-message", "repositoryNotConfigured");
    } else if (!dashboardStatus.repository_available) {
      setLocalizedMessage("repository-message", "repositoryUnavailable");
    } else if (repositoryStateMessages.includes(repositoryMessage.dataset.translationKey)) {
      setMessage("repository-message", "");
    }
    await renderReviewHistory();
    const exportButton = document.querySelector('button[onclick="exportJson()"]');
    if (exportButton) exportButton.disabled = !hasWorkspace;
    if (hasWorkspace) {
      state = await api("/api/data");
      updateActiveReviewLabel();
      renderProjectLabel();
      const previousModel = document.getElementById("model").value;
      const models = clear("model");
      Object.keys(state.reports).sort().forEach(function (id) { option(models, id, id); });
      if (previousModel && state.reports[previousModel]) models.value = previousModel;
      renderBoard();
      showReport();
      if (workspaceChanged) contestantSelection = null;
      renderModels();
    } else {
      state = {
        reports: {}, judgments: {}, leaderboard: [], review_modes: [],
        leaderboards: {code_audit: [], golden_feature: []},
      };
      updateActiveReviewLabel();
      renderProjectLabel();
      clear("model");
      renderBoard();
      setMessage("partial-score-status", "");
      clear("run-models");
      contestantSelection = null;
      clear("findings");
      showReport();
      setMessage("models-message", "");
      setMessage("contestants-count", "");
    }
    if (workspaceChanged || reviewSetupBaselineResetRequested ||
        reviewSetupBaseline === null) {
      restoreReviewFocusControls(state.manifest || null);
      reviewSetupPending = false;
      reviewSetupBaseline = reviewSetupSignature();
      reviewSetupBaselineResetRequested = false;
    }
    renderJob(dashboardStatus.job);
    renderUsage();
    wizardDataError = false;
    wizardDataReady = true;
    updateWorkspaceActions();
    clearTimeout(pollTimer);
    if (dashboardStatus.job && ["queued", "running"].includes(dashboardStatus.job.status)) {
      pollTimer = setTimeout(reload, 1200);
    }
  } catch (error) {
    document.getElementById("project").textContent = error.message;
    wizardDataReady = false;
    wizardDataError = true;
    renderWizard();
  }
}

// BEGIN contestant-selection (pure helpers, unit-tested under Node without a DOM)
function hasContestantReport(reports, id) {
  return Object.prototype.hasOwnProperty.call(reports, id);
}
function reconcileContestantSelection(previous, models, reports) {
  const runnable = models
    .map(function (model) { return model.id; })
    .filter(function (id) { return !hasContestantReport(reports, id); });
  const selected = previous === null
    ? runnable
    : runnable.filter(function (id) { return previous.includes(id); });
  return { runnable: runnable, selected: selected };
}
// END contestant-selection
// BEGIN wizard-state (pure helper, unit-tested under Node without a DOM)
function deriveWizardState(
  status, data, repositoryInput, reviewSetupUnsaved = false, reviewSetupPending = false,
) {
  const repositoryReady = status.repository_available === true &&
    String(repositoryInput || "").trim() === String(status.repository_path || "");
  const workspaceReady = repositoryReady && !!status.workspace && !!data.manifest;
  const reports = data.reports || {};
  const judgments = data.judgments || {};
  const reportIds = Object.keys(reports);
  const reviewModes = data.review_modes ||
    (data.manifest && data.manifest.review_modes) || ["code_audit", "golden_feature"];
  const auditSelected = reviewModes.includes("code_audit");
  const featureSelected = reviewModes.includes("golden_feature");
  const job = status.job;
  const auditRunning = !!job && job.workspace === status.workspace &&
    ["queued", "running"].includes(job.status);
  const reportsReady = workspaceReady && reportIds.length > 0 && !auditRunning;
  const rubricLimits = { improvements: 20, gold: 20 };
  function reportFindingsReviewed(id) {
    if (!auditSelected) return true;
    const report = reports[id] || {};
    const judgment = judgments[id] || {};
    const findingJudgments = judgment.findings || {};
    return Array.isArray(report.findings) && report.findings.every(
      function (finding) {
        const saved = Object.prototype.hasOwnProperty.call(findingJudgments, finding.id)
          ? findingJudgments[finding.id] : null;
        return !!saved && ["confirmed", "false_positive"].includes(saved.verdict);
      }
    );
  }
  function reportRubricSaved(id) {
    const judgment = judgments[id] || {};
    const rubric = judgment.rubric || {};
    const keys = [];
    if (auditSelected) keys.push("improvements");
    if (featureSelected) keys.push("gold");
    return keys.every(function (key) {
      return Number.isInteger(rubric[key]) && rubric[key] >= 0 && rubric[key] <= rubricLimits[key];
    });
  }
  const findingsReady = reportsReady && (!auditSelected ||
    reportIds.every(reportFindingsReviewed));
  const rubricsReady = reportsReady && reportIds.every(reportRubricSaved);
  const adjudicationReady = findingsReady && rubricsReady;
  const reviewStepReady = workspaceReady && !reviewSetupUnsaved && !reviewSetupPending;
  const complete = [repositoryReady, reviewStepReady, reportsReady, adjudicationReady];
  const unlocked = [true, false, false, false, false];
  for (let index = 1; index < unlocked.length; index += 1) {
    unlocked[index] = unlocked[index - 1] && complete[index - 1];
  }
  return {
    complete: complete,
    unlocked: unlocked,
    maxUnlocked: unlocked.lastIndexOf(true),
    workspaceReady: workspaceReady,
    findingsReady: findingsReady,
    rubricsReady: rubricsReady,
    auditRunning: auditRunning,
    reviewSetupUnsaved: reviewSetupUnsaved,
    reviewSetupPending: reviewSetupPending,
    reviewModes: reviewModes,
    auditSelected: auditSelected,
    featureSelected: featureSelected,
  };
}
// END wizard-state
const wizardStepKeys = [
  "wizardStepRepository", "wizardStepReview", "wizardStepReports",
  "wizardStepAdjudication", "wizardStepResults",
];
function wizardGateKeyFor(step, progress) {
  for (let gate = 0; gate < step; gate += 1) {
    if (progress.complete[gate]) continue;
    if (gate === 0) return "wizardRepoGate";
    if (gate === 1) {
      if (progress.reviewSetupPending) return "wizardReviewGate";
      return progress.reviewSetupUnsaved
        ? "wizardUnsavedReviewSettings" : "wizardReviewGate";
    }
    if (gate === 2) return progress.auditRunning ? "wizardAuditRunning" : "wizardReportsGate";
    return progress.findingsReady ? "wizardRubricGate" : "wizardFindingsGate";
  }
  return "";
}
function renderWizard() {
  const panels = document.querySelectorAll("[data-wizard-panel]");
  const buttons = document.querySelectorAll("[data-wizard-step]");
  if (!wizardDataReady) {
    setLocalizedMessage("wizard-message", wizardDataError ? "wizardRefreshFailed" : "wizardLoading");
    panels.forEach(function (panel) { panel.hidden = true; });
    buttons.forEach(function (button, index) {
      button.removeAttribute("aria-current");
      button.setAttribute("aria-disabled", "true");
      document.getElementById("wizard-status-" + index).textContent = t("wizardLocked");
      document.getElementById("wizard-hint-" + index).textContent = "";
    });
    document.querySelectorAll(".wizard-next, .wizard-back").forEach(function (button) {
      button.disabled = true;
    });
    return;
  }
  const repositoryInput = document.getElementById("repo-path").value;
  const progress = deriveWizardState(
    dashboardStatus, state, repositoryInput,
    reviewSetupHasUnsavedChanges(), reviewSetupPending,
  );
  wizardProgressState = progress;
  if (wizardCurrentStep === null) {
    wizardCurrentStep = progress.complete.findIndex(function (complete) { return !complete; });
    if (wizardCurrentStep < 0) wizardCurrentStep = 4;
  }
  if (wizardCurrentStep > progress.maxUnlocked) wizardCurrentStep = progress.maxUnlocked;
  setMessage("wizard-message", "");
  const current = wizardCurrentStep;
  const progressBar = document.getElementById("wizard-progress");
  progressBar.setAttribute("aria-valuenow", String(current + 1));
  progressBar.setAttribute("aria-valuetext", t("wizardProgressLabel", {current: current + 1}));
  document.getElementById("wizard-progress-fill").style.width = ((current + 1) * 20) + "%";
  buttons.forEach(function (button) {
    const index = Number(button.dataset.wizardStep);
    const unlocked = progress.unlocked[index];
    const complete = progress.complete[index] || (index === 4 && progress.complete[3]);
    const isCurrent = index === current;
    const statusKey = !unlocked ? "wizardLocked" :
      (isCurrent ? "wizardCurrent" : (complete ? "wizardDone" : "wizardReady"));
    const hintKey = unlocked ? "" : wizardGateKeyFor(index, progress);
    const hint = hintKey ? t(hintKey) : "";
    button.classList.toggle("is-complete", !!complete);
    button.setAttribute("aria-disabled", String(!unlocked));
    if (isCurrent) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
    document.getElementById("wizard-status-" + index).textContent = t(statusKey);
    document.getElementById("wizard-hint-" + index).textContent = hint;
    button.setAttribute("aria-label",
      (index + 1) + ". " + t(wizardStepKeys[index]) + ". " + t(statusKey) +
      (hint ? ". " + hint : ""));
  });
  panels.forEach(function (panel) {
    const index = Number(panel.dataset.wizardPanel);
    panel.hidden = index !== current || !progress.unlocked[index];
  });
  document.querySelectorAll(".wizard-next").forEach(function (button) {
    const index = Number(button.dataset.wizardNext);
    const canApplyReviewSettings = index === 1 && current === 1 &&
      progress.workspaceReady && !progress.reviewSetupPending &&
      !reviewSetupSaving && selectedReviewModes().length > 0;
    button.disabled = index === 1
      ? !canApplyReviewSettings : !progress.complete[index];
  });
  document.querySelectorAll(".wizard-back").forEach(function (button) {
    const index = Number(button.dataset.wizardBack);
    button.disabled = reviewSetupSaving || index <= 0 || !progress.unlocked[index - 1];
  });
}
function goToWizardStep(index) {
  if (!wizardDataReady || !wizardProgressState) return;
  if (!wizardProgressState.unlocked[index]) {
    const gateKey = wizardGateKeyFor(index, wizardProgressState);
    if (gateKey) setLocalizedMessage("wizard-message", gateKey);
    return;
  }
  wizardCurrentStep = index;
  renderWizard();
  const heading = document.getElementById("wizard-heading-" + index);
  if (heading) heading.focus();
}
function jobIsActive(job) {
  return !!job && ["queued", "running"].includes(job.status);
}
function setContestantChecked(id, checked) {
  const next = new Set(contestantSelection || []);
  if (checked) next.add(id); else next.delete(id);
  contestantSelection = (dashboardStatus.models || [])
    .map(function (model) { return model.id; })
    .filter(function (modelId) { return next.has(modelId); });
  renderContestantSummary();
}
function setAllContestants(checked) {
  const plan = reconcileContestantSelection(
    null, dashboardStatus.models || [], state.reports || {}
  );
  contestantSelection = checked ? plan.runnable : [];
  renderModels();
}
function renderContestantSummary() {
  const models = dashboardStatus.models || [];
  const plan = reconcileContestantSelection(
    contestantSelection, models, state.reports || {}
  );
  const locked = jobIsActive(dashboardStatus.job) || !plan.runnable.length;
  if (dashboardStatus.models_error) {
    setMessage("models-message", dashboardStatus.models_error);
  } else if (models.length && !plan.runnable.length) {
    setLocalizedMessage("models-message", "noRunnableContestants");
  } else {
    setLocalizedMessage("models-message", "chooseModels");
  }
  if (models.length) {
    setLocalizedMessage("contestants-count", "contestantsSelected", {
      selected: plan.selected.length, available: plan.runnable.length,
    });
  } else {
    setMessage("contestants-count", "");
  }
  document.getElementById("contestants-select-all").disabled = locked;
  document.getElementById("contestants-clear").disabled = locked;
}
function renderModels() {
  const root = document.getElementById("run-models");
  const models = dashboardStatus.models || [];
  const reports = state.reports || {};
  const plan = reconcileContestantSelection(contestantSelection, models, reports);
  contestantSelection = plan.selected;
  const chosen = new Set(plan.selected);
  root.replaceChildren();
  models.forEach(function (model) {
    const locked = hasContestantReport(reports, model.id);
    const row = node("label", locked ? "model-choice is-locked" : "model-choice");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.value = model.id;
    checkbox.checked = chosen.has(model.id);
    checkbox.disabled = locked;
    checkbox.addEventListener("change", function () {
      setContestantChecked(model.id, checkbox.checked);
    });
    const label = node("span", "", model.id);
    label.dataset.modelId = model.id;
    label.dataset.reportExists = locked ? "true" : "false";
    row.append(checkbox, label);
    if (locked) row.append(localizedNode("span", "pill", "contestantHasReport"));
    root.append(row);
  });
  const active = jobIsActive(dashboardStatus.job);
  document.getElementById("contestants-fieldset").disabled = active;
  document.getElementById("run-button").disabled = !models.length || active;
  renderContestantSummary();
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
      else if (result && result.findings !== undefined &&
          (dashboardStatus.review_modes || ["code_audit"]).includes("code_audit")) {
        detail += " - " + t(result.findings === 1 ? "findingsSavedOne" : "findingsSavedMany", {
          count: result.findings,
        });
      } else if (result) detail += " - " + t("reportSaved");
      list.append(node("li", "", detail));
    });
    root.append(list);
  }
  document.getElementById("run-button").disabled =
    ["queued", "running"].includes(job.status) || !(dashboardStatus.models || []).length;
}
function usageValue(value) {
  return value === null || value === undefined ? t("usageUnknown") : String(value);
}
function usageSummary(summary) {
  function tokenValue(field) {
    const count = summary.token_coverage[field] || 0;
    return usageValue(summary[field]) + " (" + count + "/" + summary.attempts + ")";
  }
  return [
    t("usageAttempts") + ": " + summary.attempts,
    t("usageInput") + ": " + tokenValue("prompt_tokens"),
    t("usageOutput") + ": " + tokenValue("completion_tokens"),
    t("usageCache") + ": " + tokenValue("cached_tokens"),
    t("usageCacheWrite") + ": " + tokenValue("cache_write_tokens"),
    t("usageReasoning") + ": " + tokenValue("reasoning_tokens"),
    t("usageCacheRate") + ": " + (summary.cache_rate === null ? t("usageUnknown") : (Number(summary.cache_rate) * 100).toFixed(1) + "%") + " (" + summary.cache_rate_attempts + "/" + summary.attempts + ")",
    t("usageCacheEffect") + ": " + (summary.cache_discount_usd === null ? t("usageUnknown") : "US$ " + summary.cache_discount_usd) + " (" + t("usage_" + summary.cache_discount_status) + ")",
    t("usageDiscountCoverage", {known: summary.known_cache_discount_attempts, total: summary.attempts}),
    t("usageCost") + ": " + (summary.openrouter_credits_usd === null ? t("usageUnknown") : "US$ " + summary.openrouter_credits_usd) + " (" + t("usage_" + summary.cost_status) + ")",
    t("usageCoverage", {known: summary.known_cost_attempts, total: summary.attempts}),
  ].join(" · ");
}
function renderUsage() {
  const root = document.getElementById("usage-history");
  if (!root) return;
  const runs = (state.usage && state.usage.runs) || [];
  const signature = JSON.stringify([language, runs]);
  if (root.dataset.renderSignature === signature) return;
  root.dataset.renderSignature = signature;
  const opened = new Set(Array.from(root.querySelectorAll("details[open]")).map(function (el) { return el.dataset.key; }));
  const focused = document.activeElement && document.activeElement.closest("details");
  const focusedKey = focused && root.contains(focused) ? focused.dataset.key : null;
  root.replaceChildren();
  if (!runs.length) { root.append(node("p", "muted", t("usageEmpty"))); return; }
  runs.forEach(function (run) {
    const section = node("details");
    section.dataset.key = run.run_id;
    section.open = opened.has(run.run_id);
    section.append(node("summary", "", t("usageRun") + " " + run.run_id + " — " + t("usageCost") + ": " + (run.summary.openrouter_credits_usd === null ? t("usageUnknown") : "US$ " + run.summary.openrouter_credits_usd) + " (" + t("usage_" + run.summary.cost_status) + ")"));
    section.append(node("p", "detail", usageSummary(run.summary)));
    Object.entries(run.models).forEach(function ([model, summary]) {
      section.append(node("p", "detail", model + " — " + usageSummary(summary)));
    });
    const list = node("ul", "job-outcomes");
    run.attempts.forEach(function (attempt) {
      const usage = attempt.usage || {};
      const cost = attempt.cost || {};
      const status = {in_progress: "usageRunning", failed: "usageFailed", response_received: "usageReceived"}[attempt.status];
      const fields = [attempt.model_id, t("usageStep", {step: attempt.step, attempt: attempt.attempt}),
        t(status || "usageUnknown"), t("usageInput") + ": " + usageValue(usage.prompt_tokens),
        t("usageOutput") + ": " + usageValue(usage.completion_tokens),
        t("usageCache") + ": " + usageValue(usage.cached_tokens),
        t("usageCacheWrite") + ": " + usageValue(usage.cache_write_tokens),
        t("usageReasoning") + ": " + usageValue(usage.reasoning_tokens),
        t("usageCacheEffect") + ": " + (cost.cache_discount_usd == null ? t("usageUnknown") : "US$ " + cost.cache_discount_usd),
        t("usageCost") + ": " + (cost.openrouter_credits_usd == null ? t("usageUnknown") : "US$ " + cost.openrouter_credits_usd),
        t("usageUpstream") + ": " + (cost.upstream_inference_usd == null ? t("usageUnknown") : "US$ " + cost.upstream_inference_usd),
        t("usageGeneration") + ": " + usageValue(attempt.generation_id),
        t("usageModel") + ": " + usageValue(attempt.resolved_model),
        t("usageProvider") + ": " + usageValue(attempt.provider)];
      if (attempt.session_id) fields.push(t("usageSession") + ": " + attempt.session_id);
      if (attempt.error_type || attempt.http_status) {
        fields.push(t("usageError") + ": " + usageValue(attempt.error_type) +
          (attempt.http_status ? " (HTTP " + attempt.http_status + ")" : ""));
      }
      list.append(node("li", "detail", fields.join(" · ")));
    });
    section.append(list);
    root.append(section);
    if (focusedKey === run.run_id) section.querySelector("summary").focus();
  });
}
function renderBoard() {
  const modes = state.review_modes || ["code_audit", "golden_feature"];
  const auditSelected = modes.includes("code_audit");
  const featureSelected = modes.includes("golden_feature");
  const boards = state.leaderboards || {};
  const auditRows = boards.code_audit || [];
  const featureRows = boards.golden_feature || [];
  document.getElementById("audit-score-section").hidden = !auditSelected;
  document.getElementById("gold-feature-score-section").hidden = !featureSelected;
  const summary = clear("summary");
  [
    ["contestants", auditRows.length],
    ["validatedFindings", auditRows.reduce(function (total, row) { return total + row.confirmed; }, 0)],
    ["pendingReview", auditRows.reduce(function (total, row) { return total + row.pending; }, 0)],
    ["topScore", auditRows[0] ? auditRows[0].total : 0],
  ].forEach(function (item) {
    const card = node("div", "card");
    card.append(node("div", "eyebrow", t(item[0])), node("div", "total", item[1]));
    summary.append(card);
  });
  const board = clear("board");
  const headings = node("tr");
  ["rank", "model", "bugs", "improve", "penalties",
   "total", "pending", "tokens"].forEach(function (key) {
    headings.append(node("th", "", t(key)));
  });
  board.append(headings);
  auditRows.forEach(function (row, index) {
    const line = node("tr");
    const report = state.reports[row.model] || {};
    const metadata = report.audit_metadata || {};
    const tokens = (metadata.prompt_tokens || 0) + (metadata.completion_tokens || 0);
    [index + 1, row.model, row.bugs, row.improvements,
     row.penalty, row.total + "/80", row.pending, tokens].forEach(function (value, column) {
      line.append(node("td", column > 1 ? "num" : "", value));
    });
    board.append(line);
  });
  const featureBoard = clear("golden-feature-board");
  const featureHeadings = node("tr");
  ["rank", "model", "gold", "proposal"].forEach(function (key) {
    featureHeadings.append(node("th", "", t(key)));
  });
  featureBoard.append(featureHeadings);
  featureRows.forEach(function (row, index) {
    const line = node("tr");
    [index + 1, row.model, row.gold + "/20", row.golden_feature].forEach(
      function (value, column) {
        line.append(node("td", column === 2 ? "num" : "", value));
      },
    );
    featureBoard.append(line);
  });
  const podium = clear("goldpodium");
  featureRows.forEach(function (row) {
    const card = node("div", "card");
    const report = state.reports[row.model] || {};
    const feature = report.golden_feature || {};
    card.append(
      node("div", "eyebrow", row.model + " | " + row.gold + "/20"),
      node("h3", "gold", row.golden_feature),
      node("p", "muted", feature.mechanism || ""),
    );
    podium.append(card);
  });
  renderPartialScores();
}
function renderPartialScores() {
  const root = clear("partial-scores");
  const boards = state.leaderboards || {};
  const auditRows = boards.code_audit || [];
  const featureRows = boards.golden_feature || [];
  const pending = auditRows.reduce(function (total, row) { return total + row.pending; }, 0);
  setMessage("partial-score-status", auditRows.length ? t("pendingReview") + ": " + pending : "");
  auditRows.forEach(function (row) {
    const card = node("div", "card");
    card.append(
      node("div", "eyebrow", row.model),
      node("div", "partial-score-total", row.total + "/80"),
      node("p", "muted", t("auditScore") + " | " + t("validatedFindings") + ": " + row.confirmed),
    );
    root.append(card);
  });
  featureRows.forEach(function (row) {
    const card = node("div", "card");
    card.append(
      node("div", "eyebrow", row.model),
      node("div", "partial-score-total", row.total + "/20"),
      node("p", "muted", t("featureScore")),
    );
    root.append(card);
  });
}
function showReport() {
  setMessage("finding-save-message", "");
  setMessage("rubric-save-message", "");
  const id = document.getElementById("model").value;
  const report = state.reports[id];
  const modes = state.review_modes || ["code_audit", "golden_feature"];
  const auditSelected = modes.includes("code_audit");
  const featureSelected = modes.includes("golden_feature");
  document.getElementById("audit-findings-panel").hidden = !auditSelected;
  document.getElementById("audit-rubric-fields").hidden = !auditSelected;
  document.getElementById("gold-rubric-field").hidden = !featureSelected;
  document.getElementById("gold-feature-result").hidden = !featureSelected;
  if (!report) {
    document.getElementById("abstract").textContent = "";
    clear("gold");
    setMessage("count", "");
    ["improvements", "goldscore"].forEach(function (field) {
      document.getElementById(field).value = 0;
    });
    clear("findings");
    return;
  }
  const judgment = state.judgments[id] || {};
  document.getElementById("abstract").textContent = report.summary || t("noSummary");
  const gold = clear("gold");
  const feature = report.golden_feature || {};
  if (featureSelected && feature.name) {
    gold.append(node("h3", "gold", feature.name));
    gold.append(node("p", "muted", feature.mechanism || ""));
  }
  ["improvements", "goldscore"].forEach(function (field) {
    const rubricField = field === "goldscore" ? "gold" : field;
    document.getElementById(field).value =
      (judgment.rubric && judgment.rubric[rubricField]) || 0;
  });
  if (auditSelected) {
    const findingCount = (report.findings || []).length;
    setLocalizedMessage("count", findingCount === 1 ? "findingOne" : "findingMany", {
      count: findingCount,
    });
  } else {
    setMessage("count", "");
  }
  const root = clear("findings");
  (report.findings || []).forEach(function (finding) {
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
    function clearFindingFeedback() {
      setMessage("finding-save-message", "");
    }
    [verdict, severity, fabricated].forEach(function (field) {
      field.addEventListener("change", clearFindingFeedback);
    });
    key.addEventListener("input", clearFindingFeedback);
    const save = localizedNode("button", "primary", "save");
    save.onclick = async function () {
      const disabledControls = [
        verdict, severity, key, fabricated, document.getElementById("model"), save,
      ];
      const previouslyDisabled = disabledControls.map(function (control) {
        return control.disabled;
      });
      disabledControls.forEach(function (control) { control.disabled = true; });
      save.dataset.i18n = "savingFinding";
      save.textContent = t("savingFinding");
      setSaveFeedback("finding-save-message", "savingFinding", {}, "pending");
      try {
        await api("/api/adjudicate", {
          model: id, finding: finding.id, verdict: verdict.value,
          severity: severity.value, issue_key: key.value,
          fabricated_evidence: fabricated.checked,
        });
        await reload();
        setSaveFeedback("finding-save-message", "findingSaved", {
          finding: finding.id,
        }, "success");
      } catch (error) {
        setSaveError("finding-save-message", error);
      } finally {
        if (save.isConnected) {
          save.dataset.i18n = "save";
          save.textContent = t("save");
        }
        disabledControls.forEach(function (control, index) {
          control.disabled = previouslyDisabled[index];
        });
      }
    };
    controls.append(verdictBox, severityBox, keyBox, fabricatedBox, save);
    article.append(controls);
    root.append(article);
  });
}
async function saveRepository() {
  try {
    const result = await api("/api/settings/repository", {
      path: document.getElementById("repo-path").value,
    });
    document.getElementById("repo-path").value = result.repository_path;
    setLocalizedMessage("repository-message", "repositorySaved");
    await reload();
  } catch (error) {
    setRepositoryError("repository-message", error);
  }
}
async function openReview() {
  try {
    const reviewId = document.getElementById("review-history").value;
    await api("/api/reviews/open", { id: reviewId });
    reviewSetupPending = false;
    reviewSetupBaselineResetRequested = true;
    setLocalizedMessage("workspace-message", "reviewOpened");
    await reload();
  } catch (error) {
    setRepositoryError("workspace-message", error);
  }
}
// BEGIN new-review
function reviewSetupPayload() {
  const reviewModes = selectedReviewModes();
  const criteria = {};
  document.querySelectorAll(".priority-slider").forEach(function (slider) {
    criteria[slider.dataset.priorityId] = Number(slider.value);
  });
  const goldenFeatureAspects = Array.from(
    document.querySelectorAll("[data-golden-feature-aspect]"),
  ).filter(function (aspect) { return aspect.checked; })
    .map(function (aspect) { return aspect.value; });
  return {
    review_modes: reviewModes,
    review_focus: {
      criteria: criteria,
      custom_text: document.getElementById("focus-custom-text").value,
      golden_feature_aspects: goldenFeatureAspects,
    },
  };
}
function restoreReviewFocusControls(manifest) {
  const modes = manifest && Array.isArray(manifest.review_modes)
    ? manifest.review_modes : ["code_audit", "golden_feature"];
  const settings = manifest && manifest.review_focus_settings;
  const criteria = settings && settings.criteria && typeof settings.criteria === "object"
    ? settings.criteria : {};
  const aspects = settings && Array.isArray(settings.golden_feature_aspects)
    ? settings.golden_feature_aspects : [];
  document.querySelectorAll("[data-review-mode]").forEach(function (input) {
    input.checked = modes.includes(input.value);
  });
  document.querySelectorAll(".priority-slider").forEach(function (slider) {
    const value = criteria[slider.dataset.priorityId];
    slider.value = Number.isInteger(value) && value >= 0 && value <= 5
      ? String(value) : slider.defaultValue;
    updatePriorityLabel(slider);
  });
  document.getElementById("focus-custom-text").value =
    settings && typeof settings.custom_text === "string" ? settings.custom_text : "";
  document.querySelectorAll("[data-golden-feature-aspect]").forEach(function (aspect) {
    aspect.checked = aspects.includes(aspect.value);
  });
  document.getElementById("audit-focus-config").hidden = !modes.includes("code_audit");
  document.getElementById("golden-feature-aspects").hidden = !modes.includes("golden_feature");
  setMessage("review-mode-message", modes.length ? "" : t("selectReviewMode"));
  reviewSetupNeedsSave = !!dashboardStatus.review_id && !settings &&
    !Object.keys(state.reports || {}).length;
}
function resetReviewFocusControls() {
  document.querySelectorAll(".priority-slider").forEach(function (slider) {
    slider.value = slider.defaultValue;
    updatePriorityLabel(slider);
  });
  document.getElementById("focus-custom-text").value = "";
  document.querySelectorAll("[data-golden-feature-aspect]").forEach(function (aspect) {
    aspect.checked = false;
  });
  document.querySelectorAll("[data-review-mode]").forEach(function (mode) {
    mode.checked = true;
  });
  updateReviewModeControls();
}
function clearGeneratedPrompt() {
  const prompt = document.getElementById("prompt-text");
  prompt.textContent = "";
  prompt.hidden = true;
  document.getElementById("prompt-actions").hidden = true;
  setMessage("prompt-message", "");
}
async function continueReviewSetup() {
  if (reviewSetupSaving || reviewSetupPending || !wizardProgressState ||
      !wizardProgressState.workspaceReady || !selectedReviewModes().length) return;
  if (reviewSetupHasUnsavedChanges()) {
    reviewSetupSaving = true;
    setLocalizedMessage("workspace-message", "savingReviewSettings");
    updateWorkspaceActions();
    try {
      await api("/api/reviews/settings", reviewSetupPayload());
      rememberReviewSetupBaseline();
      setLocalizedMessage("workspace-message", "reviewSettingsSaved");
      await reload();
    } catch (error) {
      setRepositoryError("workspace-message", error);
      return;
    } finally {
      reviewSetupSaving = false;
      updateWorkspaceActions();
    }
  }
  if (reviewSetupHasUnsavedChanges() || reviewSetupPending) return;
  goToWizardStep(2);
  await loadPrompt();
}
function prepareNewReview() {
  const selectedPath = document.getElementById("repo-path").value.trim();
  if (reviewCreationInProgress || jobIsActive(dashboardStatus.job) ||
      !dashboardStatus.repository_available || !dashboardStatus.workspace ||
      selectedPath !== (dashboardStatus.repository_path || "")) return;
  reviewSetupPending = true;
  resetReviewFocusControls();
  setMessage("workspace-message", "");
  goToWizardStep(1);
}
// END new-review
async function createReview() {
  if (reviewCreationInProgress) return;
  const button = document.getElementById("create-review-button");
  reviewCreationInProgress = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = t("creatingReview");
  setLocalizedMessage("workspace-message", "creatingReviewStatus");
  updateWorkspaceActions();
  try {
    await api("/api/reviews", reviewSetupPayload());
    updateWorkspaceActions();
    setLocalizedMessage("workspace-message", "reviewCreated");
    await reload();
  } catch (error) {
    setRepositoryError("workspace-message", error);
  } finally {
    reviewCreationInProgress = false;
    button.removeAttribute("aria-busy");
    button.textContent = t("createReview");
    updateWorkspaceActions();
  }
}
async function startRun() {
  const selected = contestantSelection || [];
  if (!selected.length) {
    setLocalizedMessage("run-message", "selectAtLeastOneContestant");
    return;
  }
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
  const button = document.getElementById("save-rubric-button");
  const modes = state.review_modes || ["code_audit", "golden_feature"];
  const rubricFields = [];
  if (modes.includes("code_audit")) rubricFields.push("improvements");
  if (modes.includes("golden_feature")) rubricFields.push("goldscore");
  const disabledControls = [document.getElementById("model"), button].concat(
    rubricFields.map(function (field) { return document.getElementById(field); }),
  );
  const previouslyDisabled = disabledControls.map(function (control) {
    return control.disabled;
  });
  disabledControls.forEach(function (control) { control.disabled = true; });
  button.dataset.i18n = "savingScores";
  button.textContent = t("savingScores");
  setSaveFeedback("rubric-save-message", "savingScores", {}, "pending");
  try {
    const payload = { model: document.getElementById("model").value };
    rubricFields.forEach(function (field) {
      const rubricKey = field === "goldscore" ? "gold" : field;
      payload[rubricKey] = +document.getElementById(field).value;
    });
    await api("/api/rubric", payload);
    await reload();
    setSaveFeedback("rubric-save-message", "rubricSaved", {}, "success");
  } catch (error) {
    setSaveError("rubric-save-message", error);
  } finally {
    button.dataset.i18n = "saveScores";
    button.textContent = t("saveScores");
    disabledControls.forEach(function (control, index) {
      control.disabled = previouslyDisabled[index];
    });
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
    updateWorkspaceActions();
  });
});
document.getElementById("focus-custom-text").addEventListener("input", updateWorkspaceActions);
document.querySelectorAll("[data-golden-feature-aspect]").forEach(function (aspect) {
  aspect.addEventListener("change", updateWorkspaceActions);
});
const languageToggle = document.getElementById("language-toggle");
document.getElementById("server-alert-dismiss").addEventListener("click", dismissServerAlert);
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
document.getElementById("wizard-nav").addEventListener("click", function (event) {
  const button = event.target.closest("[data-wizard-step]");
  if (button) goToWizardStep(Number(button.dataset.wizardStep));
});
document.querySelectorAll(".wizard-next").forEach(function (button) {
  button.addEventListener("click", function () {
    if (Number(button.dataset.wizardNext) === 1) {
      continueReviewSetup();
      return;
    }
    goToWizardStep(Number(button.dataset.wizardNext) + 1);
  });
});
document.querySelectorAll(".wizard-back").forEach(function (button) {
  button.addEventListener("click", function () {
    goToWizardStep(Number(button.dataset.wizardBack) - 1);
  });
});
document.getElementById("repo-path").addEventListener("input", updateWorkspaceActions);
document.getElementById("review-history").addEventListener("change", updateWorkspaceActions);
["improvements", "goldscore"].forEach(function (id) {
  document.getElementById(id).addEventListener("input", function () {
    setMessage("rubric-save-message", "");
  });
});
updateWorkspaceActions();
document.querySelectorAll("[data-review-mode]").forEach(function (input) {
  input.addEventListener("change", updateReviewModeControls);
});
updateReviewModeControls();
applyLanguage();
reload();
</script></body></html>"""


class NoActiveWorkspace(RuntimeError):
    """Raised when an operation requires an arena but none is selected."""


def _data(workspace: Path) -> dict:
    manifest = load_json(workspace / "manifest.json")
    review_modes = normalize_review_modes(manifest.get("review_modes"))
    manifest["review_modes"] = review_modes
    reports = {p.stem: load_json(p) for p in sorted((workspace / "reports").glob("*.json"))}
    judgments = {p.stem: load_json(p) for p in sorted((workspace / "judgments").glob("*.json"))}
    return {
        "manifest": manifest,
        "review_modes": review_modes,
        "reports": reports,
        "judgments": judgments,
        "leaderboard": leaderboard(reports, judgments, review_modes),
        "leaderboards": leaderboards(reports, judgments, review_modes),
        "usage": read_usage(workspace),
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
            provider_preferences(entry)
            reasoning_preferences(entry)
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


def _render_golden_feature_aspects() -> str:
    """Render the fixed golden-feature direction checkboxes."""

    controls = []
    for aspect_id, label in GOLDEN_FEATURE_ASPECTS.items():
        suffix = "".join(part.title() for part in aspect_id.split("_"))
        controls.append(
            f'<label class="golden-feature-aspect" '
            f'for="golden-feature-aspect-{aspect_id}">'
            f'<input id="golden-feature-aspect-{aspect_id}" type="checkbox" '
            f'name="golden_feature_aspects" data-golden-feature-aspect '
            f'value="{html.escape(aspect_id)}">'
            f'<span data-i18n="goldenFeatureAspect{suffix}">{html.escape(label)}</span>'
            f'</label>'
        )
    return "\n".join(controls)


def _path_from_input(raw: Any, base: Path, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        raise ValueError(f"{field} must be a nonempty local path")
    path = Path(raw.strip())
    return (path if path.is_absolute() else base / path).resolve()


def _is_git_repository_root(path: Path) -> bool:
    try:
        validate_repository_root(path)
    except ValueError:
        return False
    return True


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


def create_server(
    workspace: Path | None = None,
    port: int = 8765,
    models_path: Path | None = None,
    data_directory: Path | None = None,
) -> ThreadingHTTPServer:
    """Create the loopback HTTP server; exposed separately to support API tests."""

    initial_workspace = validate_workspace(workspace) if workspace is not None else None
    starting_directory = Path.cwd().resolve()
    configured_models = models_path.resolve() if models_path is not None else None
    data_directory = Path(data_directory or app_data_directory()).expanduser().resolve()
    settings_error = None
    repository_path = None
    repository_available = False
    repository_error = None
    initial_review_id = None
    try:
        settings = load_settings(data_directory)
        raw_repository_path = settings["repository_path"]
        if raw_repository_path is not None:
            repository_path = Path(raw_repository_path).resolve()
            try:
                repository_path = validate_repository_root(repository_path)
                repository_available = True
            except ValueError as exc:
                repository_error = str(exc)
    except (OSError, ValueError, TypeError):
        settings_error = "Could not read a valid settings.json configuration."
    if initial_workspace is None and repository_available and repository_path is not None:
        reviews = list_reviews(data_directory, repository_path)
        if reviews:
            try:
                initial_workspace = validate_workspace(review_directory(
                    data_directory, reviews[0]["id"],
                ))
                initial_review_id = reviews[0]["id"]
            except (OSError, ValueError):
                initial_workspace = None
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

        def _dashboard_url(self) -> str:
            return f"http://127.0.0.1:{self.server.server_port}"

        def _host_error(self) -> dict:
            dashboard_url = self._dashboard_url()
            return {
                "error": f"Invalid host. Open the dashboard at {dashboard_url}.",
                "error_code": "invalid_host",
                "dashboard_url": dashboard_url,
            }

        def _authorized(self) -> bool:
            origin = self.headers.get("Origin", "")
            expected = self._dashboard_url()
            return self.headers.get("X-Arena-Token") == token and (not origin or origin == expected)

        def _authorization_error(self) -> dict:
            expected = self._dashboard_url()
            origin = self.headers.get("Origin", "")
            if origin and origin != expected:
                error_code = "origin_mismatch"
                message = f"Forbidden: request origin mismatch. Open the dashboard at {expected}."
            elif not self.headers.get("X-Arena-Token"):
                error_code = "missing_page_token"
                message = f"Forbidden: the dashboard security token is missing. Reload from {expected}."
            else:
                error_code = "stale_page_token"
                message = (
                    "Forbidden: this page's security token is out of date, possibly because "
                    f"the server restarted. Reload from {expected}."
                )
            return {
                "error": message,
                "error_code": error_code,
                "dashboard_url": expected,
            }

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
                review_id = self.server.active_review_id
                repository_path = self.server.repository_path
                repository_available = self.server.repository_available
                repository_error = self.server.repository_error
            review_modes = None
            combined_leaderboard: list[dict] = []
            boards = {"code_audit": [], "golden_feature": []}
            if workspace_path is not None:
                try:
                    data = _data(workspace_path)
                    review_modes = data["review_modes"]
                    combined_leaderboard = data["leaderboard"]
                    boards = {
                        mode: data["leaderboards"].get(mode, [])
                        for mode in boards
                    }
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            return {
                "workspace": str(workspace_path) if workspace_path else None,
                "review_id": review_id,
                "review_modes": review_modes,
                "leaderboard": combined_leaderboard,
                "leaderboards": boards,
                "repository_path": str(repository_path) if repository_path else None,
                "repository_available": repository_available,
                "repository_error": repository_error,
                "settings_error": self.server.settings_error,
                "models": models,
                "models_error": model_error,
                "job": job,
            }

        def do_GET(self):  # noqa: N802
            if not self._host_allowed():
                self._json(403, self._host_error())
                return
            if self.path == "/":
                page = (
                    PAGE.replace("__TOKEN__", json.dumps(token))
                    .replace("__FOCUS_CRITERIA__", _render_review_focus_criteria())
                    .replace("__GOLDEN_FEATURE_ASPECTS__", _render_golden_feature_aspects())
                    .replace("__MAX_CUSTOM_FOCUS_LENGTH__", str(MAX_CUSTOM_FOCUS_LENGTH))
                )
                self._reply(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self._reply(204, b"", "image/x-icon")
            elif self.path == "/api/status":
                self._json(200, self._status())
            elif self.path == "/api/reviews":
                reviews = list_reviews(
                    self.server.data_directory, self.server.repository_path,
                )
                self._json(200, {"reviews": reviews})
            elif self.path == "/api/data":
                try:
                    self._json(200, _data(self._active_workspace()))
                except NoActiveWorkspace as exc:
                    self._json(409, {"error": str(exc)})
                except InvalidJudgmentSeverity as exc:
                    self._json(400, {"error": str(exc)})
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
                self._json(403, self._host_error())
                return
            if not self._authorized():
                self._json(403, self._authorization_error())
                return
            allowed = {
                "/api/folders", "/api/workspace/open", "/api/workspace/init", "/api/run",
                "/api/rubric", "/api/adjudicate", "/api/import",
                "/api/settings/repository", "/api/reviews", "/api/reviews/open",
                "/api/reviews/settings",
            }
            if self.path not in allowed:
                self._json(404, {"error": "Not found"})
                return
            try:
                payload = self._read_payload()
                if self.path == "/api/settings/repository":
                    if set(payload) != {"path"}:
                        raise ValueError("Provide only the repository path")
                    candidate = _path_from_input(
                        payload.get("path"), self.server.starting_directory, "repository_path",
                    )
                    repository_path = save_repository_path(
                        self.server.data_directory, candidate,
                    )
                    reviews = list_reviews(self.server.data_directory, repository_path)
                    next_workspace = None
                    if reviews:
                        next_workspace = validate_workspace(review_directory(
                            self.server.data_directory, reviews[0]["id"],
                        ))
                    with self.server.state_lock:
                        self.server.repository_path = repository_path
                        self.server.repository_available = True
                        self.server.repository_error = None
                        self.server.settings_error = None
                        self.server.active_workspace = next_workspace
                        self.server.active_review_id = reviews[0]["id"] if reviews else None
                    self._json(200, {
                        "repository_path": str(repository_path),
                        "repository_available": True,
                    })
                    return
                if self.path == "/api/reviews":
                    if set(payload) - {"review_focus", "review_modes"}:
                        raise ValueError(
                            "Provide only review_modes and review_focus when creating a review"
                        )
                    configuration = normalize_review_configuration(
                        payload.get("review_modes"), payload.get("review_focus"),
                    )
                    review_modes = configuration["review_modes"]
                    repository_path = self.server.repository_path
                    if repository_path is None:
                        raise ValueError("Configure a Git repository before creating a review")
                    repository_path = validate_repository_root(repository_path)
                    review_id = create_review_id()
                    created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                    workspace_path = review_directory(self.server.data_directory, review_id)
                    initialize_workspace(
                        repository_path, workspace_path, configuration["focus"],
                        golden_feature_aspects=configuration["golden_feature_aspects"],
                        review_modes=review_modes,
                        review_focus_settings=configuration["review_focus_settings"],
                        review_id=review_id, created_at=created_at,
                    )
                    workspace_path = validate_workspace(workspace_path)
                    data = _data(workspace_path)
                    with self.server.state_lock:
                        self.server.repository_path = repository_path
                        self.server.repository_available = True
                        self.server.repository_error = None
                        self.server.active_workspace = workspace_path
                        self.server.active_review_id = review_id
                    self._json(201, {
                        **data, "review_id": review_id, "created_at": created_at,
                    })
                    return
                if self.path == "/api/reviews/settings":
                    if set(payload) != {"review_focus", "review_modes"}:
                        raise ValueError("Provide only review_modes and review_focus")
                    configuration = normalize_review_configuration(
                        payload.get("review_modes"), payload.get("review_focus"),
                    )
                    if configuration["review_focus_settings"] is None:
                        raise ValueError("review_focus settings are required")
                    with self.server.state_lock:
                        workspace_path = self.server.active_workspace
                        if workspace_path is None:
                            raise NoActiveWorkspace(
                                "No active workspace. Open a review or create one first."
                            )
                        current_job = self.server.jobs.get(self.server.current_job_id)
                        if (current_job and current_job["workspace"] == str(workspace_path)
                                and current_job["status"] in {"queued", "running"}):
                            raise RuntimeError(
                                "Cannot change review settings while an audit is running"
                            )
                        update_workspace_review_configuration(
                            workspace_path,
                            configuration["review_modes"],
                            payload["review_focus"],
                        )
                    self._json(200, _data(workspace_path))
                    return
                if self.path == "/api/reviews/open":
                    if set(payload) != {"id"}:
                        raise ValueError("Provide only a review ID")
                    repository_path = self.server.repository_path
                    if repository_path is None:
                        raise ValueError("Configure a Git repository before opening a review")
                    review_id = payload.get("id")
                    known_review = any(
                        review["id"] == review_id
                        for review in list_reviews(self.server.data_directory, repository_path)
                    )
                    if not known_review:
                        raise ValueError("Review not found for the configured repository")
                    workspace_path = validate_workspace(review_directory(
                        self.server.data_directory, review_id,
                    ))
                    data = _data(workspace_path)
                    with self.server.state_lock:
                        self.server.active_workspace = workspace_path
                        self.server.active_review_id = review_id
                    self._json(200, {**data, "review_id": review_id})
                    return
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
                        "is_git_repository": _is_git_repository_root(current),
                        "directories": [{
                            "name": child.name,
                            "path": str(child),
                            "is_workspace": (child / "manifest.json").is_file(),
                            "is_git_repository": (
                                (child / ".git").exists() and _is_git_repository_root(child)
                            ),
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
                        self.server.active_review_id = None
                    self._json(200, data)
                    return
                if self.path == "/api/workspace/init":
                    repo_path = _path_from_input(payload.get("repo"), self.server.starting_directory, "repo")
                    output_path = _path_from_input(payload.get("out"), self.server.starting_directory, "out")
                    review_modes = normalize_review_modes(payload.get("review_modes"))
                    golden_feature_aspects = []
                    if "review_focus" in payload:
                        if "focus" in payload:
                            raise ValueError("Use either review_focus or focus, not both")
                        review_focus = payload["review_focus"]
                        required_focus_keys = {"criteria", "custom_text"}
                        allowed_focus_keys = required_focus_keys | {"golden_feature_aspects"}
                        if (not isinstance(review_focus, dict)
                                or not required_focus_keys.issubset(review_focus)
                                or set(review_focus) - allowed_focus_keys):
                            raise ValueError(
                                "review_focus must contain criteria and custom_text; "
                                "golden_feature_aspects is optional"
                            )
                        focus = format_review_focus(
                            review_focus["criteria"], review_focus["custom_text"],
                        )
                        golden_feature_aspects = review_focus.get(
                            "golden_feature_aspects", [],
                        )
                        format_golden_feature_direction(golden_feature_aspects)
                        if len(focus) > MAX_FOCUS_LENGTH:
                            raise ValueError("combined review focus must be no longer than 6000 characters")
                    else:
                        focus = payload.get("focus", DEFAULT_FOCUS)
                        if not isinstance(focus, str) or len(focus) > 4000:
                            raise ValueError("focus must be text no longer than 4000 characters")
                    initialize_workspace(
                        repo_path, output_path, focus,
                        golden_feature_aspects=golden_feature_aspects,
                        review_modes=review_modes,
                    )
                    workspace_path = validate_workspace(output_path)
                    with self.server.state_lock:
                        self.server.active_workspace = workspace_path
                        self.server.active_review_id = None
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
                        provider_preferences(entry)
                        reasoning_preferences(entry)
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
                    parsed = parse_report(
                        content, approved, review_modes=manifest.get("review_modes"),
                    )
                    validate_locations(parsed, workspace_path / "snapshot")
                    parsed["audit_metadata"] = {"model_id": model, "source": "manual"}
                    save_json(workspace_path / "reports" / f"{model}.json", parsed)
                else:
                    if model not in reports:
                        raise ValueError("Unknown model")
                    path = workspace_path / "judgments" / f"{model}.json"
                    decision = load_json(path) if path.exists() else {"findings": {}, "rubric": {}}
                    modes = normalize_review_modes(
                        load_json(workspace_path / "manifest.json").get("review_modes"),
                    )
                    if self.path == "/api/rubric":
                        rubric_fields = rubric_fields_for_modes(modes)
                        maxima = {"improvements": 20, "gold": 20}
                        scores = {key: payload.get(key) for key in rubric_fields}
                        for key, score in scores.items():
                            maximum = maxima[key]
                            if type(score) is not int or not 0 <= score <= maximum:
                                raise ValueError(f"Invalid {key} score")
                        existing_rubric = decision.get("rubric", {})
                        if not isinstance(existing_rubric, dict):
                            raise ValueError("Invalid rubric data")
                        decision["rubric"] = {
                            key: value for key, value in existing_rubric.items()
                            if key in rubric_fields
                        }
                        decision["rubric"].update(scores)
                    else:
                        if "code_audit" not in modes:
                            raise ValueError(
                                "Findings can only be adjudicated for code_audit reviews"
                            )
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
    server.active_review_id = initial_review_id
    server.data_directory = data_directory
    server.repository_path = repository_path
    server.repository_available = repository_available
    server.repository_error = repository_error
    server.settings_error = settings_error
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
