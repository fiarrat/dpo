/* ==========================================================================
   Реестр обращений по персональным данным — клиентская логика.
   Ванильные ES-модули: интерфейс не требует сборки и запускается вместе
   с бэкендом одной командой.
   ========================================================================== */

/* ── Слой обращений к API ────────────────────────────────────────────── */

async function request(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => `${d.loc?.slice(-1)}: ${d.msg}`).join('; ')
      : (detail || `Ошибка ${res.status}`);
    throw new Error(message);
  }
  return data;
}

const api = {
  get: (url) => request(url),
  post: (url, body) => request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  }),
  patch: (url, body) => request(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  }),
  del: (url) => request(url, { method: 'DELETE' }),
  form: (url, formData) => request(url, { method: 'POST', body: formData }),
  formPatch: (url, formData) => request(url, { method: 'PATCH', body: formData }),
};

/* ── Состояние ───────────────────────────────────────────────────────── */

const state = {
  ref: null,
  labels: {},          // словари value -> подпись, по каждому справочнику
  entities: [],
  inboxes: [],
  services: [],
  filters: {},
  sort: 'urgency',
  order: 'asc',
  page: 1,
  pageSize: 50,
  lastPage: null,
  selectedId: null,
  analyzeFiles: [],
  analyzeResult: null,
};

/* ── Мелкие помощники ────────────────────────────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function fmtDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function fmtDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** Склонение: 1 день, 2 дня, 5 дней. */
function plural(n, one, few, many) {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (last > 1 && last < 5) return few;
  if (last === 1) return one;
  return many;
}

function daysLeftText(n) {
  if (n === null || n === undefined) return '';
  if (n < 0) {
    const d = Math.abs(n);
    return `просрочено на ${d} ${plural(d, 'рабочий день', 'рабочих дня', 'рабочих дней')}`;
  }
  if (n === 0) return 'истекает сегодня';
  return `осталось ${n} ${plural(n, 'рабочий день', 'рабочих дня', 'рабочих дней')}`;
}

const URGENCY_TONE = {
  OVERDUE: 'red', TODAY: 'orange', CRITICAL: 'orange',
  HIGH: 'amber', MEDIUM: 'blue', LOW: 'slate', NONE: 'slate',
};

const STATUS_TONE = {
  NEW: 'blue', TRIAGE: 'blue', IDENTITY_PENDING: 'amber', IN_PROGRESS: 'amber',
  DRAFTED: 'blue', ANSWERED: 'green', CLOSED: 'slate',
  REJECTED: 'slate', NOT_APPLICABLE: 'slate',
};

const label = (dict, value) => state.labels[dict]?.[value] ?? value ?? '—';

const TERMINAL_STATUSES = new Set(['ANSWERED', 'CLOSED', 'REJECTED', 'NOT_APPLICABLE']);

/** Короткое имя юрлица: в таблице полное наименование не помещается. */
function entityName(row) {
  const found = state.entities.find((e) => e.id === row.legal_entity_id);
  if (found) return found.short_name || found.name;
  return row.legal_entity_mentioned || '';
}

function toast(message, kind = '') {
  const node = el('div', { class: `toast ${kind ? `toast--${kind}` : ''}` }, message);
  $('#toasts').append(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .25s';
    setTimeout(() => node.remove(), 260);
  }, kind === 'error' ? 6500 : 3200);
}

async function guard(fn, { silent = false } = {}) {
  try { return await fn(); }
  catch (err) { if (!silent) toast(err.message, 'error'); return null; }
}

/* ── Модальное окно ──────────────────────────────────────────────────── */

function openModal(title, contentNode, actions = []) {
  const modal = $('#modal');
  modal.innerHTML = '';
  modal.append(
    el('h3', {}, title),
    contentNode,
    el('div', { class: 'modal__actions' },
      ...actions.map((a) => el('button', {
        class: `btn ${a.primary ? 'btn--primary' : 'btn--ghost'}`,
        onClick: a.onClick,
      }, a.label)),
      el('button', { class: 'btn btn--ghost', onClick: closeModal }, 'Закрыть')),
  );
  $('#modal-backdrop').hidden = false;
}

function closeModal() { $('#modal-backdrop').hidden = true; }

$('#modal-backdrop').addEventListener('click', (e) => {
  if (e.target.id === 'modal-backdrop') closeModal();
});

/* ── Загрузка справочных данных ──────────────────────────────────────── */

async function loadReference() {
  const ref = await api.get('/api/reference');
  state.ref = ref;
  const dict = (list) => Object.fromEntries(list.map((o) => [o.value, o.label]));
  state.labels = {
    request_type: dict(ref.request_types),
    subject_type: dict(ref.subject_types),
    requester_kind: dict(ref.requester_kinds),
    status: dict(ref.statuses),
    urgency: dict(ref.urgencies),
    channel: dict(ref.channels),
    service_category: dict(ref.service_categories),
  };
  [state.entities, state.inboxes, state.services] = await Promise.all([
    api.get('/api/legal-entities'),
    api.get('/api/inboxes'),
    api.get('/api/services'),
  ]);
  renderSystemBox(ref.system);
  const dl = $('#inbox-list');
  dl.innerHTML = '';
  state.inboxes.forEach((i) => dl.append(el('option', { value: i.email })));
}

function renderSystemBox(system) {
  const ocr = system.extraction;
  const rows = [
    { on: ocr.tesseract && ocr.russian_ocr, warn: ocr.tesseract && !ocr.russian_ocr,
      text: ocr.tesseract
        ? (ocr.russian_ocr ? 'OCR: русский + английский' : 'OCR: нет русского пакета')
        : 'OCR недоступен' },
    { on: ocr.poppler, text: ocr.poppler ? 'Сканы PDF распознаются' : 'Сканы PDF не распознаются' },
    { on: system.llm.configured, text: system.llm.configured ? 'ИИ-разбор включён' : 'ИИ-разбор выключен' },
  ];
  const approx = system.calendar.details.filter((y) => y.approximate);
  const years = system.calendar.years;
  rows.push({
    on: approx.length === 0 && years.length > 0,
    warn: approx.length > 0,
    text: years.length
      ? `Календарь: ${years[0]}–${years[years.length - 1]}`
      : 'Календарь не загружен',
  });

  $('#sysbox').innerHTML = '';
  rows.forEach((r) => {
    const cls = r.warn ? 'is-warn' : (r.on ? 'is-on' : 'is-off');
    $('#sysbox').append(el('div', { class: 'sysbox__row' },
      el('span', { class: `sysbox__dot ${cls}` }), r.text));
  });
}

/* ── Реестр: фильтры ─────────────────────────────────────────────────── */

const FILTER_GROUPS = [
  { key: 'urgency', title: 'Срочность', dict: 'urgency', source: () => state.ref.urgencies },
  { key: 'status', title: 'Статус', dict: 'status', source: () => state.ref.statuses },
  { key: 'subject_type', title: 'Вид субъекта', dict: 'subject_type', source: () => state.ref.subject_types },
  { key: 'requester_kind', title: 'Кто обращается', dict: 'requester_kind', source: () => state.ref.requester_kinds },
  { key: 'type_group', title: 'Категория обращения', dict: null,
    source: () => Object.entries(state.ref.type_groups).map(([value, label]) => ({ value, label })) },
  { key: 'request_type', title: 'Тип обращения', dict: 'request_type', source: () => state.ref.request_types },
  { key: 'inbox_email', title: 'Ящик получения', dict: null,
    source: () => state.inboxes.map((i) => ({ value: i.email, label: i.label || i.email })) },
  { key: 'legal_entity_id', title: 'Юридическое лицо', dict: null,
    source: () => state.entities.map((e) => ({ value: String(e.id), label: e.short_name || e.name })) },
  { key: 'service_id', title: 'Сервис / бизнес-процесс', dict: null,
    source: () => state.services.map((s) => ({ value: String(s.id), label: s.name })) },
];

function toggleFilter(key, value) {
  const current = state.filters[key] ?? [];
  state.filters[key] = current.includes(value)
    ? current.filter((v) => v !== value)
    : [...current, value];
  if (!state.filters[key].length) delete state.filters[key];
  state.page = 1;
  loadRegistry();
}

function renderFilters(facets = {}) {
  const grid = $('#filters-grid');
  grid.innerHTML = '';
  for (const group of FILTER_GROUPS) {
    const options = group.source();
    if (!options.length) continue;
    const counts = facets[group.key] ?? {};
    const box = el('div', { class: 'fgroup' }, el('div', { class: 'fgroup__title' }, group.title));
    const list = el('div', { class: 'fgroup__options' });
    for (const opt of options) {
      const active = (state.filters[group.key] ?? []).includes(opt.value);
      const n = counts[opt.value];
      // Пустые значения показываем только если они уже выбраны — иначе список
      // из 26 типов обращений перегружает панель.
      if (!active && group.key === 'request_type' && !n) continue;
      list.append(el('button', {
        class: `fopt ${active ? 'is-on' : ''}`,
        onClick: () => toggleFilter(group.key, opt.value),
      }, opt.label, n ? el('span', { class: 'fopt__n' }, n) : null));
    }
    box.append(list);
    grid.append(box);
  }
  renderActiveChips();
}

function renderActiveChips() {
  const box = $('#active-chips');
  box.innerHTML = '';
  for (const [key, values] of Object.entries(state.filters)) {
    const group = FILTER_GROUPS.find((g) => g.key === key);
    for (const v of [].concat(values)) {
      let text = v;
      if (group) {
        const opt = group.source().find((o) => o.value === v);
        text = opt ? opt.label : v;
      } else if (key === 'flag') {
        text = v === 'red' ? 'Красный флажок' : v === 'blue' ? 'Синий флажок' : v;
      } else if (key === 'overdue') text = 'Просроченные';
      else if (key === 'open_only') text = 'Только в работе';
      else if (key === 'q') text = `Поиск: ${v}`;
      box.append(el('span', { class: 'chip' },
        `${group ? group.title + ': ' : ''}${text}`,
        el('button', {
          title: 'Убрать',
          onClick: () => {
            const cur = state.filters[key];
            if (Array.isArray(cur)) {
              state.filters[key] = cur.filter((x) => x !== v);
              if (!state.filters[key].length) delete state.filters[key];
            } else delete state.filters[key];
            if (key === 'q') $('#f-q').value = '';
            state.page = 1;
            loadRegistry();
          },
        }, '×')));
    }
  }
}

function buildQuery() {
  const p = new URLSearchParams();
  for (const [key, value] of Object.entries(state.filters)) {
    for (const v of [].concat(value)) p.append(key, v);
  }
  p.set('sort', state.sort);
  p.set('order', state.order);
  p.set('page', state.page);
  p.set('page_size', state.pageSize);
  return p.toString();
}

/* ── Реестр: отрисовка ───────────────────────────────────────────────── */

async function loadRegistry() {
  const [page, stats] = await Promise.all([
    guard(() => api.get(`/api/requests?${buildQuery()}`)),
    guard(() => api.get('/api/requests/stats')),
  ]);
  if (!page) return;
  state.lastPage = page;
  renderTiles(stats);
  renderFilters(page.facets);
  renderTable(page);
  $('#nav-count').textContent = stats ? stats.open : '';
  $('#registry-sub').textContent =
    `Найдено ${page.total} ${plural(page.total, 'обращение', 'обращения', 'обращений')}`
    + (stats ? ` · в работе ${stats.open} · просрочено ${stats.overdue}` : '');
}

function renderTiles(stats) {
  if (!stats) return;
  const tiles = [
    { key: 'overdue', label: 'Просрочено', value: stats.overdue, tone: 'red',
      filter: { urgency: ['OVERDUE'], open_only: ['true'] } },
    { key: 'due_today', label: 'Истекает сегодня', value: stats.due_today, tone: 'orange',
      filter: { urgency: ['TODAY'], open_only: ['true'] } },
    { key: 'due_week', label: 'Срок в ближайшую неделю', value: stats.due_week, tone: 'amber',
      filter: { urgency: ['TODAY', 'CRITICAL', 'HIGH', 'MEDIUM'], open_only: ['true'] } },
    { key: 'rkn', label: 'Роскомнадзор', value: stats.rkn, tone: 'blue',
      filter: { type_group: ['RKN'], open_only: ['true'] } },
    { key: 'blue', label: 'Спорные (синий)', value: stats.blue, tone: 'blue',
      filter: { flag: ['blue'], open_only: ['true'] } },
    { key: 'red', label: 'Не про ПД (красный)', value: stats.red, tone: 'red',
      filter: { flag: ['red'], open_only: ['true'] } },
    { key: 'open', label: 'Всего в работе', value: stats.open, tone: '',
      filter: { open_only: ['true'] } },
  ];
  const box = $('#tiles');
  box.innerHTML = '';
  for (const t of tiles) {
    box.append(el('div', {
      class: `tile ${t.tone ? `tile--${t.tone}` : ''}`,
      onClick: () => { state.filters = { ...t.filter }; state.page = 1; $('#f-q').value = ''; loadRegistry(); },
    }, el('div', { class: 'tile__value' }, t.value ?? 0),
       el('div', { class: 'tile__label' }, t.label)));
  }
}

function renderTable(page) {
  const tbody = $('#tbody');
  tbody.innerHTML = '';
  $('#empty').hidden = page.items.length > 0;

  for (const r of page.items) {
    const tone = URGENCY_TONE[r.urgency] ?? 'slate';
    // У закрытого обращения срок остаётся в карточке как факт, но подпись
    // «Без срока» рядом с датой сбивала бы с толку — пишем, что оно снято с контроля.
    const closed = TERMINAL_STATUSES.has(r.status);
    const dueCell = r.due_date
      ? el('div', { class: 'due' },
          el('span', { class: `badge badge--${closed ? 'slate' : tone} due__date` },
            fmtDate(r.due_date)),
          el('span', { class: 'due__left' },
            closed ? 'снято с контроля' : label('urgency', r.urgency)))
      : el('span', { class: 'muted' }, '—');

    const row = el('tr', {
      class: state.selectedId === r.id ? 'is-selected' : '',
      onClick: () => openRequest(r.id),
    },
      el('td', {}, el('div', { class: 'flagdots' },
        r.has_red_flag ? el('span', { class: 'flagdot flagdot--red', title: 'Не относится к персональным данным' }) : null,
        r.has_blue_flag ? el('span', { class: 'flagdot flagdot--blue', title: 'Есть спорные моменты' }) : null)),
      el('td', { class: 'td-num' }, r.reg_number,
        el('span', { class: 'td-sub' }, fmtDate(r.received_at))),
      el('td', {}, dueCell),
      el('td', { class: 'td-truncate', title: label('request_type', r.request_type) },
        label('request_type', r.request_type),
        r.secondary_types?.length
          ? el('span', { class: 'td-sub' }, `+ ещё ${r.secondary_types.length}`) : null),
      el('td', {}, r.subject_type === 'UNKNOWN'
        ? el('span', { class: 'muted' }, '—')
        : el('span', { class: 'badge badge--slate' }, label('subject_type', r.subject_type))),
      el('td', { class: 'td-truncate' },
        r.requester_name || el('span', { class: 'muted' }, '—'),
        el('span', { class: 'td-sub' }, r.requester_email || label('requester_kind', r.requester_kind))),
      el('td', { class: 'td-truncate', title: r.inbox_email }, r.inbox_email || '—'),
      el('td', { class: 'td-truncate', title: r.legal_entity_mentioned },
        entityName(r) || el('span', { class: 'muted' }, '—')),
      el('td', { class: 'td-truncate', title: r.service_mentioned },
        r.service_mentioned || el('span', { class: 'muted' }, '—')),
      el('td', {}, el('span', { class: `badge badge--${STATUS_TONE[r.status] ?? 'slate'}` },
        label('status', r.status))),
    );
    tbody.append(row);
  }

  const pager = $('#pager');
  pager.innerHTML = '';
  const pages = Math.max(1, Math.ceil(page.total / page.page_size));
  if (pages > 1) {
    pager.append(
      el('button', {
        class: 'btn btn--ghost btn--sm', disabled: page.page <= 1,
        onClick: () => { state.page -= 1; loadRegistry(); },
      }, '← Назад'),
      el('span', {}, `Страница ${page.page} из ${pages}`),
      el('button', {
        class: 'btn btn--ghost btn--sm', disabled: page.page >= pages,
        onClick: () => { state.page += 1; loadRegistry(); },
      }, 'Вперёд →'),
    );
  }
}

/* ── Карточка обращения ──────────────────────────────────────────────── */

let currentRequest = null;

async function openRequest(id) {
  const data = await guard(() => api.get(`/api/requests/${id}`));
  if (!data) return;
  currentRequest = data;
  state.selectedId = id;
  $('#drawer').hidden = false;
  $('#drawer-backdrop').hidden = false;
  document.body.style.overflow = 'hidden';
  renderDrawer(data);
  $$('#tbody tr').forEach((tr) => tr.classList.remove('is-selected'));
}

function closeDrawer() {
  $('#drawer').hidden = true;
  $('#drawer-backdrop').hidden = true;
  document.body.style.overflow = '';
  currentRequest = null;
  state.selectedId = null;
  loadRegistry();
}

$('#drawer-backdrop').addEventListener('click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('#modal-backdrop').hidden) closeModal();
  else if (!$('#drawer').hidden) closeDrawer();
});

function renderDrawer(r) {
  const openFlags = r.flags.filter((f) => !f.resolved_at);
  const tabs = [
    { key: 'overview', title: 'Обращение' },
    { key: 'deadlines', title: 'Сроки', n: r.deadlines?.deadlines?.length },
    { key: 'flags', title: 'Флажки', n: openFlags.length || null },
    { key: 'attachments', title: 'Вложения', n: r.attachments.length || null },
    { key: 'draft', title: 'Ответ', n: r.drafts.length || null },
    { key: 'history', title: 'История' },
  ];

  const inner = $('#drawer-inner');
  inner.innerHTML = '';
  inner.append(
    el('div', { class: 'dhead' },
      el('div', {},
        el('h2', {}, r.subject_line || 'Без темы'),
        el('div', { class: 'dhead__meta' },
          `${r.reg_number} · поступило ${fmtDateTime(r.received_at)}`)),
      el('button', { class: 'icon-btn', onClick: closeDrawer, title: 'Закрыть (Esc)' }, '×')),
    el('div', { class: 'dtabs' }, ...tabs.map((t) => el('button', {
      class: `dtab ${t.key === 'overview' ? 'is-active' : ''}`,
      dataset: { pane: t.key },
      onClick: (e) => {
        $$('.dtab', inner).forEach((b) => b.classList.toggle('is-active', b === e.currentTarget));
        $$('.dpane', inner).forEach((p) => p.classList.toggle('is-active', p.dataset.pane === t.key));
      },
    }, t.title, t.n ? el('span', { class: 'dtab__n' }, t.n) : null))),
    paneOverview(r), paneDeadlines(r), paneFlags(r),
    paneAttachments(r), paneDraft(r), paneHistory(r),
  );
}

function selectField(labelText, dict, options, value, onChange) {
  const sel = el('select', { onChange: (e) => onChange(e.target.value) });
  for (const o of options) {
    sel.append(el('option', { value: o.value, selected: o.value === value }, o.label));
  }
  return el('label', { class: 'field' }, el('span', {}, labelText), sel);
}

async function patchRequest(payload, { reopenTab } = {}) {
  const updated = await guard(() => api.patch(`/api/requests/${currentRequest.id}`, payload));
  if (!updated) return;
  currentRequest = updated;
  renderDrawer(updated);
  if (reopenTab) {
    const btn = $$(`.dtab`).find((b) => b.dataset.pane === reopenTab);
    btn?.click();
  }
  toast('Сохранено', 'ok');
}

function paneOverview(r) {
  const entityOptions = [{ value: '', label: '— не выбрано —' },
    ...state.entities.map((e) => ({ value: String(e.id), label: e.short_name || e.name }))];
  const serviceOptions = [{ value: '', label: '— не выбрано —' },
    ...state.services.map((s) => ({ value: String(s.id), label: s.name }))];

  const grouped = {};
  for (const t of state.ref.request_types) {
    (grouped[t.group] ??= []).push(t);
  }
  const typeSelect = el('select', {
    onChange: (e) => patchRequest({ request_type: e.target.value }),
  });
  for (const [group, items] of Object.entries(grouped)) {
    const og = el('optgroup', { label: state.ref.type_groups[group] ?? group });
    items.forEach((t) => og.append(el('option',
      { value: t.value, selected: t.value === r.request_type }, t.label)));
    typeSelect.append(og);
  }

  const confidence = Math.round((r.classification_confidence ?? 0) * 100);
  const unconfirmed = (r.unconfirmed_fields ?? []).length > 0;

  return el('div', { class: 'dpane is-active', dataset: { pane: 'overview' } },
    unconfirmed ? el('div', { class: 'warnbox' },
      el('strong', {}, 'Требует подтверждения. '),
      `Часть полей заполнена автоматически (уверенность ${confidence}%). `,
      'Проверьте тип обращения и вид субъекта — от них зависит срок.') : null,

    r.summary ? el('div', { class: 'card card--blue' },
      el('div', { class: 'card__title' }, 'Суть обращения'),
      el('div', { class: 'card__body' }, r.summary)) : null,

    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Квалификация'),
      el('div', { class: 'field' }, el('span', {}, 'Тип обращения'), typeSelect),
      el('div', { class: 'field-row' },
        selectField('Вид субъекта персональных данных', 'subject_type',
          state.ref.subject_types, r.subject_type,
          (v) => patchRequest({ subject_type: v })),
        selectField('Кто обращается', 'requester_kind',
          state.ref.requester_kinds, r.requester_kind,
          (v) => patchRequest({ requester_kind: v }))),
      el('div', { class: 'field-row' },
        selectField('Юридическое лицо', null, entityOptions,
          r.legal_entity_id ? String(r.legal_entity_id) : '',
          (v) => patchRequest({ legal_entity_id: v ? Number(v) : null })),
        selectField('Сервис / бизнес-процесс', null, serviceOptions,
          r.service_id ? String(r.service_id) : '',
          (v) => patchRequest({ service_id: v ? Number(v) : null }))),
      r.legal_entity_mentioned && !r.legal_entity_id
        ? el('p', { class: 'muted', style: 'font-size:12px;margin:-4px 0 12px' },
            `В тексте упомянуто: «${r.legal_entity_mentioned}» — в справочнике не найдено.`)
        : null),

    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Обработка'),
      el('div', { class: 'field-row' },
        selectField('Статус', 'status', state.ref.statuses, r.status,
          (v) => patchRequest({ status: v })),
        el('label', { class: 'field' }, el('span', {}, 'Исполнитель'),
          el('input', {
            type: 'text', value: r.assignee ?? '',
            onChange: (e) => patchRequest({ assignee: e.target.value }),
          }))),
      el('label', { class: 'checkline is-critical' },
        el('input', {
          type: 'checkbox', checked: !!r.identity_confirmed_at,
          onChange: (e) => patchRequest({ identity_confirmed: e.target.checked }),
        }),
        el('span', {}, 'Личность / полномочия заявителя подтверждены',
          el('span', { class: 'checkline__ref' },
            r.identity_confirmed_at
              ? `Подтверждено ${fmtDateTime(r.identity_confirmed_at)} — срок считается от этой даты.`
              : 'ч. 4 ст. 14 ФЗ-152. Пока не подтверждено, срок считается от даты письма.'))),
      el('label', { class: 'checkline' },
        el('input', {
          type: 'checkbox', checked: !!r.extension_applied,
          onChange: (e) => patchRequest({ extension_applied: e.target.checked }),
        }),
        el('span', {}, 'Срок продлён',
          el('span', { class: 'checkline__ref' },
            'ст. 20 ч. 1 и ч. 4 ФЗ-152: не более чем на 5 рабочих дней и только при '
            + 'направлении мотивированного уведомления с указанием причин.'))),
      el('label', { class: 'field', style: 'margin-top:12px' },
        el('span', {}, 'Срок из документа (приоритетнее расчётного)'),
        el('input', {
          type: 'date', value: r.manual_due_date ?? '',
          onChange: (e) => patchRequest(e.target.value
            ? { manual_due_date: e.target.value }
            : { clear_manual_due_date: true }),
        }))),

    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Поступление'),
      el('dl', { class: 'kv' },
        el('dt', {}, 'Ящик получения'), el('dd', {}, r.inbox_email || '—'),
        el('dt', {}, 'Заявитель'), el('dd', {}, r.requester_name || '—'),
        el('dt', {}, 'Email заявителя'), el('dd', {}, r.requester_email || '—'),
        el('dt', {}, 'Телефон'), el('dd', {}, r.requester_phone || '—'),
        el('dt', {}, 'Канал'), el('dd', {}, label('channel', r.channel ?? 'EMAIL')),
        el('dt', {}, 'Определено'), el('dd', {},
          r.classified_by === 'MANUAL' ? 'вручную'
            : `${r.classified_by === 'LLM' ? 'ИИ + правила' : 'правила'}, уверенность ${confidence}%`))),

    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Текст обращения'),
      el('div', { class: 'bodytext' }, r.body_text || '(пусто)')),

    el('div', { class: 'section' },
      el('button', {
        class: 'btn btn--ghost btn--sm',
        onClick: async () => {
          await guard(() => api.post(`/api/requests/${r.id}/reanalyze?use_llm=true`));
          openRequest(r.id);
          toast('Обращение разобрано заново', 'ok');
        },
      }, '↻ Разобрать заново'),
      el('button', {
        class: 'btn btn--danger btn--sm', style: 'margin-left:8px',
        onClick: async () => {
          if (!confirm(`Удалить обращение ${r.reg_number}? Действие необратимо.`)) return;
          await guard(() => api.del(`/api/requests/${r.id}`));
          closeDrawer();
          toast('Обращение удалено', 'ok');
        },
      }, 'Удалить')),
  );
}

function paneDeadlines(r) {
  const d = r.deadlines ?? {};
  const items = d.deadlines ?? [];
  return el('div', { class: 'dpane', dataset: { pane: 'deadlines' } },
    d.summary ? el('div', { class: 'card card--blue' },
      el('div', { class: 'card__title' }, 'Правило расчёта'),
      el('div', { class: 'card__body' }, d.summary)) : null,

    ...(d.warnings ?? []).map((w) => el('div', { class: 'warnbox' }, w)),

    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Контрольные точки'),
      ...items.map((x) => {
        const isPrimary = d.primary && x.code === d.primary.code;
        const tone = x.unit === 'IMMEDIATE' ? 'amber' : URGENCY_TONE[x.urgency] ?? 'slate';
        const when = x.unit === 'IMMEDIATE' ? 'с момента обращения'
          : x.unit === 'HOURS' ? fmtDateTime(x.due_at)
          : fmtDate(x.due_date);
        return el('div', { class: `card card--${tone === 'slate' ? 'blue' : tone}` },
          el('div', { class: 'card__head' },
            el('div', { class: 'card__title' }, x.title,
              isPrimary ? el('span', { class: 'badge badge--blue', style: 'margin-left:8px' }, 'основной срок') : null),
            el('span', { class: `badge badge--${tone}` }, when)),
          el('div', { class: 'card__body' },
            x.unit !== 'IMMEDIATE' && x.working_days_left !== null
              ? el('div', {}, daysLeftText(x.working_days_left)) : null,
            el('div', { class: 'muted', style: 'font-size:11.5px;margin-top:2px' },
              `${x.amount ? x.amount + ' ' : ''}${
                { WORKING_DAYS: 'рабочих дней', CALENDAR_DAYS: 'календарных дней',
                  HOURS: 'часов', IMMEDIATE: '' }[x.unit] ?? ''} · ${x.counted_from_label}`
              + (x.counted_from ? ` (${fmtDate(x.counted_from)})` : '')),
            x.note ? el('div', { style: 'margin-top:6px' }, x.note) : null,
            x.extended_due_date
              ? el('div', { style: 'margin-top:6px' },
                  `С продлением: ${fmtDate(x.extended_due_date)}. ${x.extension_ref}`)
              : null,
            x.manual ? el('div', { style: 'margin-top:6px' }, 'Дата задана вручную из документа.') : null),
          el('div', { class: 'card__ref' }, x.legal_ref));
      })),
    items.length === 0
      ? el('p', { class: 'muted' }, 'Для этого типа обращения нормативный срок не установлен.')
      : null,
  );
}

function paneFlags(r) {
  const pane = el('div', { class: 'dpane', dataset: { pane: 'flags' } });
  const red = r.flags.filter((f) => f.level === 'RED');
  const blue = r.flags.filter((f) => f.level === 'BLUE');

  const renderGroup = (title, list, tone, hint) => {
    if (!list.length) return null;
    return el('div', { class: 'section' },
      el('div', { class: 'section__title' }, title),
      hint ? el('p', { class: 'muted', style: 'font-size:12px;margin:-4px 0 10px' }, hint) : null,
      ...list.map((f) => el('div', { class: `card card--${tone} ${f.resolved_at ? 'is-resolved' : ''}` },
        el('div', { class: 'card__head' },
          el('div', { class: 'card__title' }, f.code),
          el('span', { class: 'badge badge--slate' },
            f.source === 'MANUAL' ? 'вручную' : f.source === 'LLM' ? 'ИИ' : 'правило')),
        el('div', { class: 'card__body' }, f.reason),
        f.resolved_at
          ? el('div', { class: 'card__ref' },
              `Снят ${fmtDateTime(f.resolved_at)}${f.resolved_by ? ` · ${f.resolved_by}` : ''}`
              + (f.resolution ? ` — ${f.resolution}` : ''))
          : null,
        el('div', { style: 'margin-top:9px' },
          f.resolved_at
            ? el('button', {
                class: 'btn btn--ghost btn--sm',
                onClick: async () => {
                  await guard(() => api.post(
                    `/api/requests/${r.id}/flags/${f.id}/resolve`, { reopen: true }));
                  openRequest(r.id);
                },
              }, 'Вернуть в работу')
            : el('button', {
                class: 'btn btn--ghost btn--sm',
                onClick: () => resolveFlagDialog(r.id, f),
              }, 'Снять флажок')))));
  };

  pane.append(
    renderGroup('🔴 Не относится к персональным данным', red, 'red',
      'Красный флажок означает: обращение вне периметра ФЗ-152. Передайте его '
      + 'профильной команде и закройте статусом «Не про ПД».'),
    renderGroup('🔵 Спорные моменты', blue, 'blue',
      'Синий флажок — вопрос, который должен решить DPO. Снимайте с описанием '
      + 'принятого решения: запись останется в истории обращения.'),
    !r.flags.length
      ? el('p', { class: 'muted' }, 'Флажков нет — обращение выглядит однозначным.')
      : null,
    el('button', {
      class: 'btn btn--ghost btn--sm',
      onClick: () => addFlagDialog(r.id),
    }, '＋ Поставить флажок вручную'),
  );
  return pane;
}

function addFlagDialog(requestId) {
  const level = el('select', {},
    el('option', { value: 'BLUE' }, '🔵 Синий — спорный момент'),
    el('option', { value: 'RED' }, '🔴 Красный — не относится к персональным данным'));
  const code = el('input', { type: 'text', placeholder: 'Короткий код, например CHECK_SOURCE' });
  const reason = el('textarea', { rows: 4, placeholder: 'В чём именно вопрос и что нужно решить' });
  openModal('Новый флажок', el('div', {},
    el('label', { class: 'field' }, el('span', {}, 'Тип флажка'), level),
    el('label', { class: 'field' }, el('span', {}, 'Код'), code),
    el('label', { class: 'field' }, el('span', {}, 'Описание'), reason),
  ), [{
    label: 'Поставить', primary: true,
    onClick: async () => {
      if (!reason.value.trim()) { toast('Опишите суть флажка', 'error'); return; }
      await guard(() => api.post(`/api/requests/${requestId}/flags`, {
        level: level.value, code: code.value.trim() || 'MANUAL', reason: reason.value.trim(),
      }));
      closeModal();
      openRequest(requestId);
      toast('Флажок поставлен', 'ok');
    },
  }]);
}

function resolveFlagDialog(requestId, flag) {
  const resolution = el('textarea', { rows: 4, placeholder: 'Какое решение принято и почему' });
  const who = el('input', { type: 'text', placeholder: 'Кто снял', value: 'DPO' });
  openModal('Снять флажок', el('div', {},
    el('p', { class: 'muted', style: 'margin-top:0' }, flag.reason),
    el('label', { class: 'field' }, el('span', {}, 'Принятое решение'), resolution),
    el('label', { class: 'field' }, el('span', {}, 'Кто снял'), who),
  ), [{
    label: 'Снять', primary: true,
    onClick: async () => {
      await guard(() => api.post(`/api/requests/${requestId}/flags/${flag.id}/resolve`, {
        resolution: resolution.value.trim(), resolved_by: who.value.trim() || 'DPO',
      }));
      closeModal();
      openRequest(requestId);
      toast('Флажок снят', 'ok');
    },
  }]);
}

function paneAttachments(r) {
  const pane = el('div', { class: 'dpane', dataset: { pane: 'attachments' } });
  const input = el('input', {
    type: 'file', multiple: true, style: 'display:none',
    onChange: async (e) => {
      const fd = new FormData();
      [...e.target.files].forEach((f) => fd.append('files', f));
      fd.append('reanalyze_after', 'true');
      const res = await guard(() => api.form(`/api/requests/${r.id}/attachments`, fd));
      if (res) { toast(`Добавлено файлов: ${res.length}`, 'ok'); openRequest(r.id); }
    },
  });

  pane.append(
    el('div', { style: 'margin-bottom:14px' },
      el('button', { class: 'btn btn--ghost btn--sm', onClick: () => input.click() },
        '⇪ Добавить файлы'), input),
    ...r.attachments.map((a) => el('div', {
      class: `card ${a.needs_review ? 'card--amber' : ''}`,
    },
      el('div', { class: 'card__head' },
        el('div', { class: 'card__title' }, a.filename),
        el('span', { class: 'badge badge--slate' }, a.extraction_method || '—')),
      el('div', { class: 'card__body' },
        `${Math.round(a.size_bytes / 1024)} КБ`
        + (a.page_count ? ` · ${a.page_count} стр.` : '')
        + ` · извлечено ${a.char_count} симв.`),
      a.extraction_error ? el('div', { class: 'warnbox', style: 'margin-top:8px' }, a.extraction_error) : null,
      a.needs_review ? el('div', { class: 'warnbox', style: 'margin-top:8px' },
        el('strong', {}, 'Нужна вычитка. '),
        'Текст распознан плохо или не распознан — откройте и поправьте вручную.') : null,
      el('div', { style: 'margin-top:9px;display:flex;gap:6px' },
        el('button', {
          class: 'btn btn--ghost btn--sm',
          onClick: () => viewAttachment(r.id, a.id),
        }, 'Показать текст'),
        el('a', {
          class: 'btn btn--ghost btn--sm',
          href: `/api/requests/${r.id}/attachments/${a.id}/download`,
        }, 'Скачать')))),
    !r.attachments.length ? el('p', { class: 'muted' }, 'Вложений нет.') : null,
  );
  return pane;
}

async function viewAttachment(requestId, attachmentId) {
  const a = await guard(() => api.get(`/api/requests/${requestId}/attachments/${attachmentId}`));
  if (!a) return;
  const ta = el('textarea', { rows: 18, class: 'bodytext', style: 'width:100%' });
  ta.value = a.extracted_text || '';
  openModal(`Текст: ${a.filename}`, el('div', {},
    el('p', { class: 'muted', style: 'margin-top:0' },
      `Способ извлечения: ${a.extraction_method}. Поправьте текст, если распознавание ошиблось — `
      + 'обращение будет разобрано заново.'),
    el('label', { class: 'field' }, el('span', {}, 'Распознанный текст'), ta),
  ), [{
    label: 'Сохранить и переразобрать', primary: true,
    onClick: async () => {
      const fd = new FormData();
      fd.append('text', ta.value);
      await guard(() => api.formPatch(
        `/api/requests/${requestId}/attachments/${attachmentId}`, fd));
      closeModal();
      openRequest(requestId);
      toast('Текст сохранён, обращение переразобрано', 'ok');
    },
  }]);
}

function paneDraft(r) {
  const pane = el('div', { class: 'dpane', dataset: { pane: 'draft' } });
  const latest = r.drafts[r.drafts.length - 1];

  const matches = r.template_matches ?? [];
  const tplSelect = el('select', {},
    el('option', { value: '' }, matches.length
      ? `Автоподбор: ${matches[0].title}` : 'Автоподбор (типовые ответы не найдены)'),
    ...matches.map((m) => el('option', { value: String(m.template_id) },
      `${m.title} — совпадение ${m.score}`)));

  const instructions = el('textarea', {
    rows: 2, placeholder: 'Дополнительные указания к тексту (необязательно)',
  });

  pane.append(
    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Сформировать ответ'),
      matches.length === 0 ? el('div', { class: 'warnbox' },
        'Подходящий типовой ответ не найден. Будет собрана структурная заготовка по '
        + 'типу обращения. Загрузите свои типовые ответы в разделе «Типовые ответы».') : null,
      el('label', { class: 'field' }, el('span', {}, 'Основа ответа'), tplSelect),
      el('label', { class: 'field' }, el('span', {}, 'Указания'), instructions),
      el('button', {
        class: 'btn btn--primary btn--sm',
        onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          btn.textContent = 'Готовлю…';
          await guard(() => api.post(`/api/requests/${r.id}/draft`, {
            template_id: tplSelect.value ? Number(tplSelect.value) : null,
            use_llm: true,
            instructions: instructions.value.trim(),
          }));
          openRequest(r.id);
          $$('.dtab').find((b) => b.dataset.pane === 'draft')?.click();
          toast('Драфт готов', 'ok');
        },
      }, 'Сформировать драфт')),

    latest ? el('div', { class: 'section' },
      el('div', { class: 'section__title' },
        `Драфт от ${fmtDateTime(latest.created_at)} · ${latest.generator}`),
      latest.unresolved_placeholders?.length ? el('div', { class: 'warnbox' },
        el('strong', {}, 'Не заполнены поля: '),
        latest.unresolved_placeholders.join(', '),
        '. Они оставлены в тексте в квадратных скобках — заполните перед отправкой.') : null,
      (() => {
        const ta = el('textarea', { rows: 20, class: 'bodytext', style: 'width:100%' });
        ta.value = latest.body;
        pane._draftArea = ta;
        return el('label', { class: 'field' }, el('span', {}, 'Текст ответа'), ta);
      })(),
      el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
        el('button', {
          class: 'btn btn--ghost btn--sm',
          onClick: async () => {
            await guard(() => api.patch(`/api/drafts/${latest.id}`,
              { body: pane._draftArea.value }));
            toast('Драфт сохранён', 'ok');
          },
        }, 'Сохранить'),
        el('button', {
          class: 'btn btn--ghost btn--sm',
          onClick: async () => {
            await navigator.clipboard.writeText(pane._draftArea.value);
            toast('Текст скопирован в буфер обмена', 'ok');
          },
        }, 'Копировать'),
        el('button', {
          class: 'btn btn--ghost btn--sm',
          onClick: () => {
            const blob = new Blob([pane._draftArea.value], { type: 'text/plain;charset=utf-8' });
            const a = el('a', { href: URL.createObjectURL(blob), download: `${r.reg_number}.txt` });
            document.body.append(a); a.click(); a.remove();
          },
        }, 'Скачать .txt'),
        el('button', {
          class: 'btn btn--primary btn--sm',
          onClick: () => patchRequest({ status: 'ANSWERED' }, { reopenTab: 'draft' }),
        }, 'Отметить «Ответ направлен»')),
    ) : null,

    latest?.checklist?.length ? el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Проверить перед отправкой'),
      ...latest.checklist.map((c, i) => el('label', {
        class: `checkline ${c.critical ? 'is-critical' : ''}`,
      },
        el('input', {
          type: 'checkbox', checked: !!c.done,
          onChange: async (e) => {
            const list = latest.checklist.map((x, j) =>
              j === i ? { ...x, done: e.target.checked } : x);
            await guard(() => api.patch(`/api/drafts/${latest.id}`, { checklist: list }));
          },
        }),
        el('span', {}, c.critical ? '⚠ ' : '', c.text,
          c.ref ? el('span', { class: 'checkline__ref' }, c.ref) : null))),
    ) : null,
  );
  return pane;
}

function paneHistory(r) {
  return el('div', { class: 'dpane', dataset: { pane: 'history' } },
    el('div', { class: 'timeline' },
      ...r.events.map((e) => el('div', { class: 'tl-item' },
        el('div', { class: 'tl-item__time' }, `${fmtDateTime(e.created_at)} · ${e.actor}`),
        el('div', {}, e.message)))),
    !r.events.length ? el('p', { class: 'muted' }, 'Событий нет.') : null);
}

/* ── Новое обращение ─────────────────────────────────────────────────── */

function newRequestDialog() {
  const inbox = el('input', { type: 'text', list: 'inbox-list', placeholder: 'privacy@company.ru' });
  const from = el('input', { type: 'text', placeholder: 'ivanov@mail.ru' });
  const name = el('input', { type: 'text', placeholder: 'Иванов Иван Иванович' });
  const subject = el('input', { type: 'text', placeholder: 'Тема письма' });
  const received = el('input', { type: 'datetime-local' });
  const body = el('textarea', { rows: 9, placeholder: 'Текст обращения' });
  const files = el('input', { type: 'file', multiple: true });

  openModal('Новое обращение', el('div', {},
    el('div', { class: 'field-row' },
      el('label', { class: 'field' }, el('span', {}, 'Ящик получения'), inbox),
      el('label', { class: 'field' }, el('span', {}, 'Дата поступления'), received)),
    el('div', { class: 'field-row' },
      el('label', { class: 'field' }, el('span', {}, 'От кого (email)'), from),
      el('label', { class: 'field' }, el('span', {}, 'ФИО заявителя'), name)),
    el('label', { class: 'field' }, el('span', {}, 'Тема'), subject),
    el('label', { class: 'field' }, el('span', {}, 'Текст обращения'), body),
    el('label', { class: 'field' }, el('span', {}, 'Файлы (PDF, фото, DOCX, EML)'), files),
  ), [{
    label: 'Зарегистрировать', primary: true,
    onClick: async () => {
      if (!body.value.trim() && !files.files.length) {
        toast('Нужен текст обращения или хотя бы один файл', 'error');
        return;
      }
      const fd = new FormData();
      [...files.files].forEach((f) => fd.append('files', f));
      fd.append('inbox_email', inbox.value.trim());
      fd.append('requester_email', from.value.trim());
      fd.append('requester_name', name.value.trim());
      fd.append('subject_line', subject.value.trim());
      fd.append('body_text', body.value);
      if (received.value) fd.append('received_at', received.value);
      fd.append('use_llm', 'true');
      const created = await guard(() => api.form('/api/requests/upload', fd));
      if (!created) return;
      closeModal();
      await loadRegistry();
      openRequest(created.id);
      toast(`Зарегистрировано: ${created.reg_number}`, 'ok');
    },
  }]);
}

/* ── Разбор документа ────────────────────────────────────────────────── */

function renderFileList() {
  const list = $('#filelist');
  list.innerHTML = '';
  state.analyzeFiles.forEach((f, i) => {
    list.append(el('li', {},
      el('span', {}, f.name),
      el('span', { class: 'muted' }, `${Math.round(f.size / 1024)} КБ`),
      el('button', {
        class: 'icon-btn', style: 'width:22px;height:22px;font-size:13px',
        onClick: () => { state.analyzeFiles.splice(i, 1); renderFileList(); },
      }, '×')));
  });
}

function setupAnalyzeView() {
  const zone = $('#dropzone');
  const input = $('#file-input');

  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    state.analyzeFiles.push(...input.files);
    input.value = '';
    renderFileList();
  });
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.remove('is-over');
  }));
  zone.addEventListener('drop', (e) => {
    state.analyzeFiles.push(...e.dataTransfer.files);
    renderFileList();
  });

  $('#btn-analyze').addEventListener('click', runAnalyze);
  $('#btn-register').addEventListener('click', registerAnalyzed);
}

async function runAnalyze() {
  const btn = $('#btn-analyze');
  const text = $('#a-text').value.trim();
  if (!state.analyzeFiles.length && !text) {
    toast('Загрузите файл или вставьте текст', 'error');
    return;
  }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Разбираю…';

  let result;
  if (state.analyzeFiles.length) {
    const fd = new FormData();
    state.analyzeFiles.forEach((f) => fd.append('files', f));
    fd.append('subject_line', $('#a-subject').value);
    fd.append('from_email', $('#a-from').value);
    fd.append('inbox_email', $('#a-inbox').value);
    fd.append('body_text', text);
    fd.append('use_llm', 'true');
    result = await guard(() => api.form('/api/analyze/files', fd));
  } else {
    result = await guard(() => api.post('/api/analyze', {
      text, subject_line: $('#a-subject').value,
      from_email: $('#a-from').value, inbox_email: $('#a-inbox').value, use_llm: true,
    }));
  }

  btn.disabled = false;
  btn.textContent = 'Разобрать';
  if (!result) return;

  state.analyzeResult = result;
  if (result.extracted_text) $('#a-text').value = result.extracted_text;
  $('#btn-register').disabled = false;
  renderAnalyzeResult(result);
}

function renderAnalyzeResult(res) {
  const c = res.classification;
  const d = res.deadlines;
  const box = $('#analyze-result');
  box.innerHTML = '';

  const red = c.flags.filter((f) => f.level === 'RED');
  const blue = c.flags.filter((f) => f.level === 'BLUE');
  const tone = URGENCY_TONE[d.urgency] ?? 'slate';

  box.append(
    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Квалификация'),
      el('dl', { class: 'kv' },
        el('dt', {}, 'Тип обращения'),
        el('dd', {}, el('strong', {}, label('request_type', c.request_type)),
          ` · уверенность ${Math.round(c.confidence * 100)}%`),
        el('dt', {}, 'Кто обращается'), el('dd', {}, label('requester_kind', c.requester_kind)),
        el('dt', {}, 'Вид субъекта'), el('dd', {}, label('subject_type', c.subject_type)),
        el('dt', {}, 'Юридическое лицо'), el('dd', {}, res.legal_entity.name || '— не определено —'),
        el('dt', {}, 'Сервис / процесс'), el('dd', {}, res.service.name || '— не определено —'),
        c.secondary_types.length ? el('dt', {}, 'Ещё требования') : null,
        c.secondary_types.length
          ? el('dd', {}, c.secondary_types.map((t) => label('request_type', t)).join('; ')) : null)),

    el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Срок ответа'),
      d.due_date
        ? el('div', { class: `card card--${tone === 'slate' ? 'blue' : tone}` },
            el('div', { class: 'card__head' },
              el('div', { class: 'card__title' }, d.primary?.title ?? 'Ответ'),
              el('span', { class: `badge badge--${tone}` }, fmtDate(d.due_date))),
            el('div', { class: 'card__body' }, d.summary),
            el('div', { class: 'card__ref' }, d.primary?.legal_ref ?? ''))
        : el('p', { class: 'muted' }, 'Нормативный срок для этого типа не установлен.'),
      ...(d.warnings ?? []).map((w) => el('div', { class: 'warnbox' }, w))),

    red.length ? el('div', { class: 'section' },
      el('div', { class: 'section__title' }, '🔴 Не относится к персональным данным'),
      ...red.map((f) => el('div', { class: 'card card--red' },
        el('div', { class: 'card__title' }, f.code),
        el('div', { class: 'card__body' }, f.reason)))) : null,

    blue.length ? el('div', { class: 'section' },
      el('div', { class: 'section__title' }, '🔵 Спорные моменты'),
      ...blue.map((f) => el('div', { class: 'card card--blue' },
        el('div', { class: 'card__title' }, f.code),
        el('div', { class: 'card__body' }, f.reason)))) : null,

    res.files?.length ? el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'Обработанные файлы'),
      ...res.files.map((f) => el('div', { class: `card ${f.error ? 'card--amber' : ''}` },
        el('div', { class: 'card__head' },
          el('div', { class: 'card__title' }, f.filename),
          el('span', { class: 'badge badge--slate' }, f.method)),
        el('div', { class: 'card__body' },
          `${f.chars} симв.${f.pages ? ` · ${f.pages} стр.` : ''}`),
        f.error ? el('div', { class: 'warnbox', style: 'margin-top:8px' }, f.error) : null,
        ...(f.warnings ?? []).map((w) => el('div', { class: 'warnbox', style: 'margin-top:8px' }, w))))) : null,

    c.signals?.length ? el('div', { class: 'section' },
      el('div', { class: 'section__title' }, 'На чём основано решение'),
      el('div', { class: 'card' }, el('div', { class: 'card__body' },
        c.signals.slice(0, 12).map((s) =>
          `«${s.matched}» → ${s.key} (+${s.weight})`).join('\n')),
      )) : null,
  );
}

async function registerAnalyzed() {
  const fd = new FormData();
  state.analyzeFiles.forEach((f) => fd.append('files', f));
  fd.append('inbox_email', $('#a-inbox').value);
  fd.append('requester_email', $('#a-from').value);
  fd.append('subject_line', $('#a-subject').value);
  fd.append('body_text', $('#a-text').value);
  fd.append('use_llm', 'true');
  const created = await guard(() => api.form('/api/requests/upload', fd));
  if (!created) return;
  toast(`Зарегистрировано: ${created.reg_number}`, 'ok');
  state.analyzeFiles = [];
  renderFileList();
  $('#a-text').value = '';
  $('#a-subject').value = '';
  $('#btn-register').disabled = true;
  switchView('registry');
  await loadRegistry();
  openRequest(created.id);
}

/* ── Типовые ответы ──────────────────────────────────────────────────── */

async function loadTemplates() {
  const templates = await guard(() => api.get('/api/templates'));
  if (!templates) return;
  const grid = $('#tpl-grid');
  grid.innerHTML = '';
  if (!templates.length) {
    grid.append(el('p', { class: 'muted' },
      'Типовых ответов пока нет. Загрузите файлы или напишите шаблон вручную — '
      + 'система будет подставлять в них данные обращения и рассчитанные сроки.'));
    return;
  }
  for (const t of templates) {
    grid.append(el('div', { class: `tpl-card ${t.is_active ? '' : 'is-inactive'}` },
      el('div', { class: 'tpl-card__title' }, t.title),
      el('div', { class: 'tpl-card__tags' },
        ...(t.request_types.length
          ? t.request_types.map((x) => el('span', { class: 'badge badge--blue' },
              label('request_type', x)))
          : [el('span', { class: 'badge badge--slate' }, 'Универсальный')]),
        ...t.subject_types.map((x) => el('span', { class: 'badge badge--slate' },
          label('subject_type', x)))),
      t.placeholders.length
        ? el('div', { class: 'muted', style: 'font-size:11.5px' },
            `Плейсхолдеры: ${t.placeholders.join(', ')}`)
        : el('div', { class: 'muted', style: 'font-size:11.5px' },
            'Плейсхолдеров нет — текст подставится как есть'),
      el('div', { class: 'tpl-card__preview' }, t.body.slice(0, 260) + '…'),
      el('div', { class: 'tpl-card__actions' },
        el('button', { class: 'btn btn--ghost btn--sm', onClick: () => editTemplate(t) }, 'Изменить'),
        el('button', {
          class: 'btn btn--ghost btn--sm',
          onClick: () => api.patch(`/api/templates/${t.id}`, { is_active: !t.is_active })
            .then(loadTemplates),
        }, t.is_active ? 'Выключить' : 'Включить'),
        el('button', {
          class: 'btn btn--danger btn--sm',
          onClick: async () => {
            if (!confirm(`Удалить шаблон «${t.title}»?`)) return;
            await guard(() => api.del(`/api/templates/${t.id}`));
            loadTemplates();
          },
        }, 'Удалить')),
    ));
  }
}

function multiSelect(options, selected) {
  const sel = el('select', { multiple: true, size: 8 });
  for (const o of options) {
    sel.append(el('option', { value: o.value, selected: selected.includes(o.value) }, o.label));
  }
  return sel;
}

function editTemplate(t = null) {
  const title = el('input', { type: 'text', value: t?.title ?? '' });
  const subjectLine = el('input', { type: 'text', value: t?.subject_line ?? '' });
  const body = el('textarea', { rows: 14 });
  body.value = t?.body ?? '';
  const types = multiSelect(state.ref.request_types, t?.request_types ?? []);
  const subjects = multiSelect(state.ref.subject_types, t?.subject_types ?? []);

  openModal(t ? 'Изменить типовой ответ' : 'Новый типовой ответ', el('div', {},
    el('label', { class: 'field' }, el('span', {}, 'Название'), title),
    el('label', { class: 'field' }, el('span', {}, 'Тема письма для ответа'), subjectLine),
    el('div', { class: 'field-row' },
      el('label', { class: 'field' },
        el('span', {}, 'Для каких типов обращений (пусто — универсальный)'), types),
      el('label', { class: 'field' },
        el('span', {}, 'Для каких видов субъектов'), subjects)),
    el('label', { class: 'field' },
      el('span', {}, 'Текст. Плейсхолдеры: {{ФИО}}, {{НОМЕР}}, {{ДАТА ОБРАЩЕНИЯ}}, '
        + '{{СРОК}}, {{ЮЛ}}, {{АДРЕС}}, {{ИНН}}, {{НОМЕР ОПЕРАТОРА}}, {{СЕРВИС}}, '
        + '{{ОТВЕТСТВЕННЫЙ}}, {{ЯЩИК}}'), body),
  ), [{
    label: 'Сохранить', primary: true,
    onClick: async () => {
      if (!title.value.trim() || !body.value.trim()) {
        toast('Заполните название и текст', 'error');
        return;
      }
      const payload = {
        title: title.value.trim(), body: body.value, subject_line: subjectLine.value.trim(),
        request_types: [...types.selectedOptions].map((o) => o.value),
        subject_types: [...subjects.selectedOptions].map((o) => o.value),
      };
      const ok = await guard(() => t
        ? api.patch(`/api/templates/${t.id}`, payload)
        : api.post('/api/templates', payload));
      if (!ok) return;
      closeModal();
      loadTemplates();
      toast('Типовой ответ сохранён', 'ok');
    },
  }]);
}

function setupTemplatesView() {
  $('#btn-tpl-new').addEventListener('click', () => editTemplate(null));
  $('#btn-tpl-upload').addEventListener('click', () => $('#tpl-file-input').click());
  $('#tpl-file-input').addEventListener('change', async (e) => {
    const fileList = [...e.target.files];
    e.target.value = '';
    if (!fileList.length) return;
    const types = multiSelect(state.ref.request_types, []);
    openModal('Загрузка типовых ответов', el('div', {},
      el('p', { class: 'muted', style: 'margin-top:0' },
        `Файлов: ${fileList.length}. Каждый станет отдельным шаблоном; плейсхолдеры `
        + 'вида {{ФИО}} будут найдены автоматически.'),
      el('label', { class: 'field' },
        el('span', {}, 'Для каких типов обращений (можно не выбирать)'), types),
    ), [{
      label: 'Загрузить', primary: true,
      onClick: async () => {
        const fd = new FormData();
        fileList.forEach((f) => fd.append('files', f));
        fd.append('request_types',
          [...types.selectedOptions].map((o) => o.value).join(','));
        const created = await guard(() => api.form('/api/templates/upload', fd));
        if (!created) return;
        closeModal();
        loadTemplates();
        toast(`Загружено шаблонов: ${created.length}`, 'ok');
      },
    }]);
  });
}

/* ── Справочники ─────────────────────────────────────────────────────── */

const REF_SCHEMAS = {
  'legal-entities': {
    title: 'юридическое лицо',
    columns: [['name', 'Наименование'], ['inn', 'ИНН'],
              ['rkn_operator_number', '№ в реестре операторов'], ['dpo_email', 'Email DPO']],
    fields: [
      ['name', 'Полное наименование', 'text'],
      ['short_name', 'Краткое наименование', 'text'],
      ['inn', 'ИНН', 'text'], ['kpp', 'КПП', 'text'], ['ogrn', 'ОГРН', 'text'],
      ['address', 'Юридический адрес', 'text'],
      ['rkn_operator_number', 'Регистрационный номер в реестре операторов', 'text'],
      ['dpo_name', 'Ответственный за обработку ПД', 'text'],
      ['dpo_email', 'Email ответственного', 'text'],
      ['aliases', 'Синонимы для распознавания в тексте (через запятую)', 'list'],
    ],
  },
  inboxes: {
    title: 'почтовый ящик',
    columns: [['email', 'Адрес'], ['label', 'Название'], ['purpose', 'Тематика']],
    fields: [
      ['email', 'Адрес ящика', 'text'], ['label', 'Название', 'text'],
      ['purpose', 'Тематика (privacy, dpo, hr, support, general)', 'text'],
      ['legal_entity_id', 'Юридическое лицо по умолчанию', 'entity'],
      ['imap_host', 'IMAP-сервер', 'text'], ['imap_port', 'IMAP-порт', 'number'],
      ['imap_user', 'IMAP-логин', 'text'],
      ['imap_password_env', 'Имя переменной окружения с паролем', 'text'],
      ['imap_folder', 'Папка', 'text'],
    ],
  },
  services: {
    title: 'сервис / бизнес-процесс',
    columns: [['name', 'Название'], ['code', 'Код'], ['category', 'Категория'], ['owner', 'Владелец']],
    fields: [
      ['name', 'Название', 'text'], ['code', 'Код', 'text'],
      ['category', 'Категория', 'category'],
      ['description', 'Описание', 'text'],
      ['owner', 'Владелец процесса', 'text'], ['owner_email', 'Email владельца', 'text'],
      ['legal_entity_id', 'Юридическое лицо', 'entity'],
      ['systems', 'Информационные системы (через запятую)', 'list'],
      ['keywords', 'Ключевые слова для распознавания (через запятую)', 'list'],
      ['retention_note', 'Сроки хранения', 'text'],
    ],
  },
};

let currentRef = 'legal-entities';

async function loadReferenceView(kind = currentRef) {
  currentRef = kind;
  $$('.ref-tab').forEach((b) => b.classList.toggle('is-active', b.dataset.ref === kind));
  const schema = REF_SCHEMAS[kind];
  const rows = await guard(() => api.get(`/api/${kind}`));
  if (!rows) return;
  await loadReference();

  const table = el('table', { class: 'table' },
    el('thead', {}, el('tr', {},
      ...schema.columns.map(([, title]) => el('th', {}, title)),
      el('th', {}, ''))),
    el('tbody', {}, ...rows.map((row) => el('tr', { class: row.is_active ? '' : 'is-inactive' },
      ...schema.columns.map(([key]) => el('td', {}, String(row[key] ?? '—') || '—')),
      el('td', {}, el('button', {
        class: 'btn btn--ghost btn--sm', onClick: () => editRefItem(kind, row),
      }, 'Изменить'))))));

  const box = $('#ref-content');
  box.innerHTML = '';
  box.append(
    el('div', { style: 'margin-bottom:12px' },
      el('button', {
        class: 'btn btn--primary btn--sm', onClick: () => editRefItem(kind, null),
      }, `＋ Добавить ${schema.title}`),
      kind === 'inboxes' ? el('span', { class: 'muted', style: 'margin-left:12px;font-size:12px' },
        'Пароль IMAP хранится в переменной окружения, не в базе.') : null),
    el('div', { class: 'table-wrap' }, table));
}

function editRefItem(kind, item) {
  const schema = REF_SCHEMAS[kind];
  const inputs = {};
  const form = el('div', {});
  for (const [key, title, type] of schema.fields) {
    let input;
    if (type === 'entity') {
      input = el('select', {},
        el('option', { value: '' }, '— не выбрано —'),
        ...state.entities.map((e) => el('option', {
          value: String(e.id), selected: String(item?.[key] ?? '') === String(e.id),
        }, e.short_name || e.name)));
    } else if (type === 'category') {
      input = el('select', {}, ...state.ref.service_categories.map((c) => el('option', {
        value: c.value, selected: (item?.[key] ?? 'OTHER') === c.value,
      }, c.label)));
    } else {
      input = el('input', {
        type: type === 'number' ? 'number' : 'text',
        value: type === 'list' ? (item?.[key] ?? []).join(', ') : (item?.[key] ?? ''),
      });
    }
    inputs[key] = { input, type };
    form.append(el('label', { class: 'field' }, el('span', {}, title), input));
  }

  openModal(item ? `Изменить: ${schema.title}` : `Новый ${schema.title}`, form, [{
    label: 'Сохранить', primary: true,
    onClick: async () => {
      const payload = {};
      for (const [key, { input, type }] of Object.entries(inputs)) {
        const v = input.value;
        if (type === 'list') payload[key] = v.split(',').map((s) => s.trim()).filter(Boolean);
        else if (type === 'number') payload[key] = v ? Number(v) : 0;
        else if (type === 'entity') payload[key] = v ? Number(v) : null;
        else payload[key] = v;
      }
      const ok = await guard(() => item
        ? api.patch(`/api/${kind}/${item.id}`, payload)
        : api.post(`/api/${kind}`, payload));
      if (!ok) return;
      closeModal();
      loadReferenceView(kind);
      toast('Сохранено', 'ok');
    },
  }]);
}

/* ── Навигация ───────────────────────────────────────────────────────── */

function switchView(name) {
  $$('.nav__item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.dataset.view === name));
  if (name === 'templates') loadTemplates();
  if (name === 'reference') loadReferenceView();
}

/* ── Инициализация ───────────────────────────────────────────────────── */

let searchTimer = null;

function setupRegistryView() {
  $('#f-q').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const v = e.target.value.trim();
      if (v) state.filters.q = [v]; else delete state.filters.q;
      state.page = 1;
      loadRegistry();
    }, 320);
  });
  const reset = () => {
    state.filters = {};
    state.page = 1;
    $('#f-q').value = '';
    loadRegistry();
  };
  $('#btn-reset').addEventListener('click', reset);
  $('#btn-reset-2').addEventListener('click', reset);
  $('#btn-toggle-filters').addEventListener('click', (e) => {
    const grid = $('#filters-grid');
    grid.hidden = !grid.hidden;
    e.currentTarget.textContent = grid.hidden ? 'Фильтры ▾' : 'Фильтры ▴';
  });
  $('#btn-new').addEventListener('click', newRequestDialog);
  $$('#table thead th[data-sort]').forEach((th) => th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (state.sort === key) state.order = state.order === 'asc' ? 'desc' : 'asc';
    else { state.sort = key; state.order = 'asc'; }
    loadRegistry();
  }));
}

function setupTheme() {
  const saved = localStorage.getItem('dpo-theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('dpo-theme', next);
  });
}

async function init() {
  setupTheme();
  $$('.nav__item').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));
  setupRegistryView();
  setupAnalyzeView();
  setupTemplatesView();
  $$('.ref-tab').forEach((b) => b.addEventListener('click', () => loadReferenceView(b.dataset.ref)));

  try {
    await loadReference();
  } catch (err) {
    toast(`Не удалось загрузить справочники: ${err.message}`, 'error');
    return;
  }
  await loadRegistry();
}

init();
