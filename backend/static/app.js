/*
  YOLO Web Builder v4 - Wizard UI
  - One step at a time (1..6)
  - Stepper/progress bar reflects current step
  - Only checked export items run; INT8 -> show calib fields
  - Final step renders summary and submits to backend
*/

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function setHidden(el, hidden) {
  if (!el) return;
  el.classList.toggle('hidden', !!hidden);
}

function nowClockString() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const y = d.getFullYear();
  const m = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hh = pad(d.getHours());
  const mm = pad(d.getMinutes());
  const ss = pad(d.getSeconds());
  return `${y}-${m}-${day} ${hh}:${mm}:${ss}`;
}

function startClock() {
  const node = $('#clockText');
  if (!node) return;
  const tick = () => { node.textContent = nowClockString(); };
  tick();
  setInterval(tick, 1000);
}

// ---------------------- password visibility toggles ----------------------
function wireEyeButtons() {
  // event delegation: works for dynamic dataset cards
  document.addEventListener('click', (e) => {
    const btn = e.target.closest ? e.target.closest('button.eye-btn') : null;
    if (!btn) return;
    const id = btn.getAttribute('data-eye-for');
    if (!id) return;
    const input = document.getElementById(id);
    if (!input) return;
    const isPw = (input.getAttribute('type') || '').toLowerCase() === 'password';
    input.setAttribute('type', isPw ? 'text' : 'password');
    btn.setAttribute('aria-pressed', isPw ? 'true' : 'false');
  });
}

// ---------------------- datasets (dynamic list) ----------------------

const DATASET_ROLE_OPTIONS = [];

function datasetTemplate(idx) {
  return `
  <div class="subcard" id="dataset_${idx}" data-dataset="${idx}">
    <div class="row between">
      <div class="subcard-title">${escapeHtml(t('datasetTitle', {n: idx + 1}))}</div>
      <button class="btn" type="button" data-remove-ds="${idx}">${escapeHtml(t('removeBtn'))}</button>
    </div>
    <div class="grid">
      <label>${escapeHtml(t('dsName'))} <input data-k="name" placeholder="${escapeHtml(t('dsNamePh'))}" value="dataset_${idx + 1}"></label>
      <label>${escapeHtml(t('dsApiKey'))}
        <div class="pw-wrap">
          <input id="ds_api_key_${idx}" type="password" data-k="api_key" placeholder="${escapeHtml(t('dsApiKeyPh'))}">
          <button class="eye-btn" type="button" data-eye-for="ds_api_key_${idx}" aria-label="${escapeHtml(t('toggleApiKey'))}">👁</button>
        </div>
      </label>
      <label>${escapeHtml(t('dsWorkspace'))} <input data-k="workspace" placeholder="${escapeHtml(t('dsWorkspacePh'))}"></label>
      <label>${escapeHtml(t('dsProject'))} <input data-k="project" placeholder="${escapeHtml(t('dsProjectPh'))}"></label>
      <label>${escapeHtml(t('dsVersion'))} <input data-k="version" type="number" min="1" value="1"></label>
      <label>${escapeHtml(t('dsFormat'))}
        <select data-k="format">
          <option value="yolov8">YOLOv8</option>
          <option value="yolo11">YOLOv11</option>
        </select>
      </label>
      <label>${escapeHtml(t('dsClassName'))}
        <input data-k="role" placeholder="${escapeHtml(t('dsClassNamePh'))}" value="">
      </label>
    </div>
    <div class="hint">${t('dsHint')}</div>
  </div>`;
}
// store count; actual rendering uses ordinal
let datasetsState = [0];

// Step4 run-based state (Train&Export)
const runState = {};
// Legacy manual merge state (kept for backward compatibility; not used in Train&Export bars)
const manualMergeState = {};


function snapshotDatasetsUI() {
  try { return readDatasetsFromUI(); } catch { return []; }
}

function restoreDatasetsUI(list) {
  const root = $('#datasets');
  if (!root) return;
  (list || []).forEach((d, idx) => {
    const blk = root.querySelector(`[data-dataset="${idx}"]`);
    if (!blk) return;
    const set = (k, v) => {
      const n = blk.querySelector(`[data-k="${k}"]`);
      if (!n) return;
      n.value = (v === undefined || v === null) ? '' : String(v);
    };
    set('name', d.name || '');
    set('api_key', d.api_key || '');
    set('workspace', d.workspace || '');
    set('project', d.project || '');
    set('version', Number.isFinite(d.version) ? d.version : (d.version || ''));
    set('format', (d.format || 'yolov8').toLowerCase());
    set('role', d.role || '');
  });
}

function renderDatasets() {
  const root = $('#datasets');
  if (!root) return;
  root.innerHTML = datasetsState.map((_, idx) => datasetTemplate(idx)).join('');

  // when user focuses any field in a dataset card, set "apply to" index
  $$('[data-dataset]', root).forEach((blk) => {
    blk.addEventListener('focusin', () => {
      const i = parseInt(blk.getAttribute('data-dataset'), 10);
      const n = $('#rf_apply_idx');
      if (n && Number.isFinite(i)) n.value = String(i + 1);
    });
  });

  // wire remove
  $$('[data-remove-ds]', root).forEach(btn => {
    btn.addEventListener('click', () => {
      const i = parseInt(btn.getAttribute('data-remove-ds'), 10);
      if (Number.isFinite(i)) {
        const snap = snapshotDatasetsUI();
        snap.splice(i, 1);
        datasetsState.splice(i, 1);
        if (datasetsState.length === 0) datasetsState.push(0);
        renderDatasets();
        restoreDatasetsUI(snap);
        refreshDynamicUI();
      }
    });
  });
}

// ---------------------- Roboflow URL / dataset path parsing ----------------------

function parseRfRef(input) {
  const raw = (input || '').trim();
  if (!raw) throw new Error('請先貼上 Roboflow Dataset URL 或 <workspace>/<project>/<version>。');

  // Accept short form: workspace/project/version
  if (!raw.includes('://')) {
    const parts = raw.split('/').map(s => s.trim()).filter(Boolean);
    if (parts.length < 3) throw new Error('格式需為 <workspace>/<project>/<version>。');
    const ver = parts.find(p => /^\d+$/.test(p));
    if (!ver) throw new Error('找不到 version（數字）。請在 Roboflow Universe 點左側 Dataset/Versions，複製含 /dataset/<version> 的網址再 Parse。');
    return { workspace: parts[0], project: parts[1], version: parseInt(ver, 10) };
  }

  // URL form
  let u;
  try { u = new URL(raw); } catch {
    throw new Error('URL 解析失敗，請確認貼上的是完整 URL。');
  }
  const segs = u.pathname.split('/').map(s => s.trim()).filter(Boolean);
  if (segs.length < 2) throw new Error('URL path 不包含 workspace/project。');

  const workspace = segs[0];
  const project = segs[1];
  // common patterns: /<ws>/<proj>/dataset/<ver>  or /<ws>/<proj>/<ver>
  const verSeg = segs.find(s => /^\d+$/.test(s)) || (u.searchParams.get('version') || '');
  if (!verSeg || !/^\d+$/.test(verSeg)) throw new Error('找不到 version（數字）。請在 Roboflow Universe 點左側 Dataset/Versions，複製含 /dataset/<version> 的網址再 Parse。');
  return { workspace, project, version: parseInt(verSeg, 10) };
}

function applyRfRefToDataset(ref, idx0, apiKey) {
  const snap = snapshotDatasetsUI();
  // ensure enough dataset cards
  while (datasetsState.length < idx0 + 1) datasetsState.push(datasetsState.length);
  renderDatasets();
  restoreDatasetsUI(snap);

  const root = $('#datasets');
  if (!root) return;
  const blk = root.querySelector(`[data-dataset="${idx0}"]`);
  if (!blk) return;

  const get = (k) => {
    const n = blk.querySelector(`[data-k="${k}"]`);
    return n ? String(n.value || '') : '';
  };
  const set = (k, v) => {
    const n = blk.querySelector(`[data-k="${k}"]`);
    if (n) n.value = String(v);
  };

  // Fill from URL
  set('workspace', ref.workspace);
  set('project', ref.project);
  set('version', ref.version);

  // Best-effort fill API key: cannot be inferred from URL; only use user-provided quick key
  const curKey = get('api_key').trim();
  const quickKey = (apiKey || '').trim();
  if (quickKey && (!curKey || curKey === 'roboflow_api_key')) {
    set('api_key', quickKey);
  }

  // Best-effort fill Name: use project slug if still default
  const curName = get('name').trim();
  if (!curName || /^dataset_\d+$/i.test(curName)) {
    set('name', ref.project);
  }
}

// ---------------------- Roboflow Download Code parsing (Jupyter / Terminal / Raw URL) ----------------------

let rfQuickMode = 'jupyter';
const rfQuickRaw = { jupyter: '', terminal: '', rawurl: '' };
const rfQuickKey = { jupyter: '', terminal: '', rawurl: '' };

function normalizeRfFormat(fmt) {
  const f = (fmt || '').toString().trim().toLowerCase();
  if (!f) return '';
  // UI currently supports only yolov8 / yolo11
  if (f.includes('11')) return 'yolo11';
  return 'yolov8';
}

function maskApiKeyInText(raw) {
  let key = '';
  let out = raw || '';

  // Pattern A: api_key="..."
  out = out.replace(/api_key\s*=\s*["']([^"']+)["']/g, (m, k) => {
    if (!key && k && !k.includes('$')) key = k;
    return m.replace(k, '***');
  });

  // Pattern B: Roboflow("API_KEY") or Roboflow('API_KEY')
  out = out.replace(/Roboflow\s*\(\s*["']([^"']+)["']\s*\)/g, (m, k) => {
    if (!key && k && !k.includes('$')) key = k;
    return m.replace(k, '***');
  });

  // Pattern C: api_key=... in URL query
  out = out.replace(/api_key=([^\s"'&]+)/g, (m, k) => {
    const v = decodeURIComponent(k || '');
    if (!key && v && !v.includes('$')) key = v;
    return m.replace(k, '***');
  });

  return { key, masked: out };
}

function rfQuickRebuildRawFromVisible(mode, visible) {
  const savedKey = (rfQuickKey[mode] || '').trim();
  if (savedKey && visible.includes('***')) {
    // best-effort: restore key into hidden raw
    return visible.split('***').join(savedKey);
  }
  return visible;
}

function rfQuickHandleInput(mode, el) {
  const visible = (el.value || '');
  const raw = rfQuickRebuildRawFromVisible(mode, visible);
  const { key, masked } = maskApiKeyInText(raw);

  if (key) rfQuickKey[mode] = key;
  rfQuickRaw[mode] = raw;

  if (masked !== visible) {
    // update UI without exposing key
    const pos = el.selectionStart;
    el.value = masked;
    try { el.setSelectionRange(pos, pos); } catch {}
  }
}

function extractFirstUrl(text) {
  const m = (text || '').match(/https?:\/\/[^\s"'<>]+/);
  return m ? m[0] : '';
}

function parsePythonDownloadCode(code) {
  const raw = (code || '').trim();
  if (!raw) throw new Error('請先貼上 Jupyter/Python 的 download code。');

  // api key
  let apiKey = '';
  const mKeyA = raw.match(/Roboflow\s*\(\s*api_key\s*=\s*["']([^"']+)["']\s*\)/);
  const mKeyB = raw.match(/Roboflow\s*\(\s*["']([^"']+)["']\s*\)/);
  if (mKeyA) apiKey = mKeyA[1];
  else if (mKeyB) apiKey = mKeyB[1];
  apiKey = (apiKey || '').trim();
  if (apiKey.includes('$')) apiKey = '';

  // workspace / project
  const mWs = raw.match(/workspace\s*\(\s*["']([^"']+)["']\s*\)/);
  const mPr = raw.match(/project\s*\(\s*["']([^"']+)["']\s*\)/);
  const workspace = mWs ? mWs[1].trim() : '';
  const project = mPr ? mPr[1].trim() : '';

  // version
  const mVer = raw.match(/version\s*\(\s*["']?(\d+)["']?\s*\)/);
  const version = mVer ? parseInt(mVer[1], 10) : NaN;

  // format
  let fmt = '';
  const mFmtA = raw.match(/download\s*\(\s*["']([^"']+)["']\s*\)/);
  const mFmtB = raw.match(/download\s*\(\s*[^)]*model_format\s*=\s*["']([^"']+)["']/);
  if (mFmtA) fmt = mFmtA[1];
  else if (mFmtB) fmt = mFmtB[1];
  fmt = normalizeRfFormat(fmt);

  if (!workspace || !project || !Number.isFinite(version)) {
    throw new Error('解析失敗：找不到 workspace / project / version。請確認貼上的是 Roboflow 的 download code。');
  }
  return { api_key: apiKey, workspace, project, version, format: fmt };
}

function parseCliDownloadCode(code) {
  const raw = (code || '').trim();
  if (!raw) throw new Error('請先貼上 Terminal 的 download code。');

  // roboflow CLI
  if (/roboflow\s+download\b/i.test(raw)) {
    const mFmt = raw.match(/(?:^|\s)-f\s+([^\s]+)/i);
    const fmt = normalizeRfFormat(mFmt ? mFmt[1] : '');
    const mPath = raw.match(/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)\/(\d+)\b/);
    if (!mPath) throw new Error('解析失敗：找不到 <workspace>/<project>/<version>。');
    return {
      api_key: '',
      workspace: mPath[1],
      project: mPath[2],
      version: parseInt(mPath[3], 10),
      format: fmt,
    };
  }

  // curl "https://api.roboflow.com/..."
  if (/curl\b/i.test(raw)) {
    const url = extractFirstUrl(raw);
    if (!url) throw new Error('解析失敗：找不到 URL。');
    return parseRawUrlDownloadCode(url);
  }

  // Fallback: maybe a raw URL or dataset path
  return parseRawUrlDownloadCode(raw);
}

function parseRawUrlDownloadCode(text) {
  const raw = (text || '').trim();
  if (!raw) throw new Error('請先貼上 Raw URL 或 <workspace>/<project>/<version>。');

  // If there is a URL inside the text, extract it
  const url = extractFirstUrl(raw) || raw;

  // short form: workspace/project/version
  if (!url.includes('://')) {
    const ref = parseRfRef(url);
    return { api_key: '', workspace: ref.workspace, project: ref.project, version: ref.version, format: '' };
  }

  let u;
  try { u = new URL(url); } catch { throw new Error('URL 解析失敗，請確認貼上完整 URL。'); }

  // REST API route: /:workspace/:project/:version/:format?api_key=...
  if (u.hostname.includes('api.roboflow.com')) {
    const segs = u.pathname.split('/').map(s => s.trim()).filter(Boolean);
    const workspace = segs[0] || '';
    const project = segs[1] || '';
    const version = segs[2] ? parseInt(segs[2], 10) : NaN;
    const fmt = normalizeRfFormat(segs[3] || '');
    const apiKey = (u.searchParams.get('api_key') || '').trim();
    return { api_key: apiKey && !apiKey.includes('$') ? apiKey : '', workspace, project, version, format: fmt };
  }

  // Universe / other URL: fall back to parseRfRef (workspace/project/version)
  const ref = parseRfRef(url);
  return { api_key: '', workspace: ref.workspace, project: ref.project, version: ref.version, format: '' };
}

function applyParsedRoboflowToDataset(parsed, idx0) {
  const snap = snapshotDatasetsUI();
  while (datasetsState.length < idx0 + 1) datasetsState.push(datasetsState.length);
  renderDatasets();
  restoreDatasetsUI(snap);

  const root = $('#datasets');
  if (!root) return;
  const blk = root.querySelector(`[data-dataset="${idx0}"]`);
  if (!blk) return;

  const get = (k) => {
    const n = blk.querySelector(`[data-k="${k}"]`);
    return n ? String(n.value || '') : '';
  };
  const set = (k, v) => {
    const n = blk.querySelector(`[data-k="${k}"]`);
    if (n) n.value = String(v);
  };

  if (parsed.workspace) set('workspace', parsed.workspace);
  if (parsed.project) set('project', parsed.project);
  if (Number.isFinite(parsed.version)) set('version', parsed.version);

  // API key: only set when parsed has real key (not empty)
  const apiKey = (parsed.api_key || '').trim();
  if (apiKey && !apiKey.includes('***')) set('api_key', apiKey);

  // Format: if parsed provided a supported format
  const fmt = normalizeRfFormat(parsed.format || '');
  if (fmt) set('format', fmt);

  // Best-effort fill Name: use project slug if still default
  const curName = get('name').trim();
  if (!curName || /^dataset_\d+$/i.test(curName)) {
    if (parsed.project) set('name', parsed.project);
  }
}

function setRfMode(mode) {
  rfQuickMode = mode;
  $$('.rf-tab').forEach(btn => {
    const m = btn.getAttribute('data-rf-mode');
    const active = (m === mode);
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  $$('.rf-panel').forEach(p => {
    const m = p.getAttribute('data-rf-panel');
    p.hidden = (m !== mode);
  });
}

function initRfQuickParse() {
  const box = $('#rfQuickBox');
  if (!box) return;

  // tabs
  $$('.rf-tab', box).forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.getAttribute('data-rf-mode') || 'jupyter';
      setRfMode(mode);
    });
  });

  // input masking (keep raw in memory)
  const bind = (mode, sel) => {
    const el = $(sel);
    if (!el) return;
    ['input', 'change'].forEach(evt => el.addEventListener(evt, () => rfQuickHandleInput(mode, el)));
    el.addEventListener('paste', () => setTimeout(() => rfQuickHandleInput(mode, el), 0));
  };
  bind('jupyter', '#rf_code_jupyter');
  bind('terminal', '#rf_code_terminal');
  bind('rawurl', '#rf_code_rawurl');

  // default
  setRfMode('jupyter');

  // parse
  $('#rf_parse_code_btn')?.addEventListener('click', () => {
    const err = $('#rf_parse_err');
    if (err) err.textContent = '';
    try {
      const idx0 = Math.max(0, Math.trunc(numVal('#rf_apply_idx', 1)) - 1);
      let parsed;
      const mode = rfQuickMode || 'jupyter';
      const raw = (rfQuickRaw[mode] || '').trim() || strVal(mode === 'jupyter' ? '#rf_code_jupyter' : mode === 'terminal' ? '#rf_code_terminal' : '#rf_code_rawurl');

      if (mode === 'jupyter') parsed = parsePythonDownloadCode(raw);
      else if (mode === 'terminal') parsed = parseCliDownloadCode(raw);
      else parsed = parseRawUrlDownloadCode(raw);

      applyParsedRoboflowToDataset(parsed, idx0);

      // After Parse: clear the active panel (avoid leaving code/key), and auto-advance Dataset #
      rfQuickRaw[mode] = '';
      rfQuickKey[mode] = rfQuickKey[mode] || '';
      const activeEl = mode === 'jupyter' ? $('#rf_code_jupyter') : mode === 'terminal' ? $('#rf_code_terminal') : $('#rf_code_rawurl');
      if (activeEl) activeEl.value = '';

      // increment "Dataset #" (apply index) and auto-add dataset if needed
      const nextIdx1 = idx0 + 2; // 1-based
      const applyN = $('#rf_apply_idx');
      if (applyN) applyN.value = String(nextIdx1);
      if (nextIdx1 > datasetsState.length) {
        addDataset();
      }
    } catch (e) {
      if (err) err.textContent = e.message || String(e);
    }
  });
}

function addDataset() {
  const snap = snapshotDatasetsUI();
  datasetsState.push(datasetsState.length);
  renderDatasets();
  restoreDatasetsUI(snap);
  refreshDynamicUI();
}

function readDatasetsFromUI() {
  const root = $('#datasets');
  if (!root) return [];
  const blocks = $$('[data-dataset]', root);
  return blocks.map((blk) => {
    const get = (k) => {
      const n = blk.querySelector(`[data-k="${k}"]`);
      return n ? n.value : '';
    };
    return {
      name: get('name').trim(),
      api_key: get('api_key').trim(),
      workspace: get('workspace').trim(),
      project: get('project').trim(),
      version: parseInt(get('version'), 10),
      format: (get('format').trim() || 'yolov8').toLowerCase(),
      role: (get('role') || '').trim(),
    };
  });
}


const STEP_NOTE_KEYS = {
  1: ['note1_1','note1_2'],
  2: ['note2_1','note2_2','note2_3'],
  4: ['note4_1','note4_2','note4_3'],
  6: ['note6_1','note6_2'],
};

const HOME_NOTE_KEYS = ['homeNote_1','homeNote_2','homeNote_3'];

// ---------------------- wizard state ----------------------

const STEPS = [1, 2, 4, 6];
let currentStep = 1;

// Step confirmation gating: after any edits in a step, that step and all following
// steps must be confirmed again by pressing that step's Submit/Next.
const stepConfirmed = {1:false, 2:false, 4:false, 6:false};

function invalidateFromStep(step){
  const i = STEPS.indexOf(step);
  if (i < 0) return;
  for (let k = i; k < STEPS.length; k++) stepConfirmed[STEPS[k]] = false;
  updateConfirmUI();
}

function markStepConfirmed(step){
  if (!(step in stepConfirmed)) return;
  stepConfirmed[step] = true;
  updateConfirmUI();
}

function firstUnconfirmedRequired(){
  for (const s of [1,2,4]) {
    if (!stepConfirmed[s]) return s;
  }
  return null;
}

function updateConfirmUI(){
  STEPS.forEach(s => {
    const sec = document.querySelector(`.step[data-step="${s}"]`);
    if (!sec) return;
    const btn = sec.querySelector(s===6 ? '[data-action="final"]' : '[data-action="next"]');
    if (!btn) return;
    btn.classList.toggle('neon-green', !!stepConfirmed[s]);
    btn.classList.toggle('neon-red', !stepConfirmed[s]);
  });
}

let maxUnlockedStep = 1;

function showStep(step, {scroll = true} = {}) {
  currentStep = step;
  // show only the selected step
  $$('.step').forEach(s => {
    const n = parseInt(s.getAttribute('data-step'), 10);
    s.hidden = (n !== step);
  });
  // update progress/stepper + stage notes
  updateProgressUI();
  updateStageNotes();
  refreshDynamicUI();
  updateConfirmUI();
  if (scroll) {
    // scroll to top of wizard
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}


function updateStageNotes() {
  const ul = $('#stageNotesList');
  if (!ul) return;

  const homeVisible = !$('#homeView')?.classList.contains('hidden');
  const keys = homeVisible ? HOME_NOTE_KEYS : (STEP_NOTE_KEYS[currentStep] || []);
  ul.innerHTML = keys.map(k => `<li>${escapeHtml(t(k))}</li>`).join('');
}


function updateProgressUI() {
  const fill = $('#progressFill');
  const hint = $('#progressHint');
  // fill should stop at the current step icon (not exceed)
  const track = document.querySelector('.progress-bar');
  const activeBtn = document.querySelector(`.progress-step[data-step="${currentStep}"]`);
  if (fill && track && activeBtn) {
    const tr = track.getBoundingClientRect();
    const br = activeBtn.getBoundingClientRect();
    const center = (br.left + br.right) / 2;
    const pct = ((center - tr.left) / tr.width) * 100;
    fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }

  $$('.progress-step').forEach(btn => {
    const s = parseInt(btn.getAttribute('data-step'), 10);
    if (s < currentStep) btn.setAttribute('data-state', 'done');
    else if (s === currentStep) btn.setAttribute('data-state', 'active');
    else if (s <= maxUnlockedStep) btn.setAttribute('data-state', '');
    else btn.setAttribute('data-state', 'locked');

    btn.disabled = (s > maxUnlockedStep);
  });

  if (hint) {
    hint.textContent = t('progressHint', {cur: currentStep, total: STEPS.length, unlocked: maxUnlockedStep});
  }
}

function setError(step, msg) {
  const n = $(`#err${step}`);
  if (n) n.textContent = msg || '';
}

function setQuantWarn(msg) {
  const n = $('#quantWarn');
  if (n) n.textContent = msg || '';
}

function setBalErr(msg) {
  const n = $('#bal_err');
  if (n) n.textContent = msg || '';
}

// ---------------------- dynamic UI rules ----------------------


function computeRoleGroups(datasets) {
  const groups = [];
  const map = {};
  datasets.forEach(d => {
    const role = (d.role || '').trim();
    if (!role) return;
    if (!map[role]) { map[role] = []; groups.push(role); }
    map[role].push(d.name);
  });
  
return { order: groups, map };
}

// ---------------------- Step4: Train&Export run bars ----------------------

const HYBRID_KEY = '__hybrid';

function defaultRunConfig(kind, role, allDatasetNames=[]) {
  const base = {
    kind,
    role: role || null,
    selected: false,
    open: false,
    saved: false,
    dataset: {
      checked: Array.from(allDatasetNames),
      confirmed: [],
      mode: null,
    },
    hybrid: {
      output_dir: 'datasets/hybird',
      split_seed: 42,
      split_ratio: [0.7, 0.2, 0.1],
    },
    train: {
      family: 'yolov8',
      size: 's',
      epochs: 50,
      batch: 16,
      imgsz: 640,
      workers: 4,
      device: '0',
      optimizer: 'AdamW',
      lr0: 0.001,
      patience: 50,
      close_mosaic: 10,
      pretrained: true,
    },
    export: {
      onnx: { enabled: false, open: false, simplify: false, fp16: false },
      engine: { enabled: false, open: false, fp32: true, fp16: true, int8: false, calib_num: 300, quant: { ptq: false, qat: false } },
      tflite: { enabled: false, open: false, fp32: true, fp16: true, int8: false, calib_num: 300, quant: { ptq: false, qat: false } },
    },
    quant: { ptq: false, qat: false },
    balance: { enabled: false, target: 'mean', custom_type: 'multiplier', custom_value: 0.7 },
  };
  if (kind === 'single') {
    // Single-class run: balancing irrelevant
    base.balance.enabled = false;
  }
  return base;
}

function normalizeRunQuantState(r) {
  if (!r || typeof r !== 'object') return;
  r.export = r.export || {};
  r.export.engine = r.export.engine || {};
  r.export.tflite = r.export.tflite || {};

  const eng = r.export.engine;
  const tfl = r.export.tflite;
  const legacy = { ptq: !!(r.quant?.ptq), qat: !!(r.quant?.qat) };

  const engineHadNested = !!eng.quant;
  const tfliteHadNested = !!tfl.quant;

  eng.quant = { ptq: !!(eng.quant?.ptq), qat: !!(eng.quant?.qat) };
  tfl.quant = { ptq: !!(tfl.quant?.ptq), qat: !!(tfl.quant?.qat) };

  if ((legacy.ptq || legacy.qat) && !engineHadNested && !tfliteHadNested) {
    if (eng.int8) {
      eng.quant.ptq = legacy.ptq;
      eng.quant.qat = legacy.qat;
    }
    if (tfl.int8) {
      tfl.quant.ptq = legacy.ptq;
      tfl.quant.qat = legacy.qat;
    }
  }

  if (!eng.int8) eng.quant = { ptq: false, qat: false };
  if (!tfl.int8) tfl.quant = { ptq: false, qat: false };
  if (eng.quant.qat && !eng.quant.ptq) eng.quant.qat = false;
  if (tfl.quant.qat && !tfl.quant.ptq) tfl.quant.qat = false;

  // legacy shared quant state is kept only for backward-compatible snapshots; UI no longer uses it
  r.quant = { ptq: false, qat: false };
}

function deepMergeDefaults(dst, defaults) {
  // Recursively fills missing fields in dst from defaults (does NOT overwrite existing values).
  // This prevents runtime errors when loading older drafts/tasks with partial runState schemas.
  if (!dst || typeof dst !== 'object') return JSON.parse(JSON.stringify(defaults));
  if (!defaults || typeof defaults !== 'object') return dst;

  Object.keys(defaults).forEach((k) => {
    const dv = defaults[k];
    const tv = dst[k];

    if (dv && typeof dv === 'object' && !Array.isArray(dv)) {
      if (!tv || typeof tv !== 'object' || Array.isArray(tv)) dst[k] = {};
      deepMergeDefaults(dst[k], dv);
    } else {
      if (tv === undefined) dst[k] = dv;
    }
  });

  return dst;
}

function ensureRunStateFromDatasets() {
  const ds = readDatasetsFromUI();
  const { order, map } = computeRoleGroups(ds);
  // role bars
  order.forEach(role => {
    const names = map[role] || [];
    const dflt = defaultRunConfig('single', role, names);
    if (!runState[role]) runState[role] = dflt;
    else deepMergeDefaults(runState[role], dflt);
    normalizeRunQuantState(runState[role]);
    // keep checked default if empty
    if (!Array.isArray(runState[role].dataset?.checked) || runState[role].dataset.checked.length === 0) {
      runState[role].dataset = runState[role].dataset || {};
      runState[role].dataset.checked = Array.from(names);
    }
    // prune checked/confirmed if datasets removed
    const setNames = new Set(names);
    runState[role].dataset.checked = (runState[role].dataset.checked || []).filter(n => setNames.has(n));
    runState[role].dataset.confirmed = (runState[role].dataset.confirmed || []).filter(n => setNames.has(n));
    if ((runState[role].dataset.confirmed || []).length === 0) {
      runState[role].dataset.mode = null;
    }
    // enforce single-kind invariants
    runState[role].kind = 'single';
    runState[role].role = role;
    runState[role].balance.enabled = false;
  });

  // remove roles no longer present
  Object.keys(runState).forEach(k => {
    if (k === HYBRID_KEY) return;
    if (!order.includes(k)) delete runState[k];
  });

  // hybrid bar
  const hdflt = defaultRunConfig('hybrid', null, []);
  if (!runState[HYBRID_KEY]) runState[HYBRID_KEY] = hdflt;
  else deepMergeDefaults(runState[HYBRID_KEY], hdflt);
  normalizeRunQuantState(runState[HYBRID_KEY]);
  runState[HYBRID_KEY].kind = 'hybrid';
  runState[HYBRID_KEY].role = null;

  // disable hybrid when roles < 2
  runState[HYBRID_KEY].disabled = (order.length < 2);

  return { order, map };
}

function runLabel(key) {
  return key === HYBRID_KEY ? 'Hybrid' : key;
}


function updateRunVisual(key){
  // Update pill + neon state without rerendering (prevents scroll jump while typing).
  const bar = document.querySelector(`.run-bar[data-run="${CSS.escape(key)}"]`);
  if (!bar) return;
  const r = runState[key];
  if (!r) return;
  const pill = bar.querySelector('.run-pill');
  if (pill && r.selected) pill.textContent = r.saved ? t('s4_pill_saved') : t('s4_pill_not_saved');
  bar.classList.toggle('saved-neon', !!(r.selected && r.saved));
  bar.classList.toggle('dirty-neon', !!(r.selected && !r.saved));
}

function setRunDirty(key) {
  if (!runState[key]) return;
  runState[key].saved = false;
  invalidateFromStep(4);
  setDirty(true);
  scheduleProjectStateSync();
  updateRunVisual(key);
}

function setRunField(key, path, value) {
  const obj = runState[key];
  if (!obj) return;
  const parts = (path || '').split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    if (!cur[p]) cur[p] = {};
    cur = cur[p];
  }
  cur[parts[parts.length - 1]] = value;
  setRunDirty(key);
}


function enforceRunRules(key) {
  const r = runState[key];
  if (!r) return;

  // Export accordion: if disabled, force closed
  ['onnx','engine','tflite'].forEach(k => {
    const e = r.export && r.export[k];
    if (!e) return;
    if (!e.enabled) e.open = false;
  });

  // Quant options are tracked independently per INT8 export target.
  r.export.engine = r.export.engine || {};
  r.export.tflite = r.export.tflite || {};
  r.export.engine.quant = { ptq: !!(r.export.engine.quant?.ptq), qat: !!(r.export.engine.quant?.qat) };
  r.export.tflite.quant = { ptq: !!(r.export.tflite.quant?.ptq), qat: !!(r.export.tflite.quant?.qat) };

  if (!(r.export?.engine?.enabled && r.export.engine.int8)) {
    r.export.engine.quant.ptq = false;
    r.export.engine.quant.qat = false;
  }
  if (!(r.export?.tflite?.enabled && r.export.tflite.int8)) {
    r.export.tflite.quant.ptq = false;
    r.export.tflite.quant.qat = false;
  }
  if (r.export.engine.quant.qat && !r.export.engine.quant.ptq) r.export.engine.quant.qat = false;
  if (r.export.tflite.quant.qat && !r.export.tflite.quant.ptq) r.export.tflite.quant.qat = false;
  r.quant = { ptq: false, qat: false };
}
function renderRunBars() {
  const root = $('#runBars');
  if (!root) return;
  const { order, map } = ensureRunStateFromDatasets();

  // helper to build header
  const buildHeader = (key) => {
    const r = runState[key];
    const selected = !!r.selected;
    const disabled = !!r.disabled;
    const title = runLabel(key);
    const subtitle = (key === HYBRID_KEY)
      ? (disabled ? t('s4_hybrid_need2') : t('s4_hybrid_desc'))
      : t('s4_role_datasets', { n: (map[key]||[]).length });
    const pill = selected ? (r.saved ? t('s4_pill_saved') : t('s4_pill_not_saved')) : t('s4_pill_not_selected');
    const arrowDisabled = (!selected) || disabled;

    return `
      <div class="run-bar-header">
        <label class="choice" style="margin:0;">
          <input type="checkbox" data-action="selectRun" data-run="${escapeHtml(key)}" ${selected ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
          <span class="dot"></span>
        </label>
        <div>
          <div class="run-title">${escapeHtml(title)}</div>
          <div class="run-sub">${escapeHtml(subtitle)}</div>
        </div>
        <div class="run-meta">
          <span class="run-pill">${escapeHtml(pill)}</span>
          <button class="run-arrow" type="button" data-action="toggleOpen" data-run="${escapeHtml(key)}" ${arrowDisabled ? 'disabled' : ''}>${r.open ? '▴' : '▾'}</button>
        </div>
      </div>
    `;
  };

  const bars = [];
  order.forEach(role => bars.push(role));
  bars.push(HYBRID_KEY);

  root.innerHTML = bars.map(key => {
    const r = runState[key];
    const cls = ['run-bar', r.selected ? 'selected' : '', r.selected ? (r.saved ? 'saved-neon' : 'dirty-neon') : '', r.disabled ? 'disabled' : ''].filter(Boolean).join(' ');
    const detailHtml = (r.selected && r.open && !r.disabled) ? renderRunDetail(key, r, map) : '';
    return `<div class="${cls}" data-run="${escapeHtml(key)}">${buildHeader(key)}${detailHtml}</div>`;
  }).join('');

  wireRunBarsEvents(root, map, order);
}

function wireRunBarsEvents(root, map, order) {
  // single delegated handler
  root.querySelectorAll('[data-action="selectRun"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const key = cb.getAttribute('data-run');
      if (!runState[key]) return;

      // Multi-select behavior: selecting Hybrid adds one extra combined multi-class run.
      // Do not auto-toggle any other target here.

      runState[key].selected = !!cb.checked;
      if (!runState[key].selected) runState[key].open = false;
      setRunDirty(key);
      refreshDynamicUI();
    });
  });

  root.querySelectorAll('[data-action="toggleOpen"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.getAttribute('data-run');
      const r = runState[key];
      if (!r || !r.selected || r.disabled) return;
      r.open = !r.open;
      refreshDynamicUI();
    });
  });

  // detail controls (change/input)
  // IMPORTANT: Do NOT re-render the whole Step4 UI on every keystroke.
  // The `input` event fires for every value change (each keystroke), while `change`
  // fires when the value is committed (e.g., blur/enter/select). Re-rendering on
  // `input` will rebuild DOM and may cause the page to jump/scroll while typing.
  const onRunField = (el, evtType) => {
    const key = el.getAttribute('data-run');
    const path = el.getAttribute('data-run-field');
    if (!key || !path) return;

    // special parse: hybrid split_ratio string -> array
    if (path === 'hybrid.split_ratio_str') {
      // Only parse on commit (change). While typing, keep the textarea stable.
      if (evtType === 'input') return;
      const raw = String(el.value || '').trim();
      const parts = raw.split(',').map(x => Number(String(x).trim())).filter(x => Number.isFinite(x));
      if (parts.length === 3) {
        setRunField(key, 'hybrid.split_ratio', parts);
      } else {
        toast(t('s4_hybrid_ratio_warn'), 'warn');
      }
      refreshDynamicUI();
      return;
    }

    let v;
    if (el.type === 'checkbox') v = !!el.checked;
    else if (el.type === 'number') {
      const raw = String(el.value ?? '').trim();
      v = raw === '' ? '' : Number(raw);
      if (v !== '' && !Number.isFinite(v)) v = '';
    } else v = el.value;

    if (v !== '') setRunField(key, path, v);

    // intra-field rules
    const r = runState[key];
    if (path === 'export.engine.quant.ptq' && !v) r.export.engine.quant.qat = false;
    if (path === 'export.engine.quant.qat' && v) {
      if (!r.export.engine.quant.ptq) {
        r.export.engine.quant.qat = false;
        toast(t('s4_quant_need_ptq'), 'warn');
      }
    }
    if (path === 'export.tflite.quant.ptq' && !v) r.export.tflite.quant.qat = false;
    if (path === 'export.tflite.quant.qat' && v) {
      if (!r.export.tflite.quant.ptq) {
        r.export.tflite.quant.qat = false;
        toast(t('s4_quant_need_ptq'), 'warn');
      }
    }
    if (path === 'export.engine.int8' && !v) {
      r.export.engine.quant.ptq = false;
      r.export.engine.quant.qat = false;
    }
    if (path === 'export.tflite.int8' && !v) {
      r.export.tflite.quant.ptq = false;
      r.export.tflite.quant.qat = false;
    }

    enforceRunRules(key);
    updateRunVisual(key);
    // Only re-render on committed changes, or when toggles require layout updates.
    // For `input` events (typing), update state only to prevent scroll-jumping.
    if (evtType === 'change') {
      refreshDynamicUI();
    }
  };

  root.querySelectorAll('[data-run-field]').forEach(el => {
    const handler = (ev) => onRunField(el, ev?.type || 'change');
    el.addEventListener('change', handler);
    const tag = (el.tagName || '').toLowerCase();
    const typ = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'textarea' || (tag === 'input' && typ !== 'checkbox' && typ !== 'radio')) {
      el.addEventListener('input', handler);
    }
  });

  // export accordion toggles
  root.querySelectorAll('[data-action="toggleExportOpen"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.getAttribute('data-run');
      const exp = btn.getAttribute('data-exp');
      const r = runState[key];
      if (!r || !exp) return;
      const obj = r.export && r.export[exp];
      if (!obj || !obj.enabled) return;
      obj.open = !obj.open;
      refreshDynamicUI();
    });
  });

// dataset checkboxes
  root.querySelectorAll('[data-action="dsToggle"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const key = cb.getAttribute('data-run');
      const name = cb.getAttribute('data-name');
      const r = runState[key];
      if (!r) return;
      r.dataset.checked = r.dataset.checked || [];
      const set = new Set(r.dataset.checked);
      if (cb.checked) set.add(name);
      else set.delete(name);
      r.dataset.checked = Array.from(set);
      setRunDirty(key);
      // update button label via rerender
      refreshDynamicUI();
    });
  });

  // confirm datasets
  root.querySelectorAll('[data-action="confirmDatasets"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.getAttribute('data-run');
      const r = runState[key];
      if (!r) return;
      const sel = Array.from(new Set(r.dataset.checked || []));
      if (!sel.length) {
        toast(t('s4_ds_tip_atleast1'), 'warn');
        return;
      }
      r.dataset.confirmed = sel;
      r.dataset.mode = sel.length <= 1 ? 'select' : 'merge';
      setRunDirty(key);
      refreshDynamicUI();
    });
  });

  // save run
  root.querySelectorAll('[data-action="saveRun"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.getAttribute('data-run');
      const msg = validateOneRun(key);
      if (msg) {
        toast(msg, 'warn');
        return;
      }
      runState[key].saved = true;
      setDirty(true);
      scheduleProjectStateSync();
      refreshDynamicUI();
      updateRunVisual(key);
      toast(t('s4_saved_toast', {name: runLabel(key)}), 'ok');
    });
  });
}

function renderRunDetail(key, r, map) {
  const isHybrid = (key === HYBRID_KEY);
  const title = runLabel(key);

  const family = r.train.family || '';
  const size = r.train.size || '';
  const onnx = r.export.onnx || {};
  const eng = r.export.engine || {};
  const tfl = r.export.tflite || {};

  const renderQuantInline = (variant) => {
    const titleKey = variant === 'engine' ? 's4_quant_title_engine' : 's4_quant_title_tflite';
    const pathPrefix = variant === 'engine' ? 'export.engine.quant' : 'export.tflite.quant';
    const q = variant === 'engine' ? (eng.quant || {}) : (tfl.quant || {});
    const ptq = !!q.ptq;
    return `
      <div class="inline-panel inline-panel-quant">
        <div class="inline-panel-title">${escapeHtml(t(titleKey))}</div>
        <label class="choice">
          <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="${pathPrefix}.ptq" ${ptq?'checked':''}>
          <span class="dot"></span>
          <span class="text">${escapeHtml(t('s4_quant_ptq'))}</span>
        </label>
        <label class="choice">
          <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="${pathPrefix}.qat" ${q.qat?'checked':''} ${ptq?'':'disabled'}>
          <span class="dot"></span>
          <span class="text">${escapeHtml(t('s4_quant_qat'))}</span>
        </label>
        <div class="hint">${escapeHtml(t('s4_quant_hint'))}</div>
      </div>
    `;
  };

  // dataset selection block (single only)
  let dsBlock = '';
  if (!isHybrid) {
    const names = map[key] || [];
    const checked = new Set(r.dataset?.checked || names);
    const confirmed = Array.from(r.dataset?.confirmed || []);
    const checkedCnt = Array.from(checked).length;
    const btnLabel = checkedCnt <= 1 ? t('s4_ds_btn_select') : t('s4_ds_btn_merge');
    const confirmText = confirmed.length
      ? `Confirmed: ${(r.dataset.mode || (confirmed.length<=1?'select':'merge')).toUpperCase()} → ${confirmed.join(', ')}`
      : t('s4_ds_not_confirmed');

    const rows = names.map(n => {
      const on = checked.has(n);
      return `
        <div class="merge-row">
          <label class="choice" style="margin:0;">
            <input type="checkbox" data-action="dsToggle" data-run="${escapeHtml(key)}" data-name="${escapeHtml(n)}" ${on?'checked':''}>
            <span class="dot"></span>
            <span class="text"><span class="name">${escapeHtml(n)}</span></span>
          </label>
          <span class="small">${escapeHtml(title)}</span>
        </div>
      `;
    }).join('');
    dsBlock = `
      <div class="subcard">
        <div class="subcard-title">Dataset selection</div>
        <div class="hint">只會使用已確認（Select/Merge）之 datasets 進入訓練；未選到的 datasets <b>不會</b> 進入訓練。</div>
        <div class="merge-list">${rows}</div>
        <div class="actions-row">
          <div class="hint" style="margin-right:auto;">${escapeHtml(confirmText)}</div>
          <button class="btn" type="button" data-action="confirmDatasets" data-run="${escapeHtml(key)}" ${checkedCnt===0?'disabled':''}>${btnLabel}</button>
        </div>
      </div>
    `;
  } else {
    dsBlock = `
      <div class="subcard">
        <div class="subcard-title">${escapeHtml(t('s4_hybrid_ds_title'))}</div>
        <div class="hint">${escapeHtml(t('s4_hybrid_ds_hint'))}</div>
      </div>
    `;
  }

  const balanceBlock = isHybrid ? `
    <div class="subcard">
      <div class="subcard-title">${escapeHtml(t('s4_balance_title'))}</div>
      <div class="hint">${escapeHtml(t('s4_balance_intro'))}</div>
      <label class="choice">
        <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="balance.enabled" ${r.balance.enabled?'checked':''}>
        <span class="dot"></span>
        <span class="text">${escapeHtml(t('s4_balance_enable'))}</span>
      </label>
      <div class="hint hint-blue balance-help">${escapeHtml(t('s4_balance_enable_hint'))}</div>
      ${r.balance.enabled ? `
      <div class="balance-fields">
        <label class="opt-field">${escapeHtml(t('s4_balance_target'))}
          <select data-run="${escapeHtml(key)}" data-run-field="balance.target">
            <option value="mean" ${r.balance.target==='mean'?'selected':''}>${escapeHtml(t('s4_balance_target_mean'))}</option>
            <option value="max" ${r.balance.target==='max'?'selected':''}>${escapeHtml(t('s4_balance_target_max'))}</option>
            <option value="custom" ${r.balance.target==='custom'?'selected':''}>${escapeHtml(t('s4_balance_target_custom'))}</option>
          </select>
        </label>
        ${r.balance.target==='custom' ? `
        <label class="opt-field">${escapeHtml(t('s4_balance_custom_type'))}
          <select data-run="${escapeHtml(key)}" data-run-field="balance.custom_type">
            <option value="multiplier" ${r.balance.custom_type==='multiplier'?'selected':''}>multiplier</option>
            <option value="count" ${r.balance.custom_type==='count'?'selected':''}>count</option>
          </select>
        </label>
        <label class="opt-field">${escapeHtml(t('s4_balance_custom_value'))}
          <input type="number" step="0.01" data-run="${escapeHtml(key)}" data-run-field="balance.custom_value" value="${escapeHtml(String(r.balance.custom_value ?? ''))}">
        </label>
        ` : ``}
      </div>` : ``}
      <div class="info-banner warn" style="margin-top:14px;">
        ${escapeHtml(t('s6_jetson_warn'))}
      </div>
    </div>
  ` : ``;

  return `
    <div class="run-detail">
      ${dsBlock}
      ${balanceBlock}

      <div class="subcard">
        <div class="subcard-title">Train</div>
        <div class="grid">
          <label>${escapeHtml(t('s4_model_family'))}
            <select data-run="${escapeHtml(key)}" data-run-field="train.family">
              <option value="" ${!family?'selected':''}>Select...</option>
              <option value="yolov8" ${family==='yolov8'?'selected':''}>yolov8</option>
              <option value="yolo11" ${family==='yolo11'?'selected':''}>yolo11</option>
            </select>
          </label>
          <label>Model size
            <select data-run="${escapeHtml(key)}" data-run-field="train.size">
              <option value="" ${!size?'selected':''}>Select...</option>
              ${['n','s','m','l','x'].map(v => `<option value="${v}" ${size===v?'selected':''}>${v}</option>`).join('')}
            </select>
          </label>
          <label>${escapeHtml(t('s4_epochs'))}
            <input type="number" min="1" data-run="${escapeHtml(key)}" data-run-field="train.epochs" value="${escapeHtml(String(r.train.epochs))}">
          </label>
          <label>${escapeHtml(t('s4_batch'))}
            <input type="number" min="1" data-run="${escapeHtml(key)}" data-run-field="train.batch" value="${escapeHtml(String(r.train.batch))}">
          </label>
          <label>${escapeHtml(t('s4_imgsz'))}
            <input type="number" min="32" data-run="${escapeHtml(key)}" data-run-field="train.imgsz" value="${escapeHtml(String(r.train.imgsz))}">
          </label>
          <label>${escapeHtml(t('s4_workers'))}
            <input type="number" min="0" data-run="${escapeHtml(key)}" data-run-field="train.workers" value="${escapeHtml(String(r.train.workers))}">
          </label>
          <label>${escapeHtml(t('s4_device'))}
            <input data-run="${escapeHtml(key)}" data-run-field="train.device" value="${escapeHtml(String(r.train.device))}" placeholder="${escapeHtml(t('s4_device_ph'))}">
          </label>
          <label>${escapeHtml(t('s4_optimizer'))}
            <select data-run="${escapeHtml(key)}" data-run-field="train.optimizer">
              ${['SGD','Adam','AdamW','RMSProp','auto'].map(v => `<option value="${v}" ${(r.train.optimizer===v)?'selected':''}>${v}</option>`).join('')}
            </select>
          </label>
          <label>${escapeHtml(t('s4_lr0'))}
            <input type="number" step="0.0001" data-run="${escapeHtml(key)}" data-run-field="train.lr0" value="${escapeHtml(String(r.train.lr0))}">
          </label>
          <label>${escapeHtml(t('s4_patience'))}
            <input type="number" min="0" data-run="${escapeHtml(key)}" data-run-field="train.patience" value="${escapeHtml(String(r.train.patience))}">
          </label>
          <label>${escapeHtml(t('s4_close_mosaic'))}
            <input type="number" min="0" data-run="${escapeHtml(key)}" data-run-field="train.close_mosaic" value="${escapeHtml(String(r.train.close_mosaic))}">
          </label>
        </div>
        <div class="hint">${escapeHtml(t(isHybrid ? 's4_train_hint_hybrid' : 's4_train_hint_single'))}</div>
      </div>

      <div class="subcard">
        <div class="subcard-title">${escapeHtml(t('s4_export_title'))}</div>

        <div class="opt-row">
          <label class="choice opt-main">
            <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.onnx.enabled" ${onnx.enabled?'checked':''}>
            <span class="dot"></span>
            <span class="text">${escapeHtml(t('s4_export_onnx_label'))}</span>
          </label>
          <button class="mini-arrow" type="button" data-action="toggleExportOpen" data-run="${escapeHtml(key)}" data-exp="onnx" ${onnx.enabled?'':'disabled'}>${onnx.open?'▴':'▾'}</button>
        </div>
        ${(onnx.enabled && onnx.open) ? `
          <div class="opt-details">
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.onnx.simplify" ${onnx.simplify?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_onnx_detail_simplify'))}</span>
            </label>
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.onnx.fp16" ${onnx.fp16?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_onnx_detail_fp16'))}</span>
            </label>
          </div>
        ` : ``}

        <hr class="soft opt-divider">

        <div class="opt-row">
          <label class="choice opt-main">
            <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.engine.enabled" ${eng.enabled?'checked':''}>
            <span class="dot"></span>
            <span class="text">${escapeHtml(t('s4_export_engine_label'))}</span>
          </label>
          <button class="mini-arrow" type="button" data-action="toggleExportOpen" data-run="${escapeHtml(key)}" data-exp="engine" ${eng.enabled?'':'disabled'}>${eng.open?'▴':'▾'}</button>
        </div>
        ${(eng.enabled && eng.open) ? `
          <div class="opt-details">
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.engine.fp32" ${eng.fp32?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_engine_fp32'))}</span>
            </label>
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.engine.fp16" ${eng.fp16?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_engine_fp16'))}</span>
            </label>
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.engine.int8" ${eng.int8?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_engine_int8'))}</span>
            </label>
            ${eng.int8 ? `
              <div class="stack-gap-md">
                <label class="opt-field">${escapeHtml(t('s4_export_int8_calib'))}
                  <input type="number" min="1" data-run="${escapeHtml(key)}" data-run-field="export.engine.calib_num" value="${escapeHtml(String(eng.calib_num||300))}">
                </label>
                ${renderQuantInline('engine')}
              </div>
            ` : ``}
            <div class="hint">${escapeHtml(t('s4_export_engine_hint'))}</div>
          </div>
        ` : ``}

        <hr class="soft opt-divider">

        <div class="opt-row">
          <label class="choice opt-main">
            <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.tflite.enabled" ${tfl.enabled?'checked':''}>
            <span class="dot"></span>
            <span class="text">${escapeHtml(t('s4_export_tflite_label'))}</span>
          </label>
          <button class="mini-arrow" type="button" data-action="toggleExportOpen" data-run="${escapeHtml(key)}" data-exp="tflite" ${tfl.enabled?'':'disabled'}>${tfl.open?'▴':'▾'}</button>
        </div>
        ${(tfl.enabled && tfl.open) ? `
          <div class="opt-details">
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.tflite.fp32" ${tfl.fp32?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_tflite_fp32'))}</span>
            </label>
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.tflite.fp16" ${tfl.fp16?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_tflite_fp16'))}</span>
            </label>
            <label class="choice">
              <input type="checkbox" data-run="${escapeHtml(key)}" data-run-field="export.tflite.int8" ${tfl.int8?'checked':''}>
              <span class="dot"></span>
              <span class="text">${escapeHtml(t('s4_export_tflite_int8'))}</span>
            </label>
            ${tfl.int8 ? `
              <div class="stack-gap-md">
                <label class="opt-field">${escapeHtml(t('s4_export_int8_calib'))}
                  <input type="number" min="1" data-run="${escapeHtml(key)}" data-run-field="export.tflite.calib_num" value="${escapeHtml(String(tfl.calib_num||300))}">
                </label>
                ${renderQuantInline('tflite')}
              </div>
            ` : ``}
          </div>
        ` : ``}
      </div>

      <div class="actions-row">
        <button class="btn" type="button" data-action="saveRun" data-run="${escapeHtml(key)}">Save</button>
      </div>
    </div>
  `;
}

function validateOneRun(key) {
  const r = runState[key];
  if (!r || !r.selected) return '';
  const label = runLabel(key);

  // dataset confirmation for single
  if (r.kind === 'single') {
    const conf = Array.from(r.dataset?.confirmed || []);
    if (!conf.length) return `「${label}」尚未按下 Select/Merge 確認 datasets。`;
  }

  // train
  if (!r.train?.family) return `「${label}」請選擇 Model family。`;
  if (!r.train?.size) return `「${label}」請選擇 Model size。`;
  const epochs = Number(r.train.epochs);
  const batch = Number(r.train.batch);
  const imgsz = Number(r.train.imgsz);
  if (!Number.isFinite(epochs) || epochs < 1) return `「${label}」Epochs 必須 ≥ 1。`;
  if (!Number.isFinite(batch) || batch < 1) return `「${label}」Batch 必須 ≥ 1。`;
  if (!Number.isFinite(imgsz) || imgsz < 32) return `「${label}」imgsz 必須 ≥ 32。`;

  // export validation
  const eng = r.export?.engine || {};
  if (eng.enabled) {
    const any = !!eng.fp32 || !!eng.fp16 || !!eng.int8;
    if (!any) return `「${label}」Engine：請至少勾選一種精度（FP32/FP16/INT8）。`;
    if (eng.int8) {
      const n = Number(eng.calib_num || 0);
      if (!Number.isFinite(n) || n < 1) return `「${label}」Engine INT8：Calib num 必須 ≥ 1。`;
    }
  }
  const tfl = r.export?.tflite || {};
  if (tfl.enabled) {
    const any = !!tfl.fp32 || !!tfl.fp16 || !!tfl.int8;
    if (!any) return `「${label}」TFLite：請至少勾選一種精度（FP32/FP16/INT8）。`;
    if (tfl.int8) {
      const n = Number(tfl.calib_num || 0);
      if (!Number.isFinite(n) || n < 1) return `「${label}」TFLite INT8：Calib num 必須 ≥ 1。`;
    }
  }

  // quant rules (independent per export target)
  const engQ = eng.quant || {};
  const tflQ = tfl.quant || {};
  if (engQ.qat && !engQ.ptq) return `「${label}」Engine INT8 需先選 PTQ 才能選 QAT。`;
  if (engQ.ptq && !(eng.enabled && !!eng.int8)) return `「${label}」Engine PTQ 需要勾選 INT8 Engine。`;
  if (tflQ.qat && !tflQ.ptq) return `「${label}」TFLite INT8 需先選 PTQ 才能選 QAT。`;
  if (tflQ.ptq && !(tfl.enabled && !!tfl.int8)) return `「${label}」TFLite PTQ 需要勾選 INT8 TFLite。`;

  // balance (hybrid only)
  if (r.kind === 'hybrid' && r.balance?.enabled) {
    const tgt = r.balance.target || 'mean';
    if (tgt === 'custom') {
      const v = Number(r.balance.custom_value);
      if (!Number.isFinite(v)) return `「${label}」Balance：Custom value 必須是數字。`;
      if (r.balance.custom_type === 'multiplier') {
        if (!(v > 0)) return `「${label}」Balance：multiplier 必須 > 0。`;
      } else {
        if (!(v >= 1)) return `「${label}」Balance：count 必須 ≥ 1。`;
      }
    }
  }

  return '';
}

function getSelectedRunKeys() {
  const { order } = ensureRunStateFromDatasets();
  const keys = [];
  order.forEach(role => { if (runState[role]?.selected) keys.push(role); });
  if (runState[HYBRID_KEY]?.selected) keys.push(HYBRID_KEY);
  return keys;
}

function toast(msg, level='info') {
  // show feedback near the active step so success/error messages stay visible.
  const box = (currentStep === 6 ? $('#err6') : null)
    || (currentStep === 4 ? ($('#runErr') || $('#err4')) : null)
    || $('#err6') || $('#runErr') || $('#err4');
  if (!box) return;
  box.textContent = msg || '';
  box.classList.remove('ok','warn');
  if (level === 'ok') box.classList.add('ok');
  if (level === 'warn') box.classList.add('warn');
  if (msg) setTimeout(() => { if (box.textContent === msg) box.textContent=''; }, 4500);
}



function renderMergeSections() {
  const box = $('#mergeBox');
  const root = $('#mergeSections');
  const err = $('#mergeErr');
  if (!box || !root) return;

  const hybridOn = isChecked('#hybrid_en');
  setHidden(box, hybridOn);
  if (hybridOn) return;

  const ds = readDatasetsFromUI();
  const { order, map } = computeRoleGroups(ds);

  if (!order.length) {
    root.innerHTML = '<div class="hint">請先到 Step2 新增 datasets，並填寫 Class name。</div>';
    if (err) err.textContent = '';
    return;
  }

  root.innerHTML = order.map(role => {
    const names = map[role] || [];
    const st = manualMergeState[role] || {};
    const checked = st.checked ? new Set(st.checked) : new Set(names);
    // initialize defaults
    if (!manualMergeState[role]) manualMergeState[role] = { checked, confirmed: [], mode: null };
    else manualMergeState[role].checked = checked;

    const checkedCnt = Array.from(checked).length;
    const btnLabel = checkedCnt <= 1 ? 'Select' : 'Merge';
    const disabled = checkedCnt === 0 ? 'disabled' : '';
    const confirmed = (st.confirmed || []);
    const confirmText = confirmed.length ? `Confirmed: ${(st.mode || (confirmed.length<=1?'select':'merge')).toUpperCase()} → ${confirmed.join(', ')}` : t('s4_ds_not_confirmed');

    const items = names.map(n => {
      const isOn = checked.has(n);
      return `<label class="choice"><input type="checkbox" data-mm="cb" data-role="${role}" data-name="${n}" ${isOn?'checked':''}><span class="dot"></span><span class="text">${n}</span></label>`;
    }).join('');

    return `
      <div class="merge-section" data-role="${role}">
        <div class="merge-head">
          <div class="merge-title">Class name: ${role}</div>
          <div class="merge-badge">${names.length} dataset(s)</div>
        </div>
        <div class="merge-body">${items}</div>
        <div class="merge-actions">
          <div class="merge-confirm" id="mm_confirm_${cssSafe(role)}">${confirmText}</div>
          <button class="btn" type="button" data-mm="confirm" data-role="${role}" ${disabled}>${btnLabel}</button>
        </div>
      </div>
    `;
  }).join('');

  // wire events (delegation)
  root.querySelectorAll('input[data-mm="cb"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const role = cb.getAttribute('data-role');
      const name = cb.getAttribute('data-name');
      if (!manualMergeState[role]) manualMergeState[role] = { checked: new Set(), confirmed: [], mode: null };
      if (cb.checked) manualMergeState[role].checked.add(name);
      else manualMergeState[role].checked.delete(name);
        renderSummary();
    });
  });
  root.querySelectorAll('button[data-mm="confirm"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const role = btn.getAttribute('data-role');
      const st = manualMergeState[role];
      const sel = Array.from(st.checked || []);
      if (!sel.length) return;
      st.confirmed = sel;
      st.mode = sel.length <= 1 ? 'select' : 'merge';
      // update confirm label
      const c = $(`#mm_confirm_${cssSafe(role)}`);
      if (c) c.textContent = `Confirmed: ${st.mode.toUpperCase()} → ${sel.join(', ')}`;
      setDirty(true);
      renderSummary();
    });
  });
}

function cssSafe(s) {
  return String(s).replace(/[^a-zA-Z0-9_\-]/g,'_');
}

function refreshDynamicUI() {
  // Step 1 mode / SSH box
  const mode = getMode();
  setHidden($('#sshBox'), mode !== 'ssh');

  // Step4 bars depend on Step2 datasets
  try { renderRunBars(); } catch (e) { console.error(e); }

  // Step 6: summary
  if (currentStep === 6) {
    renderSummary();
  }
}

function getMode() {
  const n = $('input[name="mode"]:checked');
  return n ? n.value : '';
}

function isChecked(sel) {
  const n = $(sel);
  return !!(n && n.checked);
}

function numVal(sel, fallback = null) {
  const n = $(sel);
  if (!n) return fallback;
  const t = String(n.value || '').trim();
  if (t === '') return fallback;
  const v = Number(t);
  return Number.isFinite(v) ? v : fallback;
}

function strVal(sel, fallback = '') {
  const n = $(sel);
  if (!n) return fallback;
  return String(n.value || '').trim();
}

function parseCSVInts(s) {
  const t = (s || '').trim();
  if (!t) return null;
  const arr = t.split(',').map(x => parseInt(x.trim(), 10)).filter(x => Number.isFinite(x));
  return arr.length ? arr : null;
}

// ---------------------- validation per step ----------------------

function validateStep(step) {
  setError(step, '');
  if (step === 1) {
    const task = strVal('#task_name','').trim();
    if (!task) return 'Task 必填（且需唯一，用於保存專案）。';
    const mode = getMode();
    if (!mode) return '請選擇 Mode。';
    if (mode === 'ssh') {
      if (!strVal('#ssh_host') || !strVal('#ssh_user')) return 'SSH 模式必填：Host 與 User。';
      const pwd = (strVal('#ssh_pass','') || '').trim();
      const key = (strVal('#ssh_key','') || '').trim();
      if (!pwd && !key) return 'SSH 模式需提供「Password」或「Private key (PEM)」至少一種驗證方式。';
      const py = (strVal('#ssh_py','python3') || '').trim();
      if (!py) return 'SSH 模式必填：Python（可填 python3 或 <env>/bin/python）。';
      const base = py.split('/').pop();
      if (py.includes('/') && !['python','python3'].includes(base)) {
        return 'Python 欄位需填「python 可執行檔」而不是資料夾，例如：python3 或 /home/.../miniconda3/envs/ENV/bin/python。';
      }
    }

    return '';
  }

  if (step === 2) {
    const ds = readDatasetsFromUI();
    if (!ds.length) return '至少需要 1 個 dataset。';
    // Name must be globally unique (folder name) and also unique within each Class name
    const seen = new Set();
    const seenByRole = {};
    for (let i = 0; i < ds.length; i++) {
      const d = ds[i];
      const role = (d.role || '').trim();
      if (d.name) {
        if (seen.has(d.name)) return `Dataset Name 需唯一：${d.name} 重複。`;
        seen.add(d.name);
      }
      if (role) {
        seenByRole[role] = seenByRole[role] || new Set();
        if (d.name && seenByRole[role].has(d.name)) return `同一個 Class name（${role}）下的 Name 需唯一：${d.name} 重複。`;
        if (d.name) seenByRole[role].add(d.name);
      }
    }
    for (let i = 0; i < ds.length; i++) {
      const d = ds[i];
      if (!d.name) return `Dataset #${i + 1}：Name 必填。`;
      if (!d.role) return `Dataset #${i + 1}：Class name（Step2）必填。`;
      if (!/^[a-zA-Z0-9_\-]+$/.test(d.name)) return `Dataset #${i + 1}：Name 只能包含 a-z/A-Z/0-9/_/-。`;
      if (!d.api_key) return `Dataset #${i + 1}：API key 必填。`;
      if (!d.workspace) return `Dataset #${i + 1}：Workspace 必填。`;
      if (!d.project) return `Dataset #${i + 1}：Project 必填。`;
      if (!Number.isFinite(d.version) || d.version < 1) return `Dataset #${i + 1}：Version 必須 ≥ 1。`;
    }
    // Name must be unique across all datasets (used as merge/select key)
    const names = ds.map(d => (d.name || '').trim()).filter(Boolean);
    const dup = findDuplicates(names);
    if (dup.length) return `Dataset Name 必須唯一；重複：${dup.join(', ')}`;

    return '';
  }

  if (step === 4) {
    ensureRunStateFromDatasets();
    const keys = getSelectedRunKeys();
    if (!keys.length) return '請至少選擇 1 個訓練項目（Class 或 Hybrid）。';

    const hybridSelected = keys.includes(HYBRID_KEY);
    if (hybridSelected && (runState[HYBRID_KEY]?.disabled)) return 'Hybrid 需要至少 2 種 Class name 才能使用。';

    for (const k of keys) {
      const msg = validateOneRun(k);
      if (msg) return msg;
      if (!runState[k]?.saved) return `「${runLabel(k)}」尚未按 Save 保存細項。`;
    }
    return '';
  }

  if (step === 6) {
    // Review step is informational; final submit will validate again server-side.
    return '';
  }

  return '';
}

// ---------------------- spec builder ----------------------


function buildManualMergeSpec() {
  const hybridOn = isChecked('#hybrid_en');
  if (hybridOn) {
    return { enabled: false, output_dir: 'datasets/manual_merge', split_ratio: [0.7,0.2,0.1], split_seed: 42, selections: {} };
  }
  const ds = readDatasetsFromUI();
  const { order, map } = computeRoleGroups(ds);

  // enabled only if at least one role has confirmed selection (button pressed)
  const selections = {};
  let enabled = false;
  order.forEach(role => {
    const st = manualMergeState[role];
    if (st && Array.isArray(st.confirmed) && st.confirmed.length) {
      selections[role] = Array.from(st.confirmed);
      enabled = true;
    }
  });

  // split settings reuse Step3 fields (even when Hybrid is OFF)
  const ratioRaw = strVal('#hybrid_ratio','0.7,0.2,0.1');
  const parts = ratioRaw.split(',').map(x => Number(x.trim())).filter(x => Number.isFinite(x));
  const split_ratio = (parts.length === 3) ? parts : [0.7,0.2,0.1];
  const split_seed = numVal('#hybrid_seed', 42);

  return {
    enabled,
    output_dir: 'datasets/manual_merge',
    split_ratio,
    split_seed,
    selections,
  };
}

function buildBundleSpec() {
  const mode = getMode() || 'bundle';
  const task = (strVal('#task_name','') || '').trim();
  const datasets = readDatasetsFromUI();

  // Build runs from Step4 runState
  ensureRunStateFromDatasets();
  const keys = getSelectedRunKeys();

  const runs = keys.map((key) => {
    const r = runState[key];
    const isHybrid = (key === HYBRID_KEY);
    const runTrain = {
      family: (r.train.family || 'yolov8'),
      size: (r.train.size || 'n'),
      epochs: Math.trunc(Number(r.train.epochs || 50)),
      imgsz: Math.trunc(Number(r.train.imgsz || 640)),
      batch: Math.trunc(Number(r.train.batch || 16)),
      workers: Math.trunc(Number(r.train.workers || 4)),
      device: String(r.train.device || '0'),
      optimizer: String(r.train.optimizer || 'AdamW'),
      lr0: Number(r.train.lr0 || 0.001),
      patience: Math.trunc(Number(r.train.patience || 50)),
      close_mosaic: Math.trunc(Number(r.train.close_mosaic || 10)),
      project: 'runs/train',
      name: 'exp',
    };

    const onnx = r.export?.onnx || {};
    const eng = r.export?.engine || {};
    const tfl = r.export?.tflite || {};

    const runSpec = {
      kind: isHybrid ? 'hybrid' : 'single',
      role: isHybrid ? null : key,
      dataset_names: isHybrid ? [] : Array.from(r.dataset?.confirmed || []),
      // Hybrid settings (only meaningful when kind=hybrid)
      hybrid: {
        enabled: isHybrid,
        output_dir: String((r.hybrid?.output_dir) || 'datasets/hybird'),
        split_ratio: Array.isArray(r.hybrid?.split_ratio) ? r.hybrid.split_ratio : [0.7,0.2,0.1],
        split_seed: Math.trunc(Number(r.hybrid?.split_seed || 42)),
      },
      train: runTrain,
      export_onnx: {
        enabled: !!(onnx.enabled || onnx.fp16),
        simplify: !!onnx.simplify,
        fp16: !!onnx.fp16,
      },
      export_engine: {
        enabled: !!(eng.enabled || eng.fp32 || eng.fp16 || eng.int8),
        fp32: !!eng.fp32,
        fp16: !!eng.fp16,
        int8: !!eng.int8,
        quant: {
          ptq: !!(eng.quant?.ptq),
          qat: !!(eng.quant?.qat),
        },
        calib: {
          num: Math.trunc(Number(eng.calib_num || 300)),
          seed: 42,
          split: 'val',
        },
      },
      export_tflite: {
        enabled: !!(tfl.enabled || tfl.fp32 || tfl.fp16 || tfl.int8),
        fp32: !!tfl.fp32,
        fp16: !!tfl.fp16,
        int8: !!tfl.int8,
        quant: {
          ptq: !!(tfl.quant?.ptq),
          qat: !!(tfl.quant?.qat),
        },
        calib: {
          num: Math.trunc(Number(tfl.calib_num || 300)),
          seed: 42,
          split: 'val',
        },
      },
      quant: {
        ptq: false,
        qat: false,
      },
      balance: {
        enabled: isHybrid ? !!(r.balance?.enabled) : false,
        target: String((r.balance?.target) || 'mean'),
        custom_type: String((r.balance?.custom_type) || 'multiplier'),
        custom_value: Number((r.balance?.custom_value) || 1.0),
      },
    };
    return runSpec;
  });

  return { mode, task, datasets, runs };
}

function buildSSHSpec() {
  return {
    host: strVal('#ssh_host'),
    port: Math.trunc(numVal('#ssh_port', 22)),
    username: strVal('#ssh_user'),
    password: strVal('#ssh_pass') || null,
    private_key: strVal('#ssh_key') || null,
    remote_dir: strVal('#ssh_dir', '~/yolo_web_builder_runs') || '~/yolo_web_builder_runs',
    python: strVal('#ssh_py', 'python3') || 'python3',
  };
}

// ---------------------- summary ----------------------

function renderSummary() {
  const sum = $('#summary');
  if (!sum) return;

  const spec = buildBundleSpec();

  const runLines = [];
  (spec.runs || []).forEach((r) => {
    const tag = (r.kind === 'hybrid') ? 'HYBRID' : (r.role || 'UNKNOWN');
    const key = `${(spec.task || 'task').trim()}_${tag}`;
    const dsInfo = (r.kind === 'hybrid')
      ? `datasets: ALL（${spec.datasets.length}）`
      : `datasets: ${(r.dataset_names || []).join(', ') || '(none)'}`;

    const ex = [];
    if (r.export_onnx?.enabled) ex.push(`ONNX${r.export_onnx.fp16 ? '(FP16)' : '(FP32)'}${r.export_onnx.simplify ? '+simplify' : ''}`);
    if (r.export_engine?.enabled) {
      const p = [];
      if (r.export_engine.fp32) p.push('FP32');
      if (r.export_engine.fp16) p.push('FP16');
      if (r.export_engine.int8) p.push('INT8');
      ex.push(`Engine:${p.join('/') || 'None'}`);
    }
    if (r.export_tflite?.enabled) {
      const p = [];
      if (r.export_tflite.fp32) p.push('FP32');
      if (r.export_tflite.fp16) p.push('FP16');
      if (r.export_tflite.int8) p.push('INT8');
      ex.push(`TFLite:${p.join('/') || 'None'}`);
    }

    const q = [];
    if (r.export_engine?.quant?.ptq) q.push('Engine PTQ');
    if (r.export_engine?.quant?.qat) q.push('Engine QAT(placeholder)');
    if (r.export_tflite?.quant?.ptq) q.push('TFLite PTQ');
    if (r.export_tflite?.quant?.qat) q.push('TFLite QAT(placeholder)');

    runLines.push([
      `<div class="item"><div class="k">Run</div><div class="v"><b>${escapeHtml(key)}</b></div></div>`,
      `<div class="item"><div class="k">Source</div><div class="v">${escapeHtml(dsInfo)}</div></div>`,
      `<div class="item"><div class="k">Model</div><div class="v">${escapeHtml((r.train.family==='yolo11'?'yolo11':'yolov8') + (r.train.size||'n') + '.pt')}</div></div>`,
      `<div class="item"><div class="k">Epoch/Batch/Img</div><div class="v">${escapeHtml(String(r.train.epochs))} / ${escapeHtml(String(r.train.batch))} / ${escapeHtml(String(r.train.imgsz))}</div></div>`,
      `<div class="item"><div class="k">Exports</div><div class="v">${escapeHtml(ex.join(' · ') || 'None')}</div></div>`,
      `<div class="item"><div class="k">Quant</div><div class="v">${escapeHtml(q.join(' · ') || 'None')}</div></div>`,
      (r.kind==='hybrid' ? `<div class="item"><div class="k">Balancing</div><div class="v">${escapeHtml(r.balance?.enabled ? (r.balance.target==='custom' ? `CUSTOM(${r.balance.custom_type}:${r.balance.custom_value})` : String(r.balance.target)) : 'OFF')}</div></div>` : '')
    ].join(''));
  });

  const items = [
    { k: 'Task', v: (spec.task || '').trim() || '-' },
    { k: 'Mode', v: spec.mode === 'ssh' ? 'SSH run' : 'Bundle only' },
    { k: 'Datasets', v: `${(spec.datasets || []).length} dataset(s)` },
    { k: 'Runs', v: `${(spec.runs || []).length} run(s)` },
  ];

  sum.innerHTML =
    items.map(it => `
      <div class="item">
        <div class="k">${it.k}</div>
        <div class="v">${escapeHtml(it.v)}</div>
      </div>
    `).join('') +
    (runLines.length ? `<div class="divider"></div>${runLines.join('<div class="divider"></div>')}` : '');
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function findDuplicates(arr) {
  const seen = new Set();
  const dup = new Set();
  (arr || []).forEach(x => {
    if (seen.has(x)) dup.add(x);
    else seen.add(x);
  });
  return Array.from(dup);
}


function formatApiErrorDetail(detail) {
  if (detail == null) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (item && typeof item === 'object') {
        const loc = Array.isArray(item.loc) ? item.loc.join(' -> ') : '';
        const msg = item.msg || JSON.stringify(item);
        return loc ? `${loc}: ${msg}` : String(msg);
      }
      return String(item);
    }).filter(Boolean).join('; ');
  }
  if (typeof detail === 'object') {
    try {
      return Object.entries(detail).map(([k, v]) => `${k}: ${formatApiErrorDetail(v)}`).join('; ');
    } catch {
      return JSON.stringify(detail);
    }
  }
  return String(detail);
}

function setViewMode(mode) {
  const home = $('#homeView');
  const wizard = $('#wizardView');
  const showHome = mode === 'home';
  if (home) {
    home.classList.toggle('hidden', !showHome);
    home.hidden = !showHome;
    home.setAttribute('aria-hidden', showHome ? 'false' : 'true');
  }
  if (wizard) {
    wizard.classList.toggle('hidden', showHome);
    wizard.hidden = showHome;
    wizard.setAttribute('aria-hidden', showHome ? 'true' : 'false');
  }
}

// ---------------------- submit ----------------------

function _timeoutSignal(timeoutMs){
  const t = Number.isFinite(timeoutMs) ? timeoutMs : 30000;
  // Prefer AbortSignal.timeout when available (newer browsers), otherwise fall back.
  if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
    return AbortSignal.timeout(t);
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), t);
  // attach cleanup hook
  ctl.signal._cleanup = () => clearTimeout(timer);
  return ctl.signal;
}

async function postJSON(url, data, opts = {}) {
  const signal = _timeoutSignal(opts.timeoutMs);
  let r;
  try {
    r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal,
    });
  } catch (e) {
    try { signal?._cleanup?.(); } catch {}
    if (e && (e.name === 'AbortError')) throw new Error('Request timeout');
    throw e;
  }
  try { signal?._cleanup?.(); } catch {}
  const text = await r.text();
  let j;
  try { j = JSON.parse(text); } catch {
    throw new Error(text || `HTTP ${r.status}`);
  }
  if (!r.ok) {
    const msg = formatApiErrorDetail(j.detail ?? j.message ?? text ?? `HTTP ${r.status}`) || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return j;
}

async function getJSON(url, opts = {}) {
  const signal = _timeoutSignal(opts.timeoutMs);
  let r;
  try {
    r = await fetch(url, { method: 'GET', signal });
  } catch (e) {
    try { signal?._cleanup?.(); } catch {}
    if (e && (e.name === 'AbortError')) throw new Error('Request timeout');
    throw e;
  }
  try { signal?._cleanup?.(); } catch {}
  const text = await r.text();
  let j;
  try { j = JSON.parse(text); } catch {
    throw new Error(text || `HTTP ${r.status}`);
  }
  if (!r.ok) {
    const msg = formatApiErrorDetail(j.detail ?? j.message ?? text ?? `HTTP ${r.status}`) || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return j;
}

function normalizeTerminalText(input){
  let s = String(input ?? "");
  // Remove ANSI escape/control sequences
  s = s.replace(/\x1B\[[0-?]*[ -\/]*[@-~]/g, "");
  // Normalize CRLF first
  s = s.replace(/\r\n/g, "\n");
  // For tqdm-like carriage-return updates, keep only the last frame per physical line
  s = s.split("\n").map(line => {
    if (!line.includes("\r")) return line;
    return line.split("\r").pop();
  }).join("\n");
  // Remove remaining bare CR and backspace controls
  s = s.replace(/\r/g, "");
  s = s.replace(/\x08+/g, "");
  s = s.replace(/\t/g, "  ");
  s = s.replace(/\n{4,}/g, "\n\n\n");
  return s;
}

let __outAutoScrollEnabled = true;
let __outScrollWired = false;

function __wireOutScrollBehavior(){
  if (__outScrollWired) return;
  const out = $('#out');
  if (!out) return;
  __outScrollWired = true;
  const nearBottom = () => (out.scrollHeight - out.scrollTop - out.clientHeight) <= 24;
  out.addEventListener('scroll', () => {
    // If user drags away from bottom, stop sticky autoscroll.
    __outAutoScrollEnabled = nearBottom();
  }, { passive: true });
}

function writeOut(msg) {
  const out = $('#out');
  if (!out) return;
  __wireOutScrollBehavior();
  const wasNearBottom = (out.scrollHeight - out.scrollTop - out.clientHeight) <= 24;
  out.textContent = normalizeTerminalText(msg);
  if (__outAutoScrollEnabled || wasNearBottom) {
    out.scrollTop = out.scrollHeight;
    __outAutoScrollEnabled = true;
  }
}

async function finalSubmit() {
  setError(6, '');
  writeOut('');

  if (isDirty) {
    const ok = confirm('此專案尚未儲存（Save）。\n\n仍要直接 Submit 嗎？（建議先按 Save 以便在首頁保留紀錄）');
    if (!ok) return;
  }

  // Validate step6 itself
  const msg = validateStep(6);
  if (msg) {
    setError(6, msg);
    return;
  }

  const need = firstUnconfirmedRequired();
  if (need) {
    toast(`請回到 Step ${need} 按下 Submit/Next 重新確認後再重訓/Submit。`, 'warn');
    showStep(need);
    return;
  }

  const spec = buildBundleSpec();
  renderSummary();

  const mode = spec.mode;
  if (mode === 'bundle') {
    writeOut('Building bundle...');
    try {
      const j = await postJSON('/api/build_bundle', spec);
      writeOut(`OK\njob_id: ${j.job_id}\nDownload: ${j.download_url || j.downloadUrl || `/download/${j.job_id}.zip`}`);
      // open download
      const url = j.download_url || j.downloadUrl || `/download/${j.job_id}.zip`;
      window.open(url, '_blank');
    } catch (e) {
      setError(6, `Build failed: ${e.message}`);
      writeOut(`ERROR\n${e.message}`);
    }
    return;
  }

  // ssh (async job)
  try {
    const ssh = buildSSHSpec();
    lastSSHPayload = {bundle: spec, ssh};
    await startSSHJob(spec, ssh);
    // Mark Step6 confirmed once a job is started
    markStepConfirmed(6);
  } catch (e) {
    setError(6, `SSH run failed: ${e.message}`);
    writeOut(`ERROR
${e.message}`);
  }
}



// ---------------------- Step6: async SSH job controls ----------------------
let activeJobId = null;
let jobPollTimer = null;
let jobPollIntervalMs = 2000;
let lastSSHPayload = null; // {bundle, ssh}

function setJobPollInterval(ms){
  const n = Number.isFinite(ms) ? ms : 2000;
  if (jobPollIntervalMs === n) return;
  jobPollIntervalMs = n;
  if (jobPollTimer) {
    clearInterval(jobPollTimer);
    jobPollTimer = setInterval(pollSSHJob, jobPollIntervalMs);
  }
}

function showJobControls(show){
  const card = $('#jobControlCard');
  if (!card) return;
  if (show) card.removeAttribute('hidden');
  else card.setAttribute('hidden','');
}

async function pollSSHJob(){
  if (!activeJobId) return;
  try{
    const st = await postJSON('/api/ssh_status', {job_id: activeJobId}, {timeoutMs: 8000});
    const status = $('#jobStatus');
    if (status) {
      let extra = '';
      if (st.oom_detected) extra = `\nNOTE: ${st.oom_message || 'CUDA out of memory'}`;
      const age = (typeof st.log_age_sec === 'number') ? st.log_age_sec : null;
      const ageLine = (age !== null) ? `\nlog_age: ${age}s` : '';
      const statLine = (st.stat ? `\nstat: ${st.stat}` : '');
      const etimeLine = (st.etime ? `\netime: ${st.etime}` : '');
      const exitLine = (typeof st.exit_code === 'number') ? `\nexit_code: ${st.exit_code}` : '';
      const stageLine = (st.stage ? `\nstage: ${st.stage}` : '');
      let hint = '';
      if (st.state === 'running' && age !== null && age >= 900) {
        hint = `\nHINT: log 超過 ${age}s 未更新，可能卡住；建議按下診斷（procs / results.csv）。`;
      }
      if ((st.state === 'finished' || st.state === 'failed') && !st.got_artifacts) {
        if (st.stage && st.stage !== 'pipeline_done') {
          hint += `\nHINT: 狀態已結束但 pipeline 可能沒進到訓練（stage=${st.stage}）。請按 log_end / stage / errors，確認是卡在 pip 或 run_remote.sh。`;
        } else {
          hint += `\nHINT: 狀態已結束但還沒有 artifacts.zip。建議先按 run.log / errors 看最後停在哪一步，再確認 runs/train 與 results.csv 是否存在。`;
        }
      }
      status.textContent = `state: ${st.state}${extra}${statLine}${etimeLine}${exitLine}${stageLine}${ageLine}${hint}
updated: ${nowClockString()}`;
    }
    if (typeof st.log_tail === 'string') {
      // keep output readable: show latest tail
      writeOut(st.log_tail || '');
    }
    const dlBtn = $('#downloadArtifactsBtn');
    if (dlBtn && st.artifacts_url) {
      dlBtn.hidden = false;
      dlBtn.onclick = () => window.open(st.artifacts_url, '_blank');
    }
    // Keep polling even after finished, but slow down.
    if (st.state === 'finished') setJobPollInterval(10000);
    else setJobPollInterval(2000);
  } catch(e){
    const status = $('#jobStatus');
    if (status) status.textContent = `status error: ${e.message}`;
  }
}

async function sshControl(action){
  if (!activeJobId) return;
  try {
    const r = await postJSON('/api/ssh_control', {job_id: activeJobId, action}, {timeoutMs: 12000});
    await pollSSHJob();
    return r;
  } catch (e) {
    // Avoid unhandled rejections from button click handlers.
    toast(`SSH ${action} failed: ${e.message}`, 'error');
    throw e;
  }
}

function wireJobControls(){
  $('#pauseBtn')?.addEventListener('click', async () => { try { await sshControl('pause'); } catch {} });
  $('#resumeBtn')?.addEventListener('click', async () => { try { await sshControl('resume'); } catch {} });
  $('#terminateBtn')?.addEventListener('click', async () => {
    if (!confirm('確定要終止此 Job 嗎？')) return;
    try {
      const r = await sshControl('terminate');
      if (r && r.state && r.state !== 'finished') {
        const force = confirm('已送出 SIGTERM，但程序仍存活。是否要強制終止（SIGKILL）？');
        if (force) await sshControl('terminate_force');
      }
    } catch (e) {
      // Timeout is the most common reason the UI feels "stuck" on terminate.
      const force = confirm(`終止請求失敗：${e.message}\n\n是否要改用強制終止（SIGKILL）？`);
      if (force) {
        try { await sshControl('terminate_force'); } catch {}
      }
    }
  });
  $('#cmdRunBtn')?.addEventListener('click', async () => {
    if (!activeJobId) return;
    await runSSHDiagnostic((($('#cmdInput')?.value || 'nvidia-smi').trim()) || 'nvidia-smi');
  });

  // Quick diagnostics (safe allowlist on backend)
  $('#diagGpuBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:gpu'); } catch {} });
  $('#diagProcsBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:procs'); } catch {} });
  $('#diagJobBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:job'); } catch {} });
  $('#diagRunsBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:runs'); } catch {} });
  $('#diagResultsBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:results'); } catch {} });
  $('#diagLogBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:log'); } catch {} });
  $('#diagLogEndBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:log_end'); } catch {} });
  $('#diagStageBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:stage'); } catch {} });
  $('#diagErrBtn')?.addEventListener('click', async () => { try { await runSSHDiagnostic('diag:errors'); } catch {} });
  $('#retrainBtn')?.addEventListener('click', async () => {
    const need = firstUnconfirmedRequired();
    if (need) {
      toast(`請先回到 Step ${need} 重新確認（Submit/Next）後才能重訓。`, 'warn');
      showStep(need);
      return;
    }
    if (activeJobId) {
      try { await sshControl('terminate'); } catch {}
    }
    // Rebuild spec from current UI and start a fresh job
    await finalSubmit();
  });
}

async function runSSHDiagnostic(cmd){
  if (!activeJobId) return;
  const out = $('#cmdOut');
  if (out) out.textContent = 'running...';
  const inp = $('#cmdInput');
  if (inp && cmd) inp.value = cmd;
  try {
    const r = await postJSON('/api/ssh_exec', {job_id: activeJobId, command: cmd}, {timeoutMs: 8000});
    const txt = normalizeTerminalText((r.stdout || r.stderr || '').trim());
    if (out) out.textContent = txt || `(exit ${r.exit_code ?? '?'})`;
  } catch (e) {
    if (out) out.textContent = `ERROR: ${e.message}`;
    throw e;
  }
}

async function startSSHJob(bundleSpec, sshSpec){
  writeOut('SSH job starting...');
  const payload = {bundle: bundleSpec, ssh: sshSpec};
  lastSSHPayload = payload;
  const j = await postJSON('/api/ssh_run', payload);
  activeJobId = j.job_id;
  showJobControls(true);
  const dlBtn = $('#downloadArtifactsBtn');
  if (dlBtn) dlBtn.hidden = true;
  const status = $('#jobStatus');
  if (status) status.textContent = `job_id: ${activeJobId}
state: starting`;
  if (jobPollTimer) clearInterval(jobPollTimer);
  jobPollTimer = setInterval(pollSSHJob, 2000);
  await pollSSHJob();
}

// ---------------------- wiring ----------------------

function unlockStep(step) {
  maxUnlockedStep = Math.max(maxUnlockedStep, step);
  updateProgressUI();
}

function handleNext() {
  const msg = validateStep(currentStep);
  if (msg) {
    setError(currentStep, msg);
    return;
  }

  // mark this step confirmed
  markStepConfirmed(currentStep);

  // advance using STEPS array (non-contiguous steps: 1,2,4,6)
  const i = STEPS.indexOf(currentStep);
  if (i < 0) {
    showStep(STEPS[0] || 1);
    return;
  }
  const nextStep = STEPS[Math.min(STEPS.length - 1, i + 1)];
  unlockStep(nextStep);
  showStep(nextStep);
}

function handleBack() {
  const i = STEPS.indexOf(currentStep);
  const prevStep = (i > 0) ? STEPS[i - 1] : (STEPS[0] || 1);
  showStep(prevStep);
}

function wireWizardButtons() {
  $$('[data-action="next"]').forEach(btn => btn.addEventListener('click', handleNext));
  $$('[data-action="back"]').forEach(btn => btn.addEventListener('click', handleBack));
  $$('[data-action="final"]').forEach(btn => btn.addEventListener('click', finalSubmit));
}


function wireHelpButtons() {
  // Toggle per-step help panels (hidden by default)
  $$('.help-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const step = btn.getAttribute('data-help');
      const panel = document.getElementById(`help-step-${step}`);
      if (!panel) return;
      if (panel.hasAttribute('hidden')) panel.removeAttribute('hidden');
      else panel.setAttribute('hidden', '');
    });
  });
}
function wireStepperButtons() {
  $$('.progress-step').forEach(btn => {
    btn.addEventListener('click', () => {
      const s = parseInt(btn.getAttribute('data-step'), 10);
      if (!Number.isFinite(s)) return;
      if (s <= maxUnlockedStep) {
        showStep(s);
      }
    });
  });
}

function wireDynamicControls() {
  // mode selection shows ssh box
  $$('input[name="mode"]').forEach(n => n.addEventListener('change', () => {
    refreshDynamicUI();
    setError(1, '');
  }));

  // hybrid toggle
  $('#hybrid_en')?.addEventListener('change', () => {
    refreshDynamicUI();
    setError(3, '');
  });

  // train family/size
  $('#m_family')?.addEventListener('change', () => {
    refreshDynamicUI();
    setError(4, '');
  });
  $('#m_size')?.addEventListener('change', () => setError(4, ''));


  // balance controls
  ['#bal_en','#bal_target','#bal_custom_type','#bal_custom_value'].forEach(sel => {
    $(sel)?.addEventListener('change', () => {
      refreshDynamicUI();
      setBalErr('');
      setError(4,'');
      renderSummary();
    });
    $(sel)?.addEventListener('input', () => {
      refreshDynamicUI();
      setBalErr('');
      setError(4,'');
    });
  });
  // export toggles
  ['#onnx_en', '#eng_en', '#tfl_en', '#eng_int8', '#tfl_int8'].forEach(sel => {
    $(sel)?.addEventListener('change', () => {
      refreshDynamicUI();
      setError(5, '');
      setError(6, '');
    });
  });

  $('#qat')?.addEventListener('change', () => {
    renderSummary();
  });

  // add dataset
  $('#addDataset')?.addEventListener('click', () => { addDataset(); renderMergeSections(); renderSummary(); });

  
// Step 2: Roboflow download code parse (Jupyter / Terminal / Raw URL)
initRfQuickParse();
}

// Init after DOM is ready (fix: clock & buttons not responding when script runs too early)

// ---------------------- snapshot / restore ----------------------
function syncRunStateFromDOM() {
  const root = $('#runBars');
  if (!root) return;

  const setSilent = (key, path, value) => {
    const obj = runState[key];
    if (!obj) return;
    const parts = (path || '').split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (!cur[p] || typeof cur[p] !== 'object') cur[p] = {};
      cur = cur[p];
    }
    cur[parts[parts.length - 1]] = value;
  };

  // sync visible run detail fields
  root.querySelectorAll('[data-run-field]').forEach((el) => {
    const key = el.getAttribute('data-run');
    const path = el.getAttribute('data-run-field');
    if (!key || !path || !runState[key]) return;
    if (path === 'hybrid.split_ratio_str') return;

    let v;
    if (el.type === 'checkbox') v = !!el.checked;
    else if (el.type === 'number') {
      const raw = String(el.value ?? '').trim();
      if (!raw) return; // don't clobber with 0
      const n = Number(raw);
      if (!Number.isFinite(n)) return;
      v = n;
    } else {
      v = el.value;
    }
    setSilent(key, path, v);
  });

  // hybrid split ratio string -> array
  root.querySelectorAll('[data-run-field="hybrid.split_ratio_str"]').forEach((el) => {
    const key = el.getAttribute('data-run');
    if (!key || !runState[key]) return;
    const raw = String(el.value || '').trim();
    const parts = raw.split(',').map(x => Number(String(x).trim())).filter(x => Number.isFinite(x));
    if (parts.length === 3) setSilent(key, 'hybrid.split_ratio', parts);
  });
}

function snapshotUI() {
  // keep Step4 dynamic fields before snapshot
  try { syncRunStateFromDOM(); } catch {}
  // store scalar fields by id (excluding dataset cards, which are stored separately)
  const fields = {};
  $$('input[id], select[id], textarea[id]').forEach(el => {
    const id = el.id;
    if (!id) return;
    // dataset card fields: ds_*_<idx> are handled by datasets array
    if (/^ds_/.test(id)) return;
    if (el.type === 'checkbox') fields[id] = { t: 'cb', v: !!el.checked };
    else if (el.type === 'radio') fields[id] = { t: 'rb', v: !!el.checked };
    else fields[id] = { t: 'v', v: el.value };
  });

  const ds = readDatasetsFromUI();
  const mm = {};
  Object.keys(manualMergeState || {}).forEach(role => {
    const st = manualMergeState[role] || {};
    mm[role] = {
      checked: Array.from(st.checked || []),
      confirmed: Array.from(st.confirmed || []),
      mode: st.mode || null,
    };
  });

  return {
    fields,
    datasets: ds,
    manualMergeState: mm,
    runState: JSON.parse(JSON.stringify(runState)),
    maxUnlockedStep,
    currentStep,
  };
}

function applyUISnapshot(snap) {
  if (!snap) return;
  isApplyingSnapshot = true;
  try {
    // restore dataset cards first
    const ds = snap.datasets || [];
    datasetsState = Array(ds.length || 1).fill(0);
    renderDatasets();
    // fill dataset fields
    ds.forEach((d, idx) => {
      const root = $(`#dataset_${idx}`);
      if (!root) return;
      root.querySelector('[data-k="name"]')?.setAttribute('value', d.name || '');
      // Note: inputs created with value attribute; set value via property:
      const nameEl = root.querySelector('[data-k="name"]');
      if (nameEl) nameEl.value = d.name || '';
      const roleEl = root.querySelector('[data-k="role"]');
      if (roleEl) roleEl.value = d.role || '';
      const apiEl = root.querySelector('[data-k="api_key"]');
      if (apiEl) apiEl.value = d.api_key || '';
      const wsEl = root.querySelector('[data-k="workspace"]');
      if (wsEl) wsEl.value = d.workspace || '';
      const prEl = root.querySelector('[data-k="project"]');
      if (prEl) prEl.value = d.project || '';
      const verEl = root.querySelector('[data-k="version"]');
      if (verEl) verEl.value = String(d.version ?? '');
      const fmtEl = root.querySelector('[data-k="format"]');
      if (fmtEl) fmtEl.value = (d.format || 'yolov8');
      const dlEl = root.querySelector('[data-k="download_link"]');
      if (dlEl) dlEl.value = d.download_link || '';
    });

    // restore scalar fields
    const fields = snap.fields || {};
    Object.keys(fields).forEach(id => {
      const meta = fields[id];
      const el = document.getElementById(id);
      if (!el) return;
      if (meta.t === 'cb') el.checked = !!meta.v;
      else el.value = meta.v ?? '';
    });
    // restore Step4 run state (mutate; runState is const)
    Object.keys(runState).forEach(k => { delete runState[k]; });
    Object.assign(runState, (snap.runState || {}));
    Object.values(runState).forEach(normalizeRunQuantState);

    maxUnlockedStep = snap.maxUnlockedStep || 1;
    currentStep = snap.currentStep || 1;

    refreshDynamicUI();
    updateProgressUI();
    showStep(currentStep);
    renderSummary();
  } finally {
    isApplyingSnapshot = false;
  }
}

// ---------------------- home / projects ----------------------
function loadProjectIndex() {
  try {
    const raw = localStorage.getItem(LS_INDEX_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}
function saveProjectIndex(arr) {
  localStorage.setItem(LS_INDEX_KEY, JSON.stringify(arr || []));
}
function loadProject(id) {
  try {
    const raw = localStorage.getItem(LS_PROJECT_KEY_PREFIX + id);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
function saveProject(id, payload) {
  localStorage.setItem(LS_PROJECT_KEY_PREFIX + id, JSON.stringify(payload));
}
function deleteProject(id) {
  localStorage.removeItem(LS_PROJECT_KEY_PREFIX + id);
}

// ---------------------- Projects persistence (server-side) ----------------------

async function loadServerProjectIndex(){
  try {
    const res = await getJSON('/api/project_list', {timeoutMs: 8000});
    return (res && Array.isArray(res.items)) ? res.items : [];
  } catch {
    return null;
  }
}

async function loadServerProject(id){
  try {
    const res = await getJSON('/api/project_get/' + encodeURIComponent(id), {timeoutMs: 8000});
    return (res && res.snapshot) ? res : null;
  } catch {
    return null;
  }
}

async function saveServerProject(id, task, snapshot, createdAt, updatedAt){
  return await postJSON('/api/project_save', {id, task, snapshot, createdAt, updatedAt}, {timeoutMs: 12000});
}

async function deleteServerProject(id){
  await postJSON('/api/project_delete', {id}, {timeoutMs: 12000});
  return true;
}

let homeProjectIndexCache = [];
let serverSaveTimer = null;
let serverSaveInFlight = false;
let lastServerSaveWarnAt = 0;

function sortProjectIndex(items) {
  return [...(items || [])].sort((a,b)=> (b.updatedAt||'').localeCompare(a.updatedAt||''));
}

function setHomeProjectIndexCache(items) {
  homeProjectIndexCache = sortProjectIndex(items || []);
  saveProjectIndex(homeProjectIndexCache);
}

function upsertLocalProjectCache(id, payload, meta = {}) {
  if (!id) return;
  saveProject(id, payload);
  const idx = loadProjectIndex();
  const pos = idx.findIndex(x => x.id === id);
  const merged = {
    ...(pos >= 0 ? idx[pos] : {id}),
    ...meta,
    id,
  };
  if (pos >= 0) idx[pos] = merged;
  else idx.push(merged);
  setHomeProjectIndexCache(idx);
}

function removeLocalProjectCache(id) {
  if (!id) return;
  deleteProject(id);
  const idx = loadProjectIndex().filter(x => x.id !== id);
  setHomeProjectIndexCache(idx);
}

async function getAuthoritativeProjectIndex(){
  const serverIdx = await loadServerProjectIndex();
  if (serverIdx !== null) {
    setHomeProjectIndexCache(serverIdx);
    return homeProjectIndexCache;
  }
  const localIdx = sortProjectIndex(loadProjectIndex());
  homeProjectIndexCache = localIdx;
  return localIdx;
}

async function refreshProjectIndexFromServer(){
  await getAuthoritativeProjectIndex();
}

function setDirty(v) {
  isDirty = !!v;
  const warn = $('#saveWarn');
  if (warn) warn.hidden = !isDirty;
}

async function showHome() {
  setViewMode('home');
  const items = await getAuthoritativeProjectIndex();
  renderHome(items);
  updateStageNotes();
}


function showWizard() {
  setViewMode('wizard');
  updateStageNotes();
}


function renderHome(items = null) {
  // update static strings
  const lang = getLang();
  $('#langZh')?.classList.toggle('active', lang === 'zh');
  $('#langEn')?.classList.toggle('active', lang === 'en');
  const titleNode = $('#homeTitle');
  if (titleNode) titleNode.textContent = t('projects');
  const newBtn = $('#newTaskBtn');
  if (newBtn) newBtn.textContent = t('newTask');
  const ruleNode = $('#homeRule');
  if (ruleNode) ruleNode.innerHTML = t('rule');
  const emptyText = $('#homeEmptyText');
  if (emptyText) emptyText.textContent = t('empty');

  const list = $('#projectList');
  const empty = $('#homeEmpty');
  if (!list || !empty) return;
  const idx = sortProjectIndex(items || homeProjectIndexCache || loadProjectIndex());
  homeProjectIndexCache = idx;
  list.innerHTML = '';
  empty.style.display = idx.length ? 'none' : 'block';

  idx.forEach(item => {
    const task = item.task || '(Untitled)';
    const card = document.createElement('div');
    card.className = 'proj-card';
    card.setAttribute('data-id', item.id);
    card.innerHTML = `
      <div class="proj-cover" aria-hidden="true">
        <div class="proj-cover-tag">YOLO</div>
      </div>
      <button class="proj-kebab" type="button" aria-label="menu">⋯</button>
      <div class="proj-body">
        <div class="proj-title">${escapeHtml(task)}</div>
        <div class="proj-meta">
          <div>${escapeHtml(t('created'))}: ${escapeHtml(fmtTs(item.createdAt))}</div>
          <div>${escapeHtml(t('updated'))}: ${escapeHtml(fmtTs(item.updatedAt))}</div>
        </div>
      </div>
      <div class="proj-menu hidden" role="menu">
        <button type="button" data-menu="open">${escapeHtml(t('open'))}</button>
        <button type="button" data-menu="dup">${escapeHtml(t('duplicate'))}</button>
        <button type="button" data-menu="rm">${escapeHtml(t('remove'))}</button>
      </div>
    `;
    list.appendChild(card);
  });
}

function startNewTask() {
  currentProjectId = null;
  lastSavedProjectFile = "";
  const draft = loadDraftSnapshot();
  if (draft) {
    applyUISnapshot(draft);
  } else {
    applyUISnapshot(initialSnapshot);
  }
  setDirty(true); // new task is unsaved (draft-only)
  showWizard();
  showStep(1);
}

async function openProjectById(id) {
  let payload = null;
  const remote = await loadServerProject(id);
  if (remote) {
    payload = { snapshot: remote.snapshot };
    upsertLocalProjectCache(id, payload, {
      task: remote.task || '(Untitled)',
      createdAt: remote.createdAt || '',
      updatedAt: remote.updatedAt || '',
    });
  } else {
    payload = loadProject(id);
  }
  if (!payload) {
    toast('無法讀取專案，請確認伺服器同步資料是否存在。', 'error');
    return;
  }
  currentProjectId = id;
  lastSavedProjectFile = `projects/${id}.json`;
  applyUISnapshot(payload.snapshot);
  clearDraftSnapshot();
  setDirty(false);
  showWizard();
}

async function removeProjectById(id) {
  try {
    await deleteServerProject(id);
  } catch (e) {
    toast(`刪除失敗：${e.message}`, 'error');
    return;
  }
  removeLocalProjectCache(id);
  await showHome();
}



async function duplicateProjectById(id) {
  let payload = loadProject(id);
  if (!payload) {
    const remote = await loadServerProject(id);
    if (remote) payload = { snapshot: remote.snapshot };
  }
  if (!payload) {
    toast('找不到可複製的專案內容。', 'error');
    return;
  }
  const snap = structuredClone ? structuredClone(payload.snapshot || {}) : JSON.parse(JSON.stringify(payload.snapshot || {}));
  const fields = snap.fields || {};
  const task = (fields['task_name']?.v || '').trim();
  const base = task ? `${task}_Duplicate` : 'Task_Duplicate';
  const index = await getAuthoritativeProjectIndex();
  const dupTask = makeUniqueTask(base, index);
  fields['task_name'] = { t: 'v', v: dupTask };
  snap.fields = fields;
  snap.currentStep = 1;
  snap.maxUnlockedStep = Math.max(1, snap.maxUnlockedStep || 1);

  const newId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  const now = nowIso();
  try {
    await saveServerProject(newId, dupTask, snap, now, now);
  } catch (e) {
    toast(`複製失敗：${e.message}`, 'error');
    return;
  }
  upsertLocalProjectCache(newId, { snapshot: snap }, { id: newId, task: dupTask, createdAt: now, updatedAt: now });
  await showHome();
}

async function saveCurrentProject({navigateHome = false, silent = false} = {}) {
  const task = (strVal('#task_name') || '').trim();
  if (!task) {
    alert('Task 必填。');
    showStep(1);
    return false;
  }

  const index = await getAuthoritativeProjectIndex();
  const exists = index.find(x => x.task === task && x.id !== currentProjectId);
  if (exists) {
    alert('Task 需唯一（已存在相同 Task）。請修改 Task 後再 Save。');
    showStep(1);
    return false;
  }

  const snap = snapshotUI();
  const now = nowIso();
  if (!currentProjectId) currentProjectId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  const currentMeta = index.find(x => x.id === currentProjectId);
  const createdAt = currentMeta?.createdAt || now;

  const saveResp = await saveServerProject(currentProjectId, task, snap, createdAt, now);
  upsertLocalProjectCache(currentProjectId, { snapshot: snap }, {
    id: currentProjectId,
    task,
    createdAt,
    updatedAt: now,
  });
  clearDraftSnapshot();
  setDirty(false);
  lastSavedProjectFile = saveResp?.project_file || '';
  await refreshProjectIndexFromServer();
  const prevScrollY = window.scrollY;
  showWizard();
  updateProgressUI();
  showStep(currentStep, {scroll: false});
  renderSummary();
  window.scrollTo({ top: prevScrollY, behavior: 'auto' });

  if (!silent) {
    const loc = lastSavedProjectFile
      ? (getLang() === 'en' ? ` (backend/jobs/${lastSavedProjectFile})` : `（backend/jobs/${lastSavedProjectFile}）`)
      : '';
    toast(t('project_saved_toast', { task, loc }), 'ok');
  }

  if (navigateHome) {
    await showHome();
  }
  return true;
}

function wireHomeButtons() {
  $('#newTaskBtn')?.addEventListener('click', startNewTask);
  $('#homeBtn')?.addEventListener('click', () => { showHome().catch(() => {}); });
  $('#saveBtn')?.addEventListener('click', () => {
    saveCurrentProject({ navigateHome: false }).catch((e) => toast(`Save failed: ${e.message}`, 'error'));
  });

  // language toggle (Home)
  $('#langZh')?.addEventListener('click', () => setLang('zh'));
  $('#langEn')?.addEventListener('click', () => setLang('en'));

  // project list interactions (delegated)
  const list = $('#projectList');
  if (list) {
    list.addEventListener('click', (e) => {
      const target = e.target;
      const card = target.closest ? target.closest('.proj-card') : null;
      if (!card) return;
      const id = card.getAttribute('data-id');

      // menu toggle
      if (target.closest && target.closest('.proj-kebab')) {
        e.stopPropagation();
        // close other menus
        $$('.proj-menu').forEach(m => { if (m.closest('.proj-card') !== card) m.classList.add('hidden'); });
        const menu = card.querySelector('.proj-menu');
        if (menu) menu.classList.toggle('hidden');
        return;
      }

      // menu action
      const menuBtn = target.closest ? target.closest('[data-menu]') : null;
      if (menuBtn) {
        e.stopPropagation();
        const action = menuBtn.getAttribute('data-menu');
        card.querySelector('.proj-menu')?.classList.add('hidden');
        if (action === 'open') openProjectById(id).catch(() => {});
        else if (action === 'dup') duplicateProjectById(id).catch(() => {});
        else if (action === 'rm') removeProjectById(id).catch(() => {});
        return;
      }

      // default: open
      openProjectById(id).catch(() => {});
    });
  }

  // click outside closes menus (register once)
  document.addEventListener('click', (e) => {
    const t = e.target;
    if (t.closest && t.closest('.proj-card')) return;
    $$('.proj-menu').forEach(m => m.classList.add('hidden'));
  });



  // mark dirty on any change inside wizardView
  const markDirtyAndInvalidate = (ev) => {
    if (isApplyingSnapshot) return;
    setDirty(true);
    scheduleProjectStateSync();
    const sec = ev?.target?.closest ? ev.target.closest('.step') : null;
    if (!sec) return;
    const s = parseInt(sec.getAttribute('data-step') || '', 10);
    if (Number.isFinite(s)) invalidateFromStep(s);
  };
  $('#wizardView')?.addEventListener('input', markDirtyAndInvalidate);
  $('#wizardView')?.addEventListener('change', markDirtyAndInvalidate);
}

window.addEventListener('DOMContentLoaded', () => {
  startClock();
  wireEyeButtons();
  renderDatasets();
  wireWizardButtons();
  wireStepperButtons();
  wireHelpButtons();
  wireDynamicControls();
  wireHomeButtons();
  wireJobControls();
  refreshDynamicUI();
  renderMergeSections();
  updateProgressUI();
  showStep(1);
  initialSnapshot = snapshotUI();
  applyLangToDOM();
  showHome().catch(() => {});
});

// ---------------------- project persistence (Task list) ----------------------
const LS_INDEX_KEY = 'ywbv4_projects_index_v1';
const LS_PROJECT_KEY_PREFIX = 'ywbv4_project_v1:'; // + id
// Draft should NOT survive closing the browser/tab.
// Use sessionStorage so unsaved content is cleared on reopen.
const LS_DRAFT_KEY = 'ywbv4_draft_v1';

let currentProjectId = null;
let initialSnapshot = null;
let isApplyingSnapshot = false;
let isDirty = false;
let lastSavedProjectFile = "";

// Manual merge UI state is defined near the top of this file.

// draft autosave (debounced)
let draftTimer = null;
function saveDraftSnapshot() {
  try {
    const snap = snapshotUI();
    sessionStorage.setItem(LS_DRAFT_KEY, JSON.stringify({
      ts: nowIso(),
      snapshot: snap,
    }));
  } catch {
    // ignore
  }
}
function scheduleDraftSave() {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    draftTimer = null;
    saveDraftSnapshot();
  }, 350);
}

async function persistCurrentProjectToServer({silent = false} = {}) {
  if (serverSaveInFlight || !currentProjectId || isApplyingSnapshot) return false;
  const task = (strVal('#task_name') || '').trim();
  if (!task) return false;

  const index = await getAuthoritativeProjectIndex();
  const exists = index.find(x => x.task === task && x.id !== currentProjectId);
  if (exists) return false;

  const snap = snapshotUI();
  const now = nowIso();
  const currentMeta = index.find(x => x.id === currentProjectId);
  const createdAt = currentMeta?.createdAt || now;

  serverSaveInFlight = true;
  try {
    const saveResp = await saveServerProject(currentProjectId, task, snap, createdAt, now);
    upsertLocalProjectCache(currentProjectId, { snapshot: snap }, {
      id: currentProjectId,
      task,
      createdAt,
      updatedAt: now,
    });
    clearDraftSnapshot();
    setDirty(false);
    lastSavedProjectFile = saveResp?.project_file || '';
    return true;
  } catch (e) {
    if (!silent || (Date.now() - lastServerSaveWarnAt > 3000)) {
      toast(`同步失敗：${e.message}`, 'warn');
      lastServerSaveWarnAt = Date.now();
    }
    return false;
  } finally {
    serverSaveInFlight = false;
  }
}

function scheduleServerAutosave() {
  if (serverSaveTimer) clearTimeout(serverSaveTimer);
  serverSaveTimer = setTimeout(() => {
    serverSaveTimer = null;
    persistCurrentProjectToServer({silent: true}).catch(() => {});
  }, 1200);
}

function scheduleProjectStateSync() {
  if (currentProjectId) scheduleServerAutosave();
  else scheduleDraftSave();
}

function loadDraftSnapshot() {
  try {
    const raw = sessionStorage.getItem(LS_DRAFT_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    return obj?.snapshot || null;
  } catch {
    return null;
  }
}
function clearDraftSnapshot() {
  try { sessionStorage.removeItem(LS_DRAFT_KEY); } catch {}
}

window.addEventListener('storage', (e) => {
  if (!e.key) return;
  const hitProjectIndex = e.key === LS_INDEX_KEY;
  const hitProjectItem = e.key.startsWith(LS_PROJECT_KEY_PREFIX);
  if (hitProjectIndex || hitProjectItem) {
    if (!$('#homeView')?.classList.contains('hidden')) {
      showHome().catch(() => {});
    }
  }
});

function nowIso() { return new Date().toISOString(); }

function makeUniqueTask(base, indexArr) {
  const existing = new Set((indexArr||[]).map(x => (x.task||'').trim()).filter(Boolean));
  if (!existing.has(base)) return base;
  let n = 2;
  while (existing.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

function fmtTs(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch { return iso; }
}

// ---------------------- i18n (Site-wide) ----------------------
const LANG_KEY = 'ywbv4_lang_v1';

const I18N = {
  zh: {
    // top / layout
    pageTitle: 'YOLO Web Builder v4 · Roboflow Datasets',
    brandTitle: 'YOLO Web Builder v4 · Roboflow Datasets',
    brandSub: 'Detection only · Wizard 模式 · 只執行勾選項目',
    clockLabel: '本機時間',
    homeBtn: '首頁',
    langZh: '中文',
    langEn: 'English',

    heroTitle: '建立可重現的 YOLO 訓練/匯出流程',
    heroDesc: '依序完成 4 個步驟。每一步按 <b>Submit</b> 才會解鎖下一步；按 <b>Back</b> 可回到前一步。',
    notesTitle: '重要提醒（依目前步驟顯示）',

    stepModeLabel: 'Mode',
    stepDatasetLabel: 'Dataset',
    stepTrainExportLabel: 'Train&Export',
    stepReviewLabel: 'Review',
    progressHintInit: '完成 Step 1 後會解鎖後續步驟。',
    progressHint: '目前在 Step {cur} / {total}（已解鎖到 Step {unlocked}）',

    step1Title: 'Step 1 · Mode',
    step2Title: 'Step 2 · Dataset(s)（Roboflow）',
    step4Title: 'Step 4 · Train&Export',
    step6Title: 'Step 6 · Review & Run',

    // Home
    projects: '專案',
    newTask: '新建專案',
    rule: '規則：專案需按下 <b>Save</b> 才會被保存並出現在此列表。',
    empty: '試著做點什麼！',
    homeNote_1: '首頁是專案列表：點「新建專案」建立新的 Task；已保存的專案會顯示在列表中。',
    homeNote_2: '專案內容只有按下 Save 才會保存；未保存離開可能會遺失變更。',
    homeNote_3: '可用「開啟 / 複製 / 刪除」管理專案；複製會以新 ID 建立一份設定。',
    created: '建立',
    updated: '更新',
    open: '開啟',
    duplicate: '複製',
    remove: '刪除',

    // Step notes
    note1_1: '選擇執行模式：只產生 bundle，或由網站 SSH 到你的設備執行。',
    note1_2: '若使用 SSH，建議使用金鑰（key）而非密碼，並使用非 root 的低權限帳號。',
    note2_1: '資料集來源目前僅支援 Roboflow：需要 API key、workspace slug、project slug、version。',
    note2_2: 'Format 建議選 YOLO8/YOLO11；多數情境可用 YOLO8 格式下載並用於 YOLO11 訓練。',
    note2_3: '注意：按 +Add dataset 時系統會保留你已填寫的內容，不會復原。',
    note4_1: '本步驟（Train&Export）以 Target bar 呈現：勾選後可展開細項，並需按 Save 保存該 Target。',
    note4_2: '可同時勾選多個 Target。每個被勾選的 Class name Target 會各自訓練 1 個單一類別模型；若同時勾選 Hybrid，則會再額外訓練 1 個整合所有 Class name 群組資料集的多類別模型。',
    note4_3: 'Hybrid Target 才會出現 Data balancing（SMOTE-like oversampling），且只作用在 train。',
    note6_1: '請先在 Summary 確認所有選項。',
    note6_2: 'Submit 後會依模式產生 bundle 或透過 SSH 執行，並提供 logs/artifacts 下載。',

    // Dataset card
    datasetTitle: 'Dataset #{n}（Roboflow）',
    removeBtn: 'Remove',
    dsName: 'Name',
    dsNamePh: 'dataset_1',
    dsApiKey: 'API key',
    dsApiKeyPh: 'roboflow_api_key',
    toggleApiKey: 'toggle api key',
    dsWorkspace: 'Workspace (slug)',
    dsWorkspacePh: 'workspace-slug',
    dsProject: 'Project (slug)',
    dsProjectPh: 'project-slug',
    dsVersion: 'Version',
    dsFormat: 'Format',
    dsClassName: 'Class name（此 dataset 對應的類別名稱）',
    dsClassNamePh: '例如：cat / helmet / plate',
    dsHint: 'Name 只允許 a-z/A-Z/0-9/_/-；Hybrid 合併時會把每個 dataset 映射成你填的類別名稱。',
    // buttons / misc
    helpBtn: 'HELP?',
    btnBack: '返回',
    btnSubmit: '下一步',
    jobCtrlTitle: '訓練控制',
    btnPause: '暫停',
    btnResume: '繼續',
    btnRetrain: '重訓',
    btnTerminate: '終止',
    btnDownloadArtifacts: '下載專案產出.zip',
    cmdPlaceholder: 'nvidia-smi',
    btnRunCmd: '執行',
    btnSave: '保存',

    // Step 1 form (static labels)
    s1_rule: '規則：請先填寫 Task（唯一）並選擇運行模式。若選 SSH，必須填寫 SSH 連線資訊。',
    s1_task_label: 'Task（唯一）',
    s1_task_ph: '例如：helmet_v1 / face_aug / plate_y11',
    s1_task_hint: 'Task 用於保存/區分每次專案紀錄；需唯一。按下 Save 後才會被保存。',
    s1_mode_bundle: '只產生 bundle（下載）',
    s1_mode_ssh: '由網站 SSH 到你的設備執行',
    s1_ssh_title: 'SSH 設定（選 SSH 才需要）',
    s1_ssh_host: 'Host（主機）',
    s1_ssh_host_ph: '1.2.3.4 或 hostname',
    s1_ssh_port: 'Port',
    s1_ssh_user: 'User',
    s1_ssh_user_ph: 'ubuntu',
    s1_ssh_dir: 'Remote dir（遠端目錄）',
    s1_ssh_py: 'Python executable',
    s1_ssh_py_ph: 'python3 or /home/.../env/bin/python',
    s1_ssh_py_hint: 'Python must be an <strong>executable</strong>, e.g. <code>python3</code> or <code>/home/.../miniconda3/envs/ENV/bin/python</code>. For conda/venv, the interpreter is usually <code>$ENV/bin/python</code>.',
    s1_ssh_pass: 'Password（密碼）',
    s1_ssh_pass_ph: '（可留空，用 key）',
    s1_ssh_key_label: 'Private key（PEM，選填）',
    s1_ssh_key_ph: '-----BEGIN OPENSSH PRIVATE KEY-----',
    s1_ssh_hint: '建議使用 SSH key，並使用權限受限的帳號（避免 root）。',
    help1_html: `<ul>
      <li><b>只產生 bundle</b>：只產生可執行的 bundle zip（下載後你自行在任意機器執行）。</li>
      <li><b>SSH 執行</b>：由本網站透過 SSH 連到你的設備執行（需要填 Host/User/認證）。</li>
      <li><b>Host</b>：目標機器（建議填 IP 或可解析的主機名）。</li>
      <li><b>Remote dir</b>：遠端放置 bundle 與輸出檔的目錄（建議用絕對路徑）。</li>
      <li><b>Python</b>：遠端執行用的 python 指令或完整路徑（例如 conda env 的 python）。</li>
    </ul>`,

    // Step 2 quick fill (static area)
    s2_rule: '規則：資料集目前僅支援 Roboflow。至少需要 1 個 dataset（API key / workspace / project / version）。若要 Hybrid，請新增 ≥2 並為每個 dataset 填寫「類別名稱」。',
    s2_quick_title: '快速填入（貼 Download Code / CLI / Raw URL，自動解析）',
    s2_tab_jupyter: 'Jupyter',
    s2_tab_terminal: 'Terminal',
    s2_tab_rawurl: 'Raw URL',
    s2_panel_jupyter_label: 'Jupyter / Python 下載程式碼',
    s2_panel_jupyter_ph: '貼 Roboflow『Jupyter』下載程式碼（Python snippet）',
    s2_panel_terminal_label: 'Terminal 下載程式碼',
    s2_panel_terminal_ph: '貼 Roboflow『Terminal』下載程式碼（例如 roboflow CLI 或 curl 指令）',
    s2_panel_rawurl_label: 'Raw URL',
    s2_panel_rawurl_ph: '貼 Raw URL（REST API）或 Universe URL（例如 https://universe.roboflow.com/<workspace>/<project>/<version> ）',
    s2_hint_jupyter: '支援：<code>Roboflow(api_key="..."...)</code>、<code>.download("yolov8")</code>。',
    s2_hint_terminal: '支援：<code>roboflow download -f &lt;format&gt;</code> 或 <code>curl "https://api.roboflow.com/..."</code>。',
    s2_hint_rawurl: '支援：REST API URL（含 <code>api_key</code>）或 <code>&lt;workspace&gt;/&lt;project&gt;/&lt;version&gt;</code>（格式不足時不會覆蓋 Dataset 的 Format）。',
    s2_apply_to: '套用到 Dataset #',
    s2_parse_hint: '三種方式擇一：選好語法類型後貼上，再按 Parse。包含 API key 時，會自動遮罩避免明文留在畫面。',
    s2_parse_btn: 'Parse（解析）',
    s2_add_ds_btn: '+ 新增 dataset',
    help2_html: `<ul>
      <li><b>API key</b>：Roboflow 的 API key（<u>無法從 URL 自動推算</u>），請自行貼上。</li>
      <li><b>Workspace (slug)</b>：Roboflow Universe/Workspace 的識別字（URL 第一段）。</li>
      <li><b>Project (slug)</b>：資料集專案識別字（URL 第二段）。</li>
      <li><b>Version</b>：資料集版本號（URL 內的 <code>/dataset/&lt;version&gt;</code>）。</li>
      <li><b>Format</b>：下載格式（建議 YOLOv8 / YOLOv11）。</li>
      <li><b>Class name</b>：此資料集對應的類別名稱（Hybrid 合併時會以此建立最終 names）。</li>
      <li><b>快速填入</b>：貼 Roboflow 的 Download Code/URL，按 Parse 自動填入欄位。</li>
    </ul>`,

    // Step 4 (static area)
    s4_rule: '規則：本專案僅支援 Detection。請選 YOLOv8/YOLO11 與大小 n/s/m/l/x，並設定超參數。',
    s4_targets_title: 'Targets（訓練 & 匯出）',
    s4_targets_hint: '勾選 1 個或多個 Target 後，右側「▾」可展開細項。每個 Target 的細項都必須按下 <b>Save</b> 才算完成。<br>每個被勾選的 Class name Target 會各自訓練 1 個單一類別模型；若同時勾選 <b>Hybrid</b>，則會再額外訓練 1 個整合 Step2 所有 Class name 群組資料集的多類別模型。',
    help4_html: `<ul>
      <li><b>Model family/size</b>：選 YOLOv8/YOLO11 + 尺寸（n/s/m/l/x）。</li>
      <li><b>epochs/batch/imgsz/workers</b>：訓練輪數/批次/輸入尺寸/資料載入執行緒數。</li>
      <li><b>Device</b>：可填 <code>cpu</code>、單 GPU <code>0</code> 或多 GPU <code>0,1,...</code>。</li>
      <li><b>Data balancing</b>：SMOTE-like oversampling（重複取樣）只作用在 <b>train</b>。</li>
    </ul>`,


    // Step 4 dynamic (Target bars / details)
    s4_hybrid_need2: '需要至少 2 種 Class name 才能使用 Hybrid',
    s4_hybrid_desc: '使用所有類別底下的 datasets（多類別訓練）',
    s4_role_datasets: '{n} 個 dataset',
    s4_pill_saved: '已保存',
    s4_pill_not_saved: '未保存',
    s4_pill_not_selected: '未選擇',
    s4_warn_hybrid_mutex: '提醒：勾選 Hybrid 代表額外加入 1 個整合所有 Class name 群組資料集的多類別訓練 Target。',
    s4_hybrid_ratio_warn: 'Hybrid split ratio 格式需為 3 個數字，例如：0.7,0.2,0.1',

    s4_train_title: 'Train',
    s4_model_family: '模型系列',
    s4_model_size: '模型尺寸',
    s4_epochs: 'Epochs',
    s4_batch: 'Batch',
    s4_imgsz: 'Image size (imgsz)',
    s4_workers: 'Workers',
    s4_device: 'Device',
    s4_device_ph: '0 或 cpu',
    s4_optimizer: 'Optimizer',
    s4_lr0: 'LR',
    s4_patience: 'Patience',
    s4_close_mosaic: 'Close mosaic',
    s4_train_hint_hybrid: 'Hybrid：多類別訓練（single_cls = false）',
    s4_train_hint_single: '非 Hybrid：單一類別訓練（single_cls = true）',
    s4_hybrid_ds_title: 'Hybrid datasets',
    s4_hybrid_ds_hint: 'Hybrid 會直接使用 Step2 所有 Class name 底下的 datasets 進行多類別訓練（不需在此手動選擇）。',
    s4_balance_title: 'Data balancing（SMOTE-like oversampling）',
    s4_balance_intro: '僅對 train 做平衡，並輸出平衡前/後各類別資料量對比圖（CSV + PNG）。',
    s4_balance_enable: 'Enable balancing',
    s4_balance_enable_hint: '啟用後只會作用在 train split；不會改動 val / test。',
    s4_balance_target: 'Balance target',
    s4_balance_custom_type: 'Custom type',
    s4_balance_custom_value: 'Custom value',
    s4_balance_target_mean: 'mean（推薦）',
    s4_balance_target_max: 'max',
    s4_balance_target_custom: 'custom',

    s4_export_title: 'Export',
    s4_export_onnx_label: '匯出 ONNX（預設 FP32）',
    s4_export_engine_label: '匯出 TensorRT Engine（.engine）',
    s4_export_tflite_label: '匯出 TFLite（.tflite）',
    s4_export_onnx_detail_simplify: 'simplify（去冗餘/常數折疊；有助部署，動態 shape 可能不相容）',
    s4_export_onnx_detail_fp16: 'FP16 ONNX（勾選後輸出為 FP16，不會同時產出 FP32+FP16 兩份）',
    s4_export_engine_fp32: 'FP32 Engine',
    s4_export_engine_fp16: 'FP16 Engine（Jetson Nano 主要建議）',
    s4_export_engine_int8: 'INT8 Engine（需要 calibration）',
    s4_export_tflite_fp32: 'FP32 TFLite',
    s4_export_tflite_fp16: 'FP16 TFLite',
    s4_export_tflite_int8: 'INT8 TFLite（需要 calibration）',
    s4_export_int8_calib: 'INT8 calib num',
    s4_export_engine_hint: '注意：TensorRT .engine 不具可攜性，不同 GPU/不同 TensorRT 版本通常需要重建。',

    s4_quant_title: 'Quantization（流程控制）',
    s4_quant_title_engine: 'Quantization（Engine INT8）',
    s4_quant_title_tflite: 'Quantization（TFLite INT8）',
    s4_quant_ptq: 'PTQ',
    s4_quant_qat: 'QAT（placeholder；需先選 PTQ）',
    s4_quant_hint: 'QAT placeholder 的目的：先確保整體流程可用，再逐步擴充真正 QAT。',
    s4_quant_need_ptq: '需先選 PTQ 才能選 QAT。',

    s4_ds_btn_select: 'Select',
    s4_ds_btn_merge: 'Merge',
    s4_ds_confirmed: '已確認',
    s4_ds_not_confirmed: '尚未確認',
    s4_ds_tip_atleast1: '至少需勾選 1 個 dataset。',
    s4_saved_toast: '已保存：{name}',

    // Step 6 (static area)
    s6_rule: '規則：請確認所有選擇。按 <b>Save</b> 會保存目前專案內容，且<b>停留在目前頁面</b>；按 Submit 後才會依 mode 產生 bundle 或 SSH 執行。',
    s6_save_warn: '提醒：此專案尚未儲存，請按下 Save 以保存。',
    project_saved_toast: '專案配置已儲存：{task}{loc}',
    help6_html: `<ul>
      <li>此頁會整理你前面所有選擇，請先確認無誤。</li>
      <li>按 <b>Submit</b> 後才會真正執行（或產生 bundle / 或 SSH 執行）。</li>
      <li>執行完成後可下載 artifacts（results.png、曲線圖、log）。</li>
    </ul>`,
    s6_quant_title: '量化（Quantization）',
    s6_jetson_warn: 'Jetson Nano 提醒：Nano（Maxwell GPU）不支援 INT8 硬體推論；若選 INT8，可能被忽略或回退，部署建議以 FP16 TensorRT Engine 為主。',
    s6_ptq_text: 'PTQ（需要勾選 INT8 Engine 或 INT8 TFLite）',
    s6_qat_text: 'QAT（目前為 placeholder，只做流程記錄，不會真的 QAT 訓練）',
    s6_qat_hint: 'QAT placeholder 的目的：先確保整體流程可用，再逐步擴充真正 QAT。',

  },

  en: {
    // top / layout
    pageTitle: 'YOLO Web Builder v4 · Roboflow Datasets',
    brandTitle: 'YOLO Web Builder v4 · Roboflow Datasets',
    brandSub: 'Detection only · Wizard mode · run only checked items',
    clockLabel: 'Local time',
    homeBtn: 'Home',
    langZh: '中文',
    langEn: 'English',

    heroTitle: 'Build a reproducible YOLO train/export workflow',
    heroDesc: 'Complete 4 steps in order. Click <b>Submit</b> to unlock the next step; click <b>Back</b> to return.',
    notesTitle: 'Important notes (depends on current step)',

    stepModeLabel: 'Mode',
    stepDatasetLabel: 'Dataset',
    stepTrainExportLabel: 'Train&Export',
    stepReviewLabel: 'Review',
    progressHintInit: 'After completing Step 1, the later steps will be unlocked.',
    progressHint: 'Step {cur} / {total} (unlocked up to Step {unlocked})',

    step1Title: 'Step 1 · Mode',
    step2Title: 'Step 2 · Dataset(s) (Roboflow)',
    step4Title: 'Step 4 · Train&Export',
    step6Title: 'Step 6 · Review & Run',

    // Home
    projects: 'Projects',
    newTask: 'New Task',
    rule: 'Rule: a project appears here only after you click <b>Save</b>.',
    empty: 'Try to do something!',
    homeNote_1: 'Home is the project list: click “New Task” to create a task; saved projects appear in the list.',
    homeNote_2: 'A project is persisted only after you click Save; leaving without saving may lose changes.',
    homeNote_3: 'Use Open / Duplicate / Remove to manage projects; Duplicate creates a new copy with a new ID.',
    created: 'Created',
    updated: 'Updated',
    open: 'Open',
    duplicate: 'Duplicate',
    remove: 'Remove',

    // Step notes
    note1_1: 'Choose how to run: generate a bundle, or execute on your device via SSH.',
    note1_2: 'If using SSH, prefer key-based auth (not password) and a non-root, least-privilege account.',
    note2_1: 'Datasets currently support Roboflow only: API key, workspace slug, project slug, version are required.',
    note2_2: 'Recommended formats: YOLOv8/YOLOv11. In many cases you can download YOLOv8-format data and train YOLOv11.',
    note2_3: 'Note: clicking +Add dataset preserves what you already filled; it will not reset.',
    note4_1: 'Train&Export is shown as Target bars: enable a target, expand details, and click Save to mark it done.',
    note4_2: 'You may select multiple Targets. Each selected Class name Target trains its own single-class model; if Hybrid is also selected, the project additionally trains one combined multi-class model using all datasets across the Class name groups from Step 2.',
    note4_3: 'Data balancing (SMOTE-like oversampling) appears only for the Hybrid target and affects train split only.',
    note6_1: 'Confirm all selections in Summary first.',
    note6_2: 'Submit will generate a bundle or run via SSH and provide logs/artifacts for download.',

    // Dataset card
    datasetTitle: 'Dataset #{n} (Roboflow)',
    removeBtn: 'Remove',
    dsName: 'Name',
    dsNamePh: 'dataset_1',
    dsApiKey: 'API key',
    dsApiKeyPh: 'roboflow_api_key',
    toggleApiKey: 'toggle api key',
    dsWorkspace: 'Workspace (slug)',
    dsWorkspacePh: 'workspace-slug',
    dsProject: 'Project (slug)',
    dsProjectPh: 'project-slug',
    dsVersion: 'Version',
    dsFormat: 'Format',
    dsClassName: 'Class name (role for this dataset)',
    dsClassNamePh: 'e.g., cat / helmet / plate',
    dsHint: 'Name allows a-z/A-Z/0-9/_/-. For Hybrid merge, each dataset maps to the role you enter here.',
    // buttons / misc
    helpBtn: 'HELP?',
    btnBack: 'Back',
    btnSubmit: 'Submit',
    jobCtrlTitle: 'Training controls',
    btnPause: 'Pause',
    btnResume: 'Resume',
    btnRetrain: 'Retrain',
    btnTerminate: 'Terminate',
    btnDownloadArtifacts: 'Download project_artifacts.zip',
    cmdPlaceholder: 'nvidia-smi',
    btnRunCmd: 'Run',
    btnSave: 'Save',

    // Step 1 form (static labels)
    s1_rule: 'Rule: enter a unique Task name and choose a run mode. If you select SSH, you must provide SSH connection info.',
    s1_task_label: 'Task (unique)',
    s1_task_ph: 'e.g., helmet_v1 / face_aug / plate_y11',
    s1_task_hint: 'Task is used to save and distinguish each run; it must be unique. It is saved only after you click Save.',
    s1_mode_bundle: 'Generate bundle only',
    s1_mode_ssh: 'Run via SSH',
    s1_ssh_title: 'SSH Settings (required only for SSH mode)',
    s1_ssh_host: 'Host',
    s1_ssh_host_ph: '1.2.3.4 or hostname',
    s1_ssh_port: 'Port',
    s1_ssh_user: 'User',
    s1_ssh_user_ph: 'ubuntu',
    s1_ssh_dir: 'Remote dir',
    s1_ssh_py: 'Python executable',
    s1_ssh_py_ph: 'python3 or /home/.../env/bin/python',
    s1_ssh_py_hint: 'Python must be an <strong>executable</strong>, e.g. <code>python3</code> or <code>/home/.../miniconda3/envs/ENV/bin/python</code>. For conda/venv, the interpreter is usually <code>$ENV/bin/python</code>.',
    s1_ssh_pass: 'Password',
    s1_ssh_pass_ph: '(optional, use key)',
    s1_ssh_key_label: 'Private key (PEM, optional)',
    s1_ssh_key_ph: '-----BEGIN OPENSSH PRIVATE KEY-----',
    s1_ssh_hint: 'Recommendation: use an SSH key and a non-root, low-privilege account.',
    help1_html: `<ul>
      <li><b>Generate bundle only</b>: create a runnable bundle zip that you can download and run on any machine.</li>
      <li><b>Run via SSH</b>: this website connects to your machine via SSH and runs the bundle remotely.</li>
      <li><b>Host</b>: the target machine (IP or resolvable hostname).</li>
      <li><b>Remote dir</b>: directory on the remote machine to place the bundle and outputs (prefer absolute paths).</li>
      <li><b>Python</b>: the python command/path on the remote machine (e.g., conda env python).</li>
    </ul>`,

    // Step 2 quick fill (static area)
    s2_rule: 'Rule: datasets currently support Roboflow only. You need at least 1 dataset (API key / workspace / project / version). For Hybrid, add ≥2 and fill “Class name” for each dataset.',
    s2_quick_title: 'Quick fill (paste Download Code / CLI / Raw URL and auto-parse)',
    s2_tab_jupyter: 'Jupyter',
    s2_tab_terminal: 'Terminal',
    s2_tab_rawurl: 'Raw URL',
    s2_panel_jupyter_label: 'Jupyter / Python Download Code',
    s2_panel_jupyter_ph: 'Paste Roboflow “Jupyter” download code (Python snippet)',
    s2_panel_terminal_label: 'Terminal Download Code',
    s2_panel_terminal_ph: 'Paste Roboflow “Terminal” download code (roboflow CLI or curl)',
    s2_panel_rawurl_label: 'Raw URL',
    s2_panel_rawurl_ph: 'Paste Raw URL (REST API) or Universe URL (e.g., https://universe.roboflow.com/<workspace>/<project>/<version>)',
    s2_hint_jupyter: 'Supported: <code>Roboflow(api_key="..."...)</code>, <code>.download("yolov8")</code>.',
    s2_hint_terminal: 'Supported: <code>roboflow download -f &lt;format&gt;</code> or <code>curl "https://api.roboflow.com/..."</code>.',
    s2_hint_rawurl: 'Supported: REST API URL (with <code>api_key</code>) or <code>&lt;workspace&gt;/&lt;project&gt;/&lt;version&gt;</code> (partial formats won’t overwrite Dataset Format).',
    s2_apply_to: 'Apply to Dataset #',
    s2_parse_hint: 'Choose one mode: paste the snippet/URL and click Parse. If an API key is detected, it will be masked to avoid leaving it in plain text.',
    s2_parse_btn: 'Parse',
    s2_add_ds_btn: '+ Add dataset',
    help2_html: `<ul>
      <li><b>API key</b>: your Roboflow API key (cannot be inferred from URL).</li>
      <li><b>Workspace (slug)</b>: the workspace identifier (first part of the URL).</li>
      <li><b>Project (slug)</b>: the project identifier (second part of the URL).</li>
      <li><b>Version</b>: dataset version number (<code>/dataset/&lt;version&gt;</code>).</li>
      <li><b>Format</b>: download format (YOLOv8 / YOLOv11 recommended).</li>
      <li><b>Class name</b>: role/class name for this dataset (used to build final names for Hybrid).</li>
      <li><b>Quick fill</b>: paste Roboflow download code/URL and click Parse to autofill fields.</li>
    </ul>`,

    // Step 4 (static area)
    s4_rule: 'Rule: this project supports Detection only. Choose YOLOv8/YOLO11 and size (n/s/m/l/x), then set hyperparameters.',
    s4_targets_title: 'Targets (Train & Export)',
    s4_targets_hint: 'After selecting one or more Targets, use the “▾” on the right to expand details. Each Target’s details must be finalized by clicking <b>Save</b>.<br>Each selected Class name Target trains its own single-class model. If <b>Hybrid</b> is also selected, the project additionally trains one combined multi-class model using all datasets across the Class name groups from Step 2.',
    help4_html: `<ul>
      <li><b>Model family/size</b>: choose YOLOv8/YOLO11 + size (n/s/m/l/x).</li>
      <li><b>epochs/batch/imgsz/workers</b>: training epochs/batch size/image size/data loader workers.</li>
      <li><b>Device</b>: use <code>cpu</code>, single GPU <code>0</code>, or multi GPU <code>0,1,...</code>.</li>
      <li><b>Data balancing</b>: SMOTE-like oversampling affects <b>train</b> only.</li>
    </ul>`,


    // Step 4 dynamic (Target bars / details)
    s4_hybrid_need2: 'Hybrid requires at least 2 distinct Class name values',
    s4_hybrid_desc: 'Use all datasets across classes (multi-class training)',
    s4_role_datasets: '{n} dataset(s)',
    s4_pill_saved: 'Saved',
    s4_pill_not_saved: 'Not saved',
    s4_pill_not_selected: 'Not selected',
    s4_warn_hybrid_mutex: 'Reminder: selecting Hybrid adds one extra combined multi-class training Target that uses all datasets across the Class name groups from Step 2.',
    s4_hybrid_ratio_warn: 'Hybrid split ratio must be 3 numbers, e.g. 0.7,0.2,0.1',

    s4_train_title: 'Train',
    s4_model_family: 'Model family',
    s4_model_size: 'Model size',
    s4_epochs: 'Epochs',
    s4_batch: 'Batch',
    s4_imgsz: 'Image size (imgsz)',
    s4_workers: 'Workers',
    s4_device: 'Device',
    s4_device_ph: '0 or cpu',
    s4_optimizer: 'Optimizer',
    s4_lr0: 'LR',
    s4_patience: 'Patience',
    s4_close_mosaic: 'Close mosaic',
    s4_train_hint_hybrid: 'Hybrid: multi-class training (single_cls = false)',
    s4_train_hint_single: 'Non-hybrid: single-class training (single_cls = true)',
    s4_hybrid_ds_title: 'Hybrid datasets',
    s4_hybrid_ds_hint: 'Hybrid directly uses all datasets grouped by Class name from Step 2 for multi-class training. No manual selection is needed here.',
    s4_balance_title: 'Data balancing (SMOTE-like oversampling)',
    s4_balance_intro: 'Balance the train split only and export before/after class-count comparison outputs (CSV + PNG).',
    s4_balance_enable: 'Enable balancing',
    s4_balance_enable_hint: 'When enabled, balancing affects the train split only and does not modify val / test.',
    s4_balance_target: 'Balance target',
    s4_balance_custom_type: 'Custom type',
    s4_balance_custom_value: 'Custom value',
    s4_balance_target_mean: 'mean (recommended)',
    s4_balance_target_max: 'max',
    s4_balance_target_custom: 'custom',

    s4_export_title: 'Export',
    s4_export_onnx_label: 'Export ONNX (default FP32)',
    s4_export_engine_label: 'Export TensorRT Engine (.engine)',
    s4_export_tflite_label: 'Export TFLite (.tflite)',
    s4_export_onnx_detail_simplify: 'simplify (fold constants/redundancy; helps deployment; may conflict with dynamic shapes)',
    s4_export_onnx_detail_fp16: 'FP16 ONNX (outputs FP16 only; will not also output FP32)',
    s4_export_engine_fp32: 'FP32 Engine',
    s4_export_engine_fp16: 'FP16 Engine (recommended for Jetson Nano)',
    s4_export_engine_int8: 'INT8 Engine (requires calibration)',
    s4_export_tflite_fp32: 'FP32 TFLite',
    s4_export_tflite_fp16: 'FP16 TFLite',
    s4_export_tflite_int8: 'INT8 TFLite (requires calibration)',
    s4_export_int8_calib: 'INT8 calib num',
    s4_export_engine_hint: 'Note: TensorRT .engine is not portable; different GPUs/TensorRT versions typically require rebuilding.',

    s4_quant_title: 'Quantization (flow control)',
    s4_quant_title_engine: 'Quantization (Engine INT8)',
    s4_quant_title_tflite: 'Quantization (TFLite INT8)',
    s4_quant_ptq: 'PTQ',
    s4_quant_qat: 'QAT (placeholder; PTQ required)',
    s4_quant_hint: 'Placeholder purpose: validate the end-to-end pipeline first, then add real QAT later.',
    s4_quant_need_ptq: 'PTQ must be enabled before enabling QAT.',

    s4_ds_btn_select: 'Select',
    s4_ds_btn_merge: 'Merge',
    s4_ds_confirmed: 'Confirmed',
    s4_ds_not_confirmed: 'Not confirmed',
    s4_ds_tip_atleast1: 'Select at least one dataset.',
    s4_saved_toast: 'Saved: {name}',

    // Step 6 (static area)
    s6_rule: 'Rule: review all choices. Clicking <b>Save</b> saves the current project content and <b>keeps you on this page</b>. Clicking Submit will then either generate a bundle or run via SSH depending on your mode.',
    s6_save_warn: 'Reminder: this task has not been saved yet. Click Save to save it.',
    project_saved_toast: 'Project configuration saved: {task}{loc}',
    help6_html: `<ul>
      <li>This page summarizes all your selections. Please verify before running.</li>
      <li>Execution starts only after clicking <b>Submit</b> (bundle generation or SSH run).</li>
      <li>After completion you can download artifacts (results.png, curves, logs).</li>
    </ul>`,
    s6_quant_title: 'Quantization',
    s6_jetson_warn: 'Jetson Nano note: Nano (Maxwell GPU) does not support INT8 hardware inference. If INT8 is selected, it may be ignored or fall back. For deployment, FP16 TensorRT Engine is recommended.',
    s6_ptq_text: 'PTQ (requires selecting INT8 Engine or INT8 TFLite)',
    s6_qat_text: 'QAT (placeholder; recorded only, no actual QAT training)',
    s6_qat_hint: 'Purpose of the placeholder: verify the end-to-end pipeline first, then add real QAT later.',

  },
};

function getLang() {
  const v = (localStorage.getItem(LANG_KEY) || 'zh').toLowerCase();
  return v === 'en' ? 'en' : 'zh';
}

function t(key, vars) {
  const lang = getLang();
  let s = (I18N[lang] && I18N[lang][key]) || I18N.zh[key] || key;
  if (vars && typeof s === 'string') {
    Object.keys(vars).forEach(k => {
      s = s.split(`{${k}}`).join(String(vars[k]));
    });
  }
  return s;
}

function applyLangToDOM() {
  const lang = getLang();

  // html lang for screen readers / browser
  document.documentElement.setAttribute('lang', lang === 'en' ? 'en' : 'zh-Hant');

  // title
  try { document.title = t('pageTitle'); } catch {}

  // active toggle
  $('#langZh')?.classList.toggle('active', lang === 'zh');
  $('#langEn')?.classList.toggle('active', lang === 'en');

  // text nodes
  $$('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (!key) return;
    el.textContent = t(key);
  });

  // html nodes (with <b>/<code>)
  $$('[data-i18n-html]').forEach(el => {
    const key = el.getAttribute('data-i18n-html');
    if (!key) return;
    el.innerHTML = t(key);
  });

  // placeholders
  $$('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (!key) return;
    el.setAttribute('placeholder', t(key));
  });
}

function setLang(lang) {
  const v = (lang === 'en') ? 'en' : 'zh';
  localStorage.setItem(LANG_KEY, v);
  document.documentElement.setAttribute('lang', v === 'en' ? 'en' : 'zh-Hant');
  applyLangToDOM();

  // Re-render dynamic parts without losing user inputs.
  const homeVisible = !$('#homeView')?.classList.contains('hidden');
  if (homeVisible) {
    renderHome(homeProjectIndexCache);
  } else {
    const snap = snapshotUI();
    applyUISnapshot(snap);
    showWizard();
  }

  // Notes list is generated; re-render it on language change.
  updateStageNotes();
}

