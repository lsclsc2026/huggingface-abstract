/* Paper Daily — local-first Hugging Face daily papers reader */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
  const MAX_KEYWORDS = 12;
  const MIN_KEYWORD_LENGTH = 2;
  const MAX_KEYWORD_LENGTH = 30;
  const MAX_PROFILE_NOTES = 300;

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
    sort: "relevance",
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
    settingsOpen: false,
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
      relevance_score: paper.relevance || 0,
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
      updateSettingsChrome();
      updateDateChrome();
      const picker = $("#date-picker");
      if (picker) picker.max = state.today;

      await loadHistory();
      if (meta.active_run) {
        state.activeRun = meta.active_run;
        state.run = meta.active_run;
        state.runDockHidden = false;
        state.date = meta.active_run.date;
        updateDateChrome();
        renderLoading();
        await loadDay(state.date, { silent: true });
        beginPolling(meta.active_run);
      } else {
        await loadDay(state.today);
      }
    } catch (error) {
      renderFatal(error);
    }
  }

  function bindStaticEvents() {
    $("#brand-btn")?.addEventListener("click", () => goToDate(state.today || state.date));
    $("#theme-btn")?.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    $("#settings-btn")?.addEventListener("click", openSettings);
    $("#open-settings-link")?.addEventListener("click", openSettings);
    $("#prev-day")?.addEventListener("click", () => shiftDay(-1));
    $("#next-day")?.addEventListener("click", () => shiftDay(1));
    $("#today-btn")?.addEventListener("click", () => goToDate(state.today));
    $("#date-picker")?.addEventListener("change", (event) => {
      const value = event.target.value;
      if (value) goToDate(value);
    });
    $("#run-btn")?.addEventListener("click", () => startRun());
    $("#dock-close")?.addEventListener("click", () => {
      state.runDockHidden = true;
      updateRunUI();
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
      if (payload.active_run) {
        state.activeRun = payload.active_run;
        state.run = payload.active_run;
      }
      updateCounts();
      updateDateChrome();
      renderDay();
      scheduleDigestPoll();
      updateRunUI();
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
    const hasData = Boolean(state.data?.papers?.length);
    const running = Boolean(state.activeRun);
    const title = $("#run-btn-title");
    const sub = $("#run-btn-sub");
    const runButton = $("#run-btn");
    if (title) {
      if (running) title.textContent = "正在提取…";
      else title.textContent = hasData
        ? state.date === state.today
          ? "刷新今日"
          : "刷新这一天"
        : state.date === state.today
          ? "今日提取"
          : "提取这一天";
    }
    if (sub) {
      if (running) sub.textContent = "任务进行中，可继续阅读";
      else if (hasData) sub.textContent = "只处理新文章，不重复分析旧文章";
      else sub.textContent = "抓取新文章并生成摘要";
    }
    if (runButton) {
      runButton.disabled = running || state.starting;
      runButton.setAttribute("aria-busy", String(running || state.starting));
    }
    const note = $("#run-note");
    if (note) {
      const scheduled = Boolean(state.settings?.schedule?.enabled);
      note.textContent = running
        ? "任务在后台进行，完成后会自动刷新。"
        : scheduled
          ? "后台每 15 分钟检查新文章，摘要只处理新增。"
          : "不会自动抓取，只有点击时才开始。";
    }
    updateSidebarActive();
  }

  function updateSidebarActive() {
    $$(".history-item").forEach((item) => {
      item.classList.toggle("is-active", item.dataset.date === state.date);
    });
  }

  function updateSettingsChrome() {
    if (!state.settings) return;
    const node = $("#model-status");
    if (!node) return;
    const llm = state.settings.llm;
    node.classList.remove("is-ready", "is-off");
    node.classList.add(llm.configured ? "is-ready" : "is-off");
    const title = node.querySelector("strong");
    const detail = node.querySelector("span");
    if (title) title.textContent = llm.configured ? llm.model : "模型尚未配置";
    if (detail) {
      detail.textContent = llm.configured ? "已连接 · 个性化摘要已开启" : "当前使用规则摘要，点击设置";
    }
  }


  function updateCounts() {
    const papers = state.data?.papers || [];
    const bookmarked = papers.filter((paper) => paper.bookmarked).length;
    const counts = {
      "count-all": papers.length,
      "count-bookmarked": bookmarked,
    };
    Object.entries(counts).forEach(([id, value]) => {
      const node = document.getElementById(id);
      if (node) node.textContent = String(value);
    });
    const average = papers.length
      ? Math.round(papers.reduce((sum, paper) => sum + Number(paper.relevance || 0), 0) / papers.length)
      : 0;
    const statValues = {
      "stat-average": average,
      "stat-code": papers.filter((paper) => paper.github_url).length,
      "stat-bookmarked": bookmarked,
      "stat-pdf": papers.filter((paper) => paper.has_pdf).length,
    };
    Object.entries(statValues).forEach(([id, value]) => {
      const node = document.getElementById(id);
      if (node) node.textContent = String(value);
    });
    setActiveFilter(state.filter);
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
          <strong>${esc(label)}${entry.bookmarked ? " · 有待读" : ""}</strong>
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
    const average = papers.length
      ? Math.round(papers.reduce((sum, paper) => sum + Number(paper.relevance || 0), 0) / papers.length)
      : 0;
    const withCode = papers.filter((paper) => paper.github_url).length;
    const bookmarked = papers.filter((paper) => paper.bookmarked).length;
    const cached = papers.filter((paper) => paper.has_pdf).length;

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
            <p class="day-sub">Hugging Face Daily Papers，按你的关注关键词重新排序。${finished ? ` 更新于 ${esc(timeFromIso(finished))}。` : ""}</p>
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
            <button class="ghost-btn is-accent" data-action="start-run">${icon("refresh")} <span>${isToday ? "刷新今日" : "刷新"}</span></button>
          </div>
        </div>
        <div class="stats-strip">
          <div class="stat"><strong id="stat-average">${average}</strong><span>平均相关度</span></div>
          <div class="stat"><strong id="stat-code">${withCode}</strong><span>篇有代码</span></div>
          <div class="stat"><strong id="stat-bookmarked">${bookmarked}</strong><span>篇待读</span></div>
          <div class="stat"><strong id="stat-pdf">${cached}</strong><span>篇 PDF 已缓存</span></div>
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
            <option value="relevance"${state.sort === "relevance" ? " selected" : ""}>按相关度</option>
            <option value="upvotes"${state.sort === "upvotes" ? " selected" : ""}>按社区热度</option>
            <option value="newest"${state.sort === "newest" ? " selected" : ""}>按发布时间</option>
          </select>
          ${icon("down")}
        </div>
        <div class="mobile-filter-pills">
          ${[
            ["all", "全部"],
            ["bookmarked", "待读"],
          ]
            .map(
              ([value, label]) =>
                `<button class="mobile-filter-pill${state.filter === value ? " is-active" : ""}" data-filter="${value}">${label}</button>`,
            )
            .join("")}
        </div>
      </div>
      <div class="result-line">
        <span><strong id="result-count">${papers.length}</strong> <span id="result-label">篇论文</span></span>
        <span id="result-hint">${state.data.digest?.source === "llm" ? "AI 排序" : "相关度排序"}</span>
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
    const scheduled = Boolean(state.settings?.schedule?.enabled);
    if (!scheduled || state.date !== state.today) return;
    state.digestTimer = window.setTimeout(async () => {
      if (state.selectedId || state.settingsOpen) {
        scheduleDigestPoll();
        return;
      }
      try {
        const payload = await api(`/api/days/${encodeURIComponent(state.date)}`);
        const oldPapers = state.data?.papers?.length || 0;
        const oldRun = state.data?.latest_run?.id || "";
        const oldDigestAt = state.data?.digest?.generated_at || "";
        const newPapers = payload.papers?.length || 0;
        const newRun = payload.latest_run?.id || "";
        const newDigestAt = payload.digest?.generated_at || "";
        if (newPapers !== oldPapers || newRun !== oldRun || newDigestAt !== oldDigestAt) {
          state.data = payload;
          if (payload.active_run) {
            state.activeRun = payload.active_run;
            state.run = payload.active_run;
            updateRunUI();
          }
          updateCounts();
          renderDay();
          updateDateChrome();
          if (payload.active_run) beginPolling(payload.active_run);
        } else {
          scheduleDigestPoll();
        }
      } catch {
        scheduleDigestPoll();
      }
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
    const isToday = state.date === state.today;
    const active = state.activeRun && state.activeRun.date === state.date;
    const run = state.run && state.run.date === state.date ? state.run : active;
    const configured = Boolean(state.settings?.llm?.configured);
    const dateLabel = isToday ? "今天" : monthDay(state.date);
    const title = isToday
      ? "今天，AI 研究<br><em>又向前了一点点</em>。"
      : `${esc(dateLabel)}，<br><em>还没有提取</em>。`;
    const kicker = active ? "BRIEFING IN PROGRESS" : "NO BRIEFING YET";
    const copy = active
      ? "正在读取 Hugging Face Daily Papers，并生成中文摘要与导读。任务在后台进行，你可以先浏览往期内容。"
      : state.settings?.schedule?.enabled
        ? "Hugging Face 每天会陆续上架新论文。后台每 15 分钟检查一次，当前还没有可显示的今日论文；也可以手动触发一次提取。"
        : "Hugging Face 每天收录几十篇新论文。点击一次，抓取当天全部论文，用你的关键词生成中文摘要、相关度排序和阅读顺序。";
    const progress = active && run ? Math.round(((run.processed || 0) / Math.max(run.total || 0, 1)) * 100) : 0;
    return `<section class="empty-state">
      <div class="empty-ornament">${esc(shortDate(state.date))}</div>
      <div class="empty-kicker"><i></i> ${kicker}</div>
      <h1 class="empty-title">${title}</h1>
      <p class="empty-copy">${copy}</p>
      <div class="empty-actions">
        <button class="big-btn" id="empty-run-btn" data-action="start-run"${active ? " disabled" : ""}>
          ${icon(active ? "spark" : "arrow")} ${active ? "正在提取…" : isToday ? "今日提取" : `提取 ${monthDay(state.date)}`}
        </button>
        <button class="big-btn secondary" data-action="open-settings">${icon("settings")} 调整研究兴趣</button>
      </div>
      <p class="empty-note" id="empty-run-message">${active ? esc(run?.message || "准备开始…") : state.settings?.schedule?.enabled ? "后台自动检查已开启；每天 16:00 生成一次今日导读。" : "不会自动抓取，只有点击时才开始。"}${active ? ` · ${progress}%` : ""}</p>
      ${
        !configured
          ? `<div class="model-warning">${icon("spark")}<span>还没有配置私有模型。现在也能提取，但只会生成规则摘要。连接模型后重新提取，即可获得中文深度总结与个性化推荐。<button data-action="open-settings">去查看配置</button></span></div>`
          : ""
      }
      <div class="empty-features">
        <div class="empty-feature"><span>01 / AUTO CHECK</span><strong>每 15 分钟检查新文章</strong><p>有新文章才分析；每天 16:00 只生成一次今日导读。</p></div>
        <div class="empty-feature"><span>02 / YOUR MODEL</span><strong>接你的私有模型</strong><p>兼容 OpenAI 协议，DeepSeek、Qwen、vLLM、Ollama 都能接。</p></div>
        <div class="empty-feature"><span>03 / YOUR INTERESTS</span><strong>按关键词重排</strong><p>在设置里写下关注方向，相关度和阅读顺序会跟着调整。</p></div>
      </div>
    </section>`;
  }

  function filteredPapers() {
    const papers = [...(state.data?.papers || [])];
    const query = state.query.trim().toLowerCase();
    let result = papers.filter((paper) => {
      if (state.filter === "bookmarked" && !paper.bookmarked) return false;
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
      result.sort((a, b) => (b.upvotes || 0) - (a.upvotes || 0) || Number(b.relevance) - Number(a.relevance));
    } else if (state.sort === "newest") {
      result.sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)) || Number(b.relevance) - Number(a.relevance));
    } else {
      result.sort((a, b) => Number(b.relevance || 0) - Number(a.relevance || 0) || (b.upvotes || 0) - (a.upvotes || 0));
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
        state.sort === "upvotes" ? "按社区热度" : state.sort === "newest" ? "按发布时间" : state.data?.digest?.source === "llm" ? "AI 排序" : "相关度排序";
    }
    const clear = $(".clear-search");
    if (clear) clear.hidden = !state.query;
    setActiveFilter(state.filter);

    if (!papers.length) {
      let message = "没有符合当前筛选的论文。";
      let action = '<button class="text-btn" data-action="clear-filters">清除筛选与搜索</button>';
      if (state.query) message = `没有找到包含「${state.query}」的论文。`;
      else if (state.filter === "bookmarked") message = "还没有待读论文。打开一篇论文，点击书签加入待读。";
      list.innerHTML = `<div class="paper-empty"><strong>这里暂时是空的</strong><p>${esc(message)}</p>${action}</div>`;
      return;
    }
    list.innerHTML = papers
      .map((paper, index) => paperCard(paper, index))
      .join("");
  }

  function paperCard(paper, index) {
    const analysis = analysisOf(paper);
    const score = Math.round(Number(paper.relevance ?? 0));
    const title = analysis.title_zh || paper.title;
    const showOriginal = analysis.source === "llm" && title.trim() !== paper.title.trim();
    const tags = paperTags(paper, 4);
    const top = index < 3 && score >= 60;
    const bookmarked = Boolean(paper.bookmarked);
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
          ${score >= 80 ? `<span class="chip is-highlight">值得精读</span>` : ""}
          ${paper.github_url ? `<span class="chip is-code">CODE</span>` : ""}
          ${isFallback ? `<span class="chip" title="未经过私有模型深度分析">规则</span>` : ""}
          <span class="relevance-chip${score >= 70 ? " is-hot" : ""}" title="与你的研究画像相关度 ${score}/100">
            <span>相关度 ${score}</span><span class="relevance-track"><i style="width:${Math.max(2, Math.min(100, score))}%"></i></span>
          </span>
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
          <button class="action-icon${bookmarked ? " is-on" : ""}" data-action="toggle-bookmark" data-id="${esc(paper.id)}" title="${bookmarked ? "移出待读" : "加入待读"}" aria-label="${bookmarked ? "移出待读" : "加入待读"}" aria-pressed="${bookmarked}">${icon("bookmark")}<span>${bookmarked ? "已待读" : "待读"}</span></button>
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
    let list = visible.some((item) => item.id === id) ? visible : [...(state.data?.papers || [])].sort((a, b) => Number(b.relevance) - Number(a.relevance));
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
    const score = Math.round(Number(paper.relevance ?? 0));
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
            <button class="detail-icon-btn${paper.bookmarked ? " is-on" : ""}" data-action="toggle-bookmark" data-id="${esc(paper.id)}" title="${paper.bookmarked ? "移出待读" : "加入待读"}">${icon("bookmark")}<span>${paper.bookmarked ? "已待读" : "待读"}</span></button>
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

              <div class="note-box">
                <div class="note-head"><h3>PRIVATE NOTE / 我的笔记</h3><button class="save-note" data-action="save-note">保存笔记</button></div>
                <textarea id="note-input" placeholder="写点自己的判断、跟进事项或实验想法… Ctrl / ⌘ + Enter 保存">${esc(paper.note || "")}</textarea>
              </div>

              <div class="detail-nav">
                <button data-action="detail-prev"${prev ? "" : " disabled"}>${icon("left")}<span>${prev ? esc(analysisOf(prev).title_zh || prev.title) : "上一篇"}</span></button>
                <button data-action="detail-next"${next ? "" : " disabled"}><span>${next ? esc(analysisOf(next).title_zh || next.title) : "下一篇"}</span>${icon("right")}</button>
              </div>
            </article>

            <aside class="detail-aside">
              <div class="relevance-panel">
                <div class="score-ring" style="--score:${Math.max(0, Math.min(100, score))}"><strong>${score}</strong><span>RELEVANCE</span></div>
                <div class="score-copy"><strong>${score >= 80 ? "值得优先精读" : score >= 60 ? "值得快速浏览" : "按需阅读"}</strong><span>与你的研究画像的匹配程度</span></div>
              </div>
              <dl class="detail-meta">
                <div><dt>Authors</dt><dd>${esc((paper.authors || []).join(", ") || "未知")}</dd></div>
                <div><dt>Affiliation</dt><dd>${esc(paper.organization || "未标注")}</dd></div>
                <div><dt>Published</dt><dd>${esc(String(paper.published_at || "").slice(0, 10) || "未知")}</dd></div>
                <div><dt>Reading</dt><dd>约 ${Number(analysis.read_minutes || 12)} 分钟 · ${esc(analysis.paper_type || "论文")}</dd></div>
                <div><dt>Community</dt><dd>${paper.upvotes || 0} 赞${paper.comments ? ` · ${paper.comments} 条讨论` : ""}</dd></div>
                <div><dt>arXiv</dt><dd>${esc(paper.id)}</dd></div>
              </dl>
              <div class="link-stack">
                ${paper.pdf_url ? `<button class="side-link is-primary" data-action="download-pdf" data-id="${esc(paper.id)}"><span>${icon("download")} 下载 PDF</span>${icon("arrow")}</button>` : ""}
                ${links.map((item) => `<a class="side-link" href="${esc(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer"><span>${icon(item.icon)} ${esc(item.label)}</span>${icon("external")}</a>`).join("")}
              </div>
              <p class="source-note">${fallback ? "本页为规则摘要，未经过私有模型分析。配置模型后可在日报页刷新。" : "由你的私有模型基于论文摘要生成。AI 可能出错，关键结论请回到原文核对。"}</p>
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
    if (!state.settingsOpen) document.body.classList.remove("modal-open");
  }

  function navigateDetail(direction) {
    if (!state.detailList.length) return;
    let index = state.detailIndex + direction;
    if (index < 0 || index >= state.detailList.length) return;
    const paper = state.detailList[index];
    state.detailIndex = index;
    openPaper(paper.id);
  }



  async function toggleBookmark(paper) {
    if (!paper) return;
    try {
      const payload = await api(`/api/papers/${encodeURIComponent(paper.id)}`, {
        method: "PATCH",
        body: { bookmarked: !paper.bookmarked },
      });
      replacePaper(payload.paper);
      updateCounts();
      renderPaperList();
      syncDetailButtons(payload.paper);
      await loadHistory();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function syncDetailButtons(paper) {
    if (!paper || state.selectedId !== paper.id) return;
    const root = $("#detail-root");
    if (!root) return;
    const bookmarkButton = root.querySelector('[data-action="toggle-bookmark"]');
    if (bookmarkButton) {
      bookmarkButton.classList.toggle("is-on", Boolean(paper.bookmarked));
      const label = bookmarkButton.querySelector("span");
      if (label) label.textContent = paper.bookmarked ? "已待读" : "待读";
    }
  }

  async function saveNote() {
    const paper = findPaper(state.selectedId);
    const input = $("#note-input");
    if (!paper || !input) return;
    try {
      const payload = await api(`/api/papers/${encodeURIComponent(paper.id)}`, {
        method: "PATCH",
        body: { note: input.value },
      });
      replacePaper(payload.paper);
      toast("笔记已保存。", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function downloadPdf(paper, button) {
    if (!paper || !paper.pdf_url) {
      toast("这篇论文没有可用的 PDF 链接。", "error");
      return;
    }
    if (button?.dataset.busy === "1") return;
    if (button) {
      button.dataset.busy = "1";
      button.classList.add("is-loading");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    toast("正在准备 PDF，首次下载会慢一些…", "info", 3000);
    try {
      const response = await fetch(`/api/papers/${encodeURIComponent(paper.id)}/pdf`);
      if (!response.ok) {
        let message = `下载失败（HTTP ${response.status}）`;
        try {
          const payload = await response.json();
          if (payload?.detail) message = payload.detail;
        } catch {
          /* ignore */
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      if (blob.size < 1000) throw new Error("下载文件不完整，请稍后重试。");
      paper.has_pdf = true;
      updateCounts();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${sanitizeFilename(paper.id)}_${sanitizeFilename(analysisOf(paper).title_zh || paper.title)}.pdf`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 2500);
      toast("PDF 已下载到浏览器下载目录。", "success");
    } catch (error) {
      toast(`PDF 下载失败：${error.message}`, "error", 6500);
    } finally {
      if (button) {
        button.dataset.busy = "";
        button.classList.remove("is-loading");
        button.disabled = false;
        button.removeAttribute("aria-busy");
      }
    }
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

  async function startRun() {
    if (state.activeRun || state.starting) {
      toast("已有提取任务正在进行。", "info");
      return;
    }
    const hasData = Boolean(state.data?.papers?.length);
    state.starting = true;
    updateDateChrome();
    try {
      const payload = await api("/api/runs", {
        method: "POST",
        body: { date: state.date, refresh: false },
      });
      state.run = payload.run;
      state.activeRun = payload.run;
      state.runDockHidden = false;
      state.terminalHandled = "";
      state.starting = false;
      state.data = state.data?.date === state.date ? { ...state.data, active_run: payload.run } : state.data;
      updateDateChrome();
      updateRunUI();
      if (!state.data?.papers?.length) $("#content").innerHTML = renderEmpty();
      beginPolling(payload.run);
      toast(
        payload.reused
          ? "继续已有的提取任务…"
          : hasData
            ? "正在检查新文章，只更新新增内容和导读。"
            : "开始提取新文章。任务在后台运行，可继续浏览。",
        "info",
      );
    } catch (error) {
      state.starting = false;
      updateDateChrome();
      toast(error.message, "error", 7000);
    }
  }

  function beginPolling(run) {
    if (!run?.id) return;
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    const poll = async () => {
      try {
        const payload = await api(`/api/runs/${encodeURIComponent(run.id)}`);
        const current = payload.run;
        handleRunUpdate(current);
        if (["queued", "running"].includes(current.status)) {
          state.pollTimer = window.setTimeout(poll, 1200);
        }
      } catch (error) {
        state.pollTimer = window.setTimeout(poll, 3500);
      }
    };
    poll();
  }

  function handleRunUpdate(run) {
    if (!run) return;
    state.run = run;
    if (["queued", "running"].includes(run.status)) {
      state.activeRun = run;
    } else {
      state.activeRun = null;
    }
    updateRunUI();
    updateDateChrome();
    const terminalKey = `${run.id}:${run.status}`;
    if (!["queued", "running"].includes(run.status) && state.terminalHandled !== terminalKey) {
      state.terminalHandled = terminalKey;
      handleRunFinished(run);
    }
  }

  async function handleRunFinished(run) {
    if (run.status === "completed") {
      toast(run.total ? `晨报完成：${run.total} 篇论文已就绪。` : run.message || "任务完成。", "success", 5200);
      if (run.date === state.date) await loadDay(run.date, { silent: true });
      await loadHistory();
    } else if (run.status === "failed") {
      toast(`提取失败：${run.message || "未知错误"}`, "error", 9000);
      if (run.date === state.date && state.data) {
        updateRunUI();
      }
    } else if (run.status === "interrupted") {
      toast(run.message || "任务已中断。", "error", 7000);
    }
  }

  function updateRunUI() {
    const run = state.run;
    const active = Boolean(state.activeRun);
    const dock = $("#run-dock");
    const top = $("#top-progress");
    if (top) top.hidden = !active;
    if (!dock) return;
    if (!run || state.runDockHidden || (!active && run.date !== state.date)) {
      dock.hidden = true;
      return;
    }
    dock.hidden = false;
    dock.classList.toggle("is-done", run.status === "completed");
    dock.classList.toggle("is-error", ["failed", "interrupted"].includes(run.status));
    const stage = $("#dock-stage");
    const title = $("#dock-title");
    const message = $("#dock-message");
    const count = $("#dock-count");
    const percentNode = $("#dock-percent");
    const bar = $("#dock-bar");
    const stageLabels = {
      queued: "准备任务",
      fetching: "读取 Daily Papers",
      analyzing: "生成个性化摘要",
      digest: "撰写今日导读",
      done: "任务完成",
      failed: "任务失败",
      stopped: "任务中断",
    };
    const titles = {
      queued: "排队中…",
      fetching: "连接 Hugging Face…",
      analyzing: run.llm_enabled ? "正在逐篇精读与总结" : "正在整理论文信息",
      digest: "正在写今日导读",
      done: "今日晨报已完成",
      failed: "这次没有完成",
      stopped: "任务已中断",
    };
    if (stage) stage.textContent = stageLabels[run.stage] || "正在提取";
    if (title) title.textContent = titles[run.status === "running" ? run.stage : run.status] || run.message || "正在提取";
    if (message) message.textContent = run.message || "准备中…";
    const total = Number(run.total || 0);
    const processed = Number(run.processed || 0);
    const percent = run.status === "completed" ? 100 : total ? Math.round((processed / total) * 100) : run.stage === "fetching" ? 8 : 0;
    if (bar) bar.style.width = `${percent}%`;
    if (percentNode) percentNode.textContent = `${percent}%`;
    if (count) count.textContent = total ? `${processed} / ${total} 篇` : "正在读取列表";

    const emptyButton = $("#empty-run-btn");
    if (emptyButton) {
      emptyButton.disabled = active;
      emptyButton.innerHTML = active ? `${icon("spark")} 正在提取…` : `${icon("arrow")} ${state.date === state.today ? "今日提取" : `提取 ${monthDay(state.date)}`}`;
    }
    const emptyMessage = $("#empty-run-message");
    if (emptyMessage && !state.data?.papers?.length) {
      const failedHere = !active && run.status === "failed" && run.date === state.date;
      emptyMessage.textContent = active
        ? `${run.message || "准备开始…"} · ${percent}%`
        : failedHere
          ? run.message || "提取失败，请稍后重试。"
          : "不会自动抓取，也不会在后台定时消耗模型额度。";
    }
  }

  /* ------------------------------------------------------------------------
     Settings
     ------------------------------------------------------------------------ */

  function openSettings() {
    closeSidebar();
    closeDetail();
    state.settingsOpen = true;
    renderSettings();
    document.body.classList.add("modal-open");
    requestAnimationFrame(() => $("#settings-root .detail-close")?.focus());
  }

  function closeSettings() {
    $("#settings-root").innerHTML = "";
    state.settingsOpen = false;
    if (!$("#detail-root")?.innerHTML) document.body.classList.remove("modal-open");
  }

  function renderSettings() {
    const root = $("#settings-root");
    const settings = state.settings;
    if (!root || !settings) return;
    const llm = settings.llm;
    const profile = settings.profile;
    const keywords = profile.interests || [];
    root.innerHTML = `<div class="overlay" data-action="close-settings">
      <div class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div class="settings-head">
          <div><h2 id="settings-title">设置你的晨报</h2><p>调整关注关键词，让摘要和排序更贴近你</p></div>
          <button class="detail-close" data-action="close-settings" aria-label="关闭设置">${icon("close")}</button>
        </div>
        <div class="settings-scroll">
          <section class="settings-section">
            <h3>MODEL / 模型</h3>
            <div class="llm-card">
              <div class="llm-card-head">
                <i class="status-dot" style="background:${llm.configured ? "var(--success)" : "var(--flag)"}"></i>
                <strong>${llm.configured ? "已连接，可以生成深度摘要" : "未连接，当前使用规则摘要"}</strong>
                <span>${llm.configured ? "READY" : "OFFLINE"}</span>
              </div>
              <div class="llm-rows">
                <div class="llm-row"><span>Model</span><code>${esc(llm.model || "未配置")}</code></div>
              </div>
              <div class="settings-actions">
                <button class="ghost-btn" data-action="test-model"${llm.configured ? "" : " disabled"}>${icon("spark")} 测试连接</button>
              </div>
              <p class="source-note" id="model-test-result">${llm.configured ? "测试会发送一条极短的请求，确认模型是否可用。" : "模型尚未配置。"}</p>
            </div>
          </section>

          <section class="settings-section">
            <h3>RESEARCH PROFILE / 关注关键词</h3>
            <p class="settings-tip">写下你关心的方向，Paper Daily 会按这些词重新计算每篇论文的相关度。最多 ${MAX_KEYWORDS} 个，每个 ${MIN_KEYWORD_LENGTH}–${MAX_KEYWORD_LENGTH} 个字。</p>
            <div class="keyword-editor">
              <div class="keyword-chips" id="keyword-chips">
                ${keywords
                  .map(
                    (item) => `<span class="keyword-chip">${esc(item)}<button data-action="remove-keyword" data-term="${esc(item)}" aria-label="移除 ${esc(item)}">×</button></span>`,
                  )
                  .join("") || '<span class="keyword-empty">还没有关键词，请先添加一个。</span>'}
              </div>
              <div class="keyword-add">
                <input id="keyword-input" type="text" maxlength="${MAX_KEYWORD_LENGTH}" placeholder="例如：Agent、RAG、视频生成" autocomplete="off">
                <button class="ghost-btn" data-action="add-keyword">添加</button>
              </div>
              <div class="keyword-count"><span id="keyword-count">${keywords.length} / ${MAX_KEYWORDS}</span><span>按关键词匹配相关度并重排</span></div>
            </div>
            <label class="field-label" for="profile-notes">阅读偏好（可选）</label>
            <textarea id="profile-notes" class="profile-notes" maxlength="${MAX_PROFILE_NOTES}" placeholder="例如：先讲结论，多举实际产品场景，少用公式。">${esc(profile.notes || "")}</textarea>
            <div class="settings-actions">
              <button class="ghost-btn is-accent" data-action="save-profile">${icon("check")} 配置生效</button>
            </div>
            <p class="source-note" id="profile-save-result">保存后会立即重新排序；摘要正文会在下一次「刷新今日」时使用新关键词。</p>
          </section>
        </div>
      </div>
    </div>`;
    updateKeywordCount();
  }

  function currentKeywords() {
    return $$(".keyword-chip", $("#keyword-chips") || document).map((chip) => {
      const clone = chip.cloneNode(true);
      clone.querySelector("button")?.remove();
      return clone.textContent.trim();
    }).filter(Boolean);
  }

  function updateKeywordCount() {
    const count = currentKeywords().length;
    const node = $("#keyword-count");
    if (node) node.textContent = `${count} / ${MAX_KEYWORDS}`;
  }

  function addKeyword() {
    const input = $("#keyword-input");
    const container = $("#keyword-chips");
    if (!input || !container) return true;
    const value = input.value.trim();
    if (!value) {
      input.focus();
      return true;
    }
    if (value.length < MIN_KEYWORD_LENGTH || value.length > MAX_KEYWORD_LENGTH) {
      toast(`每个关键词需要 ${MIN_KEYWORD_LENGTH}–${MAX_KEYWORD_LENGTH} 个字。`, "error");
      input.focus();
      return false;
    }
    if (currentKeywords().some((item) => item.toLowerCase() === value.toLowerCase())) {
      toast("这个关键词已经存在。", "info");
      input.value = "";
      return true;
    }
    if (currentKeywords().length >= MAX_KEYWORDS) {
      toast(`最多只能添加 ${MAX_KEYWORDS} 个关键词。`, "error");
      return false;
    }
    const empty = container.querySelector(".keyword-empty");
    if (empty) empty.remove();
    const chip = document.createElement("span");
    chip.className = "keyword-chip";
    chip.append(document.createTextNode(value));
    const remove = document.createElement("button");
    remove.dataset.action = "remove-keyword";
    remove.dataset.term = value;
    remove.setAttribute("aria-label", `移除 ${value}`);
    remove.textContent = "×";
    chip.append(remove);
    container.append(chip);
    input.value = "";
    input.focus();
    updateKeywordCount();
    return true;
  }

  function removeKeyword(term) {
    const chip = $$(".keyword-chip", $("#keyword-chips") || document).find((item) => {
      const clone = item.cloneNode(true);
      clone.querySelector("button")?.remove();
      return clone.textContent.trim() === term;
    });
    chip?.remove();
    const container = $("#keyword-chips");
    if (container && !container.querySelector(".keyword-chip")) {
      container.innerHTML = '<span class="keyword-empty">还没有关键词，请先添加一个。</span>';
    }
    updateKeywordCount();
  }

  async function saveProfile(button) {
    const input = $("#keyword-input");
    if (input?.value.trim() && !addKeyword()) return;
    const interests = currentKeywords();
    if (!interests.length) {
      toast("请至少保留一个关注关键词。", "error");
      return;
    }
    if (interests.length > MAX_KEYWORDS) {
      toast(`最多只能添加 ${MAX_KEYWORDS} 个关键词。`, "error");
      return;
    }
    const notes = ($("#profile-notes")?.value || "").trim();
    if (notes.length > MAX_PROFILE_NOTES) {
      toast(`阅读偏好不能超过 ${MAX_PROFILE_NOTES} 个字。`, "error");
      return;
    }
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${icon("refresh")} 正在生效…`;
    try {
      const payload = await api("/api/profile", {
        method: "PUT",
        body: { interests, notes },
      });
      state.settings = payload.settings;
      updateSettingsChrome();
      renderSettings();
      toast(`配置已生效，已重新排序 ${payload.rescored} 篇论文。`, "success", 5000);
      await loadDay(state.date, { silent: true });
      updateDateChrome();
    } catch (error) {
      toast(`配置生效失败：${error.message}`, "error", 7000);
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  async function testModel(button) {
    if (!button) return;
    const resultNode = $("#model-test-result");
    button.disabled = true;
    const original = button.innerHTML;
    button.innerHTML = `${icon("refresh")} 测试中…`;
    button.classList.add("is-loading");
    if (resultNode) resultNode.textContent = "正在发送测试请求，部分推理模型可能需要几十秒…";
    try {
      const payload = await api("/api/settings/test", { method: "POST" });
      if (resultNode) resultNode.textContent = `连接成功 · ${payload.model} · ${payload.latency_ms} ms · 回复：${payload.reply}`;
      toast("模型连接成功。", "success");
    } catch (error) {
      if (resultNode) resultNode.textContent = `连接失败：${error.message}`;
      toast(`模型连接失败：${error.message}`, "error", 8000);
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.innerHTML = original;
    }
  }


  /* ------------------------------------------------------------------------
     Small UI helpers
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
      case "start-run":
        startRun();
        break;
      case "open-paper":
        openPaper(actionEl.dataset.id);
        break;
      case "close-detail":
        closeDetail();
        break;
      case "toggle-bookmark":
        toggleBookmark(findPaper(actionEl.dataset.id));
        break;
      case "download-pdf":
        downloadPdf(findPaper(actionEl.dataset.id), actionEl);
        break;
      case "copy-citation":
        copyCitation(findPaper(actionEl.dataset.id));
        break;
      case "save-note":
        saveNote();
        break;
      case "add-keyword":
        addKeyword();
        break;
      case "remove-keyword":
        removeKeyword(actionEl.dataset.term || "");
        break;
      case "save-profile":
        saveProfile(actionEl);
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
      case "open-settings":
        openSettings();
        break;
      case "close-settings":
        closeSettings();
        break;
      case "test-model":
        testModel(actionEl);
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
      } else if ($("#settings-root")?.innerHTML) {
        closeSettings();
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
    if (event.key === "Enter" && event.target?.id === "keyword-input") {
      event.preventDefault();
      addKeyword();
      return;
    }
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && event.target?.id === "note-input") {
      event.preventDefault();
      saveNote();
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
