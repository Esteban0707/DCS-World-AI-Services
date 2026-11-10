// DCS-Instructor GUI. Talks to gui/server.py; every text comes from /api/text (locales/en.json "gui").
// Navigation replaces the address (history.replaceState) instead of adding history entries: Chrome lets a page close
// its own window only while its history has one entry, and the program closes this window when it stops.
'use strict';

const NAV = ['status', 'services', 'connections', 'srs', 'audio', 'aircraft', 'flights', 'dcs', 'settings'];
const BUILT = new Set(['status', 'services', 'audio', 'dcs']);
const TABS = [['frequency', true], ['mission', true], ['triggers', true], ['tts', true], ['stt', true]];
const VOICES = ['F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5'];
const ENGINES = [['supertonic', true], ['kokoro', false], ['api', false]];
const SLIDERS = [  // key, min, max, step, shown as
  ['speed', 0.7, 1.5, 0.05, v => `${v.toFixed(2)}×`],
  ['steps', 2, 16, 1, v => `${v}`],
  ['silence_s', 0, 1, 0.05, v => `${v.toFixed(2)} s`],
  ['volume', 0, 1.5, 0.05, v => `${Math.round(v * 100)} %`],
];
const STATUS_POLL_MS = 1000;

const $ = id => document.getElementById(id);
let T = {};
let settings = null;
let agent = null;          // the agent open in Services
let draft = null;          // its voice settings being edited in the TTS tab
let pollTimer = null;

// --- texts -------------------------------------------------------------------------------------------------------

function lookup(path) {
  return path.split('.').reduce((node, part) => (node && node[part] !== undefined ? node[part] : undefined), T);
}

function t(path, args = {}) {
  const template = lookup(path);
  if (typeof template !== 'string') return path;
  return template.replace(/\{(\w+)\}/g, (_, name) => (args[name] === undefined ? '' : show(args[name])));
}

// A value from the server: plain data, or {key, args} naming a text in "texts".
function show(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object' && value.key) {
    const args = {};
    for (const [name, arg] of Object.entries(value.args || {})) args[name] = show(arg);
    return t(`texts.${value.key}`, args);
  }
  return String(value);
}

function applyTexts(root = document) {
  root.querySelectorAll('[data-t]').forEach(el => { el.textContent = t(el.dataset.t); });
}

// --- server ------------------------------------------------------------------------------------------------------

async function api(path, body) {
  const options = body === undefined ? {} :
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    fail(t('errors.network', { error: e.message }));
    throw e;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const field = data.field || '';
    const row = field.match(/^(events|actions)\.(\d+)$/);   // a Triggers row, numbered from 1 for the user
    const message = data.error !== 'invalid' ? t('errors.network', { error: data.error || response.status }) :
      row ? t(`errors.${row[1]}_row`, { n: Number(row[2]) + 1 }) :
      lookup(`errors.${field}`) ? t(`errors.${field}`) : t('errors.invalid', { field });
    fail(message);
    throw new Error(message);
  }
  $('error').hidden = true;
  return data;
}

function fail(message) {
  $('error').textContent = message;
  $('error').hidden = false;
}

// A page error is shown, not lost: the GUI runs in an app window without developer tools at hand.
window.addEventListener('error', e => fail(`${e.message} (${e.filename}:${e.lineno})`));
window.addEventListener('unhandledrejection', e => fail(String(e.reason && e.reason.stack || e.reason)));

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function flash(id, message) {
  $(id).textContent = message;
  setTimeout(() => { if ($(id).textContent === message) $(id).textContent = ''; }, 2500);
}

// --- navigation --------------------------------------------------------------------------------------------------

function go(hash) {
  history.replaceState(null, '', `#${hash}`);
  route();
}

// Every in-page link navigates without a history entry.
document.addEventListener('click', e => {
  const link = e.target.closest('a[href^="#"]');
  if (!link) return;
  e.preventDefault();
  go(link.getAttribute('href').slice(1));
});

function buildNav() {
  $('nav').replaceChildren(...NAV.map(id => el('a', { href: `#${id}`, 'data-id': id }, [
    el('span', { class: 'label', text: t(`nav.${id}`) }),
    BUILT.has(id) ? null : el('span', { class: 'soon', text: t('soon') }),
  ])));
}

function route() {
  const [screen, agentId, tab] = location.hash.slice(1).split('/');
  const id = NAV.includes(screen) ? screen : 'status';
  document.querySelectorAll('#nav a').forEach(a => {
    if (a.dataset.id === id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  document.querySelectorAll('.screen').forEach(s => { s.hidden = true; });
  clearInterval(pollTimer);
  if (!BUILT.has(id)) {
    $('soon-title').textContent = t(`nav.${id}`);
    $('screen-soon').hidden = false;
    return;
  }
  if (id === 'services' && agentId) { $('screen-agent').hidden = false; openAgent(agentId, tab || 'frequency'); return; }
  $(`screen-${id}`).hidden = false;
  if (id === 'status') { refreshStatus(); pollTimer = setInterval(refreshStatus, STATUS_POLL_MS); }
  if (id === 'services') openServices();
  if (id === 'audio') openAudio();
  if (id === 'dcs') openDcs();
}

// --- DCS screen ------------------------------------------------------------------------------------------------------

function showDcs(data) {
  $('dcs-install').textContent = t('dcs.install', { path: data.install, version: data.version });
  $('dcs-patches').replaceChildren(...data.patches.flatMap(p => [
    el('dt', { text: t(`dcs.patch_${p.name}`) }),
    el('dd', { class: `state-${p.state}`, text: t(`dcs.state_${p.state}`) + (p.detail ? ` · ${p.detail}` : '') }),
  ]));
  $('dcs-saved-games').replaceChildren(
    el('dt', { text: t('dcs.export') }), el('dd', { text: t(data.export ? 'dcs.installed' : 'dcs.not_installed') }),
    el('dt', { text: t('dcs.hook') }), el('dd', { text: t(data.hook ? 'dcs.installed' : 'dcs.not_installed') }));
}

async function openDcs() {
  try { showDcs(await api('/api/dcs')); } catch (e) { /* shown */ }
}

async function changeDcs(action) {
  if (action === 'remove' && !confirm(t('dcs.remove_confirm'))) return;
  $('dcs-state').textContent = t('dcs.working');
  try {
    const data = await api(`/api/dcs/${action}`, {});
    showDcs(data);
    flash('dcs-state', t(data.patches.every(p => p.state === (action === 'update' ? 'patched' : 'original'))
      ? 'dcs.done_restart' : 'dcs.needs_review'));
  } catch (e) { $('dcs-state').textContent = ''; }
}

// The program holds this connection to know the window is open; "close" means it is stopping.
function watchProgram() {
  const events = new EventSource('/api/events');
  events.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.event === 'close') { events.close(); window.close(); }
  };
}

// --- Status ------------------------------------------------------------------------------------------------------

async function refreshStatus() {
  let data;
  try { data = await api('/api/status'); } catch (e) { return; }
  $('air-light').className = `light ${data.running ? 'ok' : 'off'}`;
  $('air-text').textContent = data.running ? `${t('status.on_air')} · ${data.frequency}` : t('status.not_running');
  $('stop').hidden = !data.running;

  $('tiles').replaceChildren(...data.tiles.map(tile => el('div', { class: 'tile' }, [
    el('div', { class: 'tile-head' }, [
      el('span', { class: 'tile-name', text: t(`tiles.${tile.id}`) }),
      el('span', { class: `tile-state ${tile.light}` }, [el('span', { class: `light ${tile.light}` }),
        t(`states.${tile.state}`)]),
    ]),
    el('span', { class: 'tile-value', text: show(tile.value) || '-' }),
    el('span', { class: 'tile-detail', text: show(tile.detail) }),
  ])));

  const exchanges = data.exchanges.slice().reverse();
  $('exchanges').replaceChildren(...(exchanges.length ? exchanges.map(line => el('div', { class: `exchange ${line.who}` }, [
    el('span', { class: 'time', text: line.time }),
    el('span', { class: 'who', text: line.who === 'pilot' ? t('status.pilot') : (line.agent || '').toUpperCase() }),
    el('span', { class: 'said', text: line.text }),
    line.seconds !== null && line.seconds !== undefined ? el('span', { class: 'meta', text: `${line.seconds.toFixed(1)} s` }) : null,
  ])) : [el('p', { class: 'muted', text: t('status.no_exchanges') })]));

  $('status-services').replaceChildren(...data.services.map(svc => el('div', {
    class: `service${svc.active ? ' active' : ''}`,
  }, [
    el('div', {}, [
      el('strong', { text: svc.name || t(`services.role_${svc.id}`) }),
      svc.soon ? null : el('span', { class: 'small muted', text: t('services.detail', svc) }),
    ]),
    el('span', { class: 'freq', text: svc.soon ? t('soon') : svc.active ? serviceChannel(svc) : t('services.off') }),
  ])));
}

async function stopInstructor() {
  if (!confirm(t('status.stop_confirm'))) return;
  $('stop').disabled = true;
  $('stop').textContent = t('status.stopping');
  try { await api('/api/stop', {}); } finally {
    setTimeout(() => { $('stop').disabled = false; $('stop').textContent = t('status.stop'); }, 3000);
  }
}

// Starts SRS if it closed or crashed, and brings its client window out of the tray.
async function openSrs() {
  $('open-srs').disabled = true;
  try { await api('/api/srs/open', {}); refreshStatus(); } catch (e) { /* shown */ } finally {
    setTimeout(() => { $('open-srs').disabled = false; }, 3000);
  }
}

// --- Services ----------------------------------------------------------------------------------------------------

async function loadSettings() {
  settings = await api('/api/settings');
  return settings;
}

async function openServices() {
  let data;
  try { data = await api('/api/status'); } catch (e) { return; }
  $('agent-cards').replaceChildren(...data.services.map(svc => {
    if (svc.soon) {
      return el('div', { class: 'card soon-card' }, [
        el('div', { class: 'card-head' }, [el('strong', { text: t(`services.role_${svc.id}`) }),
          el('span', { class: 'small muted', text: t('soon') })]),
        el('span', { class: 'muted small', text: t(`services.about_${svc.id}`) }),
      ]);
    }
    return el('a', { class: 'card', href: `#services/${svc.id}` }, [
      el('div', { class: 'card-head' }, [
        el('strong', { text: svc.name }),
        el('span', { class: `tile-state ${svc.active ? 'ok' : 'off'}` }, [el('span', { class: `light ${svc.active ? 'ok' : 'off'}` }),
          svc.active ? t('services.on') : t('services.off')]),
      ]),
      el('span', { class: 'card-freq', text: serviceChannel(svc) }),
      el('span', { class: 'muted small', text: t('services.detail', svc) }),
    ]);
  }));
}

const GROUP_ROLES = ['squad', 'vehicles', 'artillery', 'ships', 'wingman', 'controller'];   // shared/agents.py GROUP_ROLES + SENSOR_ROLES

// "+ Add agent": pick a role, give it a name and a frequency (a squad also names its group in the mission).
async function openAddAgent() {
  try { await loadSettings(); } catch (e) { return; }
  $('add-role').replaceChildren(...settings.addable_roles.map(role =>
    el('option', { value: role, text: t(`services.role_${role}`) })));
  $('add-name').value = $('add-group').value = $('add-frequency').value = '';
  $('add-modulation').value = 'AM';
  addRoleChanged();
  $('add-panel').hidden = false;
  $('add-agent').hidden = true;
  $('add-name').focus();
}

function closeAddAgent() {
  $('add-panel').hidden = true;
  $('add-agent').hidden = false;
}

function addRoleChanged() {
  const role = $('add-role').value;
  $('add-role-about').textContent = t(`add.about_${role}`);
  $('add-squad').hidden = !GROUP_ROLES.includes(role);   // they follow their group in the mission
  $('add-squad').querySelector('.hint').textContent =
    t({ wingman: 'add.group_hint_wingman', vehicles: 'add.group_hint_vehicles', artillery: 'add.group_hint_artillery', ships: 'add.group_hint_ships', controller: 'add.group_hint_controller' }[role] || 'add.group_hint');
  $('add-wingman').hidden = role !== 'wingman';
  $('add-name').placeholder = t(`add.name_example_${role}`);
  $('add-frequency').placeholder = { squad: '255.000', vehicles: '256.000', artillery: '257.000', ships: '258.000', wingman: '316.000', controller: '270.000' }[role] || '252.000';
}

async function createAgent() {
  const role = $('add-role').value;
  const body = { role, name: $('add-name').value, frequency_mhz: Number($('add-frequency').value),
    modulation: $('add-modulation').value };
  if (GROUP_ROLES.includes(role)) body.group = $('add-group').value;
  if (role === 'wingman') body.number = Number($('add-number').value);
  try {
    const created = await api('/api/agents', body);
    closeAddAgent();
    go(`services/${created.id}/frequency`);
    flash('agent-state', t('add.created'));
  } catch (e) { /* shown */ }
}

async function openAgent(agentId, tab) {
  try {
    [agent] = await Promise.all([api(`/api/agents/${agentId}`), loadSettings()]);
  } catch (e) { return; }
  $('agent-title').textContent = agent.name;
  $('agent-subtitle').textContent = t('agent.subtitle', { frequency: channel(agent), role: t(`services.role_${agent.role}`) });
  $('tabs').replaceChildren(...TABS.map(([id, built]) => el('a', {
    href: `#services/${agent.id}/${id}`, role: 'tab', class: built ? '' : 'soon-tab',
    'aria-selected': String(id === tab),
  }, [t(`tabs.${id}`) + (built ? '' : ` · ${t('soon')}`)])));
  document.querySelectorAll('.tab-panel').forEach(p => { p.hidden = p.id !== `tab-${tab}`; });
  if (tab === 'frequency') openFrequency();
  if (tab === 'mission') openMission();
  if (tab === 'triggers') openTriggers();
  if (tab === 'tts') openTts();
  if (tab === 'stt') openStt();
}

// An ATC answers on every airfield's own frequency ("frequency_mode": "airfield") instead of one of its own.
function channel(a) {
  if (a.frequency_mode === 'airfield') return t('agent.airfield_frequencies');
  return `${Number(a.frequency_mhz).toFixed(3)} ${a.modulation}`;
}

function serviceChannel(svc) {
  return svc.frequency_mode === 'airfield' ? t('agent.airfield_frequencies') : svc.frequency;
}

async function saveAgent(changes, message) {
  try {
    agent = await api(`/api/agents/${agent.id}`, changes);
    $('agent-title').textContent = agent.name;
    $('agent-subtitle').textContent = t('agent.subtitle', { frequency: channel(agent), role: t(`services.role_${agent.role}`) });
    flash('agent-state', message || t('agent.saved'));
    return true;
  } catch (e) { return false; }
}

// Frequency tab
function openFrequency() {
  $('agent-name').value = agent.name;
  $('agent-name').disabled = Boolean(agent.fixed);   // the ATC keeps its name, role and tools
  $('agent-name-note').textContent = t(agent.fixed ? 'agent.name_fixed' : 'agent.name_hint', { name: '{name}' });
  $('agent-name-note').hidden = false;
  $('agent-aliases-box').hidden = Boolean(agent.fixed);   // the ATC answers as each airfield's station
  $('agent-aliases').value = (agent.aliases || []).join(', ');
  // A squad, a vehicles unit and a wingman follow their group in the mission by name.
  $('agent-squad').hidden = !GROUP_ROLES.includes(agent.role);
  $('agent-squad').querySelector('.hint').textContent =
    t(agent.role === 'wingman' ? 'agent.group_hint_wingman' : 'agent.group_hint');
  $('agent-group').value = agent.role === 'wingman' ? (agent.wingman || {}).group || ''
    : agent.role === 'controller' ? ((agent.controller || {}).sensors || [])[0] || ''
    : ((agent[agent.role] || {}).groups || [])[0] || '';
  $('agent-wingman').hidden = agent.role !== 'wingman';   // its number in the pilot's flight
  $('agent-number').value = String((agent.wingman || {}).number || 2);
  $('delete-box').hidden = Boolean(agent.fixed);
  $('agent-active').checked = Boolean(agent.active);
  const airfield = agent.frequency_mode === 'airfield';
  $('agent-frequency').value = Number(agent.frequency_mhz).toFixed(3);
  $('agent-modulation').value = agent.modulation;
  $('agent-frequency').disabled = $('agent-modulation').disabled = airfield;
  $('agent-airfield-note').hidden = !airfield;
}

function saveFrequency() {
  const changes = { active: $('agent-active').checked };
  if (!agent.fixed) {
    changes.name = $('agent-name').value;
    changes.aliases = $('agent-aliases').value.split(',').map(a => a.trim()).filter(Boolean);
  }
  if (GROUP_ROLES.includes(agent.role)) changes.group = $('agent-group').value;
  if (agent.role === 'wingman') changes.number = Number($('agent-number').value);
  if (agent.frequency_mode !== 'airfield') {
    changes.frequency_mhz = Number($('agent-frequency').value);
    changes.modulation = $('agent-modulation').value;
  }
  saveAgent(changes);
}

async function deleteAgent() {
  if (!confirm(t('agent.delete_confirm', { name: agent.name }))) return;
  try {
    const data = await api(`/api/agents/${agent.id}/delete`, {});
    go('services');
    alert(t('agent.deleted', { name: agent.name, folder: data.kept_in }));
  } catch (e) { /* shown */ }
}

// Triggers tab: events (a mission flag turns on -> the agent calls the pilot) and actions (a tool the agent may use ->
// it turns a mission flag on). The ATC is fixed: no triggers.
function openTriggers() {
  $('triggers-fixed').hidden = !agent.fixed;
  $('triggers-edit').hidden = Boolean(agent.fixed);
  $('event-rows').replaceChildren(...(agent.events || []).map(eventRow));
  $('action-rows').replaceChildren(...(agent.actions || []).map(actionRow));
  emptyNotes();
  if (assistantAgent !== agent.id) {   // each agent has its own conversation
    assistantAgent = agent.id;
    $('assistant-log').replaceChildren();
    $('assistant-input').placeholder = t('assistant.placeholder');
  }
}

function emptyNotes() {
  for (const [rows, key] of [['event-rows', 'no_events'], ['action-rows', 'no_actions']]) {
    if (!$(rows).querySelector('.trigger-row')) {
      $(rows).replaceChildren(el('p', { class: 'muted small empty', text: t(`triggers.${key}`) }));
    }
  }
}

function addRow(rows, row) {
  const empty = $(rows).querySelector('.empty');
  if (empty) empty.remove();
  $(rows).append(row);
  row.querySelector('input').focus();
}

function removeButton() {
  return el('button', { type: 'button', class: 'ghost remove', text: t('triggers.remove'),
    onclick: e => { e.target.closest('.trigger-row').remove(); emptyNotes(); } });
}

function field(label, input, size = '') {
  return el('label', { class: `field ${size}`.trim() }, [el('span', { text: label }), input]);
}

// An event fires on a flag, or on a condition our code sees happen to a squad ("when": casualties, enemies_seen...).
function eventRow(event = {}) {
  const when = el('select', { class: 'when' }, [
    el('option', { value: 'flag', text: t('triggers.when_flag') }),
    ...(agent.conditions || []).map(c => el('option', { value: c, text: t(`triggers.cond_${c}`) }))]);
  when.value = event.when || 'flag';
  const about = el('select', { class: 'about' }, [
    el('option', { value: '', text: t('triggers.about_self') }),
    ...(agent.squads || []).filter(s => s.id !== agent.id).map(s => el('option', { value: s.id, text: s.name }))]);
  about.value = event.about || '';
  const flag = field(t('triggers.flag'), el('input', { type: 'text', class: 'flag', maxlength: '40',
    value: event.flag || '', placeholder: '10' }), 'short');
  const zone = field(t('triggers.zone'), el('input', { type: 'text', class: 'zone', maxlength: '60',
    value: event.zone || '', placeholder: 'Z1' }), 'short');
  const internal = internalBox(event.internal);
  const whose = field(t('triggers.about'), about);
  const show = () => {
    const isFlag = when.value === 'flag';
    flag.hidden = internal.hidden = !isFlag;
    whose.hidden = isFlag;
    zone.hidden = when.value !== 'zone_reached';
  };
  when.addEventListener('change', show);
  show();
  return el('div', { class: 'trigger-row event' }, [
    field(t('triggers.when_event'), when), flag, whose, zone,
    field(t('triggers.instructions'), Object.assign(el('textarea', { class: 'instructions', maxlength: '1000',
      placeholder: t('triggers.instructions_example') }), { value: event.instructions || '' }), 'long'),
    internal,
    removeButton(),
  ]);
}

function actionRow(action = {}) {
  const name = el('input', { type: 'text', class: 'name', maxlength: '40', value: action.name || '',
    placeholder: 'smoke_ready' });
  // The model sees it as a tool name: lowercase words joined by "_".
  name.addEventListener('blur', () => { name.value = toolName(name.value); });
  return el('div', { class: 'trigger-row action' }, [
    field(t('triggers.action_name'), name),
    field(t('triggers.flag_set'), el('input', { type: 'text', class: 'flag', maxlength: '40', value: action.flag || '',
      placeholder: '20' }), 'short'),
    field(t('triggers.when'), Object.assign(el('textarea', { class: 'description', maxlength: '1000',
      placeholder: t('triggers.when_example') }), { value: action.description || '' }), 'long'),
    internalBox(action.internal),
    removeButton(),
  ]);
}

// Internal flag: agent to agent inside this program; DCS never sees it (no Mission Editor trigger needed).
function internalBox(checked) {
  return el('label', { class: 'field check internal-box', title: t('triggers.internal_hint') }, [
    Object.assign(el('input', { type: 'checkbox', class: 'internal' }), { checked: checked === true }),
    el('span', { text: t('triggers.internal') })]);
}

function toolName(text) {
  return text.trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

// The rows as they are on screen (saved or not); empty rows left out.
function readRows() {
  const value = (row, cls) => row.querySelector(`.${cls}`).value.trim();
  const internal = row => row.querySelector('.internal').checked;
  const events = [...$('event-rows').querySelectorAll('.trigger-row')]
    .map(row => value(row, 'when') === 'flag'
      ? { flag: value(row, 'flag'), instructions: value(row, 'instructions'), internal: internal(row) }
      : { when: value(row, 'when'), about: value(row, 'about'),
          zone: value(row, 'when') === 'zone_reached' ? value(row, 'zone') : '',
          instructions: value(row, 'instructions') })
    .filter(e => e.flag || e.instructions);
  const actions = [...$('action-rows').querySelectorAll('.trigger-row')]
    .map(row => ({ name: toolName(value(row, 'name')), flag: value(row, 'flag'), description: value(row, 'description'),
      internal: internal(row) }))
    .filter(a => a.name || a.flag || a.description);
  return { events, actions };
}

async function saveTriggers() {
  if (await saveAgent(readRows(), t('triggers.saved'))) {
    openTriggers();
    assistantNote(t('assistant.saved_note'));
  }
}

// Triggers assistant: the maker says what should happen, the model fills the rows (never saves).
let assistantAgent = null;

function assistantSay(who, text) {
  $('assistant-log').append(el('div', { class: `bubble ${who}`, text }));
  $('assistant-log').scrollTop = $('assistant-log').scrollHeight;
}

function assistantNote(text) {
  if ($('assistant-log').childElementCount) assistantSay('note', text);
}

function showDraft(draft, before) {
  const seen = new Set([...before.events, ...before.actions].map(row => JSON.stringify(row)));
  const mark = (row, data) => { if (!seen.has(JSON.stringify(data))) row.classList.add('new'); return row; };
  $('event-rows').replaceChildren(...draft.events.map(e => mark(eventRow(e), e)));
  $('action-rows').replaceChildren(...draft.actions.map(a => mark(actionRow(a), a)));
  emptyNotes();
}

async function askAssistant() {
  const message = $('assistant-input').value.trim();
  if (!message) return;
  const before = readRows();
  assistantSay('user', message);
  $('assistant-input').value = '';
  $('assistant-send').disabled = true;
  $('assistant-state').textContent = t('assistant.thinking');
  try {
    const data = await api(`/api/agents/${agent.id}/triggers/assist`, { message, draft: before });
    if (data.error) {
      const key = lookup(`assistant.error_${data.error}`) ? `assistant.error_${data.error}` : 'assistant.error_error';
      assistantSay('note', t(key, { detail: data.detail }));
    } else {
      assistantSay('assistant', data.reply);
      const changed = JSON.stringify(data.draft) !== JSON.stringify(before);
      if (changed) {
        showDraft(data.draft, before);
        assistantNote(t('assistant.review'));
      }
    }
  } catch (e) { /* shown */ } finally {
    $('assistant-send').disabled = false;
    $('assistant-state').textContent = '';
  }
}

async function resetAssistant() {
  try { await api(`/api/agents/${agent.id}/triggers/assist/reset`, {}); } catch (e) { return; }
  $('assistant-log').replaceChildren();
}

// Mission tab
function openMission() {
  $('mission-text').value = agent.mission;
  $('mission-fields').textContent = t('mission.fields', { fields: settings.mission_fields.map(f => `{${f}}`).join(' ') });
  $('mission-origin').textContent = agent.mission_is_original ? t('mission.original') : t('mission.edited');
  // A wingman: its chatter and its memory of the pilot (agents/<id>/memory.json), one line each.
  $('wingman-memory').hidden = agent.role !== 'wingman';
  if (agent.role === 'wingman') {
    const memory = agent.memory || {};
    $('wingman-chatter').value = (agent.wingman || {}).chatter || 'rare';
    $('memory-pilot').value = (memory.pilot || []).join('\n');
    $('memory-flights').value = (memory.flights || []).join('\n');
  }
}

async function saveMemory() {
  try {
    await api(`/api/agents/${agent.id}`, { chatter: $('wingman-chatter').value });
    agent = await api(`/api/agents/${agent.id}/memory`, {
      pilot: $('memory-pilot').value.split('\n'), flights: $('memory-flights').value.split('\n') });
    openMission();
    flash('agent-state', t('memory.saved'));
  } catch (e) { /* shown */ }
}

async function saveMission() {
  try {
    agent = await api(`/api/agents/${agent.id}/mission`, { text: $('mission-text').value });
    openMission();
    flash('agent-state', t('mission.saved'));
  } catch (e) { /* shown */ }
}

async function restoreMission() {
  if (!confirm(t('mission.restore_confirm'))) return;
  try {
    agent = await api(`/api/agents/${agent.id}/mission/restore`, {});
    openMission();
    flash('agent-state', t('mission.restored'));
  } catch (e) { /* shown */ }
}

// TTS tab
function openTts() {
  const voice = agent.voice || {};
  draft = {
    name: voice.name || 'M1', speed: voice.speed ?? 1.05, steps: voice.steps ?? 8, silence_s: voice.silence_s ?? 0.3,
    volume: voice.volume ?? 1.0, output: voice.output || 'srs', language: voice.language || 'en',
  };
  if (!$('test-text').value) $('test-text').value = t('tts.test_default');
  $('test-srs').textContent = t('tts.test_srs', { frequency: channel(agent) });

  $('engines').setAttribute('aria-label', t('tts.engines'));
  $('engines').replaceChildren(...ENGINES.map(([id, available]) => el('button', {
    type: 'button', 'aria-pressed': String(id === 'supertonic'), ...(available ? {} : { disabled: '' }),
  }, [t(`tts.engine_${id}`) + (available ? '' : ` · ${t('soon')}`)])));

  $('tts-language').replaceChildren(...(settings.languages || [draft.language]).map(code =>
    el('option', { value: code, text: code })));
  $('tts-language').value = draft.language;
  $('tts-output').replaceChildren(
    el('option', { value: 'srs', text: t('tts.output_srs', { frequency: channel(agent) }) }),
    el('option', { value: 'speakers', text: t('tts.output_speakers') }));
  $('tts-output').value = draft.output;

  renderVoices();
  renderSliders();
}

function renderVoices() {
  $('voices').setAttribute('aria-label', t('tts.voice'));
  $('voices').replaceChildren(...VOICES.map(id => el('button', {
    type: 'button', 'aria-pressed': String(draft.name === id),
    onclick: () => { draft.name = id; renderVoices(); },
  }, [id])));
}

function renderSliders() {
  $('sliders').replaceChildren(...SLIDERS.map(([key, min, max, step, format]) => {
    const output = el('output', { id: `out-${key}`, text: format(Number(draft[key])) });
    const input = el('input', {
      type: 'range', id: `in-${key}`, min, max, step, value: draft[key],
      oninput: e => { draft[key] = Number(e.target.value); output.textContent = format(draft[key]); },
    });
    return el('div', { class: 'slider' }, [
      el('div', { class: 'slider-top' }, [el('label', { for: `in-${key}`, text: t(`tts.${key}`) }), output]),
      input,
      el('span', { class: 'hint', text: t(`tts.${key === 'silence_s' ? 'silence' : key}_hint`) }),
    ]);
  }));
}

async function testVoice(via) {
  const buttons = [$('test-srs'), $('test-speakers')];
  buttons.forEach(b => { b.disabled = true; });
  $('test-result').textContent = `${t('tts.testing')} ${t('tts.first_test')}`;
  try {
    const { output, ...params } = draft;
    const result = await api('/api/voice/test', { text: $('test-text').value, via, params, agent: agent.id });
    $('test-result').textContent = result.error ? result.error :
      t('tts.test_result', { synthesis: result.synthesis_s, audio: result.audio_s, via: result.via });
  } catch (e) {
    $('test-result').textContent = '';
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
}

// STT tab
function openStt() {
  $('stt-provider').textContent = t(`texts.provider_${(settings.speech || {}).provider || 'none'}`);
  const language = (agent.speech || {}).language || 'en-US';
  $('stt-language').replaceChildren(...settings.stt_languages.map(code => el('option', { value: code, text: code })));
  $('stt-language').value = language;
  $('stt-mic').textContent = t('stt.microphone_now', { device: (settings.mic || {}).device || t('audio.default_device') });
}

// --- Controls and audio ------------------------------------------------------------------------------------------

function fillDevices(select, names, chosen) {
  const options = [el('option', { value: '', text: t('audio.default_device') })];
  for (const name of names) options.push(el('option', { value: name, text: name }));
  // A saved device that is not connected now (VR goggles off...) stays chosen and says so; part of a name matches.
  const match = chosen ? names.find(n => n === chosen) || names.find(n => n.toLowerCase().includes(chosen.toLowerCase())) : '';
  if (chosen && !match) options.push(el('option', { value: chosen, text: t('audio.missing_device', { device: chosen }) }));
  select.replaceChildren(...options);
  select.value = match || chosen || '';
}

async function openAudio() {
  let devices;
  try { [devices] = await Promise.all([api('/api/devices'), loadSettings()]); } catch (e) { return; }
  fillDevices($('mic'), devices.inputs, (settings.mic || {}).device || '');
  fillDevices($('output'), devices.outputs, (settings.audio || {}).output_device || '');

  const ptt = settings.ptt || {};
  $('ptt').textContent = ptt.joystick ? t('audio.ptt_value', ptt) : '-';
  const radio2 = ptt.radio2 || {};
  $('ptt-radio2').textContent = radio2.button
    ? t('audio.ptt_value', { joystick: radio2.joystick || ptt.joystick, button: radio2.button }) : t('audio.ptt_radio2_none');
  $('ptt-gate').textContent = ptt.instructor_frequency_only ? t('audio.ptt_gate') : '';

  const srs = devices.srs || {};
  const facts = [];
  if (srs.installed) {
    const audio = srs.client_audio || {};
    for (const kind of ['output', 'input']) {
      const device = audio[kind] || {};
      facts.push(el('dt', { text: t(`audio.srs_${kind}`) }), el('dd', { text: device.name || t('audio.default_device') }));
    }
  } else {
    facts.push(el('dd', { text: t('audio.srs_missing') }));
  }
  $('srs-devices').replaceChildren(...facts);
  $('srs-change').hidden = !srs.installed;
}

async function saveAudio(field, value) {
  try {
    await api('/api/audio', { [field]: value });
    flash('audio-state', t('audio.saved'));
  } catch (e) { /* shown */ }
}

// --- start -------------------------------------------------------------------------------------------------------

async function start() {
  try { T = await api('/api/text'); } catch (e) { return; }
  applyTexts();
  buildNav();
  // ?nowatch: for headless screenshots, whose virtual time never advances while the event stream stays open.
  if (!new URLSearchParams(location.search).has('nowatch')) watchProgram();
  $('stop').addEventListener('click', stopInstructor);
  $('open-srs').addEventListener('click', openSrs);
  $('save-frequency').addEventListener('click', saveFrequency);
  $('save-mission').addEventListener('click', saveMission);
  $('restore-mission').addEventListener('click', restoreMission);
  $('save-memory').addEventListener('click', saveMemory);
  $('tts-save').addEventListener('click', () => saveAgent({ voice: draft }));
  $('tts-language').addEventListener('change', e => { draft.language = e.target.value; });
  $('tts-output').addEventListener('change', e => { draft.output = e.target.value; });
  $('test-srs').addEventListener('click', () => testVoice('srs'));
  $('test-speakers').addEventListener('click', () => testVoice('speakers'));
  $('stt-save').addEventListener('click', () => saveAgent({ speech: { language: $('stt-language').value } }));
  $('mic').addEventListener('change', e => saveAudio('mic_device', e.target.value));
  $('output').addEventListener('change', e => saveAudio('output_device', e.target.value));
  $('refresh').addEventListener('click', openAudio);
  $('dcs-update').addEventListener('click', () => changeDcs('update'));
  $('dcs-remove').addEventListener('click', () => changeDcs('remove'));
  $('add-agent').addEventListener('click', openAddAgent);
  $('delete-agent').addEventListener('click', deleteAgent);
  $('add-event').addEventListener('click', () => addRow('event-rows', eventRow()));
  $('add-action').addEventListener('click', () => addRow('action-rows', actionRow()));
  $('triggers-save').addEventListener('click', saveTriggers);
  $('assistant-send').addEventListener('click', askAssistant);
  $('assistant-reset').addEventListener('click', resetAssistant);
  // Enter sends, Shift+Enter makes a new line.
  $('assistant-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); askAssistant(); }
  });
  $('add-cancel').addEventListener('click', closeAddAgent);
  $('add-create').addEventListener('click', createAgent);
  $('add-role').addEventListener('change', addRoleChanged);
  route();
}

start();
