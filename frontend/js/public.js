/**
 * Public pages, first-touch attribution, and the contact form.
 * The signed-in dashboard stays in app.js.
 */
(function () {
  const PAGES = {
    "/": "home",
    "/features": "features",
    "/about": "about",
    "/faq": "faq",
    "/terms": "terms",
    "/privacy": "privacy",
    "/contact": "contact",
  };
  const SOURCES = ["linkedin", "reddit", "instagram", "tiktok", "discord", "github", "direct", "referral", "other"];
  const REFERRERS = [
    ["linkedin.com", "linkedin"],
    ["reddit.com", "reddit"],
    ["instagram.com", "instagram"],
    ["tiktok.com", "tiktok"],
    ["discord.com", "discord"],
    ["discord.gg", "discord"],
    ["github.com", "github"],
  ];

  let shownPage = null;

  const mount = document.getElementById("gate-mount");
  const gate = document.getElementById("gate");
  if (mount && gate) mount.appendChild(gate);

  const year = document.getElementById("copyright-year");
  if (year) year.textContent = String(new Date().getFullYear());

  captureAttribution();
  showPublicPage();
  loadReviews();
  loadMilestone();
  const contact = document.getElementById("contact-form");
  if (contact) contact.addEventListener("submit", sendContact);
  window.addEventListener("hashchange", showPublicPage);

  function showPublicPage() {
    const page = currentPage();
    const changed = shownPage !== null && shownPage !== page;
    shownPage = page;
    document.body.classList.toggle("is-admin-route", page === "admin");
    placeGate(page);
    document.querySelectorAll("[data-public-page]").forEach((section) => {
      const match = section.getAttribute("data-public-page") === page;
      section.hidden = !match;
      if (match && changed && motionOn()) {
        section.classList.remove("is-page-in");
        void section.offsetWidth;
        section.classList.add("is-page-in");
      }
    });
    document.querySelectorAll(".site-links a").forEach((link) => {
      const href = link.getAttribute("href") || "";
      const current = href === "/#demo"
        ? page === "home" && location.hash === "#demo"
        : href === (page === "home" ? "/" : `/${page}`);
      if (current && !link.classList.contains("btn")) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const title = {
      home: "Wallet Worth — Subscription tracker",
      features: "Features · Wallet Worth",
      about: "About · Wallet Worth",
      faq: "FAQ · Wallet Worth",
      terms: "Terms of Service · Wallet Worth",
      privacy: "Privacy Policy · Wallet Worth",
      contact: "Contact · Wallet Worth",
      admin: "Accounts · Wallet Worth",
    }[page];
    if (document.body.classList.contains("is-signed-out") && title) {
      document.title = title;
    }
    const canonical = document.querySelector('link[rel="canonical"]');
    if (canonical && page !== "admin") {
      const path = page === "home" ? "/" : `/${page}`;
      canonical.setAttribute("href", `https://subscription-manager-mu-one.vercel.app${path}`);
    }
  }

  function placeGate(page) {
    const form = document.getElementById("gate");
    const target = document.getElementById(page === "admin" ? "admin-gate" : "gate-mount");
    if (form && target && form.parentElement !== target) target.appendChild(form);
  }

  function currentPage() {
    const path = window.location.pathname.replace(/\/$/, "") || "/";
    if (path === "/admin/analytics") return "admin";
    if (PAGES[path]) return PAGES[path];
    const hash = window.location.hash.replace("#", "");
    if (PAGES[`/${hash}`]) return PAGES[`/${hash}`];
    return "home";
  }

  function captureAttribution() {
    let existing = null;
    try {
      existing = JSON.parse(localStorage.getItem("ww-attribution") || "null");
    } catch (error) {
      existing = null;
    }
    if (existing && existing.source) return;
    const params = new URLSearchParams(window.location.search);
    const utm = (params.get("utm_source") || "").trim().toLowerCase();
    let source = "";
    if (utm) source = SOURCES.includes(utm) ? utm : "other";
    else if (document.referrer) {
      const ref = document.referrer.toLowerCase();
      const found = REFERRERS.find(([host]) => ref.includes(host));
      if (found) source = found[1];
      else if (!ref.includes(window.location.host)) source = "referral";
    }
    if (!source) return;
    localStorage.setItem("ww-attribution", JSON.stringify({
      source,
      medium: (params.get("utm_medium") || "").slice(0, 80),
      campaign: (params.get("utm_campaign") || "").slice(0, 80),
      content: (params.get("utm_content") || "").slice(0, 80),
      term: (params.get("utm_term") || "").slice(0, 80),
    }));
  }

  async function loadReviews() {
    const host = document.getElementById("public-reviews");
    if (!host) return;
    try {
      const response = await fetch("/api/reviews");
      if (!response.ok) return;
      const reviews = await response.json();
      if (!Array.isArray(reviews) || !reviews.length) {
        host.innerHTML = '<p class="fine">No approved reviews yet. Nothing here is a sample quote.</p>';
        return;
      }
      host.innerHTML = reviews.map((review) => {
        const stars = "★★★★★".slice(0, review.rating) + "☆☆☆☆☆".slice(review.rating);
        const when = review.createdAt ? new Date(review.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "";
        return `<article class="review-card"><p class="review-stars" aria-label="${review.rating} of 5">${stars}</p><p>${escapeHtml(review.body)}</p><p class="fine">${escapeHtml(review.displayName)} · ${escapeHtml(when)} · Approved</p></article>`;
      }).join("");
    } catch (error) {
      host.innerHTML = '<p class="fine">Reviews could not be loaded.</p>';
    }
  }

  async function loadMilestone() {
    const note = document.getElementById("milestone-note");
    if (!note) return;
    try {
      const response = await fetch("/api/milestone");
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload.visible) return;
      note.hidden = false;
      note.textContent = `${payload.eligible} registered accounts so far. The public goal is ${payload.goal}. This counts accounts, not a promise of unique people.`;
    } catch (error) {
      note.hidden = true;
    }
  }

  async function sendContact(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const error = document.getElementById("contact-error");
    const success = document.getElementById("contact-success");
    error.hidden = true;
    success.hidden = true;
    const payload = {
      name: form.name.value,
      email: form.email.value,
      topic: form.topic.value,
      message: form.message.value,
      company: form.company.value,
    };
    try {
      const response = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error((body.errors && body.errors[0]) || "The message was not saved.");
      if (body.delivered !== "saved" && body.ok !== true) throw new Error("The message was not saved.");
      success.hidden = false;
      form.reset();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    }
  }

  const DEMO_CATEGORIES = {
    streaming: ["Streaming", "#de3341"],
    music: ["Music", "#5b3c9c"],
    software: ["Software", "#111111"],
    cloud: ["Cloud", "#298540"],
    news: ["News", "#c12a36"],
    fitness: ["Fitness", "#298540"],
    gaming: ["Gaming", "#28162d"],
    other: ["Other", "#7d7d7d"],
  };

  function startDemo() {
    const host = document.getElementById("demo-plans");
    if (!host) return;
    let plans = starterPlans();
    let nextId = 4;
    const reset = document.getElementById("demo-reset");
    const form = document.getElementById("demo-add");
    if (reset) reset.addEventListener("click", () => {
      plans = starterPlans();
      nextId = 4;
      renderDemo(plans, "Sample list restored.", { enterAll: true });
    });
    host.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-demo]");
      if (!button) return;
      const plan = plans.find((item) => item.id === button.dataset.id);
      if (!plan) return;
      if (button.dataset.demo === "pause") {
        plan.status = plan.status === "active" ? "paused" : "active";
        renderDemo(plans, `${plan.name} is ${plan.status}. The monthly total uses active plans only.`, { pulseId: plan.id });
      } else if (button.dataset.demo === "remove") {
        const card = button.closest(".demo-plan");
        const finish = () => {
          plans = plans.filter((item) => item.id !== plan.id);
          renderDemo(plans, `${plan.name} was removed from this demo.`);
        };
        if (!motionOn() || !card) finish();
        else {
          card.classList.add("is-leaving");
          window.setTimeout(finish, 180);
        }
      }
    });
    host.addEventListener("input", (event) => {
      const field = event.target.closest("input[data-demo='price']");
      if (!field) return;
      const plan = plans.find((item) => item.id === field.dataset.id);
      const cost = Number(field.value);
      if (!plan || !Number.isFinite(cost) || cost <= 0 || cost > 9999) return;
      plan.cost = Math.round(cost * 100) / 100;
      renderDemo(plans, `${plan.name} is ${money(plan.cost)} a month in this demo.`, { keepFocus: field.dataset.id });
    });
    if (form) form.addEventListener("submit", (event) => {
      event.preventDefault();
      const error = document.getElementById("demo-error");
      const name = form.name.value.trim();
      const cost = Number(form.cost.value);
      if (name.length < 2 || name.length > 40) {
        error.textContent = "Use a name between 2 and 40 characters.";
        error.hidden = false;
        return;
      }
      if (!Number.isFinite(cost) || cost <= 0 || cost > 9999) {
        error.textContent = "Enter a monthly price between 0.01 and 9999.";
        error.hidden = false;
        return;
      }
      if (plans.length >= 12) {
        error.textContent = "The demo holds 12 plans. Remove one or reset the sample.";
        error.hidden = false;
        return;
      }
      error.hidden = true;
      const category = DEMO_CATEGORIES[form.category.value] ? form.category.value : "other";
      plans.push({
        id: `custom-${nextId}`,
        name,
        cost: Math.round(cost * 100) / 100,
        category,
        inDays: 21,
        status: "active",
      });
      nextId += 1;
      form.reset();
      renderDemo(plans, `${name} was added to this demo only.`, { enterId: plans[plans.length - 1].id });
    });
    renderDemo(plans, "Sample plans. Nothing is saved.");
  }

  function starterPlans() {
    return [
      { id: "youtube", name: "YouTube Premium", cost: 13.99, category: "streaming", inDays: 3, status: "active" },
      { id: "netflix", name: "Netflix", cost: 15.49, category: "streaming", inDays: 6, status: "active" },
      { id: "spotify", name: "Spotify", cost: 11.99, category: "music", inDays: 12, status: "active" },
    ];
  }

  const moneyToken = { month: 0, year: 0 };
  let shownMonth = null;
  let shownYear = null;

  function renderDemo(plans, message, effect = {}) {
    const active = plans.filter((plan) => plan.status === "active");
    const monthly = active.reduce((sum, plan) => sum + plan.cost, 0);
    const reminders = active.filter((plan) => plan.inDays <= 7);
    const month = document.getElementById("demo-month");
    const year = document.getElementById("demo-year");
    const reminderCount = document.getElementById("demo-reminders");
    const reminderCard = document.getElementById("demo-reminder-card");
    const count = document.getElementById("demo-count");
    const live = document.getElementById("demo-live");
    if (month) tweenMoney(month, shownMonth, monthly, "month");
    if (year) tweenMoney(year, shownYear, monthly * 12, "year");
    shownMonth = monthly;
    shownYear = monthly * 12;
    if (reminderCount) reminderCount.textContent = String(reminders.length);
    if (reminderCard) reminderCard.classList.toggle("is-alert", reminders.length > 0);
    if (count) count.textContent = `${plans.length} in the demo`;
    if (live && !effect.keepFocus) live.textContent = `${message} Monthly total ${money(monthly)}.`;
    const list = document.getElementById("demo-plans");
    if (list && !effect.keepFocus) {
      list.innerHTML = plans.length
        ? plans.map((plan) => renderPlan(plan, effect)).join("")
        : '<p class="fine">The demo list is empty. Add a plan or reset the sample.</p>';
    } else if (list && effect.keepFocus) {
      const card = list.querySelector(`[data-plan="${cssEscape(effect.keepFocus)}"]`);
      if (card) card.classList.add("is-pulsing");
    }
    const upcoming = document.getElementById("demo-upcoming");
    if (upcoming) {
      const rows = active.slice().sort((a, b) => a.inDays - b.inDays);
      upcoming.innerHTML = rows.length
        ? `<ul class="preview-list">${rows.map((plan) => `<li><i style="background:${categoryColor(plan.category)}"></i><span>${escapeHtml(plan.name)}<small>${escapeHtml(whenLabel(plan.inDays))}</small></span><b>${money(plan.cost)}</b></li>`).join("")}</ul>`
        : '<p class="fine">No active plans. Paused plans stay out of renewals and the total.</p>';
    }
    const bars = document.getElementById("demo-bars");
    if (bars) {
      bars.innerHTML = renderBars(active);
      if (motionOn()) {
        bars.querySelectorAll(".bar-fill").forEach((fill) => {
          const width = fill.style.width;
          fill.style.width = "0%";
          requestAnimationFrame(() => {
            fill.style.width = width;
          });
        });
      }
    }
  }

  function renderPlan(plan, effect) {
    const paused = plan.status === "paused";
    const entering = effect.enterAll || effect.enterId === plan.id;
    const pulsing = effect.pulseId === plan.id;
    return `<article class="demo-plan${paused ? " is-paused" : ""}${entering ? " is-entering" : ""}${pulsing ? " is-pulsing" : ""}" data-plan="${escapeHtml(plan.id)}">
      <i style="background:${categoryColor(plan.category)}"></i>
      <span>
        <strong>${escapeHtml(plan.name)}</strong>
        <small>${escapeHtml(categoryName(plan.category))} · ${escapeHtml(whenLabel(plan.inDays))}${paused ? " · Paused" : ""}</small>
      </span>
      <label class="demo-price">Monthly
        <input data-demo="price" data-id="${escapeHtml(plan.id)}" type="number" min="0.01" max="9999" step="0.01" value="${plan.cost.toFixed(2)}" aria-label="Monthly price for ${escapeHtml(plan.name)}">
      </label>
      <button type="button" data-demo="pause" data-id="${escapeHtml(plan.id)}" aria-pressed="${paused ? "true" : "false"}">${paused ? "Resume" : "Pause"}</button>
      <button type="button" data-demo="remove" data-id="${escapeHtml(plan.id)}">Remove</button>
    </article>`;
  }

  function tweenMoney(el, from, to, key) {
    if (!el) return;
    if (!motionOn() || from === null || Math.abs(from - to) < 0.001) {
      el.textContent = money(to);
      return;
    }
    const token = ++moneyToken[key];
    const start = performance.now();
    function frame(now) {
      if (moneyToken[key] !== token) return;
      const progress = Math.min(1, (now - start) / 380);
      const eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = money(from + (to - from) * eased);
      if (progress < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function cssEscape(value) {
    return window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/[^a-zA-Z0-9_-]/g, "");
  }

  function motionOn() {
    return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function renderBars(active) {
    const totals = new Map();
    active.forEach((plan) => totals.set(plan.category, (totals.get(plan.category) || 0) + plan.cost));
    const rows = [...totals.entries()].sort((a, b) => b[1] - a[1]);
    if (!rows.length) return '<p class="fine">Pause every plan and this chart goes to zero.</p>';
    const max = rows[0][1];
    return rows.map(([category, total]) => `
      <div class="bar-row">
        <div class="bar-label"><span class="dot" style="background:${categoryColor(category)}"></span>${escapeHtml(categoryName(category))}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.max(8, Math.round((total / max) * 100))}%; background:${categoryColor(category)}"></div></div>
        <b class="bar-value">${money(total)}</b>
      </div>`).join("");
  }

  function categoryName(id) {
    return (DEMO_CATEGORIES[id] || DEMO_CATEGORIES.other)[0];
  }

  function categoryColor(id) {
    return (DEMO_CATEGORIES[id] || DEMO_CATEGORIES.other)[1];
  }

  function whenLabel(days) {
    if (days <= 0) return "Renews today";
    if (days === 1) return "Renews tomorrow";
    return `Renews in ${days} days`;
  }

  function money(value) {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function startMotion() {
    const site = document.getElementById("site");
    if (!site) return;
    const steps = document.querySelectorAll(".step");
    steps.forEach((step) => {
      step.addEventListener("click", () => selectStep(step));
    });
    if (!motionOn()) return;
    site.classList.add("js-motion");
    const hero = document.querySelector(".site-hero");
    if (hero) {
      hero.addEventListener("pointermove", (event) => {
        if (event.pointerType !== "mouse") return;
        const rect = hero.getBoundingClientRect();
        hero.style.setProperty("--spot-x", `${event.clientX - rect.left}px`);
        hero.style.setProperty("--spot-y", `${event.clientY - rect.top}px`);
      });
    }
    site.addEventListener("pointermove", (event) => {
      const button = event.target.closest(".btn-primary");
      if (!button || event.pointerType !== "mouse") return;
      const rect = button.getBoundingClientRect();
      button.style.setProperty("--mx", `${event.clientX - rect.left}px`);
      button.style.setProperty("--my", `${event.clientY - rect.top}px`);
    });
    const bar = document.querySelector(".site-bar");
    if (bar) {
      const onScroll = () => bar.classList.toggle("is-stuck", window.scrollY > 8);
      onScroll();
      window.addEventListener("scroll", onScroll, { passive: true });
    }
    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        });
      }, { threshold: 0.18 });
      site.querySelectorAll(".reveal").forEach((section) => observer.observe(section));
    } else {
      site.querySelectorAll(".reveal").forEach((section) => section.classList.add("is-in"));
    }
  }

  function selectStep(step) {
    document.querySelectorAll(".step").forEach((item) => {
      const on = item === step;
      item.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".step-panel").forEach((panel) => {
      const on = panel.id === step.getAttribute("aria-controls");
      panel.hidden = !on;
      if (on && motionOn()) {
        panel.classList.remove("is-page-in");
        void panel.offsetWidth;
        panel.classList.add("is-page-in");
      }
    });
  }

  startMotion();
  startDemo();
})();
