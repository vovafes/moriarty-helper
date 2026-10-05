"use strict";

const CATEGORY_TITLES = {
  moderation: "Модерация", community: "Сообщество", support: "Поддержка", team: "Команда",
  economy: "Экономика", family: "Семья", tools: "Инструменты", system: "Система",
};

const state = { me: null, guildId: null, modules: [], channels: [], roles: [], view: "overview", query: "" };
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid instanceof Node ? kid : document.createTextNode(kid));
  return n;
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  if (res.status === 401) { showLogin(); throw new Error("not logged in"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Ошибка ${res.status}`);
  return data;
}
const base = () => `/api/guild/${state.guildId}`;

let toastTimer;
function toast(text, kind = "ok") {
  const t = $("#toast");
  t.textContent = text;
  t.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3800);
}
function showLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

// ── bootstrap ───────────────────────────────────────────────────────────────

async function init() {
  try { state.me = await api("/api/me"); } catch { return; }
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#userName").textContent = state.me.user.name;
  $("#logoutBtn").onclick = async () => { await api("/auth/logout", { method: "POST" }).catch(() => {}); location.reload(); };
  $("#menuBtn").onclick = () => $(".sidebar").classList.toggle("open");
  $("#search").oninput = e => { state.query = e.target.value.trim().toLowerCase(); renderNav(); };
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s" && window._save) { e.preventDefault(); window._save(); }
  });

  const sel = $("#guildSelect");
  sel.replaceChildren(...state.me.guilds.map(g =>
    el("option", { value: g.id, disabled: !g.bot_present }, g.bot_present ? g.name : `${g.name} (бота нет)`)));
  sel.onchange = () => selectGuild(sel.value);
  const first = state.me.guilds.find(g => g.bot_present);
  if (!first) {
    $("#main").replaceChildren(el("div", { class: "empty" },
      "Нет серверов, где есть бот и у вас достаточно прав (Администратор или «Управление сервером»)."));
    return;
  }
  const saved = localStorage.getItem("guild");
  const pick = state.me.guilds.find(g => g.id === saved && g.bot_present) || first;
  sel.value = pick.id;
  await selectGuild(pick.id);
}

async function selectGuild(id) {
  state.guildId = id;
  localStorage.setItem("guild", id);
  [state.modules, state.channels, state.roles] = await Promise.all([
    api(`${base()}/modules`), api(`${base()}/channels`), api(`${base()}/roles`),
  ]);
  go("overview");
}

// ── navigation ──────────────────────────────────────────────────────────────

function go(view) {
  window._save = null;
  state.view = view;
  $(".sidebar").classList.remove("open");
  renderNav();
  renderView();
  window.scrollTo(0, 0);
}

const matches = m => !state.query || `${m.title} ${m.description} ${m.key}`.toLowerCase().includes(state.query);

function renderNav() {
  const items = [
    el("button", { class: state.view === "overview" ? "active" : "", onclick: () => go("overview") }, "🏠 Обзор"),
    el("button", { class: state.view === "audit" ? "active" : "", onclick: () => go("audit") }, "📜 Журнал действий"),
  ];
  let shown = 0;
  for (const cat of Object.keys(CATEGORY_TITLES)) {
    const mods = state.modules.filter(m => m.category === cat && matches(m));
    if (!mods.length) continue;
    items.push(el("div", { class: "group" }, CATEGORY_TITLES[cat]));
    for (const m of mods) {
      shown++;
      items.push(el("button", { class: state.view === `mod:${m.key}` ? "active" : "", onclick: () => go(`mod:${m.key}`) },
        `${m.icon} ${m.title}`, m.toggleable ? el("span", { class: `dot ${m.config.enabled ? "on" : ""}` }) : null));
    }
  }
  if (state.query && !shown) items.push(el("div", { class: "search-empty" }, "Ничего не найдено"));
  $("#nav").replaceChildren(...items);
}

function renderView() {
  const main = $("#main");
  if (state.view === "overview") { $("#topTitle").textContent = "Обзор"; return renderOverview(main); }
  if (state.view === "audit") { $("#topTitle").textContent = "Журнал действий"; return renderAudit(main); }
  const m = state.modules.find(x => `mod:${x.key}` === state.view);
  if (m) { $("#topTitle").textContent = m.title; return renderModule(main, m); }
}

// ── overview ────────────────────────────────────────────────────────────────

async function setEnabled(m, on) {
  try {
    const fresh = await api(`${base()}/modules/${m.key}`, { method: "PUT", body: JSON.stringify({ enabled: on }) });
    m.config = fresh.config;
    toast(on ? `«${m.title}» включён` : `«${m.title}» выключен`);
  } catch (e) { toast(e.message, "err"); }
  renderNav();
}

function switchEl(checked, onchange) {
  const inp = el("input", { type: "checkbox" });
  inp.checked = checked;
  inp.addEventListener("change", () => onchange(inp.checked));
  inp.addEventListener("click", e => e.stopPropagation());
  return el("label", { class: "switch", onclick: e => e.stopPropagation() }, inp, el("i"));
}

async function renderOverview(main) {
  main.replaceChildren(el("div", { class: "empty" }, "Загрузка…"));
  const [o, audit] = await Promise.all([api(`${base()}/overview`), api(`${base()}/audit`)]);
  const day = Date.now() / 1000 - 86400;
  const sections = [];
  for (const cat of Object.keys(CATEGORY_TITLES)) {
    const mods = state.modules.filter(m => m.category === cat);
    if (!mods.length) continue;
    sections.push(el("div", { class: "cat-title" }, CATEGORY_TITLES[cat]));
    sections.push(el("div", { class: "grid-mods" }, mods.map(m =>
      el("div", { class: "mod", onclick: () => go(`mod:${m.key}`) },
        el("h4", {}, `${m.icon} ${m.title}`, m.toggleable ? switchEl(m.config.enabled, on => setEnabled(m, on)) : el("span", { class: "pill on" }, "всегда")),
        el("p", {}, m.description)))));
  }
  main.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h2", {}, o.name), el("p", { class: "sub" }, "Обзор сервера"))),
    el("div", { class: "stats" },
      el("div", { class: "stat" }, el("b", {}, String(o.members ?? "—")), el("span", {}, "участников")),
      el("div", { class: "stat" }, el("b", {}, `${o.modules_enabled}/${o.modules_total}`), el("span", {}, "модулей включено")),
      el("div", { class: "stat" }, el("b", {}, String(audit.filter(a => a.ts > day).length)), el("span", {}, "изменений за сутки"))),
    ...sections);
}

// ── inputs ──────────────────────────────────────────────────────────────────

const channelOptions = kind => state.channels.filter(c => kind ? c.kind === kind : c.kind !== "category")
  .map(c => el("option", { value: c.id }, `${c.kind === "voice" ? "🔊" : c.kind === "category" ? "📁" : "#"} ${c.name}${c.category ? ` · ${c.category}` : ""}`));
const roleOptions = () => state.roles.map(r => el("option", { value: r.id }, `@${r.name}`));
const withEmpty = opts => [el("option", { value: "" }, "— не выбрано —"), ...opts];

function buildInput(f, value) {
  const built = buildInputRaw(f, value);
  if (f.readonly && built.el) { built.el.disabled = true; }
  return built;
}

function buildInputRaw(f, value) {
  switch (f.type) {
    case "bool": {
      const inp = el("input", { type: "checkbox" }); inp.checked = !!value;
      return { node: el("label", { class: "switch" }, inp, el("i")), read: () => inp.checked, el: inp };
    }
    case "number": {
      const inp = el("input", { type: "number", min: f.min, max: f.max, step: f.step || 1, value: value ?? "" });
      return { node: inp, read: () => (inp.value === "" ? null : Number(inp.value)), el: inp };
    }
    case "longtext": { const inp = el("textarea", {}, value ?? ""); return { node: inp, read: () => inp.value, el: inp }; }
    case "select": {
      const inp = el("select", {}, f.options.map(o => el("option", { value: typeof o === "object" ? o.value : o }, typeof o === "object" ? o.label : o)));
      inp.value = value ?? f.default; return { node: inp, read: () => inp.value, el: inp };
    }
    case "multiselect": {
      const chosen = new Set(value || []);
      const boxes = f.options.map(o => {
        const v = typeof o === "object" ? o.value : o, l = typeof o === "object" ? o.label : o;
        const cb = el("input", { type: "checkbox" }); cb.checked = chosen.has(v); cb.dataset.v = v;
        return el("label", {}, cb, l);
      });
      const box = el("div", { class: "checks" }, boxes);
      return { node: box, read: () => [...box.querySelectorAll("input:checked")].map(c => c.dataset.v), el: box };
    }
    case "channel": case "role": {
      const inp = el("select", {}, withEmpty(f.type === "channel" ? channelOptions(f.kind) : roleOptions()));
      inp.value = value ? String(value) : ""; return { node: inp, read: () => (inp.value ? Number(inp.value) : null), el: inp };
    }
    case "channels": case "roles": {
      const inp = el("select", { multiple: true }, f.type === "channels" ? channelOptions(f.kind) : roleOptions());
      const chosen = new Set((value || []).map(String));
      for (const o of inp.options) o.selected = chosen.has(o.value);
      return { node: inp, read: () => [...inp.selectedOptions].map(o => Number(o.value)), el: inp };
    }
    case "user": {
      const inp = el("input", { type: "text", placeholder: "ID пользователя", value: value ?? "" });
      return { node: inp, read: () => inp.value.trim(), el: inp };
    }
    default: {
      const inp = el("input", { type: "text", value: value ?? "" }); return { node: inp, read: () => inp.value, el: inp };
    }
  }
}

const fieldRow = (f, inp) => el("div", { class: "field" },
  el("label", {}, f.label, f.help ? el("span", { class: "help" }, f.help) : null), inp.node);

// ── module page ─────────────────────────────────────────────────────────────

function renderModule(main, m) {
  const inputs = {};
  const enabledInput = m.toggleable ? buildInput({ type: "bool" }, m.config.enabled) : null;

  // consecutive fields sharing `group` form one card
  const cards = [];
  let cur = null;
  for (const f of m.fields) {
    const g = f.group || "Настройки";
    if (!cur || cur.title !== g) { cur = { title: g, help: f.group_help, rows: [] }; cards.push(cur); }
    const inp = buildInput(f, m.config[f.key]);
    inputs[f.key] = inp;
    cur.rows.push(fieldRow(f, inp));
  }

  const msg = el("span", { class: "msg" });
  const saveBtn = el("button", { class: "btn primary" }, "Сохранить");
  const resetBtn = el("button", { class: "btn" }, "Сбросить");
  let initial = {};
  const readAll = () => Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.read()]));
  initial = JSON.stringify(readAll());
  const refreshDirty = () => {
    const dirty = JSON.stringify(readAll()) !== initial;
    msg.textContent = dirty ? "Есть несохранённые изменения" : "";
    msg.className = dirty ? "msg dirty" : "msg";
    saveBtn.disabled = !dirty; resetBtn.disabled = !dirty;
  };
  for (const i of Object.values(inputs)) { i.el.addEventListener("input", refreshDirty); i.el.addEventListener("change", refreshDirty); }

  async function save() {
    if (saveBtn.disabled) return;
    saveBtn.disabled = true;
    try {
      const fresh = await api(`${base()}/modules/${m.key}`, { method: "PUT", body: JSON.stringify(readAll()) });
      m.config = fresh.config;
      toast("Сохранено");
      renderModule(main, m);       // re-render from the server's view of the config
    } catch (e) { toast(e.message, "err"); saveBtn.disabled = false; }
  }
  window._save = save;
  saveBtn.onclick = save;
  resetBtn.onclick = () => renderModule(main, m);

  if (enabledInput) enabledInput.el.addEventListener("change", async () => {
    await setEnabled(m, enabledInput.read());
  });

  main.replaceChildren(
    el("div", { class: "head" },
      el("div", {}, el("h2", {}, `${m.icon} ${m.title}`), el("p", { class: "sub" }, m.description)),
      enabledInput ? enabledInput.node : null),
    ...cards.map(c => el("div", { class: "card" }, el("h3", { class: "group-title" }, c.title),
      c.help ? el("p", { class: "group-help" }, c.help) : null, ...c.rows)),
    !cards.length ? el("div", { class: "card empty" }, "У этого модуля нет настроек.") : null,
    cards.length ? el("div", { class: "savebar" }, msg, resetBtn, saveBtn) : null,
    m.actions.length ? actionsCard(m) : null,
    ...m.tables.map(t => tableCard(m, t)));
  refreshDirty();
}

// ── actions (buttons with small parameter forms) ────────────────────────────

function actionsCard(m) {
  return el("div", { class: "card" }, el("h3", { class: "group-title" }, "Действия"),
    el("div", { class: "actions" }, m.actions.map(a =>
      el("button", { class: `btn ${a.danger ? "danger" : ""}`, title: a.description || "", onclick: () => openAction(m, a) }, a.label))));
}

function openAction(m, a) {
  const dlg = $("#dlg");
  const inputs = {};
  const rows = a.params.map(f => { const inp = buildInput(f, f.default); inputs[f.key] = inp; return fieldRow(f, inp); });
  const run = el("button", { class: `btn ${a.danger ? "danger" : "primary"}` }, a.confirm && !a.params.length ? "Подтвердить" : "Выполнить");
  const cancel = el("button", { class: "btn" }, "Отмена");
  cancel.onclick = () => dlg.close();
  run.onclick = async () => {
    run.disabled = true;
    try {
      const params = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.read()]));
      const res = await api(`${base()}/modules/${m.key}/actions/${a.key}`, { method: "POST", body: JSON.stringify({ params }) });
      toast(res.message || "Готово");
      dlg.close();
      renderModule($("#main"), m);
    } catch (e) { toast(e.message, "err"); run.disabled = false; }
  };
  dlg.replaceChildren(el("h3", {}, a.label), a.description ? el("p", { class: "group-help" }, a.description) : null,
    a.confirm ? el("p", {}, a.confirm) : null, ...rows, el("div", { class: "row" }, cancel, run));
  dlg.showModal();
}

// ── tables ──────────────────────────────────────────────────────────────────

function tableCard(m, t) {
  const body = el("tbody");
  const head = () => el("h3", { class: "card-title" }, t.title || "Данные");
  const card = el("div", { class: "card" }, head(),
    el("div", { class: "tbl-scroll" }, el("table", {}, el("thead", {}, el("tr", {}, t.columns.map(c => el("th", {}, c.label)))), body)));
  const fmt = (c, v) => {
    if (v === null || v === undefined || v === "") return "—";
    if (c.format === "time") return new Date(v * 1000).toLocaleString("ru-RU");
    if (c.format === "bool") return v ? "да" : "нет";
    return v;
  };
  api(`${base()}/modules/${m.key}/tables/${t.id}`).then(rows => {
    if (!rows.length) { card.replaceChildren(head(), el("div", { class: "empty" }, "Пока пусто.")); return; }
    const cell = (c, r) => c.format === "link" && r[c.key]
      ? el("td", {}, el("a", { href: r[c.key], target: "_blank", rel: "noopener" }, "Открыть"))
      : el("td", {}, String(fmt(c, r[c.key])));
    body.replaceChildren(...rows.map(r => el("tr", {}, t.columns.map(c => cell(c, r)))));
  }).catch(e => card.append(el("div", { class: "empty" }, e.message)));
  return card;
}

// ── audit log ───────────────────────────────────────────────────────────────

async function renderAudit(main) {
  main.replaceChildren(el("div", { class: "empty" }, "Загрузка…"));
  const rows = await api(`${base()}/audit`);
  const modTitle = k => state.modules.find(m => m.key === k)?.title || k || "—";
  const fmt = ts => new Date(ts * 1000).toLocaleString("ru-RU");
  const summary = r => {
    if (!r.details) return r.action;
    return Object.entries(r.details).map(([k, v]) =>
      v && typeof v === "object" && "to" in v ? `${k}: ${JSON.stringify(v.from)} → ${JSON.stringify(v.to)}` : `${k}: ${JSON.stringify(v)}`).join("; ");
  };
  main.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h2", {}, "📜 Журнал действий"), el("p", { class: "sub" }, "Кто и что менял в панели"))),
    rows.length
      ? el("div", { class: "card" }, el("div", { class: "tbl-scroll" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Когда", "Кто", "Модуль", "Что"].map(h => el("th", {}, h)))),
          el("tbody", {}, rows.map(r => el("tr", {},
            el("td", {}, fmt(r.ts)), el("td", {}, r.user_name || "—"), el("td", {}, modTitle(r.module)),
            el("td", {}, el("code", {}, (r.action.startsWith("action:") ? `${r.action.slice(7)} · ` : "") + summary(r)))))))))
      : el("div", { class: "card empty" }, "Изменений пока не было."));
}

init();
