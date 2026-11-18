'use strict';

const $ = selector => document.querySelector(selector);
const panel = $('#panel');
const frame = $('#preview-frame');
const api = '/api/cms?action=';
const tabs = [...$('#tabs').querySelectorAll('button')];
const labels = {title: 'Page title', description: 'Meta description', keywords: 'Keywords', robots: 'Robots',
  'og:title': 'Social share title', 'og:description': 'Social share description',
  'og:image': 'Social share image URL', canonical: 'Canonical URL'};
let schema, data, revision, backups, publishedAt, unpublished;
let dirty = false, busy = false, activeTab = 'builder', slug = '', selected = 'hero', search = '', mediaGroup = 'work';
let analytics, lastFrame = '', frameFocus = false, dragging;
const currentPage = () => data.pages.find(page => page.slug === slug);
const path = () => slug ? '/pages/' + slug : '/';
const id = () => 'block-' + crypto.randomUUID();
const date = value => value ? new Date(value).toLocaleString() : 'Never';
const copy = value => structuredClone(value);

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function append(parent, ...nodes) {
  parent.append(...nodes.filter(Boolean));
  return parent;
}
function button(text, fn, className = '', label) {
  const node = el('button', text, className);
  node.type = 'button';
  if (label) node.setAttribute('aria-label', label);
  node.addEventListener('click', fn);
  return node;
}
function option(value, label) {
  const node = el('option', label);
  node.value = value;
  return node;
}
function input(label, value, update, opts = {}) {
  const wrapper = el('label', label, 'field');
  const control = el(opts.select ? 'select' : opts.multiline ? 'textarea' : 'input');
  if (opts.select) control.append(...opts.select.map(([key, text]) => option(key, text)));
  else if (!opts.multiline) control.type = opts.type || 'text';
  if (opts.placeholder) control.placeholder = opts.placeholder;
  if (opts.min != null) control.min = opts.min;
  if (opts.max != null) control.max = opts.max;
  if (opts.maxLength) control.maxLength = opts.maxLength;
  control.value = value ?? '';
  control.addEventListener(opts.select ? 'change' : 'input', () => update(control.value));
  wrapper.append(control);
  return wrapper;
}
function check(label, value, update) {
  const wrapper = el('label', null, 'check');
  const box = el('input');
  box.type = 'checkbox';
  box.checked = value;
  box.addEventListener('change', () => update(box.checked));
  return append(wrapper, box, el('span', label));
}
function header(title, detail) {
  return append(el('div', null, 'header'), el('h1', title), detail && el('p', detail, 'muted'));
}
function card(title, ...body) {
  return append(el('div', null, 'card'), title && el('h2', title), ...body);
}
function stateLabel() {
  $('#state').textContent = dirty ? 'Unsaved edits' : unpublished ? 'Draft saved' : 'Published version';
  $('#save').disabled = busy || !dirty;
  $('#refresh').disabled = busy;
  $('#publish').disabled = busy || (!dirty && !unpublished);
  $('#logout').disabled = busy;
}
function changed() {
  dirty = true;
  stateLabel();
}
function status(message, error = false) {
  $('#status').textContent = message;
  $('#status').classList.toggle('error', error);
}
async function request(action, payload, suffix = '') {
  const options = payload === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Joo-CMS': '1'},
    body: JSON.stringify(payload), cache: 'no-store',
  };
  const response = await fetch(api + action + suffix, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function adopt(result) {
  if (result.schema) schema = result.schema;
  data = result.draft;
  revision = result.revision;
  backups = result.backups;
  publishedAt = result.publishedAt;
  unpublished = result.unpublished;
  dirty = false;
  if (slug && !currentPage()) { slug = ''; selected = 'hero'; }
  stateLabel();
}
async function operation(fn) {
  if (busy) return;
  busy = true;
  panel.inert = true;
  $('.sidebar').inert = true;
  stateLabel();
  try { await fn(); }
  catch (error) { status(error.message, true); }
  finally {
    busy = false;
    panel.inert = false;
    $('.sidebar').inert = false;
    stateLabel();
  }
}
function preview(force = false) {
  const address = path();
  $('#preview-path').textContent = address;
  const key = address + ':' + revision;
  if (!force && lastFrame === key) return;
  lastFrame = key;
  frame.src = api + 'preview&path=' + encodeURIComponent(address) + '&revision=' + revision;
}
function focusPreview() {
  if (selected && !dirty) frame.contentWindow?.postMessage({type: 'joo-focus', id: selected}, location.origin);
}
frame.addEventListener('load', () => { if (frameFocus) focusPreview(); });
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.type !== 'joo-select') return;
  const key = event.data.id;
  const item = schema.items.find(entry => entry.id === key);
  if (item && ['clients', 'work'].includes(item.group)) mediaGroup = item.group;
  const exists = schema.groups.some(group => group.id === key) || item ||
    [...data.home, ...(currentPage()?.blocks || [])].some(block => block.id === key);
  if (!exists) return;
  selected = key;
  activeTab = item && ['clients', 'work'].includes(item.group) || data.home.some(block => block.id === key && block.target)
    ? 'media' : 'builder';
  if (activeTab === 'media' && !item) mediaGroup = data.home.find(block => block.id === key).target === 'clients' ? 'clients' : 'work';
  search = '';
  paint();
  $('#inspector')?.scrollIntoView({block: 'nearest'});
});
async function saveIfDirty() {
  if (dirty) adopt(await request('save', {revision, draft: data}));
}
function confirmAction(title, note, action) {
  const dialog = $('#confirm');
  $('#confirm-title').textContent = title;
  $('#confirm-note').textContent = note;
  $('#confirm-action').textContent = action;
  dialog.returnValue = 'cancel';
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
}
async function enter() {
  adopt(await request('content'));
  $('#login').hidden = true;
  $('#studio').hidden = false;
  paint();
  preview();
}
function openPage(next) {
  slug = next;
  selected = slug ? currentPage()?.blocks[0]?.id || '' : 'hero';
  search = '';
  activeTab = 'builder';
  frameFocus = false;
  paint();
  preview();
}
function nav() {
  for (const tab of tabs) {
    if (tab.dataset.tab === activeTab) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  }
  const list = $('#pages');
  list.replaceChildren(el('h2', 'Pages'), button('Home', () => openPage(''), !slug ? 'selected' : ''));
  for (const page of data.pages) list.append(button(page.title, () => openPage(page.slug), slug === page.slug ? 'selected' : ''));
  list.append(button('+ New page', () => {
    const next = 'page-' + (data.pages.length + 1);
    let candidate = next;
    let suffix = 2;
    while (data.pages.some(page => page.slug === candidate)) candidate = next + '-' + suffix++;
    data.pages.push({slug: candidate, title: 'New page', menu: false, blocks: [], seo: {}});
    changed();
    openPage(candidate);
  }));
}
function paint() {
  nav();
  const wide = ['analytics', 'backups'].includes(activeTab);
  panel.classList.toggle('wide', wide);
  $('#preview-pane').hidden = wide;
  panel.replaceChildren();
  ({builder, media, hero, seo, analytics: showAnalytics, backups: showBackups})[activeTab]();
}
function choose(key) {
  selected = key;
  frameFocus = true;
  paint();
  $('#inspector')?.scrollIntoView({block: 'nearest'});
  focusPreview();
}
function row(item, list, reorder) {
  const key = item.id;
  const idx = list.indexOf(key);
  const text = item.label + (item.hidden ? ' · Hidden' : '');
  const name = button(text, () => choose(key), selected === key ? 'active' : '');
  name.title = text;
  if (selected === key) name.setAttribute('aria-pressed', 'true');
  const box = append(el('div', null, 'item'), name);
  if (!reorder || idx < 0) return box;
  name.draggable = true;
  name.addEventListener('dragstart', event => {
    dragging = key;
    event.dataTransfer.setData('text/plain', key);
    event.dataTransfer.effectAllowed = 'move';
  });
  name.addEventListener('dragend', () => { dragging = null; });
  box.addEventListener('dragover', event => {
    if (list.includes(dragging)) event.preventDefault();
  });
  box.addEventListener('drop', event => {
    event.preventDefault();
    if (list.includes(dragging) && dragging !== key) reorder(move(list, list.indexOf(dragging), idx));
    dragging = null;
  });
  const up = button('↑', () => reorder(move(list, idx, idx - 1)), '', 'Move ' + item.label + ' up');
  const down = button('↓', () => reorder(move(list, idx, idx + 1)), '', 'Move ' + item.label + ' down');
  up.disabled = idx === 0;
  down.disabled = idx === list.length - 1;
  return append(box, append(el('div', null, 'move'), up, down));
}
function move(list, from, to) {
  const next = [...list];
  const [entry] = next.splice(from, 1);
  next.splice(to, 0, entry);
  return next;
}
function setOrder(order) {
  data.order = order;
  changed();
  paint();
}
function createBlock(type, target = '') {
  const block = {id: id(), type, title: '', text: '', url: '', layout: 'landscape'};
  if (!slug || target) {
    block.target = target;
    data.home.push(block);
  } else currentPage().blocks.push(block);
  selected = block.id;
  changed();
  paint();
  $('#inspector')?.scrollIntoView({block: 'nearest'});
}
function addBlocks() {
  return append(el('div', null, 'actions'), ...['text', 'image', 'video', 'logo'].map(type =>
    button('+ ' + type[0].toUpperCase() + type.slice(1), () => createBlock(type))));
}
function builder() {
  const page = currentPage();
  panel.append(header('Page builder', 'Choose a section in the list or in the draft preview.'));
  if (page) return pageBuilder(page);
  const existing = schema.groups.filter(group => group.movable).map(group => group.id);
  const custom = data.home.filter(block => !block.target);
  const order = [...new Set([...data.order, ...existing, ...custom.map(block => block.id)])];
  const rows = order.map(key => {
    const group = schema.groups.find(g => g.id === key);
    const block = custom.find(b => b.id === key);
    return row({id: key, label: group?.label || block?.title || block?.type, hidden: data.hidden.includes(key)}, order, setOrder);
  });
  const global = schema.groups.filter(group => !group.movable).map(group =>
    row({...group, hidden: data.hidden.includes(group.id)}, []));
  panel.append(card('Home sections', ...rows, addBlocks()), card('Site-wide', ...global));
  const block = custom.find(entry => entry.id === selected);
  if (block) return blockInspector(block);
  const group = schema.groups.find(entry => entry.id === selected);
  const item = schema.items.find(entry => entry.id === selected);
  if (group || item) existingInspector(group || item, Boolean(item));
}
function pageBuilder(page) {
  const slugField = input('Page address', page.slug, () => {}, {maxLength: 60});
  const slugControl = slugField.querySelector('input');
  slugControl.addEventListener('change', () => {
    const value = slugControl.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    if (!value || data.pages.some(other => other !== page && other.slug === value)) {
      status('Use a unique page address.', true);
      slugControl.value = page.slug;
      return;
    }
    slug = page.slug = value;
    slugControl.value = value;
    changed();
    $('#preview-path').textContent = path();
  });
  panel.append(card('Page settings',
    input('Name', page.title, value => { page.title = value; changed(); nav(); }, {maxLength: 120}),
    slugField,
    check('Show in the site navigation', page.menu, value => { page.menu = value; changed(); }),
    button('Remove page', async () => {
      if (!await confirmAction('Remove page?', 'The page will be removed from the draft. Publish to update the public site.', 'Remove page')) return;
      data.pages = data.pages.filter(other => other !== page);
      changed();
      openPage('');
    }, 'danger')));
  const order = page.blocks.map(block => block.id);
  panel.append(card('Sections', ...page.blocks.map(block => row({id: block.id, label: block.title || block.type}, order, next => {
    page.blocks = next.map(key => page.blocks.find(candidate => candidate.id === key));
    changed();
    paint();
  })), addBlocks()));
  const block = page.blocks.find(candidate => candidate.id === selected);
  if (block) blockInspector(block);
}
function blockInspector(block) {
  const controls = [
    input(block.type === 'logo' ? 'Brand name' : 'Heading', block.title, value => { block.title = value; changed(); }, {maxLength: 150}),
  ];
  if (!block.target) controls.push(input('Text', block.text, value => { block.text = value; changed(); }, {multiline: true}));
  if (block.type !== 'text') controls.push(input(block.type === 'video' ? 'YouTube or Vimeo URL' : 'Image URL',
    block.url, value => { block.url = value; changed(); }, {placeholder: 'https://…'}));
  if (block.type === 'video' && !block.target) controls.push(input('Player aspect ratio', block.layout,
    value => { block.layout = value; changed(); }, {select: [['landscape', '16:9 landscape'], ['portrait', '9:16 portrait']]}));
  if (block.target === 'clients') controls.push(...blockMoves(block, entry => entry.target === 'clients'));
  if (block.target?.startsWith('gallery')) {
    controls.push(input('Gallery', block.target, value => { block.target = value; changed(); paint(); },
      {select: schema.galleries.map(g => [g.id, g.label])}));
    controls.push(...blockMoves(block, entry => entry.target === block.target));
  }
  controls.push(button('Remove from draft', () => {
    if (currentPage() && !block.target) currentPage().blocks = currentPage().blocks.filter(entry => entry !== block);
    else data.home = data.home.filter(entry => entry !== block);
    data.order = data.order.filter(key => key !== block.id);
    selected = '';
    changed();
    paint();
  }, 'danger'));
  const area = card(block.title || block.type[0].toUpperCase() + block.type.slice(1), ...controls);
  area.id = 'inspector';
  panel.append(area);
}
function blockMoves(block, match) {
  const entries = data.home.filter(match);
  const pos = entries.indexOf(block);
  const result = [-1, 1].map(step => {
    const control = button(step < 0 ? 'Move earlier' : 'Move later', () => {
      const a = data.home.indexOf(block);
      const b = data.home.indexOf(entries[pos + step]);
      [data.home[a], data.home[b]] = [data.home[b], data.home[a]];
      changed();
      paint();
    });
    control.disabled = !entries[pos + step];
    return control;
  });
  return [append(el('div', null, 'actions'), ...result)];
}
function existingInspector(entry, item) {
  const controls = [];
  controls.push(check('Hide on the site', item ? !!data.items[entry.id] : data.hidden.includes(entry.id), value => {
    if (item) {
      if (value) data.items[entry.id] = true;
      else delete data.items[entry.id];
    } else {
      data.hidden = data.hidden.filter(key => key !== entry.id);
      if (value) data.hidden.push(entry.id);
    }
    changed();
    paint();
  }));
  const fields = schema.fields.filter(field => item ? field.item === entry.id : field.group === entry.id && !field.item);
  for (const field of fields) {
    const key = field.id;
    const attr = key.endsWith('-aria-label') ? 'aria-label' : key.split('-').at(-1);
    const kind = {alt: 'Alt text', href: 'Link', title: 'Player title', src: field.kind === 'video' ? 'Video URL' : 'Image URL',
      'aria-label': 'Accessible label', text: field.label}[attr];
    controls.push(input(kind, data.fields[key] ?? field.value, value => {
      if (value === field.value || field.kind !== 'text' && !value.trim()) delete data.fields[key];
      else data.fields[key] = value;
      changed();
    }, {multiline: field.kind === 'text' && field.value.length > 90,
      placeholder: field.embedded ? 'Embedded image · paste a URL to replace' : ''}));
  }
  if (item) controls.push(...existingMoves(entry));
  if (!item) {
    const items = schema.items.filter(x => x.group === entry.id && !x.parent);
    if (!['clients', 'work'].includes(entry.id)) controls.push(...items.map(x => row(x, [])));
    else controls.push(button('Open media', () => { activeTab = 'media'; mediaGroup = entry.id; paint(); }));
  }
  if (entry.id === 'navigation') controls.push(input('Site logo URL', data.logo, value => { data.logo = value; changed(); },
    {placeholder: 'Default wordmark'}));
  const parent = item && schema.items.find(x => x.id === entry.parent);
  if (parent) controls.unshift(button('Back to ' + parent.label, () => choose(parent.id)));
  if (item && entry.group === 'work') controls.push(...schema.items.filter(x => x.parent === entry.id).map(x => row(x, [])));
  const area = card(entry.label, ...controls);
  area.id = 'inspector';
  panel.append(area);
}
function existingMoves(entry) {
  const peers = schema.items.filter(candidate => candidate.container === entry.container);
  if (peers.length < 2) return [];
  const ids = peers.map(peer => peer.id);
  const ordered = [...new Set([...data.itemOrder.filter(key => ids.includes(key)), ...ids])];
  const pos = ordered.indexOf(entry.id);
  return [append(el('div', null, 'actions'), ...[-1, 1].map(step => {
    const control = button(step < 0 ? 'Move earlier' : 'Move later', () => {
      data.itemOrder = [...data.itemOrder.filter(key => !ids.includes(key)), ...move(ordered, pos, pos + step)];
      changed();
      paint();
    });
    control.disabled = !ordered[pos + step];
    return control;
  }))];
}
function media() {
  panel.append(header('Videos & logos', 'Replace links, reorder items or hide them in the draft.'));
  panel.append(append(el('div', null, 'header'),
    input('Collection', mediaGroup, value => { mediaGroup = value; selected = ''; search = ''; paint(); },
      {select: [['work', 'Portfolio videos'], ['clients', 'Client logos'], ['brand', 'Site logo']]})));
  if (mediaGroup === 'brand') {
    panel.append(card('Brand logo', input('Image URL', data.logo, value => { data.logo = value; changed(); },
      {placeholder: 'Default wordmark'})));
    return;
  }
  const existing = schema.items.filter(item => item.group === mediaGroup);
  const added = data.home.filter(block => mediaGroup === 'clients' ? block.target === 'clients' : block.target.startsWith('gallery'));
  const list = el('div', null, 'stack');
  const redraw = () => {
    const query = search.trim().toLowerCase();
    const matching = (label, itemId) => (label + ' ' + schema.fields.filter(f => f.item === itemId && f.kind === 'text')
      .map(f => data.fields[f.id] || '').join(' ')).toLowerCase().includes(query);
    const ordered = [...new Set([...data.itemOrder, ...existing.map(item => item.id)])];
    const byKey = new Map(existing.map(item => [item.id, item]));
    const items = ordered.map(key => byKey.get(key)).filter(item => item && matching(item.label, item.id));
    const blocks = added.filter(block => matching(block.title || block.type, block.id));
    list.replaceChildren(...blocks.map(block => row({id: block.id, label: block.title || 'New ' + block.type}, [])),
      ...items.map(item => row({...item, hidden: !!data.items[item.id]}, [])));
    if (!list.children.length) list.append(el('p', 'No matches.', 'muted'));
  };
  const searchField = input('Search this collection', search, value => { search = value; redraw(); }, {type: 'search'});
  let gallery = schema.galleries[0].id;
  const destination = mediaGroup === 'work' && input('Add video to', gallery, value => { gallery = value; },
    {select: schema.galleries.map(g => [g.id, g.label])});
  panel.append(card(mediaGroup === 'work' ? 'Portfolio work' : 'Client logos',
    searchField, destination, button(mediaGroup === 'work' ? '+ Add video' : '+ Add logo',
      () => createBlock(mediaGroup === 'work' ? 'video' : 'logo', mediaGroup === 'work' ? gallery : 'clients'))));
  const item = existing.find(entry => entry.id === selected);
  const block = added.find(entry => entry.id === selected);
  if (block) blockInspector(block);
  if (item) existingInspector(item, true);
  panel.append(card('Items', list));
  redraw();
}
function hero() {
  const cfg = copy(data.hero || schema.hero);
  const update = (refresh = false) => {
    data.hero = cfg;
    changed();
    if (refresh) paint();
  };
  panel.append(header('Home background', 'Manage the muted montage behind the home page.'));
  const shuffle = input('Playback order', cfg.shuffle ? 'shuffle' : 'sequence',
    value => { cfg.shuffle = value === 'shuffle'; update(); },
    {select: [['sequence', 'In list order'], ['shuffle', 'Shuffle'] ]});
  panel.append(card('Appearance',
    check('Play background montage', cfg.enabled, value => { cfg.enabled = value; update(); }),
    shuffle,
    input('Black overlay (%)', cfg.overlay, value => { cfg.overlay = value === '' ? null : Number(value); update(); },
      {type: 'number', min: 0, max: 100, placeholder: 'Original gradient'}),
    button('Restore original montage', () => { data.hero = null; changed(); paint(); })));
  panel.append(card('Clips', button('+ Add clip', () => {
    cfg.clips.push({url: '', seconds: 8, start: 0});
    update(true);
  })));
  cfg.clips.forEach((clip, index) => {
    const up = button('↑', () => { cfg.clips = move(cfg.clips, index, index - 1); update(true); }, '', 'Move clip ' + (index + 1) + ' up');
    const down = button('↓', () => { cfg.clips = move(cfg.clips, index, index + 1); update(true); }, '', 'Move clip ' + (index + 1) + ' down');
    up.disabled = index === 0;
    down.disabled = index === cfg.clips.length - 1;
    panel.append(card('Clip ' + (index + 1),
      input('YouTube or Vimeo URL', clip.url, value => { clip.url = value; update(); }, {placeholder: 'https://…'}),
      append(el('div', null, 'row'),
        input('Duration (seconds)', clip.seconds, value => { clip.seconds = value === '' ? null : Number(value); update(); },
          {type: 'number', min: 2, max: 120, placeholder: '6.5–9'}),
        input('Start at (seconds)', clip.start, value => { clip.start = Number(value); update(); }, {type: 'number', min: 0, max: 14400})),
      append(el('div', null, 'actions'), up, down, button('Remove', () => { cfg.clips.splice(index, 1); update(true); }, 'danger'))));
  });
}
function seo() {
  const page = currentPage();
  const values = page ? page.seo : data.seo;
  panel.append(header('SEO', 'Search and social metadata for ' + (page?.title || 'Home') + '.'));
  const defaults = page ? {title: page.title, robots: schema.seo.robots} : schema.seo;
  const controls = Object.entries(labels).map(([key, label]) => {
    const field = input(label, values[key] ?? '', value => {
      if (value === defaults[key] || value === '') delete values[key];
      else values[key] = value;
      changed();
    }, key === 'robots' ? {select: [['', 'Use default'], ['index, follow', 'Index, follow links'],
      ['noindex, follow', 'Do not index, follow links'], ['noindex, nofollow', 'Do not index or follow']]}
      : {placeholder: defaults[key] || '', multiline: key.includes('description')});
    return field;
  });
  panel.append(card('Metadata', ...controls));
}
const eventLabels = {view: 'Page views', video: 'Video link clicks', email: 'Email clicks', whatsapp: 'WhatsApp clicks', instagram: 'Instagram clicks'};
function showAnalytics() {
  panel.append(header('Analytics', 'Last 30 days · UTC · Aggregate page views and link clicks.'));
  if (!analytics) {
    const loading = el('p', 'Loading analytics…', 'muted');
    panel.append(loading);
    request('analytics').then(result => {
      analytics = result.days;
      if (activeTab === 'analytics') paint();
    }).catch(error => { loading.textContent = error.message; });
    return;
  }
  const totals = Object.fromEntries(Object.keys(eventLabels).map(key => [key, 0]));
  const pages = new Map();
  const daily = Object.entries(analytics).map(([day, counts]) => {
    let views = 0, clicks = 0;
    for (const [entry, count] of Object.entries(counts)) {
      const [kind, url] = entry.split('|');
      if (!(kind in totals)) continue;
      totals[kind] += count;
      if (!pages.has(url)) pages.set(url, [0, 0]);
      pages.get(url)[kind === 'view' ? 0 : 1] += count;
      if (kind === 'view') views += count;
      else clicks += count;
    }
    return [day, views, clicks];
  });
  panel.append(append(el('div', null, 'metrics'), ...Object.keys(totals).map(key =>
    append(el('div', null, 'metric'), el('strong', totals[key].toLocaleString()), el('span', eventLabels[key])))));
  const byPage = [...pages].sort((a, b) => b[1][0] - a[1][0]).map(([url, counts]) => [url, ...counts]);
  panel.append(card('By page', table(['Page', 'Views', 'Clicks'], byPage)),
    card('Daily totals', table(['Date', 'Views', 'Clicks'], daily.reverse())),
    button('Refresh analytics', () => { analytics = null; paint(); }));
}
function table(columns, rows) {
  const element = el('table');
  const heading = el('tr');
  heading.append(...columns.map(value => el('th', value)));
  const body = el('tbody');
  body.append(...rows.map(row => append(el('tr'), ...row.map(value => el('td', value)))));
  return append(el('div', null, 'table'), append(element, append(el('thead'), heading), body));
}
function download(backupId = '') {
  const link = el('a', 'Download JSON');
  link.href = api + 'export' + (backupId ? '&id=' + encodeURIComponent(backupId) : '');
  link.download = 'joo-films-backup.json';
  link.click();
}
function showBackups() {
  panel.append(header('Backups', 'Restore a snapshot into the draft. The public version updates when you publish.'));
  const picker = el('input');
  picker.type = 'file';
  picker.accept = '.json,application/json';
  picker.hidden = true;
  picker.addEventListener('change', async () => {
    if (!picker.files[0]) return;
    const file = picker.files[0];
    if (file.size > 470000) return status('The selected backup is too large.', true);
    if (!await confirmAction('Import backup?', 'The backup will replace the draft. A snapshot of the current draft is kept.', 'Import')) return;
    await operation(async () => {
      const bundle = JSON.parse(await file.text());
      await saveIfDirty();
      adopt(await request('import', {revision, bundle}));
      paint(); preview();
      status('Backup imported into the draft.');
    });
  });
  panel.append(card('Draft snapshots',
    el('p', 'Last published ' + date(publishedAt), 'muted'),
    append(el('div', null, 'actions'), button('Create snapshot', () => operation(async () => {
      await saveIfDirty();
      adopt(await request('backup', {revision}));
      paint();
      status('Snapshot created.');
    })), button('Export draft', () => operation(async () => { await saveIfDirty(); download(); })),
    button('Import backup', () => { picker.value = ''; picker.click(); }), picker)));
  for (const backup of backups) panel.append(card(backup.label, el('p', date(backup.date), 'muted'),
    append(el('div', null, 'actions'), button('Download JSON', () => download(backup.id)),
      button('Restore to draft', async () => {
        if (!await confirmAction('Restore this snapshot?', 'The current draft is saved as a snapshot first.', 'Restore')) return;
        await operation(async () => {
          await saveIfDirty();
          adopt(await request('restore', {revision, id: backup.id}));
          paint(); preview();
          status('Snapshot restored to the draft.');
        });
      }))));
  if (!backups.length) panel.append(el('p', 'No snapshots yet.', 'muted'));
}
for (const tab of tabs) tab.addEventListener('click', () => {
  activeTab = tab.dataset.tab;
  search = '';
  if (activeTab === 'media' && slug) {
    slug = '';
    selected = '';
    preview();
  }
  paint();
});
$('#save').addEventListener('click', () => operation(async () => {
  await saveIfDirty();
  paint();
  status('Draft saved.');
}));
$('#refresh').addEventListener('click', () => operation(async () => {
  await saveIfDirty();
  paint();
  preview(true);
  status('Draft preview updated.');
}));
$('#publish').addEventListener('click', async () => {
  if (!await confirmAction('Publish this draft?', 'Visitors to this environment will see the new content. A snapshot of the previous version is kept.', 'Publish')) return;
  await operation(async () => {
    await saveIfDirty();
    adopt(await request('publish', {revision}));
    paint();
    preview();
    status('Published ' + date(publishedAt) + '.');
  });
});
$('#logout').addEventListener('click', async () => {
  if (dirty && !await confirmAction('Sign out?', 'Edits since the last draft save will be discarded.', 'Sign out')) return;
  await operation(async () => {
    await request('logout', {});
    dirty = false;
    location.reload();
  });
});
for (const mode of ['desktop', 'mobile']) $('#' + mode).addEventListener('click', () => {
  $('.viewport').classList.toggle('mobile', mode === 'mobile');
  $('#desktop').setAttribute('aria-pressed', String(mode === 'desktop'));
  $('#mobile').setAttribute('aria-pressed', String(mode === 'mobile'));
});
window.addEventListener('beforeunload', event => {
  if (dirty) { event.preventDefault(); event.returnValue = ''; }
});
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const pass = $('#password');
  const submit = event.currentTarget.querySelector('button');
  submit.disabled = true;
  try {
    await request('login', {password: pass.value});
    pass.value = '';
    await enter();
  } catch (error) {
    $('#login-status').textContent = error.message;
    $('#login-status').classList.add('error');
  } finally { submit.disabled = false; }
});
request('session').then(session => {
  if (session.authenticated) return enter();
  $('#login-status').textContent = session.configured ? 'Enter the studio password to continue.' : session.error;
  $('#login-form').hidden = !session.configured;
}).catch(error => { $('#login-status').textContent = error.message; });
