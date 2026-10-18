/**
 * Subscription Manager frontend.
 *
 * The Python server at main.py stores subscriptions and serves this page.
 * A subscription looks like this:
 * {
 *   id, name, category, cost, cycle, renewalDate, status,
 *   reminderDays, usage, lastUsed, notes
 * }
 * cost is the amount charged each cycle. Spending totals convert that
 * into a monthly equivalent. Paused and canceled plans stay in the list
 * and stay out of the totals.
 */

const CATEGORIES = [
  { id: "streaming", label: "Streaming", color: "#c4511a" },
  { id: "music", label: "Music", color: "#7a3e6d" },
  { id: "software", label: "Software", color: "#1e4d7b" },
  { id: "cloud", label: "Cloud", color: "#3d6b8c" },
  { id: "news", label: "News", color: "#8a5a12" },
  { id: "fitness", label: "Fitness", color: "#1f6b45" },
  { id: "gaming", label: "Gaming", color: "#5c4d8a" },
  { id: "shopping", label: "Shopping", color: "#8e4b2f" },
  { id: "other", label: "Other", color: "#6d655c" },
];

const CYCLES = [
  { id: "weekly", label: "Weekly", per: "week" },
  { id: "monthly", label: "Monthly", per: "month" },
  { id: "yearly", label: "Yearly", per: "year" },
];

const STATUSES = [
  { id: "active", label: "Active" },
  { id: "trial", label: "Free trial" },
  { id: "paused", label: "Paused" },
  { id: "canceled", label: "Canceled" },
];

const USAGES = [
  { id: "daily", label: "Every day" },
  { id: "weekly", label: "Most weeks" },
  { id: "monthly", label: "About monthly" },
  { id: "rarely", label: "Rarely" },
  { id: "never", label: "Never" },
];

const REMINDERS = [
  { id: "1", label: "1 day before" },
  { id: "3", label: "3 days before" },
  { id: "7", label: "7 days before" },
  { id: "14", label: "14 days before" },
  { id: "30", label: "30 days before" },
];

const SORTS = [
  { id: "renewal", label: "Next renewal" },
  { id: "cost", label: "Highest monthly cost" },
  { id: "usage", label: "Least used" },
  { id: "name", label: "Name" },
];

const VIEWS = ["overview", "subscriptions", "spending", "reminders", "usage"];

const PAGE = {
  overview: {
    kicker: "Dashboard",
    title: "All your subscriptions",
    intro: "What you pay, what renews soon, and what you might not need.",
  },
  subscriptions: {
    kicker: "Library",
    title: "Manage subscriptions",
    intro: "Search, filter, and update every plan in one list.",
  },
  spending: {
    kicker: "Spending",
    title: "Monthly and yearly cost",
    intro: "Active plans are totaled here. Trials stay separate until you keep them.",
  },
  reminders: {
    kicker: "Reminders",
    title: "Before the next charge",
    intro: "Active plans and free trials show up here, using the warning you set on each one.",
  },
  usage: {
    kicker: "Check-in",
    title: "What you actually use",
    intro: "Mark how often you open each active plan or trial. Rarely and never become cancel candidates.",
  },
};

const USAGE_RANK = { never: 0, rarely: 1, monthly: 2, weekly: 3, daily: 4 };
const CATEGORY_IDS = CATEGORIES.map((item) => item.id);
const CYCLE_IDS = CYCLES.map((item) => item.id);
const STATUS_IDS = STATUSES.map((item) => item.id);
const USAGE_IDS = USAGES.map((item) => item.id);
const REMINDER_IDS = REMINDERS.map((item) => Number(item.id));

const moneyFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const state = {
  subscriptions: [],
  view: "overview",
  query: "",
  category: "all",
  status: "all",
  sort: "renewal",
  editingId: null,
  confirm: null,
  loadError: "",
};

let toastTimer = 0;

function uid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `sub-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function lookup(list, id) {
  return list.find((item) => item.id === id) || null;
}

function categoryOf(id) {
  return lookup(CATEGORIES, id) || CATEGORIES[CATEGORIES.length - 1];
}

function labelOf(list, id) {
  return lookup(list, id)?.label || "";
}

function startOfToday() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function formatISODate(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function addDays(days) {
  const date = startOfToday();
  date.setDate(date.getDate() + days);
  return formatISODate(date);
}

function parseISODate(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || "")) return null;
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
    return null;
  }
  return date;
}

function daysUntil(iso) {
  const target = parseISODate(iso);
  if (!target) return null;
  return Math.round((target.getTime() - startOfToday().getTime()) / 86400000);
}

function formatDisplay(iso) {
  const date = parseISODate(iso);
  if (!date) return "No date";
  const options = { month: "short", day: "numeric" };
  if (date.getFullYear() !== startOfToday().getFullYear()) options.year = "numeric";
  return new Intl.DateTimeFormat("en-US", options).format(date);
}

function money(value) {
  return moneyFormat.format(Number.isFinite(value) ? value : 0);
}

function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function joinNames(items) {
  const names = items.map((item) => item.name);
  if (names.length <= 1) return names[0] || "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

function monthlyEquivalent(sub) {
  if (sub.cycle === "weekly") return sub.cost * (52 / 12);
  if (sub.cycle === "yearly") return sub.cost / 12;
  return sub.cost;
}

function yearlyEquivalent(sub) {
  if (sub.cycle === "weekly") return sub.cost * 52;
  if (sub.cycle === "yearly") return sub.cost;
  return sub.cost * 12;
}

function sumBy(list, fn) {
  return list.reduce((total, item) => total + fn(item), 0);
}

function isActive(sub) {
  return sub.status === "active";
}

function isTrial(sub) {
  return sub.status === "trial";
}

function isBillable(sub) {
  return isActive(sub) || isTrial(sub);
}

function isIdle(sub) {
  return isBillable(sub) && (sub.usage === "rarely" || sub.usage === "never");
}

function isReminder(sub) {
  if (!isBillable(sub)) return false;
  const days = daysUntil(sub.renewalDate);
  return Number.isFinite(days) && days <= sub.reminderDays;
}

function activeSubs() {
  return state.subscriptions.filter(isActive);
}

function trialSubs() {
  return state.subscriptions.filter(isTrial);
}

function billableSubs() {
  return state.subscriptions.filter(isBillable);
}

function idleSubs() {
  return state.subscriptions.filter(isIdle).sort(compareUsage);
}

function reminderSubs() {
  return billableSubs().filter(isReminder).sort(compareRenewal);
}

function clampCost(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(999999.99, Math.max(0, Math.round(number * 100) / 100));
}

function isValidSubscription(value) {
  return Boolean(value)
    && typeof value === "object"
    && typeof value.name === "string"
    && value.name.trim()
    && typeof value.renewalDate === "string"
    && parseISODate(value.renewalDate)
    && Number.isFinite(Number(value.cost));
}

function normalize(sub) {
  const reminderDays = Number(sub.reminderDays);
  return {
    id: String(sub.id || uid()),
    name: String(sub.name).trim().slice(0, 60),
    category: CATEGORY_IDS.includes(sub.category) ? sub.category : "other",
    cost: clampCost(sub.cost),
    cycle: CYCLE_IDS.includes(sub.cycle) ? sub.cycle : "monthly",
    renewalDate: sub.renewalDate,
    status: STATUS_IDS.includes(sub.status) ? sub.status : "active",
    reminderDays: REMINDER_IDS.includes(reminderDays) ? reminderDays : 7,
    usage: USAGE_IDS.includes(sub.usage) ? sub.usage : "monthly",
    lastUsed: parseISODate(sub.lastUsed) ? sub.lastUsed : "",
    notes: String(sub.notes || "").trim().slice(0, 240),
  };
}

async function request(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = Array.isArray(payload.errors) && payload.errors.length
      ? payload.errors.join(" ")
      : "The server could not save that change.";
    throw new Error(message);
  }
  return payload;
}

async function load() {
  const data = await request("GET", "/api/subscriptions");
  if (!Array.isArray(data)) throw new Error("The server returned an unexpected list.");
  state.subscriptions = data.filter(isValidSubscription).map(normalize);
  state.loadError = "";
}

function findSub(id) {
  return state.subscriptions.find((sub) => sub.id === id) || null;
}

function compareRenewal(a, b) {
  const days = (daysUntil(a.renewalDate) ?? 9999) - (daysUntil(b.renewalDate) ?? 9999);
  if (days !== 0) return days;
  return a.name.localeCompare(b.name);
}

function compareUsage(a, b) {
  const rank = USAGE_RANK[a.usage] - USAGE_RANK[b.usage];
  if (rank !== 0) return rank;
  return a.name.localeCompare(b.name);
}

function compareSubs(a, b) {
  if (state.sort === "cost") return monthlyEquivalent(b) - monthlyEquivalent(a) || a.name.localeCompare(b.name);
  if (state.sort === "name") return a.name.localeCompare(b.name);
  if (state.sort === "usage") return compareUsage(a, b);
  return compareRenewal(a, b);
}

function isFiltering() {
  return Boolean(state.query.trim() || state.category !== "all" || state.status !== "all");
}

function filteredSubs() {
  const query = state.query.trim().toLowerCase();
  return state.subscriptions.filter((sub) => {
    if (state.category !== "all" && sub.category !== state.category) return false;
    if (state.status !== "all" && sub.status !== state.status) return false;
    if (!query) return true;
    const haystack = `${sub.name} ${labelOf(CATEGORIES, sub.category)} ${sub.notes}`.toLowerCase();
    return haystack.includes(query);
  }).sort(compareSubs);
}

function renewalPhrase(sub) {
  const days = daysUntil(sub.renewalDate);
  if (!Number.isFinite(days)) return "No date set";
  if (days < 0) return days === -1 ? "1 day overdue" : `${Math.abs(days)} days overdue`;
  if (days === 0) return sub.status === "trial" ? "Trial ends today" : "Renews today";
  if (days === 1) return sub.status === "trial" ? "Trial ends tomorrow" : "Renews tomorrow";
  return sub.status === "trial" ? `Trial ends in ${days} days` : `Renews in ${days} days`;
}

function shortWhen(sub) {
  const days = daysUntil(sub.renewalDate);
  if (!Number.isFinite(days)) return "No date";
  if (days < 0) return days === -1 ? "1 day overdue" : `${Math.abs(days)} days overdue`;
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return `In ${days} days`;
}

function whenClass(sub) {
  const days = daysUntil(sub.renewalDate);
  if (!Number.isFinite(days)) return "";
  if (days < 0) return "is-overdue";
  if (days <= sub.reminderDays) return "is-soon";
  return "";
}

function lastUsedPhrase(iso) {
  if (!iso) return "No check-in yet";
  const days = daysUntil(iso);
  if (days === 0) return "Last used today";
  if (days === -1) return "Last used yesterday";
  if (Number.isFinite(days) && days < 0) return `Last used ${Math.abs(days)} days ago`;
  return `Last used ${formatDisplay(iso)}`;
}

function statusTag(sub) {
  return `<span class="tag tag-${escapeHtml(sub.status)}">${escapeHtml(labelOf(STATUSES, sub.status))}</span>`;
}

function usageTag(sub) {
  const idle = sub.usage === "rarely" || sub.usage === "never";
  return `<span class="tag${idle ? " tag-idle" : ""}">${escapeHtml(labelOf(USAGES, sub.usage))}</span>`;
}

function optionHtml(options, selected, allLabel) {
  const head = allLabel
    ? `<option value="all"${selected === "all" ? " selected" : ""}>${escapeHtml(allLabel)}</option>`
    : "";
  const body = options.map((option) => {
    const value = String(option.id);
    const isSelected = value === String(selected) ? " selected" : "";
    return `<option value="${escapeHtml(value)}"${isSelected}>${escapeHtml(option.label)}</option>`;
  }).join("");
  return head + body;
}

function exclusionNote() {
  const paused = state.subscriptions.filter((sub) => sub.status === "paused").length;
  const canceled = state.subscriptions.filter((sub) => sub.status === "canceled").length;
  const parts = [];
  if (paused) parts.push(plural(paused, "paused plan"));
  if (canceled) parts.push(plural(canceled, "canceled plan"));
  if (!parts.length) return "";
  const subject = parts.length === 2 ? `${parts[0]} and ${parts[1]} stay` : `${parts[0]} stays`;
  return `<p class="fine">${subject} in the list and out of the totals.</p>`;
}

function trialNote() {
  const total = sumBy(trialSubs(), monthlyEquivalent);
  if (total <= 0) return "";
  return `<p class="fine">Free trials would add another ${money(total)} per month if you keep them.</p>`;
}

function emptyBlock(title, copy, action) {
  const button = action
    ? `<button class="btn btn-primary" type="button" data-action="${action.action}">${escapeHtml(action.label)}</button>`
    : "";
  return `<div class="empty"><h2>${escapeHtml(title)}</h2><p>${escapeHtml(copy)}</p>${button}</div>`;
}

function renderUpcoming(sub) {
  return `<button class="link-row" type="button" data-action="edit" data-id="${escapeHtml(sub.id)}" aria-label="Edit ${escapeHtml(sub.name)}">
    <span class="dot" style="background:${categoryOf(sub.category).color}"></span>
    <span>
      <strong>${escapeHtml(sub.name)}</strong>
      <small>${escapeHtml(labelOf(CATEGORIES, sub.category))} · ${escapeHtml(renewalPhrase(sub))}</small>
    </span>
    <em>${money(monthlyEquivalent(sub))}/mo</em>
  </button>`;
}

function renderOverview() {
  const activeMonthly = sumBy(activeSubs(), monthlyEquivalent);
  const activeYearly = sumBy(activeSubs(), yearlyEquivalent);
  const rotating = billableSubs().length;
  const reminders = reminderSubs();
  const idle = idleSubs();
  const upcoming = billableSubs().sort(compareRenewal).slice(0, 5);
  const banner = reminders.length
    ? `<button class="banner" type="button" data-action="navigate" data-view="reminders">
        <span>${reminders.length === 1 ? "1 subscription is inside its reminder window." : `${reminders.length} subscriptions are inside their reminder window.`}</span>
        <span>Review them</span>
      </button>`
    : "";
  const upcomingBody = upcoming.length
    ? `<div class="stack">${upcoming.map(renderUpcoming).join("")}</div>`
    : `<p class="fine">Add an active subscription to see what renews next.</p>`;
  const idleBody = idle.length
    ? `<div class="stack">${idle.map(renderUpcoming).join("")}</div>
       <button class="text-btn" type="button" data-action="navigate" data-view="usage">Open check-ins</button>`
    : `<p class="fine">Nothing looks idle. Plans you mark rarely or never will gather here.</p>`;
  const categories = spendByCategory(activeSubs());

  return `
    <section class="stats">
      <button class="stat" type="button" data-action="navigate" data-view="spending">
        <span>Per month</span>
        <strong>${money(activeMonthly)}</strong>
        <em>Active plans</em>
      </button>
      <button class="stat" type="button" data-action="navigate" data-view="spending">
        <span>Per year</span>
        <strong>${money(activeYearly)}</strong>
        <em>Same plans, annualized</em>
      </button>
      <button class="stat" type="button" data-action="navigate" data-view="subscriptions">
        <span>In rotation</span>
        <strong>${rotating}</strong>
        <em>Active and on trial</em>
      </button>
      <button class="stat${reminders.length ? " is-alert" : ""}" type="button" data-action="navigate" data-view="reminders">
        <span>Reminders</span>
        <strong>${reminders.length}</strong>
        <em>${reminders.length ? "Inside the warning window" : "Nothing due soon"}</em>
      </button>
    </section>
    ${trialNote()}
    ${exclusionNote()}
    ${banner}
    <div class="split">
      <section class="panel">
        <div class="panel-head"><h2>Coming up</h2><span>Next renewals</span></div>
        ${upcomingBody}
      </section>
      <section class="panel">
        <div class="panel-head"><h2>Second look</h2><span>${idle.length ? money(sumBy(idle, monthlyEquivalent)) + "/mo" : "All in use"}</span></div>
        ${idleBody}
      </section>
    </div>
    <section class="panel block">
      <div class="panel-head"><h2>Where the money goes</h2><span>Monthly equivalent</span></div>
      ${categories.length ? renderBars(categories) : `<p class="fine">Add an active subscription to see spending by category.</p>`}
    </section>
  `;
}

function spendByCategory(subs) {
  const totals = new Map();
  subs.forEach((sub) => {
    totals.set(sub.category, (totals.get(sub.category) || 0) + monthlyEquivalent(sub));
  });
  return [...totals.entries()].sort((a, b) => b[1] - a[1]);
}

function renderBars(entries) {
  const max = Math.max(...entries.map((entry) => entry[1]), 1);
  return `<div class="bars">${entries.map(([category, value]) => {
    const pct = (value / max) * 100;
    const color = categoryOf(category).color;
    return `<div class="bar-row">
      <div class="bar-label"><span class="dot" style="background:${color}"></span>${escapeHtml(labelOf(CATEGORIES, category))}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%; background:${color}"></div></div>
      <div class="bar-value">${money(value)}</div>
    </div>`;
  }).join("")}</div>`;
}

function renderSubRow(sub) {
  const cycle = lookup(CYCLES, sub.cycle);
  const quiet = sub.status === "paused" || sub.status === "canceled" ? " is-quiet" : "";
  const equivalent = sub.cycle === "monthly" ? "" : ` · ${money(monthlyEquivalent(sub))}/mo`;
  const notes = sub.notes ? `<p class="sub-notes">${escapeHtml(sub.notes)}</p>` : "";
  return `<article class="sub${quiet}">
    <span class="dot" style="background:${categoryOf(sub.category).color}"></span>
    <div class="sub-main">
      <div class="sub-title">
        <h3>${escapeHtml(sub.name)}</h3>
        ${statusTag(sub)}
        ${usageTag(sub)}
      </div>
      <p class="sub-meta">${escapeHtml(labelOf(CATEGORIES, sub.category))} · ${escapeHtml(cycle.label)}</p>
      ${notes}
    </div>
    <div class="sub-side">
      <div class="sub-cost">
        <strong>${money(sub.cost)}</strong>
        <span>per ${escapeHtml(cycle.per)}${equivalent}</span>
      </div>
      <div class="sub-when ${whenClass(sub)}">
        <strong>${escapeHtml(shortWhen(sub))}</strong>
        <span>${escapeHtml(formatDisplay(sub.renewalDate))}</span>
      </div>
      <div class="sub-actions">
        <button type="button" data-action="edit" data-id="${escapeHtml(sub.id)}">Edit</button>
        <button class="danger" type="button" data-action="ask-delete" data-id="${escapeHtml(sub.id)}">Remove</button>
      </div>
    </div>
  </article>`;
}

function renderSubList() {
  if (!state.subscriptions.length) {
    return emptyBlock(
      "Your list is empty",
      "Add a subscription, or bring back the sample set.",
      { action: "ask-seed", label: "Load sample subscriptions" },
    );
  }
  const items = filteredSubs();
  if (!items.length) {
    return emptyBlock("No matches", "Try a different name, category, or status.");
  }
  const count = isFiltering()
    ? `${items.length} of ${state.subscriptions.length} subscriptions`
    : plural(items.length, "subscription");
  return `<p class="count">${count}</p><div class="list">${items.map(renderSubRow).join("")}</div>`;
}

function renderSubscriptions() {
  return `
    <div class="toolbar">
      <label>Search
        <input id="search" type="search" placeholder="Name, category, or note" value="${escapeHtml(state.query)}">
      </label>
      <label>Category
        <select id="filter-category">${optionHtml(CATEGORIES, state.category, "All categories")}</select>
      </label>
      <label>Status
        <select id="filter-status">${optionHtml(STATUSES, state.status, "All statuses")}</select>
      </label>
      <label>Sort
        <select id="filter-sort">${optionHtml(SORTS, state.sort)}</select>
      </label>
    </div>
    <div id="list-host">${renderSubList()}</div>
    <div class="quiet-actions">
      <button type="button" data-action="ask-seed">Restore sample data</button>
      <button type="button" data-action="download">Download JSON</button>
      <button class="danger" type="button" data-action="ask-clear">Remove all</button>
    </div>
  `;
}

function renderSpendRow(sub, total, showShare) {
  const monthly = monthlyEquivalent(sub);
  const share = total > 0 ? Math.round((monthly / total) * 100) : 0;
  const cycle = lookup(CYCLES, sub.cycle);
  return `<div class="spend-row${showShare ? "" : " no-share"}">
    <div>
      <strong>${escapeHtml(sub.name)}</strong>
      <p>${escapeHtml(labelOf(CATEGORIES, sub.category))} · ${money(sub.cost)} / ${escapeHtml(cycle.per)}</p>
    </div>
    ${showShare ? `<b>${share}%</b>` : ""}
    <strong>${money(monthly)}/mo</strong>
  </div>`;
}

function renderSpending() {
  const active = activeSubs().sort((a, b) => monthlyEquivalent(b) - monthlyEquivalent(a));
  const trials = trialSubs().sort((a, b) => monthlyEquivalent(b) - monthlyEquivalent(a));
  const monthly = sumBy(active, monthlyEquivalent);
  const yearly = sumBy(active, yearlyEquivalent);
  const idle = idleSubs();
  const idleTotal = sumBy(idle, monthlyEquivalent);
  const trialTotal = sumBy(trials, monthlyEquivalent);
  const insights = [
    idleTotal > 0
      ? `<article class="insight warm"><span class="label">Could save</span><strong>${money(idleTotal)}</strong><p>per month by canceling ${escapeHtml(joinNames(idle))}, marked rarely or never.</p></article>`
      : "",
    trialTotal > 0
      ? `<article class="insight cool"><span class="label">If trials convert</span><strong>${money(trialTotal)}</strong><p>per month would be added by ${escapeHtml(joinNames(trials))}.</p></article>`
      : "",
  ].join("");

  return `
    <section class="hero">
      <article>
        <p class="label">Monthly</p>
        <strong>${money(monthly)}</strong>
        <span>Active subscriptions</span>
      </article>
      <article>
        <p class="label">Yearly</p>
        <strong>${money(yearly)}</strong>
        <span>Weekly plans count as 52 charges. Yearly plans count once.</span>
      </article>
    </section>
    ${exclusionNote()}
    ${insights ? `<div class="insights">${insights}</div>` : ""}
    <section class="panel block">
      <div class="panel-head"><h2>By category</h2><span>Active plans, as a monthly equivalent</span></div>
      ${active.length ? renderBars(spendByCategory(active)) : `<p class="fine">Active plans will show up here.</p>`}
    </section>
    <section class="block">
      <div class="block-head"><h2>Each active plan</h2><span>Share of the monthly total</span></div>
      ${active.length ? `<div class="list spend-list">${active.map((sub) => renderSpendRow(sub, monthly, true)).join("")}</div>` : emptyBlock("No active plans", "Paused, canceled, and trial plans are not in this total.")}
    </section>
    ${trials.length ? `<section class="block"><div class="block-head"><h2>Trials</h2><span>Shown apart from the total above</span></div><div class="list spend-list">${trials.map((sub) => renderSpendRow(sub, trialTotal, false)).join("")}</div></section>` : ""}
  `;
}

function renderReminderRow(sub) {
  const cycle = lookup(CYCLES, sub.cycle);
  const trial = sub.status === "trial" ? ` ${statusTag(sub)}` : "";
  return `<article class="remind">
    <span class="dot" style="background:${categoryOf(sub.category).color}"></span>
    <div>
      <h3>${escapeHtml(sub.name)}${trial}</h3>
      <p>${escapeHtml(formatDisplay(sub.renewalDate))} · ${escapeHtml(renewalPhrase(sub))} · warns ${sub.reminderDays} ${sub.reminderDays === 1 ? "day" : "days"} before</p>
    </div>
    <div class="sub-side">
      <strong>${money(sub.cost)}/${escapeHtml(cycle.per)}</strong>
      <button type="button" data-action="edit" data-id="${escapeHtml(sub.id)}">Edit</button>
    </div>
  </article>`;
}

function reminderSection(title, note, subs) {
  if (!subs.length) return "";
  return `<section class="block">
    <div class="block-head"><h2>${escapeHtml(title)}</h2><span>${escapeHtml(note)}</span></div>
    <div class="list">${subs.map(renderReminderRow).join("")}</div>
  </section>`;
}

function renderReminders() {
  const billable = billableSubs();
  if (!billable.length) {
    return emptyBlock("No reminders yet", "Active plans and free trials will collect here as their renewal dates get close.");
  }
  const attention = reminderSubs();
  const soon = billable
    .filter((sub) => {
      const days = daysUntil(sub.renewalDate);
      return Number.isFinite(days) && days > sub.reminderDays && days <= 30;
    })
    .sort(compareRenewal);
  const later = billable
    .filter((sub) => {
      const days = daysUntil(sub.renewalDate);
      return !Number.isFinite(days) || days > 30;
    })
    .sort(compareRenewal);
  const attentionNote = plural(attention.length, "plan");
  const clear = attention.length ? "" : `<p class="fine">Nothing is inside a reminder window right now.</p>`;
  return `
    ${clear}
    ${reminderSection("Needs attention", attentionNote, attention)}
    ${reminderSection("Next 30 days", "Outside the warning window", soon)}
    ${reminderSection("Later", "More than 30 days out", later)}
  `;
}

function renderUsageCard(sub) {
  const usedToday = sub.lastUsed && daysUntil(sub.lastUsed) === 0;
  const picks = USAGES.map((usage) => {
    const pressed = usage.id === sub.usage ? "true" : "false";
    return `<button type="button" data-action="set-usage" data-id="${escapeHtml(sub.id)}" data-usage="${usage.id}" aria-pressed="${pressed}">${escapeHtml(usage.label)}</button>`;
  }).join("");
  return `<article class="check${isIdle(sub) ? " is-idle" : ""}">
    <div class="check-top">
      <div>
        <h3>${escapeHtml(sub.name)}</h3>
        <small>${escapeHtml(labelOf(CATEGORIES, sub.category))} · ${escapeHtml(lastUsedPhrase(sub.lastUsed))} · ${money(monthlyEquivalent(sub))}/mo</small>
      </div>
      ${statusTag(sub)}
    </div>
    <fieldset class="usage-picks">
      <legend class="sr-only">How often you use ${escapeHtml(sub.name)}</legend>
      ${picks}
    </fieldset>
    <div>
      <button class="btn btn-ghost" type="button" data-action="mark-used" data-id="${escapeHtml(sub.id)}" ${usedToday ? "disabled" : ""}>${usedToday ? "Used today" : "Mark used today"}</button>
    </div>
  </article>`;
}

function renderUsage() {
  const subs = billableSubs().sort(compareUsage);
  if (!subs.length) {
    return emptyBlock("Nothing to check in on", "Add an active subscription or a free trial, then rate how often you use it.");
  }
  const idle = idleSubs();
  const savings = idle.length
    ? `<section class="savings">
        <span class="label">Potential monthly savings</span>
        <strong>${money(sumBy(idle, monthlyEquivalent))}</strong>
        <ul>${idle.map((sub) => `<li><span>${escapeHtml(sub.name)}</span><b>${money(monthlyEquivalent(sub))}</b></li>`).join("")}</ul>
        <p>These are marked rarely or never. Change the rating below when that is no longer true.</p>
      </section>`
    : "";
  return `${savings}<div class="checks">${subs.map(renderUsageCard).join("")}</div>`;
}

const renderers = {
  overview: renderOverview,
  subscriptions: renderSubscriptions,
  spending: renderSpending,
  reminders: renderReminders,
  usage: renderUsage,
};

function setBadge(id, count) {
  const badge = document.getElementById(id);
  badge.hidden = count === 0;
  badge.textContent = String(count);
}

function render(options = {}) {
  const scrollY = window.scrollY;
  document.querySelectorAll(".nav-btn").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  setBadge("reminder-badge", reminderSubs().length);
  setBadge("usage-badge", idleSubs().length);
  const page = PAGE[state.view];
  document.getElementById("page-kicker").textContent = page.kicker;
  document.getElementById("page-title").textContent = page.title;
  document.getElementById("page-intro").textContent = page.intro;
  if (state.loadError) {
    document.getElementById("view").innerHTML = emptyBlock("The server is not running", state.loadError);
    document.title = "Subscription Manager";
    window.scrollTo(0, 0);
    return;
  }
  document.getElementById("view").innerHTML = renderers[state.view]();
  document.title = `${page.title} · Subscription Manager`;
  window.scrollTo(0, options.keepScroll ? scrollY : 0);
}

function refreshSubList() {
  const host = document.getElementById("list-host");
  if (host) host.innerHTML = renderSubList();
  else render();
}

function toast(message) {
  const element = document.getElementById("toast");
  element.hidden = false;
  element.textContent = message;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    element.hidden = true;
  }, 2600);
}

function fillSelect(id, options) {
  document.getElementById(id).innerHTML = optionHtml(options, "");
}

function syncRenewalLabel() {
  const status = document.getElementById("status").value;
  document.getElementById("renewal-label").textContent = status === "trial" ? "Trial ends" : "Next renewal";
}

function showErrors(errors) {
  const element = document.getElementById("form-error");
  if (!errors.length) {
    element.hidden = true;
    element.innerHTML = "";
    return;
  }
  element.hidden = false;
  element.innerHTML = `<ul>${errors.map((error) => `<li>${escapeHtml(error)}</li>`).join("")}</ul>`;
}

function openEditor(id) {
  const sub = id ? findSub(id) : null;
  if (id && !sub) return;
  const form = document.getElementById("sub-form");
  form.reset();
  showErrors([]);
  state.editingId = sub ? sub.id : null;
  document.getElementById("dialog-title").textContent = sub ? "Edit subscription" : "Add subscription";
  document.getElementById("save-btn").textContent = sub ? "Save changes" : "Add subscription";
  if (sub) {
    form.name.value = sub.name;
    form.cost.value = sub.cost.toFixed(2);
    form.cycle.value = sub.cycle;
    form.category.value = sub.category;
    form.status.value = sub.status;
    form.renewalDate.value = sub.renewalDate;
    form.reminderDays.value = String(sub.reminderDays);
    form.usage.value = sub.usage;
    form.lastUsed.value = sub.lastUsed || "";
    form.notes.value = sub.notes || "";
  } else {
    form.cycle.value = "monthly";
    form.category.value = "streaming";
    form.status.value = "active";
    form.reminderDays.value = "7";
    form.usage.value = "weekly";
    form.renewalDate.value = addDays(30);
  }
  syncRenewalLabel();
  const dialog = document.getElementById("sub-dialog");
  if (!dialog.open) dialog.showModal();
  document.getElementById("name").focus();
}

function closeEditor() {
  const dialog = document.getElementById("sub-dialog");
  if (dialog.open) dialog.close();
}

function validate(form) {
  const errors = [];
  if (!form.name.value.trim()) errors.push("Enter a name.");
  if (form.cost.value.trim() === "") errors.push("Enter a price.");
  else {
    const cost = Number(form.cost.value);
    if (!Number.isFinite(cost) || cost < 0 || cost > 999999.99) {
      errors.push("Enter a price from $0 to $999,999.99.");
    }
  }
  if (!parseISODate(form.renewalDate.value)) errors.push("Choose a renewal date.");
  if (form.lastUsed.value && !parseISODate(form.lastUsed.value)) errors.push("Choose a valid last-used date.");
  if (form.lastUsed.value && daysUntil(form.lastUsed.value) > 0) errors.push("Last used has to be today or earlier.");
  return errors;
}

async function saveForm() {
  const form = document.getElementById("sub-form");
  const errors = validate(form);
  showErrors(errors);
  if (errors.length) return;
  const editing = Boolean(state.editingId);
  const sub = normalize({
    id: state.editingId || "",
    name: form.name.value,
    category: form.category.value,
    cost: form.cost.value,
    cycle: form.cycle.value,
    renewalDate: form.renewalDate.value,
    status: form.status.value,
    reminderDays: form.reminderDays.value,
    usage: form.usage.value,
    lastUsed: form.lastUsed.value,
    notes: form.notes.value,
  });
  try {
    const saved = editing
      ? await request("PUT", `/api/subscriptions/${encodeURIComponent(sub.id)}`, sub)
      : await request("POST", "/api/subscriptions", sub);
    const stored = normalize(saved);
    const index = state.subscriptions.findIndex((item) => item.id === stored.id);
    if (index >= 0) state.subscriptions[index] = stored;
    else state.subscriptions.unshift(stored);
  } catch (error) {
    toast(error.message);
    return;
  }
  closeEditor();
  render();
  toast(editing ? `Saved ${sub.name}.` : `Added ${sub.name}.`);
}

function openConfirm(config) {
  state.confirm = config;
  document.getElementById("confirm-title").textContent = config.title;
  document.getElementById("confirm-copy").textContent = config.copy;
  document.getElementById("confirm-cancel").textContent = config.cancel;
  const accept = document.getElementById("confirm-accept");
  accept.textContent = config.accept;
  accept.classList.toggle("btn-danger", config.tone === "danger");
  accept.classList.toggle("btn-primary", config.tone !== "danger");
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog.open) dialog.showModal();
}

function closeConfirm() {
  const dialog = document.getElementById("confirm-dialog");
  if (dialog.open) dialog.close();
  state.confirm = null;
}

async function acceptConfirm() {
  const pending = state.confirm;
  if (!pending) return;
  try {
    if (pending.type === "delete") {
      const sub = findSub(pending.id);
      await request("DELETE", `/api/subscriptions/${encodeURIComponent(pending.id)}`);
      state.subscriptions = state.subscriptions.filter((item) => item.id !== pending.id);
      toast(sub ? `Removed ${sub.name}.` : "Removed.");
    } else if (pending.type === "clear") {
      const rows = await request("DELETE", "/api/subscriptions");
      state.subscriptions = rows.map(normalize);
      toast("Removed every subscription.");
    } else if (pending.type === "seed") {
      const rows = await request("POST", "/api/subscriptions/sample");
      state.subscriptions = rows.map(normalize);
      toast("Sample subscriptions loaded.");
    }
  } catch (error) {
    toast(error.message);
    return;
  }
  closeConfirm();
  render();
}

function askDelete(id) {
  const sub = findSub(id);
  if (!sub) return;
  openConfirm({
    type: "delete",
    id,
    title: `Remove ${sub.name}?`,
    copy: "It will be removed from the database. You can add it again later.",
    accept: "Remove",
    cancel: "Keep it",
    tone: "danger",
  });
}

function askClear() {
  if (!state.subscriptions.length) {
    toast("The list is already empty.");
    return;
  }
  openConfirm({
    type: "clear",
    title: "Remove every subscription?",
    copy: "This clears the list stored in the database.",
    accept: "Remove all",
    cancel: "Cancel",
    tone: "danger",
  });
}

function askSeed() {
  openConfirm({
    type: "seed",
    title: "Load sample subscriptions?",
    copy: state.subscriptions.length
      ? "This replaces the subscriptions currently stored in the database."
      : "This fills the dashboard with a sample set you can edit.",
    accept: "Load sample",
    cancel: "Cancel",
    tone: "primary",
  });
}

async function setUsage(id, usage) {
  const sub = findSub(id);
  if (!sub || !USAGE_IDS.includes(usage) || sub.usage === usage) return;
  const previous = sub.usage;
  sub.usage = usage;
  render({ keepScroll: true });
  try {
    const saved = normalize(await request("PUT", `/api/subscriptions/${encodeURIComponent(id)}`, sub));
    replaceSub(saved);
  } catch (error) {
    sub.usage = previous;
    render({ keepScroll: true });
    toast(error.message);
  }
}

async function markUsed(id) {
  const sub = findSub(id);
  if (!sub) return;
  const previous = sub.lastUsed;
  sub.lastUsed = addDays(0);
  render({ keepScroll: true });
  try {
    const saved = normalize(await request("PUT", `/api/subscriptions/${encodeURIComponent(id)}`, sub));
    replaceSub(saved);
    toast(`${sub.name} marked as used today.`);
  } catch (error) {
    sub.lastUsed = previous;
    render({ keepScroll: true });
    toast(error.message);
  }
}

function replaceSub(subscription) {
  const index = state.subscriptions.findIndex((item) => item.id === subscription.id);
  if (index >= 0) state.subscriptions[index] = subscription;
}

function downloadJson() {
  const blob = new Blob([JSON.stringify(state.subscriptions, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "subscriptions.json";
  link.click();
  URL.revokeObjectURL(url);
  toast("Downloaded subscriptions.json.");
}

function viewFromHash() {
  const name = location.hash.replace(/^#/, "");
  return VIEWS.includes(name) ? name : "overview";
}

function navigate(view) {
  if (!VIEWS.includes(view)) return;
  if (location.hash === `#${view}`) return;
  location.hash = view;
}

function onClick(event) {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const { action, id, view, usage } = target.dataset;
  if (action === "navigate") navigate(view);
  else if (action === "add") openEditor(null);
  else if (action === "edit") openEditor(id);
  else if (action === "ask-delete") askDelete(id);
  else if (action === "ask-clear") askClear();
  else if (action === "ask-seed") askSeed();
  else if (action === "set-usage") setUsage(id, usage);
  else if (action === "mark-used") markUsed(id);
  else if (action === "close-dialog") closeEditor();
  else if (action === "close-confirm") closeConfirm();
  else if (action === "accept-confirm") acceptConfirm();
  else if (action === "download") downloadJson();
}

function onInput(event) {
  if (event.target.id !== "search") return;
  state.query = event.target.value;
  refreshSubList();
}

function onChange(event) {
  const id = event.target.id;
  if (id === "filter-category") state.category = event.target.value;
  else if (id === "filter-status") state.status = event.target.value;
  else if (id === "filter-sort") state.sort = event.target.value;
  else if (id === "status") {
    syncRenewalLabel();
    return;
  } else {
    return;
  }
  refreshSubList();
}

function onSubmit(event) {
  if (event.target.id !== "sub-form") return;
  event.preventDefault();
  saveForm();
}

function bind() {
  document.addEventListener("click", onClick);
  document.addEventListener("input", onInput);
  document.addEventListener("change", onChange);
  document.addEventListener("submit", onSubmit);
  document.getElementById("sub-dialog").addEventListener("click", (event) => {
    if (event.target.id === "sub-dialog") closeEditor();
  });
  document.getElementById("confirm-dialog").addEventListener("click", (event) => {
    if (event.target.id === "confirm-dialog") closeConfirm();
  });
  document.getElementById("confirm-dialog").addEventListener("close", () => {
    state.confirm = null;
  });
  window.addEventListener("hashchange", () => {
    state.view = viewFromHash();
    render();
  });
  fillSelect("category", CATEGORIES);
  fillSelect("cycle", CYCLES);
  fillSelect("status", STATUSES);
  fillSelect("usage", USAGES);
  fillSelect("reminder-days", REMINDERS);
}

async function init() {
  bind();
  const initial = viewFromHash();
  if (location.hash !== `#${initial}`) history.replaceState(null, "", `#${initial}`);
  state.view = initial;
  document.getElementById("page-title").textContent = "Loading subscriptions";
  document.getElementById("view").textContent = "Loading subscriptions…";
  try {
    await load();
  } catch (error) {
    state.loadError = "Start it from the project folder with python main.py, then open http://127.0.0.1:8000.";
  }
  render();
}

init();
