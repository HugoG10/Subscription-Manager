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
    document.querySelectorAll("[data-public-page]").forEach((section) => {
      section.hidden = section.getAttribute("data-public-page") !== page;
    });
    const title = {
      home: "Wallet Worth — Subscription tracker",
      features: "Features · Wallet Worth",
      about: "About · Wallet Worth",
      faq: "FAQ · Wallet Worth",
      terms: "Terms of Service · Wallet Worth",
      privacy: "Privacy Policy · Wallet Worth",
      contact: "Contact · Wallet Worth",
    }[page];
    if (document.body.classList.contains("is-signed-out") && title) {
      document.title = title;
    }
    const canonical = document.querySelector('link[rel="canonical"]');
    if (canonical) {
      const path = page === "home" ? "/" : `/${page}`;
      canonical.setAttribute("href", `https://subscription-manager-mu-one.vercel.app${path}`);
    }
  }

  function currentPage() {
    const path = window.location.pathname.replace(/\/$/, "") || "/";
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

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }
})();
