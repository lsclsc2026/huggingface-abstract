/* Paper Daily — local-first Hugging Face daily papers reader */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];

  const state = {
    meta: null,
    settings: null,
    today: "",
    date: "",
    data: null,
    history: [],
    loading: true,
    query: "",
    filter: "all",
    sort: "upvotes",
    selectedId: null,
    detailList: [],
    detailIndex: -1,
    activeRun: null,
    run: null,
    runDockHidden: false,
    starting: false,
    terminalHandled: "",
    pollTimer: null,
    digestTimer: null,
    loadSeq: 0,
  };

  const ICONS = {
    arrow: "i-arrow-right",
    back: "i-arrow-left",
    calendar: "i-calendar",
    search: "i-search",
    spark: "i-spark",
    download: "i-download",
    github: "i-github",
    external: "i-external",
    bookmark: "i-bookmark",
    check: "i-check",
    close: "i-close",
    refresh: "i-refresh",
    copy: "i-copy",
    notes: "i-notes",
    file: "i-file",
    clock: "i-clock",
    folder: "i-folder",
    settings: "i-settings",
    up: "i-up",
    left: "i-chevron-left",
    right: "i-chevron-right",
    down: "i-chevron-down",
  };

  function icon(name, className = "") {
    return `<svg${className ? ` class="${className}"` : ""} aria-hidden="true"><use href="#${ICONS[name] || name}"/></svg>`;
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char];
    });
  }

  function safeUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(String(value), window.location.origin);
      if (!["http:", "https:"].includes(url.protocol)) return "";
      return url.href;
    } catch {
      return "";
    }
  }

  function link(url, label, iconName, extraClass = "") {
    const href = safeUrl(url);
    if (!href) return "";
    return `<a class="action-icon ${extraClass}" href="${esc(href)}" target="_blank" rel="noopener noreferrer" title="${esc(label)}" aria-label="${esc(label)}">${icon(iconName)}<span>${esc(label)}</span></a>`;
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function parseDay(day) {
    return new Date(`${day}T12:00:00`);
  }

  function weekday(day) {
    return WEEKDAYS[parseDay(day).getDay()] || "";
  }

  function prettyDate(day) {
    const date = parseDay(day);
    return `${date.getFullYear()} 年 ${date.getMonth() + 1} 月 ${date.getDate()} 日 · ${weekday(day)}`;
  }

  function shortDate(day) {
    return day.slice(5).replace("-", ".");
  }

  function monthDay(day) {
    const date = parseDay(day);
    return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
  }

  function addDays(day, amount) {
    const date = parseDay(day);
    date.setDate(date.getDate() + amount);
    return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
  }

  function timeFromIso(value) {
    if (!value) return "";
    try {
      return new Date(value).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }

  function dayOfYear(day) {
    const date = parseDay(day);
    const start = new Date(date.getFullYear(), 0, 1, 12, 0, 0);
    return Math.floor((date - start) / 86400000) + 1;
  }

  function sanitizeFilename(value) {
    return String(value || "paper")
      .replace(/[\\/:*?"<>|\s]+/g, "_")
      .slice(0, 90)
      .replace(/_+$/g, "");
  }

  function analysisOf(paper) {
    if (paper.analysis) return paper.analysis;
    const fallback = paper.ai_summary || paper.abstract || "";
    return {
      title_zh: paper.title,
      tldr: fallback.slice(0, 150),
      problem: "",
      summary: fallback.slice(0, 900),
      analogy: "",
      how_it_works: [],
      contrast: "",
      example: "",
      misconceptions: [],
      jargon: [],
      key_points: [],
      method: "",
      findings: "",
      why_it_matters: "",
      limitations: "",
      takeaway: "",
      tags: paper.keywords || [],
      difficulty: "进阶",
      read_minutes: 10,
      paper_type: "分析",
      source: "fallback",
      prompt_version: "fallback",
    };
  }

  function mediaOf(paper) {
    const urls = Array.isArray(paper.raw?.media_urls) ? paper.raw.media_urls : [];
    const rawVideo = paper.raw?.video_url || urls.find((url) => /\.(mp4|webm|mov)(\?|$)/i.test(url)) || "";
    const rawImage = paper.thumbnail || paper.raw?.image_url || urls.find((url) => /\.(png|jpe?g|webp|gif)(\?|$)/i.test(url)) || "";
    return { video: safeUrl(rawVideo), image: safeUrl(rawImage) };
  }

  function paperTags(paper, limit = 4) {
    const analysis = analysisOf(paper);
    const tags = (analysis.tags && analysis.tags.length ? analysis.tags : paper.keywords) || [];
    return tags.map((tag) => String(tag)).filter(Boolean).slice(0, limit);
  }

  function authorsLine(paper, limit = 3) {
    const authors = paper.authors || [];
    if (!authors.length) return "作者未知";
    const shown = authors.slice(0, limit).join(", ");
    return authors.length > limit ? `${shown} 等` : shown;
  }

  function replacePaper(paper) {
    if (!state.data || !paper) return;
    const index = state.data.papers.findIndex((item) => item.id === paper.id);
    if (index >= 0) state.data.papers[index] = paper;
  }

  function findPaper(id) {
    return state.data?.papers?.find((paper) => paper.id === id) || null;
  }

  async function api(path, options = {}) {
    const request = { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
    if (request.body && typeof request.body !== "string") {
      request.body = JSON.stringify(request.body);
      request.headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(path, request);
    } catch (error) {
      throw new Error("无法连接 Paper Daily，请确认服务仍在运行。");
    }
    let payload = null;
    const type = response.headers.get("content-type") || "";
    if (type.includes("application/json")) {
      payload = await response.json().catch(() => null);
    } else {
      const text = await response.text().catch(() => "");
      payload = text ? { detail: text } : null;
    }
    if (!response.ok) {
      const message = payload?.detail || payload?.message || `请求失败（HTTP ${response.status}）`;
      const error = new Error(typeof message === "string" ? message : JSON.stringify(message));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function toast(message, type = "info", timeout = 4600) {
    const root = $("#toast-root");
    if (!root) return;
    const node = document.createElement("div");
    node.className = `toast${type === "error" ? " is-error" : type === "success" ? " is-success" : ""}`;
    node.setAttribute("role", type === "error" ? "alert" : "status");
    const mark = document.createElement("span");
    mark.className = "toast-mark";
    const copy = document.createElement("span");
    copy.textContent = message;
    node.append(mark, copy);
    root.append(node);
    while (root.children.length > 4) root.firstElementChild?.remove();
    window.setTimeout(() => {
      node.classList.add("is-leaving");
      window.setTimeout(() => node.remove(), 190);
    }, timeout);
  }

  /* ------------------------------------------------------------------------
     Boot & theme
     ------------------------------------------------------------------------ */

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("paper-daily-theme", theme);
    } catch {
      /* private mode */
    }
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#11110d" : "#f3f0e7");
    const button = $("#theme-btn");
    if (button) {
      button.setAttribute("aria-label", theme === "dark" ? "切换浅色模式" : "切换深色模式");
      button.title = button.getAttribute("aria-label");
    }
  }

  function initialTheme() {
    try {
      const stored = localStorage.getItem("paper-daily-theme");
      if (stored === "light" || stored === "dark") return stored;
    } catch {
      /* ignore */
    }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  async function init() {
    applyTheme(initialTheme());
    bindStaticEvents();
    try {
      const meta = await api("/api/meta");
      state.meta = meta;
      state.today = meta.today;
      state.settings = meta.settings;
      state.date = meta.today;
      updateDateChrome();
      const picker = $("#date-picker");
      if (picker) picker.max = state.today;
      await loadHistory();
      await loadDay(state.today);
    } catch (error) { renderFatal(error); }
  }

  function bindStaticEvents() {
    $("#brand-btn")?.addEventListener("click", () => goToDate(state.today || state.date));
    $("#theme-btn")?.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    $("#prev-day")?.addEventListener("click", () => shiftDay(-1));
    $("#next-day")?.addEventListener("click", () => shiftDay(1));
    $("#today-btn")?.addEventListener("click", () => goToDate(state.today));
    $("#date-picker")?.addEventListener("change", (event) => {
      const value = event.target.value;
      if (value) goToDate(value);
    });
    $("#mobile-menu-btn")?.addEventListener("click", toggleSidebar);
    $("#sidebar-backdrop")?.addEventListener("click", closeSidebar);

    $$(".filter-link").forEach((button) => {
      button.addEventListener("click", () => setFilter(button.dataset.filter || "all"));
    });

    document.addEventListener("click", onDocumentClick);
    document.addEventListener("input", onDocumentInput);
    document.addEventListener("change", onDocumentChange);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", () => {
      if (window.innerWidth > 940) closeSidebar();
    });
    window.addEventListener("beforeunload", () => {
      if (state.pollTimer) window.clearTimeout(state.pollTimer);
    });
  }

  /* ------------------------------------------------------------------------
     Data loading & chrome
     ------------------------------------------------------------------------ */

  async function loadHistory() {
    try {
      const payload = await api("/api/history");
      state.history = payload.days || [];
      renderHistory();
    } catch {
      /* History is non-critical. */
    }
  }

  async function loadDay(day, options = {}) {
    if (!day) return;
    const sequence = ++state.loadSeq;
    state.date = day;
    state.loading = true;
    state.query = "";
    if (state.digestTimer) {
      window.clearTimeout(state.digestTimer);
      state.digestTimer = null;
    }
    updateDateChrome();
    updateSidebarActive();
    if (!options.silent) renderLoading();
    try {
      const payload = await api(`/api/days/${encodeURIComponent(day)}`);
      if (sequence !== state.loadSeq) return;
      state.data = payload;
      state.loading = false;
      updateCounts();
      updateDateChrome();
      renderDay();
      scheduleDigestPoll();
    } catch (error) {
      if (sequence !== state.loadSeq) return;
      state.loading = false;
      renderLoadError(error);
    }
  }

  async function goToDate(day) {
    if (!day) return;
    closeSidebar();
    closeExportMenus();
    if (day > (state.today || day)) {
      toast("还不能查看未来日期。", "error");
      return;
    }
    if (day === state.date && state.data && !state.loading) {
      updateDateChrome();
      return;
    }
    await loadDay(day);
  }

  function shiftDay(amount) {
    const next = addDays(state.date || state.today, amount);
    if (next > (state.today || next)) return;
    goToDate(next);
  }

  function updateDateChrome() {
    if (!state.date) return;
    const date = parseDay(state.date);
    const weekdayNode = $("#side-weekday");
    if (weekdayNode) weekdayNode.textContent = weekday(state.date);
    const sideDate = $("#side-date");
    if (sideDate) sideDate.textContent = shortDate(state.date);
    const sideYear = $("#side-year");
    if (sideYear) sideYear.textContent = String(date.getFullYear());
    const picker = $("#date-picker");
    if (picker) {
      picker.value = state.date;
      picker.max = state.today || state.date;
    }
    const issue = $("#issue-label");
    if (issue) issue.textContent = `NO. ${String(dayOfYear(state.date)).padStart(3, "0")}`;

    const nextBtn = $("#next-day");
    if (nextBtn) nextBtn.disabled = state.date >= (state.today || state.date);
    updateSidebarActive();
  }

  function updateSidebarActive() {
    $$(".history-item").forEach((item) => {
      item.classList.toggle("is-active", item.dataset.date === state.date);
    });
  }

  function updateCounts() {
    const node = $("#count-all");
    if (node) node.textContent = String(state.data?.papers?.length || 0);
  }

  function setActiveFilter(filter) {
    $$(".filter-link").forEach((node) => {
      node.classList.toggle("is-active", (node.dataset.filter || "all") === filter);
    });
    $$(".mobile-filter-pill").forEach((node) => {
      node.classList.toggle("is-active", (node.dataset.filter || "all") === filter);
    });
  }

  function setFilter(filter) {
    state.filter = filter;
    setActiveFilter(filter);
    renderPaperList();
    if (window.innerWidth <= 940) closeSidebar();
  }

  function renderHistory() {
    const container = $("#history-list");
    if (!container) return;
    if (!state.history.length) {
      container.innerHTML = '<div class="history-empty">还没有提取记录</div>';
      return;
    }
    container.innerHTML = state.history
      .map((entry) => {
        const day = parseDay(entry.date);
        const label = WEEKDAYS[day.getDay()].replace("星期", "周");
        return `<button class="history-item${entry.date === state.date ? " is-active" : ""}" data-date="${esc(entry.date)}" data-action="go-date" title="${esc(prettyDate(entry.date))}">
          <span>${esc(shortDate(entry.date))}</span>
          <strong>${esc(label)}</strong>
          <em>${entry.count}</em>
        </button>`;
      })
      .join("");
  }

  function renderLoading() {
    $("#content").innerHTML = `<div class="loading-view">
      <div class="loading-kicker">PREPARING YOUR BRIEFING</div>
      <div class="skeleton skeleton-title"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton-grid">
        <div class="skeleton skeleton-card"></div>
        <div class="skeleton skeleton-card"></div>
        <div class="skeleton skeleton-card"></div>
      </div>
    </div>`;
  }

  function renderLoadError(error) {
    $("#content").innerHTML = `<section class="empty-state">
      <div class="empty-kicker"><i></i> CONNECTION LOST</div>
      <h1 class="empty-title">暂时读不到<br><em>这一天</em>。</h1>
      <p class="empty-copy">${esc(error.message || "加载失败，请稍后重试。")}</p>
      <div class="empty-actions">
        <button class="big-btn" data-action="reload-page">${icon("refresh")} 重新加载页面</button>
        <button class="big-btn secondary" data-action="go-date" data-date="${esc(state.today)}">回到今天</button>
      </div>
    </section>`;
  }

  function renderFatal(error) {
    $("#content").innerHTML = `<section class="empty-state">
      <div class="empty-kicker"><i></i> SERVER NOT READY</div>
      <h1 class="empty-title">Paper Daily<br><em>还没准备好</em>。</h1>
      <p class="empty-copy">${esc(error.message || "无法连接 Paper Daily。")}<br>请稍后重试；如果问题持续存在，请检查服务状态。</p>
      <div class="empty-actions">
        <button class="big-btn" data-action="reload-page">${icon("refresh")} 重试</button>
      </div>
    </section>`;
  }

  /* ------------------------------------------------------------------------
     Day rendering
     ------------------------------------------------------------------------ */

  function renderDay() {
    if (!state.data) return;
    if (!state.data.papers || state.data.papers.length === 0) {
      $("#content").innerHTML = renderEmpty();
      return;
    }
    const papers = state.data.papers;
    const digest = state.data.digest;
    const isToday = state.date === state.today;
    const finished = state.data.latest_run?.finished_at;
    const withCode = papers.filter((paper) => paper.github_url).length;

    $("#content").innerHTML = `
      <section class="day-head">
        <div class="day-kicker">
          <span>DAILY BRIEFING</span>
          <span class="dot"></span>
          <span>${esc(prettyDate(state.date))}</span>
          ${isToday ? '<span class="live-label">· TODAY</span>' : ""}
        </div>
        <div class="day-title-row">
          <div>
            <h1 class="day-title">${isToday ? "今日论文" : `${monthDay(state.date)}`}<span class="count">${papers.length}</span></h1>
            <p class="day-sub">Hugging Face Daily Papers · 免费阅读与统一中文解读。${finished ? ` 更新于 ${esc(timeFromIso(finished))}。` : ""}</p>
          </div>
          <div class="day-actions">
            <div class="export-wrap">
              <button class="ghost-btn" data-action="toggle-export" aria-haspopup="true" aria-expanded="false">${icon("download")} <span>导出</span></button>
              <div class="export-menu" hidden>
                <small>下载</small>
                <a href="/api/days/${encodeURIComponent(state.date)}/export.md" download>Markdown 日报 <span>.md</span></a>
                <a href="/api/days/${encodeURIComponent(state.date)}/export.json" download>结构化数据 <span>.json</span></a>
              </div>
            </div>
            <button class="ghost-btn is-accent" data-action="reload-day">${icon("refresh")} <span>刷新状态</span></button>
          </div>
        </div>
        <div class="stats-strip">
          <div class="stat"><strong>${papers.length}</strong><span>篇论文</span></div>
          <div class="stat"><strong id="stat-code">${withCode}</strong><span>篇有代码</span></div>
        </div>
      </section>
      ${digest ? renderDigest(digest, papers) : renderDigestWaiting(state.data, papers)}
      <div class="toolbar">
        <div class="search-box">
          ${icon("search")}
          <input id="search-input" type="search" autocomplete="off" placeholder="搜索标题、摘要、标签…" value="${esc(state.query)}" aria-label="搜索当天论文">
          <button class="clear-search" data-action="clear-search" aria-label="清空搜索"${state.query ? "" : " hidden"}>${icon("close")}</button>
        </div>
        <div class="sort-wrap">
          <select id="sort-select" aria-label="排序方式">
            <option value="upvotes"${state.sort === "upvotes" ? " selected" : ""}>按社区热度</option>
            <option value="newest"${state.sort === "newest" ? " selected" : ""}>按发布时间</option>
          </select>
          ${icon("down")}
        </div>

      </div>
      <div class="result-line">
        <span><strong id="result-count">${papers.length}</strong> <span id="result-label">篇论文</span></span>
        <span id="result-hint">按社区热度</span>
      </div>
      <div class="paper-list" id="paper-list"></div>
    `;
    renderPaperList();
    scheduleDigestPoll();
  }

  function renderDigestWaiting(payload, papers) {
    const status = payload.digest_status || "collecting";
    const due = status === "pending";
    const digestTime = payload.digest_time || state.settings?.schedule?.digest_time || "16:00";
    const title = due ? "今日导读正在生成" : "正在收集今天的文章";
    const copy = due
      ? `今天已收集 ${papers.length} 篇论文。到达 ${digestTime} 后会生成一次今日导读；后台完成后这里会自动更新。`
      : `Hugging Face 会在一天里陆续上架论文。后台每 15 分钟检查一次，今天先持续收集，${digestTime} 再生成一次导读。`;
    return `<section class="digest-waiting">
      <div class="digest-waiting-icon">${icon("spark")}</div>
      <div class="digest-waiting-copy">
        <span class="digest-label"> TODAY'S BRIEFING</span>
        <h2>${title}</h2>
        <p>${copy}</p>
        <div class="digest-waiting-meta"><span>${papers.length} 篇已收集</span><span>导读时间 ${esc(digestTime)}</span></div>
      </div>
      <button class="ghost-btn" data-action="reload-day">${icon("refresh")} 刷新状态</button>
    </section>`;
  }

  function scheduleDigestPoll() {
    if (state.digestTimer) window.clearTimeout(state.digestTimer);
    if (state.date !== state.today) return;
    state.digestTimer = window.setTimeout(async () => {
      if (state.selectedId) { scheduleDigestPoll(); return; }
      try { await loadDay(state.date, { silent: true }); }
      catch { scheduleDigestPoll(); }
    }, 60000);
  }

  function renderDigest(digest, papers) {
    const byId = new Map(papers.map((paper) => [paper.id, paper]));
    const primer = (digest.primer || []).filter((item) => item && item.term && item.plain).slice(0, 5);
    const threads = (digest.threads || []).filter((item) => item && item.name).slice(0, 4);
    const themes = (digest.themes || []).slice(0, 3);
    const path = (digest.reading_path || []).filter((item) => item && byId.has(item.id)).slice(0, 5);
    const picks = (digest.top_picks || [])
      .map((pick) => ({ ...pick, paper: byId.get(pick.id) }))
      .filter((pick) => pick.paper);
    const pathItems = path.length
      ? path.map((item) => ({ ...item, paper: byId.get(item.id) }))
      : picks.map((pick) => ({
          id: pick.paper.id,
          why: pick.reason || analysisOf(pick.paper).tldr || "",
          before: "",
          paper: pick.paper,
        }));
    const paperRef = (id) => {
      const paper = byId.get(id);
      if (!paper) return "";
      const title = analysisOf(paper).title_zh || paper.title;
      const short = title.length > 30 ? `${title.slice(0, 30)}…` : title;
      return `<button class="paper-ref" data-action="open-paper" data-id="${esc(paper.id)}" title="${esc(title)}">${esc(short)}</button>`;
    };
    return `<section class="digest-card${primer.length || threads.length ? " is-deep" : ""}">
      <div class="digest-head">
        <span class="digest-label">${icon("spark")} 今日导读</span>
        <span class="digest-source${digest.source === "llm" ? " is-ai" : ""}">${digest.source === "llm" ? "AI 主编" : "规则导读"}</span>
      </div>
      <h2 class="digest-title">${esc(digest.headline || "今天的论文速览")}</h2>
      <p class="digest-overview">${esc(digest.overview || "")}</p>
      ${
        primer.length
          ? `<div class="primer-section">
              <span class="digest-block-label">PRIMER / 先补这几个概念</span>
              <div class="primer-grid">
                ${primer
                  .map(
                    (item) => `<article class="primer-card">
                      <strong>${esc(item.term)}</strong>
                      <p>${esc(item.plain)}</p>
                      ${item.why ? `<span class="primer-why">${esc(item.why)}</span>` : ""}
                      ${item.example ? `<em>${esc(item.example)}</em>` : ""}
                      ${(item.paper_ids || []).length ? `<div class="primer-links">${item.paper_ids.map(paperRef).join("")}</div>` : ""}
                    </article>`,
                  )
                  .join("")}
              </div>
            </div>`
          : ""
      }
      <div class="digest-lower">
        <div class="thread-column">
          <span class="digest-block-label">${threads.length ? "THREADS / 今天到底在解决什么" : "THEMES / 主题脉络"}</span>
          ${
            threads.length
              ? `<div class="thread-list">${threads
                  .map(
                    (thread, index) => `<article class="thread-item">
                      <div class="thread-head"><span class="thread-index">0${index + 1}</span><h3>${esc(thread.name)}</h3></div>
                      ${thread.problem ? `<p class="thread-row"><b>原来卡在</b>${esc(thread.problem)}</p>` : ""}
                      ${thread.approach ? `<p class="thread-row"><b>今天怎么做</b>${esc(thread.approach)}</p>` : ""}
                      ${thread.evidence ? `<p class="thread-row is-evidence"><b>证据</b>${esc(thread.evidence)}</p>` : ""}
                      ${thread.boundary || thread.caveat ? `<p class="thread-row is-caveat"><b>适用边界</b>${esc(thread.boundary || thread.caveat)}</p>` : ""}
                      ${(thread.paper_ids || []).length ? `<div class="thread-papers">${thread.paper_ids.map(paperRef).join("")}</div>` : ""}
                    </article>`,
                  )
                  .join("")}</div>`
              : `<div class="theme-list">${themes
                  .map(
                    (theme, index) => `<div class="theme-item">
                      <span class="theme-index">0${index + 1}</span>
                      <div><strong>${esc(theme.name)}</strong><p>${esc(theme.summary || "")}</p></div>
                    </div>`,
                  )
                  .join("") || '<p class="theme-item">暂无主题归纳。</p>'}</div>`
          }
        </div>
        <div class="reading-column">
          <span class="digest-block-label">READING PATH / 建议从这里开始</span>
          <div class="pick-list">
            ${pathItems
              .map((item, index) => {
                const analysis = analysisOf(item.paper);
                return `<button class="pick-item" data-action="open-paper" data-id="${esc(item.paper.id)}">
                  <span class="pick-number">${pad2(index + 1)}</span>
                  <span class="pick-copy"><strong>${esc(analysis.title_zh || item.paper.title)}</strong><span>${esc(item.why || analysis.tldr || "")}</span>${item.before ? `<em class="pick-before">先理解：${esc(item.before)}</em>` : ""}</span>
                  ${icon("arrow")}
                </button>`;
              })
              .join("") || '<p class="theme-item">打开论文列表开始阅读。</p>'}
          </div>
        </div>
      </div>
      ${digest.skip_hint ? `<p class="digest-skip"><strong>时间有限</strong> · ${esc(digest.skip_hint)}</p>` : ""}
    </section>`;
  }

  function renderEmpty() {
    const active = Boolean(state.data?.active_run);
    return `<section class="empty-state">
      <div class="empty-kicker">DAILY PAPERS</div>
      <h1 class="empty-title">${active ? "正在整理论文" : "这一天暂无论文"}</h1>
      <p class="empty-copy">${active ? "后台正在更新，完成后页面会自动刷新。" : "可以查看往期；今日内容由后台定时更新。"}</p>
      <button class="ghost-btn" data-action="reload-day">${icon("refresh")} 刷新页面状态</button>
    </section>`;
  }

  function filteredPapers() {
    const papers = [...(state.data?.papers || [])];
    const query = state.query.trim().toLowerCase();
    let result = papers.filter((paper) => {
      if (!query) return true;
      const analysis = analysisOf(paper);
      const haystack = [
        paper.title,
        analysis.title_zh,
        analysis.tldr,
        analysis.summary,
        analysis.why_it_matters,
        ...(analysis.tags || []),
        ...(paper.keywords || []),
        ...(paper.authors || []),
        paper.organization,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
    if (state.sort === "upvotes") {
      result.sort((a, b) => (b.upvotes || 0) - (a.upvotes || 0) || String(a.id).localeCompare(String(b.id)));
    } else if (state.sort === "newest") {
      result.sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)) || String(a.id).localeCompare(String(b.id)));
    } else {
      result.sort((a, b) => (b.upvotes || 0) - (a.upvotes || 0) || String(a.id).localeCompare(String(b.id)));
    }
    return result;
  }

  function renderPaperList() {
    const list = $("#paper-list");
    if (!list) return;
    const papers = filteredPapers();
    const count = $("#result-count");
    const label = $("#result-label");
    const hint = $("#result-hint");
    if (count) count.textContent = String(papers.length);
    if (label) label.textContent = state.query ? "篇匹配" : "篇论文";
    if (hint) {
      hint.textContent =
        state.sort === "upvotes" ? "按社区热度" : state.sort === "newest" ? "按发布时间" : "按社区热度";
    }
    const clear = $(".clear-search");
    if (clear) clear.hidden = !state.query;
    setActiveFilter(state.filter);

    if (!papers.length) {
      let message = "没有符合当前筛选的论文。";
      let action = '<button class="text-btn" data-action="clear-filters">清除筛选与搜索</button>';
      if (state.query) message = `没有找到包含「${state.query}」的论文。`;
      list.innerHTML = `<div class="paper-empty"><strong>这里暂时是空的</strong><p>${esc(message)}</p>${action}</div>`;
      return;
    }
    list.innerHTML = papers
      .map((paper, index) => paperCard(paper, index))
      .join("");
  }

  function paperCard(paper, index) {
    const analysis = analysisOf(paper);
    const title = analysis.title_zh || paper.title;
    const showOriginal = analysis.source === "llm" && title.trim() !== paper.title.trim();
    const tags = paperTags(paper, 4);
    const top = index < 3;
    const type = analysis.paper_type || "分析";
    const isFallback = analysis.source !== "llm";
    const media = mediaOf(paper);
    return `<article class="paper-card${top ? " is-top" : ""}${media.image || media.video ? " has-visual" : ""}" data-id="${esc(paper.id)}" style="animation-delay:${Math.min(index * 28, 260)}ms">
      <div class="paper-rank">
        <strong>${pad2(index + 1)}</strong>
        ${top ? '<span class="rank-flag"></span>' : ""}
      </div>
      <div class="paper-body">
        <div class="paper-topline">
          <span class="chip">${esc(type)}</span>
          ${paper.github_url ? `<span class="chip is-code">CODE</span>` : ""}
          ${isFallback ? `<span class="chip" title="未经过私有模型深度分析">规则</span>` : ""}
        </div>
        <h2 class="paper-title"><button data-action="open-paper" data-id="${esc(paper.id)}">${esc(title)}</button></h2>
        ${showOriginal ? `<p class="paper-original">${esc(paper.title)}</p>` : ""}
        <p class="paper-tldr">${esc(analysis.tldr || paper.ai_summary || "暂无摘要")}</p>
        <div class="paper-tags">${tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</div>
        <div class="paper-meta">
          <span>${esc(authorsLine(paper))}</span>
          <span class="meta-dot"></span>
          <span class="meta-org">${esc(paper.organization || "机构未标注")}</span>
          <span class="meta-dot"></span>
          <span>${icon("clock")} ${Number(analysis.read_minutes || 12)} min</span>
          <span class="meta-dot"></span>
          <span>${esc(analysis.difficulty || "进阶")}</span>
          ${paper.upvotes ? `<span class="meta-dot"></span><span>${icon("up")} ${paper.upvotes}</span>` : ""}
        </div>
        <div class="paper-actions">
          <button class="open-paper" data-action="open-paper" data-id="${esc(paper.id)}">阅读分析 ${icon("arrow")}</button>
          ${link(paper.hf_url, "HF 页面", "external")}
          ${link(paper.arxiv_url, "arXiv", "file")}
          ${paper.github_url ? link(paper.github_url, "GitHub", "github") : ""}
          ${paper.pdf_url ? `<button class="action-icon" data-action="download-pdf" data-id="${esc(paper.id)}" title="下载 PDF" aria-label="下载 PDF">${icon("download")}<span>PDF</span></button>` : ""}
        </div>
      </div>
      ${media.image || media.video ? `<button class="paper-visual" data-action="open-paper" data-id="${esc(paper.id)}" aria-label="打开 ${esc(title)} 的图片/视频">
        ${media.video ? `<video muted loop playsinline preload="metadata" src="${esc(media.video)}"></video>` : `<img src="${esc(media.image)}" alt="${esc(title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.paper-visual').hidden=true">`}
        ${media.video ? '<span class="media-badge">VIDEO</span>' : ""}
      </button>` : ""}
    </article>`;
  }

  /* ------------------------------------------------------------------------
     Paper actions
     ------------------------------------------------------------------------ */

  async function openPaper(id) {
    let paper = findPaper(id);
    if (!paper) {
      try {
        const payload = await api(`/api/papers/${encodeURIComponent(id)}`);
        paper = payload.paper;
        if (paper && state.data) {
          state.data.papers.push(paper);
        }
      } catch (error) {
        toast(error.message, "error");
        return;
      }
    }
    if (!paper) return;
    state.selectedId = id;
    const visible = filteredPapers();
    let list = visible.some((item) => item.id === id) ? visible : filteredPapers();
    state.detailList = list;
    state.detailIndex = list.findIndex((item) => item.id === id);
    renderDetail(paper);
    document.body.classList.add("modal-open");
    requestAnimationFrame(() => $("#detail-root .detail-close")?.focus());
  }

  function renderDetail(paper) {
    const root = $("#detail-root");
    if (!root) return;
    const analysis = analysisOf(paper);
    const title = analysis.title_zh || paper.title;
    const showOriginal = title.trim() !== String(paper.title).trim();
    const tags = paperTags(paper, 6);
    const links = [
      paper.hf_url ? { url: paper.hf_url, label: "Hugging Face 论文页", icon: "external" } : null,
      paper.arxiv_url ? { url: paper.arxiv_url, label: "arXiv 摘要页", icon: "file" } : null,
      paper.github_url ? { url: paper.github_url, label: "GitHub 代码仓库", icon: "github" } : null,
      paper.project_url && paper.project_url !== paper.github_url
        ? { url: paper.project_url, label: "项目主页", icon: "external" }
        : null,
    ].filter(Boolean);
    const total = state.data?.papers?.length || 0;
    const position = total ? (state.detailList.findIndex((item) => item.id === paper.id) + 1 || 1) : 1;
    const prev = state.detailIndex > 0 ? state.detailList[state.detailIndex - 1] : null;
    const next = state.detailIndex >= 0 && state.detailIndex < state.detailList.length - 1 ? state.detailList[state.detailIndex + 1] : null;
    const failed = paper.analysis_status === "failed";
    const fallback = analysis.source !== "llm";

    root.innerHTML = `<div class="overlay" data-action="close-detail">
      <div class="detail-panel" role="dialog" aria-modal="true" aria-labelledby="detail-title">
        <div class="detail-topbar">
          <div class="detail-breadcrumb"><span>PAPER</span><span>${pad2(position)} / ${pad2(total || 1)}</span><span>·</span><span>${esc(shortDate(paper.daily_date))}</span></div>
          <div class="detail-top-actions">
            <button class="detail-icon-btn" data-action="copy-citation" data-id="${esc(paper.id)}" title="复制引用">${icon("copy")}<span>引用</span></button>
            <button class="detail-close" data-action="close-detail" aria-label="关闭">${icon("close")}</button>
          </div>
        </div>
        <div class="detail-scroll">
          <div class="detail-grid">
            <article class="detail-main">
              <div class="detail-chips">
                <span class="chip">${esc(analysis.paper_type || "分析")}</span>
                <span class="chip">${esc(analysis.difficulty || "进阶")}</span>
                ${paper.github_url ? '<span class="chip is-code">CODE</span>' : ""}
                ${fallback ? '<span class="chip">规则摘要</span>' : `<span class="chip is-highlight">${String(analysis.prompt_version || "").startsWith("beginner-") ? "AI 科普" : "AI 精读"}</span>`}
              </div>
              <h1 class="detail-title" id="detail-title">${esc(title)}</h1>
              ${showOriginal ? `<p class="detail-original">${esc(paper.title)}</p>` : ""}
              <p class="detail-tldr">${esc(analysis.tldr || paper.ai_summary || "暂无摘要。")}</p>

              ${(() => {
                const media = mediaOf(paper);
                if (!media.image && !media.video) return "";
                return `<div class="detail-media">${media.video ? `<video controls preload="metadata" ${media.image ? `poster="${esc(media.image)}"` : ""} src="${esc(media.video)}"></video>` : `<img src="${esc(media.image)}" alt="${esc(title)}" loading="lazy" referrerpolicy="no-referrer">`}</div>`;
              })()}

              ${failed ? `<div class="model-warning">${icon("spark")}<span>这篇论文的模型总结失败，以下为规则摘要。${paper.analysis_error ? ` ${esc(paper.analysis_error)}` : ""}</span></div>` : ""}

              ${
                analysis.jargon?.length
                  ? `<section class="jargon-section"><span class="beginner-label">读之前，先认识这几个词</span><div class="jargon-list">${analysis.jargon
                      .map(
                        (item) =>
                          `<div class="jargon-item"><strong>${esc(item.term || "")}</strong><span>${esc(item.plain || "")}</span>${item.why ? `<small>${esc(item.why)}</small>` : ""}${item.example ? `<em>例如：${esc(item.example)}</em>` : ""}</div>`,
                      )
                      .join("")}</div></section>`
                  : ""
              }
              ${analysis.problem ? `<section class="plain-box"><span class="beginner-label">先讲人话</span><p>${esc(analysis.problem)}</p></section>` : ""}
              <section class="detail-section"><h3>PLAIN SUMMARY / 这篇论文做了什么</h3><p>${esc(analysis.summary || "暂无摘要。")}</p></section>
              ${
                analysis.analogy
                  ? `<section class="analogy-box"><span class="beginner-label">一个生活化的类比</span><p>${esc(analysis.analogy)}</p></section>`
                  : ""
              }
              ${
                analysis.how_it_works?.length
                  ? `<section class="detail-section"><h3>HOW IT WORKS / 它是怎么工作的</h3><ol class="steps-list">${analysis.how_it_works.map((step) => `<li>${esc(step)}</li>`).join("")}</ol></section>`
                  : ""
              }
              ${
                analysis.contrast
                  ? `<section class="contrast-box"><span class="beginner-label">和原来的做法比，改了什么</span><p>${esc(analysis.contrast)}</p></section>`
                  : ""
              }
              ${
                analysis.example
                  ? `<section class="example-box"><span class="beginner-label">举个例子</span><p>${esc(analysis.example)}</p></section>`
                  : ""
              }
              ${
                analysis.misconceptions?.length
                  ? `<section class="misconceptions-box"><span class="beginner-label">容易理解错的地方</span><div class="misconceptions-list">${analysis.misconceptions
                      .map(
                        (item) =>
                          `<div class="misconception-item"><p class="misconception-wrong">${esc(item.misunderstanding || "")}</p><p class="misconception-right">${esc(item.clarify || "")}</p></div>`,
                      )
                      .join("")}</div></section>`
                  : ""
              }
              ${
                analysis.key_points?.length
                  ? `<section class="detail-section"><h3>KEY POINTS / 关键要点</h3><ul class="detail-points">${analysis.key_points.map((point) => `<li>${esc(point)}</li>`).join("")}</ul></section>`
                  : ""
              }
              ${
                analysis.takeaway
                  ? `<section class="takeaway-box"><span class="beginner-label">读完记住这一点</span><p>${esc(analysis.takeaway)}</p></section>`
                  : ""
              }
              ${
                analysis.why_it_matters
                  ? `<section class="why-box detail-section"><h3>WHY IT MATTERS / 与你何关</h3><p>${esc(analysis.why_it_matters)}</p></section>`
                  : ""
              }
              ${analysis.limitations ? `<section class="detail-section"><h3>CAVEATS / 局限</h3><p>${esc(analysis.limitations)}</p></section>` : ""}
              ${
                analysis.method || analysis.findings
                  ? `<details class="tech-box"><summary>给进阶读者 · 方法与结果</summary><div class="tech-body">${analysis.method ? `<h3>METHOD / 方法</h3><p>${esc(analysis.method)}</p>` : ""}${analysis.findings ? `<h3>FINDINGS / 发现</h3><p>${esc(analysis.findings)}</p>` : ""}</div></details>`
                  : ""
              }
              <details class="abstract-box">
                <summary>原始摘要 · ORIGINAL ABSTRACT</summary>
                <p>${esc(paper.abstract || "暂无原始摘要。")}</p>
              </details>


              <div class="detail-nav">
                <button data-action="detail-prev"${prev ? "" : " disabled"}>${icon("left")}<span>${prev ? esc(analysisOf(prev).title_zh || prev.title) : "上一篇"}</span></button>
                <button data-action="detail-next"${next ? "" : " disabled"}><span>${next ? esc(analysisOf(next).title_zh || next.title) : "下一篇"}</span>${icon("right")}</button>
              </div>
            </article>

            <aside class="detail-aside">
              <dl class="detail-meta">
                <div><dt>Authors</dt><dd>${esc((paper.authors || []).join(", ") || "未知")}</dd></div>
                <div><dt>Affiliation</dt><dd>${esc(paper.organization || "未标注")}</dd></div>
                <div><dt>Published</dt><dd>${esc(String(paper.published_at || "").slice(0, 10) || "未知")}</dd></div>
                <div><dt>Reading</dt><dd>约 ${Number(analysis.read_minutes || 12)} 分钟 · ${esc(analysis.paper_type || "论文")}</dd></div>
                <div><dt>Community</dt><dd>${paper.upvotes || 0} 赞</dd></div>
                <div><dt>arXiv</dt><dd>${esc(paper.id)}</dd></div>
              </dl>
              <div class="link-stack">
                ${paper.pdf_url ? `<button class="side-link is-primary" data-action="download-pdf" data-id="${esc(paper.id)}"><span>${icon("download")} 下载 PDF</span>${icon("arrow")}</button>` : ""}
                ${links.map((item) => `<a class="side-link" href="${esc(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer"><span>${icon(item.icon)} ${esc(item.label)}</span>${icon("external")}</a>`).join("")}
              </div>
              <p class="source-note">${fallback ? "本页为规则摘要；内容由后台统一更新。" : "由模型基于论文摘要统一生成。AI 可能出错，关键结论请回到原文核对。"}</p>
            </aside>
          </div>
        </div>
      </div>
    </div>`;
  }

  function closeDetail() {
    if (!$("#detail-root")?.innerHTML) return;
    $("#detail-root").innerHTML = "";
    state.selectedId = null;
    document.body.classList.remove("modal-open");
  }

  function navigateDetail(direction) {
    if (!state.detailList.length) return;
    let index = state.detailIndex + direction;
    if (index < 0 || index >= state.detailList.length) return;
    const paper = state.detailList[index];
    state.detailIndex = index;
    openPaper(paper.id);
  }

  function downloadPdf(paper) {
    if (!paper?.pdf_url) { toast("这篇论文没有可用的 PDF 链接。", "error"); return; }
    const anchor = document.createElement("a");
    anchor.href = `/api/papers/${encodeURIComponent(paper.id)}/pdf`;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  async function copyCitation(paper) {
    if (!paper) return;
    const authors = paper.authors?.length
      ? `${paper.authors.slice(0, 3).join(", ")}${paper.authors.length > 3 ? ", et al." : ""}`
      : "Unknown authors";
    const citation = `${authors}. ${paper.title}. arXiv:${paper.id}. ${paper.arxiv_url || paper.hf_url || ""}`.trim();
    await copyText(citation, "引用已复制");
  }

  async function copyText(value, successMessage) {
    try {
      await navigator.clipboard.writeText(value);
      if (successMessage) toast(successMessage, "success");
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.append(textarea);
      textarea.select();
      try {
        document.execCommand("copy");
        if (successMessage) toast(successMessage, "success");
      } catch {
        toast("复制失败，请手动选择文本。", "error");
      }
      textarea.remove();
    }
  }

  /* ------------------------------------------------------------------------
     Extraction run
     ------------------------------------------------------------------------ */

  function toggleSidebar() {
    const sidebar = $("#sidebar");
    const backdrop = $("#sidebar-backdrop");
    const open = !sidebar?.classList.contains("is-open");
    sidebar?.classList.toggle("is-open", open);
    if (backdrop) backdrop.hidden = !open;
  }

  function closeSidebar() {
    $("#sidebar")?.classList.remove("is-open");
    const backdrop = $("#sidebar-backdrop");
    if (backdrop) backdrop.hidden = true;
  }

  function closeExportMenus() {
    $$(".export-menu").forEach((menu) => {
      menu.hidden = true;
    });
    $$('[data-action="toggle-export"]').forEach((button) => button.setAttribute("aria-expanded", "false"));
  }

  function clearFilters() {
    state.filter = "all";
    state.query = "";
    const input = $("#search-input");
    if (input) input.value = "";
    setActiveFilter("all");
    renderPaperList();
  }

  /* ------------------------------------------------------------------------
     Event delegation
     ------------------------------------------------------------------------ */

  function onDocumentClick(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    if (target === $("#sidebar-backdrop")) {
      closeSidebar();
      return;
    }

    const copyButton = target.closest("[data-copy]");
    if (copyButton) {
      copyText(copyButton.dataset.copy, "已复制到剪贴板。");
      return;
    }

    if (!target.closest(".export-wrap")) closeExportMenus();

    const filterButton = target.closest("[data-filter]");
    if (filterButton) {
      setFilter(filterButton.dataset.filter || "all");
      return;
    }

    const actionEl = target.closest("[data-action]");
    if (!actionEl) return;
    const action = actionEl.dataset.action;
    if (actionEl.classList.contains("overlay") && event.target !== actionEl) return;

    switch (action) {
      case "go-date":
        goToDate(actionEl.dataset.date);
        break;
      case "open-paper":
        openPaper(actionEl.dataset.id);
        break;
      case "close-detail":
        closeDetail();
        break;
      case "download-pdf":
        downloadPdf(findPaper(actionEl.dataset.id), actionEl);
        break;
      case "copy-citation":
        copyCitation(findPaper(actionEl.dataset.id));
        break;
      case "detail-prev":
        navigateDetail(-1);
        break;
      case "detail-next":
        navigateDetail(1);
        break;
      case "toggle-export": {
        const menu = actionEl.closest(".export-wrap")?.querySelector(".export-menu");
        if (menu) {
          const willOpen = menu.hidden;
          closeExportMenus();
          menu.hidden = !willOpen;
          actionEl.setAttribute("aria-expanded", String(willOpen));
        }
        break;
      }
      case "clear-search":
        state.query = "";
        {
          const input = $("#search-input");
          if (input) {
            input.value = "";
            input.focus();
          }
        }
        renderPaperList();
        break;
      case "clear-filters":
        clearFilters();
        break;
      case "copy-text":
        copyText(actionEl.dataset.value || "", "路径已复制。");
        break;
      case "reload-day":
        loadDay(state.date, { silent: true });
        break;
      case "reload-page":
        window.location.reload();
        break;
      default:
        break;
    }
  }

  function onDocumentInput(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.id === "search-input") {
      state.query = target.value;
      renderPaperList();
    }
  }

  function onDocumentChange(event) {
    const target = event.target;
    if (target instanceof HTMLSelectElement && target.id === "sort-select") {
      state.sort = target.value;
      renderPaperList();
    }
  }

  function onKeyDown(event) {
    if (event.key === "Escape") {
      if ($("#detail-root")?.innerHTML) {
        closeDetail();
        event.preventDefault();

      } else if ($("#sidebar")?.classList.contains("is-open")) {
        closeSidebar();
        event.preventDefault();
      } else {
        closeExportMenus();
      }
      return;
    }
    if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const active = document.activeElement;
      const typing = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement || active instanceof HTMLSelectElement;
      if (!typing) {
        const input = $("#search-input");
        if (input) {
          event.preventDefault();
          input.focus();
        }
      }
      return;
    }
    if ($("#detail-root")?.innerHTML) {
      const typing = event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement;
      if (!typing && event.key === "ArrowLeft") navigateDetail(-1);
      if (!typing && event.key === "ArrowRight") navigateDetail(1);
    }
  }

  init();
})();
