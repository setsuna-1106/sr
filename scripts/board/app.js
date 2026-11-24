(() => {
  const root = document.getElementById('research-ledger');
  const columns = [...root.querySelectorAll('[data-state]')];
  const template = root.querySelector('#task-template');
  const detail = root.querySelector('.detail');
  const fields = [...detail.querySelectorAll('[data-field]')];
  const status = detail.querySelector('[data-status]');
  const form = detail.querySelector('form');
  const saveButton = detail.querySelector('.detail-save');
  const focusButton = root.querySelector('.focus-action');
  const feedback = root.querySelector('.save-feedback');
  const draft = new Map();
  let board = null;
  let selected = null;
  let dragged = null;
  let focusCard = null;
  let pending = false;

  const key = card => card.id.toLowerCase();
  const cardById = id => board?.cards.find(card => key(card) === id);
  const priority = card => board.week.priorities[key(card)] || '';

  function notice(message, error = false) {
    feedback.textContent = message;
    feedback.hidden = !message;
    feedback.toggleAttribute('data-error', error);
  }

  function newLink(label, href) {
    const link = document.createElement('a');
    link.textContent = label;
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener';
    return link;
  }

  function cardNote(card) {
    if (card.status === 'waiting') {
      return [card.blocker, card.followup && ('跟进 ' + card.followup)].filter(Boolean).join(' · ') || '待补跟进信息';
    }
    if (card.status === 'done') return card.evidence ? '已附证据' : '待补证据';
    return priority(card);
  }

  function renderFocus() {
    const prioritized = Object.keys(board.week.priorities).map(cardById).filter(Boolean);
    const ordered = [...new Set([...prioritized, ...board.cards])];
    focusCard = ['doing', 'ready', 'waiting', 'done']
      .map(state => ordered.find(card => card.status === state)).find(Boolean);
    const dates = root.querySelector('[data-week-dates]');
    dates.textContent = board.week.start
      ? [board.week.start, board.week.end].map(value => value.slice(5).replace('-', '.')).join(' — ')
      : '';
    root.querySelector('.week a').href = board.week.href;
    const state = focusCard?.status;
    const labels = { doing: '正在推进', waiting: '等待跟进', done: '看板任务已完成' };
    root.querySelector('[data-focus-label]').textContent = labels[state] || (focusCard && priority(focusCard)) || board.week.label;
    root.querySelector('#research-focus-title').textContent = focusCard?.title || '暂无任务';
    root.querySelector('[data-focus-next]').textContent = state === 'waiting'
      ? cardNote(focusCard)
      : state === 'done' ? (focusCard.evidence ? '可以回看交付证据。' : '补上交付证据，便于复盘。')
      : focusCard?.notes[0] || '';
    focusButton.textContent = state === 'ready' ? '开始任务' : '查看任务';
    focusButton.disabled = pending || !focusCard;
  }

  function renderBoard() {
    for (const column of columns) {
      const cards = board.cards.filter(card => card.status === column.dataset.state);
      column.querySelector('.column-count').textContent = cards.length;
      column.querySelector('.tasks').replaceChildren(...cards.map(card => {
        const button = template.content.firstElementChild.cloneNode(true);
        button.dataset.task = key(card);
        button.draggable = !pending;
        button.querySelector('.project').textContent = card.project;
        button.querySelector('.task-id').textContent = card.id;
        button.querySelector('.task-title').textContent = card.title;
        button.querySelector('.task-note').textContent = cardNote(card);
        return button;
      }));
    }
    renderFocus();
  }

  function renderDetail() {
    const card = cardById(selected);
    detail.hidden = !card;
    root.toggleAttribute('data-detail-open', Boolean(card));
    for (const button of root.querySelectorAll('[data-task]')) {
      button.setAttribute('aria-expanded', String(button.dataset.task === selected));
    }
    if (!card) return;
    detail.querySelector('h3').textContent = card.title;
    detail.querySelector('[data-detail-notes]').replaceChildren(...card.notes.map(text => {
      const line = document.createElement('li');
      line.textContent = text;
      return line;
    }));
    const links = card.links.map(link => newLink(link.label, link.href));
    if (card.evidenceUrl) links.push(newLink('查看证据', card.evidenceUrl));
    detail.querySelector('[data-detail-links]').replaceChildren(...links);
    status.value = card.status;
    status.disabled = pending;
    for (const field of fields) {
      const label = field.closest('label');
      label.hidden = Boolean(label.dataset.show && label.dataset.show !== card.status);
      field.value = draft.get(selected)?.[field.dataset.field] ?? card[field.dataset.field];
      field.disabled = pending;
    }
    saveButton.disabled = pending || !draft.has(selected);
  }

  function openCard(id) {
    selected = id;
    renderDetail();
    status.focus();
  }

  async function load() {
    const response = await fetch('/api/board', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '读取失败');
    board = data;
  }

  async function save(card, changes, savedFields = []) {
    if (pending) return;
    pending = true;
    selected = key(card);
    for (const button of root.querySelectorAll('[data-task]')) button.draggable = false;
    focusButton.disabled = true;
    renderDetail();
    notice('正在保存…');
    try {
      const response = await fetch('/api/board', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: board.revision, id: card.id, changes }),
      });
      const data = await response.json();
      if (response.status === 409) await load();
      if (!response.ok) throw new Error(data.error || '保存失败');
      board = data;
      if (savedFields.length) draft.delete(key(card));
      notice('已写入 KANBAN.md');
    } catch (error) {
      notice(error.message || '保存失败', true);
    } finally {
      pending = false;
      if (!cardById(selected)) selected = null;
      renderBoard();
      renderDetail();
    }
  }

  function move(card, target) {
    if (card && target && card.status !== target) return save(card, { status: target });
  }

  root.querySelector('.columns').addEventListener('click', event => {
    const button = event.target.closest('[data-task]');
    if (button && !pending) openCard(button.dataset.task);
  });
  root.querySelector('.columns').addEventListener('dragstart', event => {
    const button = event.target.closest('[data-task]');
    if (!button || pending) return;
    dragged = button.dataset.task;
    event.dataTransfer.setData('text/plain', dragged);
    event.dataTransfer.effectAllowed = 'move';
  });
  root.querySelector('.columns').addEventListener('dragend', () => {
    dragged = null;
    for (const column of columns) column.removeAttribute('data-drop');
  });
  for (const column of columns) {
    column.addEventListener('dragover', event => {
      if (!dragged || pending) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      for (const item of columns) item.toggleAttribute('data-drop', item === column);
    });
    column.addEventListener('dragleave', event => {
      if (!event.relatedTarget || !column.contains(event.relatedTarget)) column.removeAttribute('data-drop');
    });
    column.addEventListener('drop', event => {
      if (!dragged || pending) return;
      event.preventDefault();
      column.removeAttribute('data-drop');
      const card = cardById(dragged);
      dragged = null;
      move(card, column.dataset.state);
    });
  }

  status.addEventListener('change', () => move(cardById(selected), status.value));
  form.addEventListener('input', event => {
    const name = event.target.dataset.field;
    const card = cardById(selected);
    if (!name || !card) return;
    const edits = draft.get(selected) || {};
    if (event.target.value === card[name]) delete edits[name];
    else edits[name] = event.target.value;
    if (Object.keys(edits).length) draft.set(selected, edits);
    else draft.delete(selected);
    saveButton.disabled = pending || !draft.has(selected);
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    const card = cardById(selected);
    const edits = draft.get(selected);
    if (card && edits) save(card, edits, Object.keys(edits));
  });
  focusButton.addEventListener('click', () => {
    if (!focusCard || pending) return;
    if (focusCard.status === 'ready') {
      move(focusCard, 'doing')?.then(() => status.focus());
    } else openCard(key(focusCard));
  });
  root.querySelector('.detail-close').addEventListener('click', () => {
    const button = [...root.querySelectorAll('[data-task]')].find(item => item.dataset.task === selected);
    selected = null;
    renderDetail();
    button?.focus({ preventScroll: true });
  });

  const appearances = [...root.querySelectorAll('[data-appearance]')];
  function setTheme(theme) {
    root.dataset.theme = theme;
    for (const button of appearances) button.setAttribute('aria-pressed', String(button.dataset.appearance === theme));
  }
  for (const button of appearances) {
    button.addEventListener('click', () => {
      setTheme(button.dataset.appearance);
      try { localStorage.setItem('sr-board-theme', button.dataset.appearance); } catch { /* Appearance still works. */ }
    });
  }
  try {
    if (localStorage.getItem('sr-board-theme') === 'chalk') setTheme('chalk');
  } catch { /* Use paper when browser storage is unavailable. */ }
  load().then(() => {
    renderBoard();
    const id = decodeURIComponent(location.hash.slice(1)).toLowerCase();
    if (cardById(id)) openCard(id);
  }).catch(error => {
    root.querySelector('#research-focus-title').textContent = '无法读取看板';
    notice(error.message, true);
  });
})();
