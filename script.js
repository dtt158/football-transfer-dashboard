const state = {
  data: null,
  transfers: [],
  filtered: [],
};

const statusLabels = {
  official: "官方确认",
  advanced: "高可信接近完成",
  negotiating: "谈判中",
  rumour: "传闻",
  expired: "排除/过期",
};

const statusClasses = {
  official: "confirmed",
  advanced: "advanced",
  negotiating: "advanced",
  rumour: "rumour",
  expired: "expired",
};

const els = {
  updatedAt: document.querySelector("#updatedAt"),
  itemCount: document.querySelector("#itemCount"),
  todayCount: document.querySelector("#todayCount"),
  confirmedCount: document.querySelector("#confirmedCount"),
  trustedCount: document.querySelector("#trustedCount"),
  rumourCount: document.querySelector("#rumourCount"),
  leagueFilter: document.querySelector("#leagueFilter"),
  statusFilter: document.querySelector("#statusFilter"),
  credibilityFilter: document.querySelector("#credibilityFilter"),
  sortMode: document.querySelector("#sortMode"),
  searchInput: document.querySelector("#searchInput"),
  resultCount: document.querySelector("#resultCount"),
  transferGrid: document.querySelector("#transferGrid"),
  hotList: document.querySelector("#hotList"),
  trustedList: document.querySelector("#trustedList"),
  sourcesGrid: document.querySelector("#sourcesGrid"),
  template: document.querySelector("#transferTemplate"),
  entityModal: document.querySelector("#entityModal"),
  entityType: document.querySelector("#entityType"),
  entityTitle: document.querySelector("#entityTitle"),
  entityImage: document.querySelector("#entityImage"),
  entityDescription: document.querySelector("#entityDescription"),
  entityLinks: document.querySelector("#entityLinks"),
};

async function loadData() {
  try {
    const response = await fetch("data/transfers.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    state.data = await response.json();
    state.transfers = Array.isArray(state.data.transfers) ? state.data.transfers : [];
  } catch (error) {
    console.error(error);
    state.data = {
      generated_at: new Date().toISOString(),
      sources: [],
      transfers: [],
    };
    state.transfers = [];
  }

  initialiseFilters();
  renderSources();
  applyFilters();
}

function initialiseFilters() {
  const leagues = uniqueSorted(state.transfers.map((item) => item.league || "其他"));
  const statuses = uniqueSorted(state.transfers.map((item) => item.status || "rumour"));

  fillSelect(els.leagueFilter, [["all", "全部联赛"], ...leagues.map((league) => [league, league])]);
  fillSelect(els.statusFilter, [
    ["all", "全部状态"],
    ...statuses.map((status) => [status, statusLabels[status] || status]),
  ]);

  [els.leagueFilter, els.statusFilter, els.credibilityFilter, els.sortMode].forEach((input) => {
    input.addEventListener("change", applyFilters);
  });
  els.searchInput.addEventListener("input", applyFilters);
}

function fillSelect(select, options) {
  select.replaceChildren(
    ...options.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }),
  );
}

function applyFilters() {
  const league = els.leagueFilter.value;
  const status = els.statusFilter.value;
  const minCredibility = Number(els.credibilityFilter.value);
  const query = els.searchInput.value.trim().toLowerCase();

  state.filtered = state.transfers.filter((item) => {
    const haystack = [
      item.player,
      item.from_club,
      item.to_club,
      item.league,
      item.summary,
      item.summary_zh,
      ...(item.sources || []).map((source) => source.name),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    return (
      (league === "all" || item.league === league) &&
      (status === "all" || item.status === status) &&
      Number(item.credibility_score || 0) >= minCredibility &&
      (!query || haystack.includes(query))
    );
  });

  const sortMode = els.sortMode.value;
  state.filtered.sort((a, b) => {
    if (sortMode === "credibility") {
      return Number(b.credibility_score || 0) - Number(a.credibility_score || 0);
    }
    if (sortMode === "recent") {
      return Date.parse(b.reported_at || 0) - Date.parse(a.reported_at || 0);
    }
    return Number(b.heat_score || 0) - Number(a.heat_score || 0);
  });

  renderSummary();
  renderRankings();
  renderTransfers();
}

function renderSummary() {
  const generated = state.data?.generated_at ? new Date(state.data.generated_at) : new Date();
  const today = new Date().toISOString().slice(0, 10);
  const todayCount = state.transfers.filter((item) => String(item.collected_at || "").startsWith(today)).length;
  const confirmed = state.transfers.filter((item) => item.status === "official").length;
  const trusted = state.transfers.filter((item) => Number(item.credibility_score || 0) >= 75).length;
  const rumours = state.transfers.filter((item) => item.status === "rumour" && Number(item.heat_score || 0) >= 60).length;

  els.updatedAt.textContent = `更新：${formatDate(generated)}`;
  els.itemCount.textContent = `${state.transfers.length} 条`;
  els.todayCount.textContent = todayCount;
  els.confirmedCount.textContent = confirmed;
  els.trustedCount.textContent = trusted;
  els.rumourCount.textContent = rumours;
  els.resultCount.textContent = `${state.filtered.length} 条结果`;
}

function renderRankings() {
  const hot = [...state.transfers].sort((a, b) => Number(b.heat_score || 0) - Number(a.heat_score || 0)).slice(0, 20);
  const trusted = [...state.transfers]
    .filter((item) => Number(item.credibility_score || 0) >= 75)
    .sort((a, b) => Number(b.credibility_score || 0) - Number(a.credibility_score || 0))
    .slice(0, 10);

  renderRankList(els.hotList, hot, "heat_score", "热度");
  renderRankList(els.trustedList, trusted, "credibility_score", "可信");
}

function renderRankList(container, items, scoreKey, label) {
  container.replaceChildren(
    ...items.map((item) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="rank-title">${escapeHtml(item.player || "未知球员")}</span>
        <span class="rank-meta">${escapeHtml(item.to_club || "未知去向")} · ${label} ${Number(item[scoreKey] || 0)}</span>
      `;
      return li;
    }),
  );
}

function renderTransfers() {
  if (!state.filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "没有匹配的转会信息。";
    els.transferGrid.replaceChildren(empty);
    return;
  }

  els.transferGrid.replaceChildren(
    ...state.filtered.map((item) => {
      const node = els.template.content.firstElementChild.cloneNode(true);
      node.querySelector(".league").textContent = item.league || "其他";
      node.querySelector("h3").replaceChildren(...renderEntityText(item.title_zh || item.title || item.player || "未知标题", item.entities || []));
      const originalTitle = node.querySelector(".original-title");
      originalTitle.textContent = item.title && item.title_zh && item.title !== item.title_zh ? item.title : "";

      const status = node.querySelector(".status");
      status.textContent = statusLabels[item.status] || item.status || "传闻";
      status.classList.add(statusClasses[item.status] || "rumour");

      node.querySelector(".route").replaceChildren(...renderEntityText(`${item.from_club || "未知"} → ${item.to_club || "未知"}`, item.entities || []));
      node.querySelector(".summary").replaceChildren(...renderEntityText(item.summary_zh || item.summary || "暂无摘要。", item.entities || []));
      node.querySelector(".heat").textContent = `热度 ${Number(item.heat_score || 0)}`;
      node.querySelector(".credibility").textContent = `可信 ${Number(item.credibility_score || 0)}`;
      node.querySelector(".reported").textContent = item.reported_at ? formatDate(new Date(item.reported_at)) : "时间未知";

      const tags = node.querySelector(".tags");
      tags.replaceChildren(...(item.tags || []).map((tag) => tagPill(tag)));

      const links = node.querySelector(".source-links");
      links.replaceChildren(...(item.sources || []).map((source) => sourceLink(source)));
      return node;
    }),
  );
}

function renderSources() {
  const sources = Array.isArray(state.data?.sources) ? state.data.sources : [];
  els.sourcesGrid.replaceChildren(
    ...sources.map((source) => {
      const card = document.createElement("article");
      card.className = "source-card";
      const grade = String(source.grade || "C").toLowerCase();
      const kind = sourceKindLabel(source.kind);
      card.innerHTML = `
        <div class="source-card-top">
          <span class="source-grade grade-${grade}">${escapeHtml(source.grade || "C")}</span>
          <span class="source-kind">${escapeHtml(kind)}</span>
        </div>
        <h3>${escapeHtml(source.name || "未知来源")}</h3>
        <p class="source-meta">${escapeHtml(source.region || "Global")} · ${escapeHtml(source.focus || "足球转会")}</p>
        <p>${escapeHtml(source.description || "暂无说明")}</p>
        ${source.url ? `<a class="source-url" href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">打开来源</a>` : ""}
      `;
      return card;
    }),
  );
}

function tagPill(tag) {
  const span = document.createElement("span");
  span.textContent = tag;
  return span;
}

function sourceLink(source) {
  const a = document.createElement("a");
  a.href = source.url || "#";
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = `${source.name || "来源"} · ${source.grade || "C"}`;
  return a;
}

function renderEntityText(text, entities) {
  const fragment = document.createDocumentFragment();
  const candidates = [...entities]
    .filter((entity) => entity.name && text.includes(entity.name))
    .sort((a, b) => b.name.length - a.name.length);

  if (!candidates.length) {
    fragment.append(document.createTextNode(text));
    return [fragment];
  }

  const pattern = new RegExp(candidates.map((entity) => escapeRegExp(entity.name)).join("|"), "g");
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) {
      fragment.append(document.createTextNode(text.slice(cursor, match.index)));
    }
    const entity = candidates.find((item) => item.name === match[0]);
    fragment.append(entityButton(entity || { name: match[0], type: "unknown" }));
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    fragment.append(document.createTextNode(text.slice(cursor)));
  }
  return [fragment];
}

function entityButton(entity) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `entity-link entity-${entity.type || "unknown"}`;
  button.textContent = entity.name;
  button.addEventListener("click", () => openEntityModal(entity));
  return button;
}

function openEntityModal(entity) {
  els.entityType.textContent = entityTypeLabel(entity.type);
  els.entityTitle.textContent = entity.name || "未知词条";
  els.entityDescription.textContent = entity.description || `${entity.name || "该词条"}：暂无补充说明。`;
  if (entity.image_url) {
    els.entityImage.src = entity.image_url;
    els.entityImage.alt = entity.name || "";
    els.entityImage.hidden = false;
  } else {
    els.entityImage.removeAttribute("src");
    els.entityImage.alt = "";
    els.entityImage.hidden = true;
  }
  const links = [];
  if (entity.wiki_url) links.push(namedLink(entity.wiki_title ? `Wiki：${entity.wiki_title}` : "Wiki 页面", entity.wiki_url));
  if (entity.search_url) links.push(namedLink("网页搜索", entity.search_url));
  els.entityLinks.replaceChildren(...links);
  els.entityModal.hidden = false;
}

function namedLink(label, url) {
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = label;
  return a;
}

function entityTypeLabel(type) {
  if (type === "player") return "球员";
  if (type === "club") return "俱乐部";
  if (type === "league") return "联赛";
  return "词条";
}

document.addEventListener("click", (event) => {
  if (event.target.matches("[data-close-modal]")) {
    els.entityModal.hidden = true;
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    els.entityModal.hidden = true;
  }
});

function sourceKindLabel(kind) {
  if (kind === "rss") return "自动采集";
  if (kind === "social") return "社交目录";
  if (kind === "reference") return "参考源";
  return "来源";
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function formatDate(date) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

loadData();
