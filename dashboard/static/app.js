"use strict";

const CATEGORY_TITLES = {
  moderation: "Модерация", community: "Сообщество", support: "Поддержка",
  team: "Команда", tools: "Инструменты",
};

const state = { me: null, guildId: null, modules: [], channels: [], roles: [], view: "overview" };
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) n.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
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

let toastTimer;
function toast(text, kind = "ok") {
  const t = $("#toast");
  t.textContent = text;
  t.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
}

function showLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }

// ── bootstrap ───────────────────────────────────────────────────────────────

async function init() {
  try { state.me = await api("/api/me"); } catch { return; }
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#userName").textContent = state.me.user.name;
  $("#logoutBtn").onclick = async () => { await api("/auth/logout", { method: "POST" }).catch(() => {}); location.reload(); };

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
  const base = `/api/guild/${id}`;
  [state.modules, state.channels, state.roles] = await Promise.all([
    api(`${base}/modules`), api(`${base}/channels`), api(`${base}/roles`),
  ]);
  state.view = "overview";
  renderNav();
  renderView();
}

// ── navigation ──────────────────────────────────────────────────────────────

function go(view) { state.view = view; renderNav(); renderView(); }

function renderNav() {
  const nav = $("#nav");
  const items = [
    el("button", { class: state.view === "overview" ? "active" : "", onclick: () => go("overview") }, "🏠 Обзор"),
    el("button", { class: state.view === "audit" ? "active" : "", onclick: () => go("audit") }, "📜 Журнал действий"),
  ];
  for (const cat of Object.keys(CATEGORY_TITLES)) {
    const mods = state.modules.filter(m => m.category === cat);
    if (!mods.length) continue;
    items.push(el("div", { class: "group" }, CATEGORY_TITLES[cat]));
    for (const m of mods) {
      items.push(el("button", { class: state.view === `mod:${m.key}` ? "active" : "", onclick: () => go(`mod:${m.key}`) },
        `${m.icon} ${m.title}`, el("span", { class: `dot ${m.config.enabled ? "on" : ""}` })));
    }
  }
  nav.replaceChildren(...items);
}

function renderView() {
  const main = $("#main");
  if (state.view === "overview") return renderOverview(main);
  if (state.view === "audit") return renderAudit(main);
  const m = state.modules.find(x => `mod:${x.key}` === state.view);
  if (m) return renderModule(main, m);
}

// ── overview ────────────────────────────────────────────────────────────────

async function renderOverview(main) {
  main.replaceChildren(el("div", { class: "empty" }, "Загрузка…"));
  const o = await api(`/api/guild/${state.guildId}/overview`);
  const grid = el("div", { class: "grid-mods" }, state.modules.map(m =>
    el("div", { class: "mod", onclick: () => go(`mod:${m.key}`) },
      el("h4", {}, m.icon, m.title, el("span", { class: `pill ${m.config.enabled ? "on" : ""}` }, m.config.enabled ? "вкл" : "выкл")),
      el("p", {}, m.description))));
  main.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h2", {}, o.name), el("p", { class: "sub" }, "Обзор сервера"))),
    el("div", { class: "stats" },
      el("div", { class: "stat" }, el("b", {}, String(o.members ?? "—")), el("span", {}, "участников")),
      el("div", { class: "stat" }, el("b", {}, `${o.modules_enabled}/${o.modules_total}`), el("span", {}, "модулей включено"))),
    state.modules.length ? grid : el("div", { class: "empty" }, "Модули ещё не подключены."));
}

// ── module form ─────────────────────────────────────────────────────────────

function optionList(items, withEmpty) {
  const opts = withEmpty ? [el("option", { value: "" }, "— не выбрано —")] : [];
  return opts.concat(items);
}

function channelOptions(kind) {
  return state.channels.filter(c => !kind || c.kind === kind)
    .map(c => el("option", { value: c.id }, `${c.kind === "voice" ? "🔊" : "#"} ${c.name}${c.category ? ` · ${c.category}` : ""}`));
}
const roleOptions = () => state.roles.map(r => el("option", { value: r.id }, `@${r.name}`));

function buildInput(f, value) {
  const get = {};
  let node;
  switch (f.type) {
    case "bool": {
      const inp = el("input", { type: "checkbox" });
      inp.checked = !!value;
      node = el("label", { class: "switch" }, inp, el("i"));
      get.read = () => inp.checked;
      break;
    }
    case "number": {
      const inp = el("input", { type: "number", min: f.min, max: f.max, step: f.step || 1, value: value ?? "" });
      node = inp; get.read = () => (inp.value === "" ? null : Number(inp.value));
      break;
    }
    case "longtext": {
      const inp = el("textarea", {}, value ?? ""); node = inp; get.read = () => inp.value; break;
    }
    case "select": {
      const inp = el("select", {}, f.options.map(o => {
        const v = typeof o === "object" ? o.value : o, l = typeof o === "object" ? o.label : o;
        return el("option", { value: v }, l);
      }));
      inp.value = value ?? f.default; node = inp; get.read = () => inp.value; break;
    }
    case "channel": case "role": {
      const inp = el("select", {}, optionList(f.type === "channel" ? channelOptions(f.kind) : roleOptions(), true));
      inp.value = value ? String(value) : ""; node = inp; get.read = () => (inp.value ? Number(inp.value) : null); break;
    }
    case "channels": case "roles": {
      const inp = el("select", { multiple: true }, f.type === "channels" ? channelOptions(f.kind) : roleOptions());
      const chosen = new Set((value || []).map(String));
      for (const o of inp.options) o.selected = chosen.has(o.value);
      node = inp; get.read = () => [...inp.selectedOptions].map(o => Number(o.value)); break;
    }
    default: {
      const inp = el("input", { type: "text", value: value ?? "" }); node = inp; get.read = () => inp.value;
    }
  }
  return { node, read: get.read };
}

function renderModule(main, m) {
  const inputs = {};
  const enabledInput = buildInput({ type: "bool" }, m.config.enabled);
  const fields = m.fields.map(f => {
    const inp = buildInput(f, m.config[f.key]);
    inputs[f.key] = inp;
    return el("div", { class: "field" },
      el("label", {}, f.label, f.help ? el("span", { class: "help" }, f.help) : null),
      inp.node);
  });
  const msg = el("span", { class: "msg" });
  const saveBtn = el("button", { class: "btn primary" }, "Сохранить");

  async function save(patch, okText) {
    saveBtn.disabled = true;
    try {
      const fresh = await api(`/api/guild/${state.guildId}/modules/${m.key}`, { method: "PUT", body: JSON.stringify(patch) });
      m.config = fresh.config;
      renderNav();
      msg.textContent = "";
      toast(okText);
    } catch (e) { toast(e.message, "err"); }
    saveBtn.disabled = false;
  }

  enabledInput.node.querySelector("input").addEventListener("change", () =>
    save({ enabled: enabledInput.read() }, enabledInput.read() ? "Модуль включён" : "Модуль выключен"));
  saveBtn.onclick = () => {
    const patch = {};
    for (const [k, inp] of Object.entries(inputs)) patch[k] = inp.read();
    save(patch, "Сохранено");
  };

  main.replaceChildren(
    el("div", { class: "head" },
      el("div", {}, el("h2", {}, `${m.icon} ${m.title}`), el("p", { class: "sub" }, m.description)),
      enabledInput.node),
    fields.length
      ? el("div", { class: "card" }, fields)
      : el("div", { class: "card empty" }, "У этого модуля пока нет настроек."),
    fields.length ? el("div", { class: "savebar" }, msg, saveBtn) : null);
}

// ── audit log ───────────────────────────────────────────────────────────────

async function renderAudit(main) {
  main.replaceChildren(el("div", { class: "empty" }, "Загрузка…"));
  const rows = await api(`/api/guild/${state.guildId}/audit`);
  const modTitle = k => state.modules.find(m => m.key === k)?.title || k || "—";
  const fmt = ts => new Date(ts * 1000).toLocaleString("ru-RU");
  const summary = r => {
    if (!r.details) return "";
    return Object.entries(r.details).map(([k, v]) =>
      v && typeof v === "object" && "to" in v ? `${k}: ${JSON.stringify(v.from)} → ${JSON.stringify(v.to)}` : `${k}: ${JSON.stringify(v)}`).join("; ");
  };
  main.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h2", {}, "📜 Журнал действий"), el("p", { class: "sub" }, "Кто и что менял в настройках"))),
    rows.length
      ? el("div", { class: "card" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Когда", "Кто", "Модуль", "Что изменилось"].map(h => el("th", {}, h)))),
          el("tbody", {}, rows.map(r => el("tr", {},
            el("td", {}, fmt(r.ts)), el("td", {}, r.user_name || "—"), el("td", {}, modTitle(r.module)),
            el("td", {}, el("code", {}, summary(r) || r.action)))))))
      : el("div", { class: "card empty" }, "Изменений пока не было."));
}

init();
