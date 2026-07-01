// Job Copilot — SPA controller (vanilla JS, no build step).
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isErr);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2800);
}

function busy(on, label = "Working…") {
  const pill = $("#status-dot");
  pill.classList.toggle("busy", on);
  $("#status-text").textContent = on ? label : "Ready";
}

function withLoad(btn, fn) {
  return async (...args) => {
    btn.classList.add("loading");
    try { return await fn(...args); }
    finally { btn.classList.remove("loading"); }
  };
}

const scoreColor = (s) => (s >= 75 ? "var(--green)" : s >= 50 ? "var(--amber)" : "var(--red)");
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function salary(j) {
  const mn = j.salary_min, mx = j.salary_max;
  if (mn && mx) return `$${Math.round(mn / 1000)}k–$${Math.round(mx / 1000)}k`;
  if (mn) return `$${Math.round(mn / 1000)}k+`;
  return null;
}

// ── Navigation ──────────────────────────────────────────────────────────────
$$(".nav-item").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.remove("active"));
    $$(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    const view = btn.dataset.view;
    $(`#view-${view}`).classList.add("active");
    if (view === "scout") loadJobs();
    if (view === "tracker") loadTracker();
    if (view === "settings") loadSettings();
  })
);

// ── Scout ───────────────────────────────────────────────────────────────────
async function loadJobs() {
  const min = $("#scout-min").value;
  const q = $("#scout-search").value.trim();
  try {
    const { jobs } = await api(`/api/jobs?min_score=${min}&q=${encodeURIComponent(q)}&status=new,saved,applied,interview,offer`);
    renderJobs(jobs);
  } catch (e) { toast(e.message, true); }
}

function renderJobs(jobs) {
  const grid = $("#scout-grid");
  const empty = $("#scout-empty");
  $("#scout-stats").innerHTML = jobs.length
    ? `<span><b>${jobs.length}</b> jobs shown</span>
       <span><b>${jobs.filter((j) => j.score >= 75).length}</b> strong matches (75+)</span>`
    : "";
  if (!jobs.length) { grid.innerHTML = ""; empty.style.display = "block"; return; }
  empty.style.display = "none";
  grid.innerHTML = jobs.map(jobCard).join("");
  $$(".job-card").forEach(wireCard);
}

function jobCard(j) {
  const sal = salary(j);
  const chips = (j.key_matches || []).slice(0, 4).map((k) => `<span class="chip">${esc(k)}</span>`).join("");
  return `<article class="job-card" data-id="${j.id}" style="--card-accent:${scoreColor(j.score)}">
    <div class="jc-top">
      <div>
        <h3 class="jc-title">${esc(j.title)}</h3>
        <div class="jc-meta">
          <span>🏢 ${esc(j.company) || "—"}</span>
          <span>🌍 ${esc(j.location) || "Remote"}</span>
          ${sal ? `<span>💰 ${sal}</span>` : ""}
        </div>
      </div>
      <div class="score-badge">${j.score}</div>
    </div>
    <p class="jc-reason">${esc(j.match_reasons)}</p>
    ${chips ? `<div class="chips">${chips}</div>` : ""}
    <div class="jc-actions">
      <a class="mini apply" href="${esc(j.apply_link)}" target="_blank" rel="noopener">Apply ↗</a>
      <button class="mini act ${j.status === "saved" ? "active" : ""}" data-status="saved">Save</button>
      <button class="mini act ${j.status === "applied" ? "active" : ""}" data-status="applied">Applied</button>
      <button class="mini dismiss" data-status="dismissed">Dismiss</button>
    </div>
  </article>`;
}

function wireCard(card) {
  const id = card.dataset.id;
  $$(".act, .dismiss", card).forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/jobs/${encodeURIComponent(id)}/status`, {
          method: "POST",
          body: JSON.stringify({ status: btn.dataset.status }),
        });
        if (btn.dataset.status === "dismissed") {
          card.style.opacity = "0";
          setTimeout(loadJobs, 180);
        } else {
          $$(".act", card).forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          toast(`Moved to ${btn.dataset.status}`);
        }
      } catch (e) { toast(e.message, true); }
    })
  );
}

$("#scout-min").addEventListener("input", (e) => { $("#score-val").textContent = e.target.value; });
$("#scout-min").addEventListener("change", loadJobs);
$("#scout-search").addEventListener("input", debounce(loadJobs, 300));

$("#btn-refresh").addEventListener("click", withLoad($("#btn-refresh"), async () => {
  busy(true, "Finding & scoring jobs…");
  try {
    const r = await api("/api/scout/refresh", { method: "POST" });
    toast(`Fetched ${r.fetched}, scored ${r.new} new jobs`);
    await loadJobs();
  } catch (e) { toast(e.message, true); }
  finally { busy(false); }
}));

// ── Tracker ─────────────────────────────────────────────────────────────────
const COLS = [
  ["saved", "Saved"], ["applied", "Applied"], ["interview", "Interview"], ["offer", "Offer"],
];

async function loadTracker() {
  try {
    const { board } = await api("/api/tracker");
    const el = $("#tracker-board");
    el.innerHTML = COLS.map(([key, label]) => {
      const cards = (board[key] || []).map(tkCard).join("") || `<div class="tk-empty">Drop jobs here</div>`;
      return `<div class="col" data-col="${key}">
        <div class="col-head">${label} <span class="count">${(board[key] || []).length}</span></div>
        ${cards}
      </div>`;
    }).join("");
    wireDnd();
  } catch (e) { toast(e.message, true); }
}

const tkCard = (j) => `<div class="tk-card" draggable="true" data-id="${j.id}">
  <div class="tk-title">${esc(j.title)}</div>
  <div class="tk-company">${esc(j.company)} · score ${j.score}</div>
</div>`;

function wireDnd() {
  let dragId = null;
  $$(".tk-card").forEach((c) => {
    c.addEventListener("dragstart", () => { dragId = c.dataset.id; c.classList.add("dragging"); });
    c.addEventListener("dragend", () => c.classList.remove("dragging"));
  });
  $$(".col").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop-hover"); });
    col.addEventListener("dragleave", () => col.classList.remove("drop-hover"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault();
      col.classList.remove("drop-hover");
      if (!dragId) return;
      try {
        await api(`/api/jobs/${encodeURIComponent(dragId)}/status`, {
          method: "POST", body: JSON.stringify({ status: col.dataset.col }),
        });
        loadTracker();
      } catch (err) { toast(err.message, true); }
    });
  });
}

// ── Tailor ──────────────────────────────────────────────────────────────────
$("#btn-tailor").addEventListener("click", withLoad($("#btn-tailor"), async () => {
  const jd = $("#tailor-jd").value.trim();
  if (!jd) return toast("Paste a job description first.", true);
  busy(true, "Tailoring resume…");
  try {
    const r = await api("/api/tailor", { method: "POST", body: JSON.stringify({ job_description: jd }) });
    $("#tailor-out").innerHTML = `
      <h4>Headline</h4><p>${esc(r.headline)}</p>
      <h4>Summary</h4><p>${esc(r.summary)}</p>
      <h4>Tailored bullets</h4><ul>${r.bullets.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
      <h4>Highlight skills</h4><div class="chips">${r.highlight_skills.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div>
      <h4>Cover letter</h4><div class="letter">${esc(r.cover_letter)}</div>
      <div class="copy-row">
        <button class="mini" id="copy-letter">Copy cover letter</button>
        <button class="mini" onclick="window.print()">Print / PDF</button>
      </div>`;
    $("#copy-letter").addEventListener("click", () => {
      navigator.clipboard.writeText(r.cover_letter); toast("Cover letter copied");
    });
  } catch (e) { toast(e.message, true); }
  finally { busy(false); }
}));

// ── Coach ───────────────────────────────────────────────────────────────────
$("#btn-coach").addEventListener("click", withLoad($("#btn-coach"), async () => {
  const role = $("#coach-role").value.trim();
  if (!role) return toast("Enter a target role first.", true);
  busy(true, "Generating questions…");
  try {
    const r = await api("/api/coach", {
      method: "POST",
      body: JSON.stringify({
        role, company: $("#coach-company").value.trim(), job_description: $("#coach-jd").value.trim(),
      }),
    });
    $("#coach-out").innerHTML = r.questions.map((q, i) => `
      <div class="qa" data-i="${i}">
        <div class="qa-q"><span>${esc(q.question)}</span><span class="qa-cat">${esc(q.category)}</span></div>
        <div class="qa-a"><div class="qa-a-inner">${esc(q.answer)}</div></div>
      </div>`).join("");
    $$(".qa-q").forEach((q) => q.addEventListener("click", () => q.parentElement.classList.toggle("open")));
  } catch (e) { toast(e.message, true); }
  finally { busy(false); }
}));

// ── Settings ────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    $("#set-queries").value = s.search_queries || "";
    $("#set-profile").value = s.candidate_profile || "";
    $("#set-provider").value = s.AI_PROVIDER || "auto";
    $("#set-aikey").placeholder = s.configured.AI_API_KEY ? "•••••• (saved)" : "Paste any AI key";
    $("#set-jsearch").placeholder = s.configured.JSEARCH_API_KEY ? "•••••• (saved)" : "Paste key";
    $("#resume-status").textContent = s.has_profile ? "Profile loaded ✓" : "No profile yet";
    const badge = $("#provider-badge");
    badge.textContent = s.detected_provider ? `Detected: ${s.detected_provider}` : "";
    badge.style.display = s.detected_provider ? "inline-block" : "none";
  } catch (e) { toast(e.message, true); }
}

$("#btn-save-settings").addEventListener("click", async () => {
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        AI_API_KEY: $("#set-aikey").value,
        AI_PROVIDER: $("#set-provider").value,
        JSEARCH_API_KEY: $("#set-jsearch").value,
        search_queries: $("#set-queries").value,
        candidate_profile: $("#set-profile").value,
      }),
    });
    $("#set-aikey").value = ""; $("#set-jsearch").value = "";
    toast("Settings saved");
    loadSettings();
  } catch (e) { toast(e.message, true); }
});

// ── Theme ───────────────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  $(".theme-ic").textContent = dark ? "☀️" : "🌙";
  $(".theme-label").textContent = dark ? "Light" : "Dark";
  localStorage.setItem("theme", theme);
}
$("#theme-toggle").addEventListener("click", () =>
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")
);
applyTheme(localStorage.getItem("theme") || "light");

$("#set-resume").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  busy(true, "Reading resume…");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/resume", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Upload failed");
    $("#set-profile").value = data.text;
    $("#resume-status").textContent = `Loaded ${data.chars.toLocaleString()} chars ✓`;
    toast("Resume parsed — review and Save");
  } catch (err) { toast(err.message, true); }
  finally { busy(false); }
});

// ── utils ───────────────────────────────────────────────────────────────────
function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Boot
loadJobs();
