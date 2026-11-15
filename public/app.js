const state = {
  candidates: [],
  discoveryRuns: [],
  conversations: [],
  connectors: [],
  profile: {},
  scoreLabels: {},
  evidenceDimensionLabels: {},
  categories: [],
  providerStatus: "",
  llmProvider: { configured: false },
  webSearch: { configured: false },
  discoveryStrategies: [],
  workflowStages: [],
  view: "home",
  chatMode: "automatic_selection",
  chatStrategyId: "pain_point",
  currentConversationId: null,
  showNewConversation: false,
  selectedId: null,
  tab: "overview",
  filter: "all",
};

const content = document.querySelector("#app-content");

function platformRoles(platform) {
  return Array.isArray(platform?.roles) ? platform.roles : [platform?.role].filter(Boolean);
}

function salesPlatforms(market) {
  return (state.platforms || []).filter((item) => platformRoles(item).includes("sales") && item.sales_market === market);
}

function sourcingPlatforms(market) {
  return (state.platforms || []).filter((item) => platformRoles(item).includes("sourcing") && item.supply_market === market);
}

function marketOptions(selected) {
  const markets = state.markets && state.markets.length ? state.markets : ["日本", "中国", "海外"];
  const groups = state.marketGroups || [];
  if (!groups.length) {
    return markets.map((market) => `<option value="${esc(market)}" ${market === selected ? "selected" : ""}>${esc(market)}</option>`).join("");
  }
  return groups.map((group) => `<optgroup label="${esc(group.group)}">${group.markets.map((market) => `<option value="${esc(market)}" ${market === selected ? "selected" : ""}>${esc(market)}</option>`).join("")}</optgroup>`).join("");
}

function sourceMarketOptions(selected) {
  const markets = [...new Set((state.platforms || []).map((item) => item.supply_market).filter(Boolean))];
  const list = markets.length ? markets : ["中国"];
  return list.map((market) => `<option value="${esc(market)}" ${market === selected ? "selected" : ""}>${esc(market)}</option>`).join("");
}
const modalRoot = document.querySelector("#modal-root");
const notice = document.querySelector("#notice");
const activeDiscoveryWatchers = new Set();

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}

function toast(message, isError = false) {
  const root = document.querySelector("#toast-root");
  const item = document.createElement("div");
  item.className = `toast${isError ? " error" : ""}`;
  item.textContent = message;
  root.append(item);
  window.setTimeout(() => item.remove(), 3600);
}

function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
}

function fmtYen(value) {
  return new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 0 }).format(Number(value || 0));
}

function modeLabel(mode) {
  return ({ discovery: "自动发现", active_research: "手动选品", specified_product: "指定商品", automatic_selection: "自动选品", manual_selection: "手动选品" })[mode] || "研究任务";
}

function categoryIcon(category) {
  return ({
    "家居收纳": "⌂", "宠物用品": "♧", "园艺用品": "❋", "DIY 五金": "⌁", "汽车内饰": "◉", "兴趣爱好": "✳",
  })[category] || "◇";
}

function statusLabel(status) {
  return ({
    researching: "研究中", ready_for_test: "可进入小测", paused: "已暂停", archived: "已归档",
  })[status] || "研究中";
}

function evidenceStatusLabel(status) {
  return ({
    missing: "待补来源", single_source: "单一来源", cross_referenced: "多来源",
  })[status] || "待补来源";
}

function taskStatusLabel(status) {
  return ({
    waiting_for_evidence: "等待证据", evidence_added_review_required: "待人工复核", blocked: "等待前置研究",
    ready_for_review: "可综合复核", in_progress: "进行中", complete: "已完成",
  })[status] || "等待证据";
}

function taskStatusClass(status) {
  return ["ready_for_review", "complete"].includes(status) ? "ready" : "";
}

async function refresh() {
  try {
    const data = await api("/api/bootstrap");
    Object.assign(state, data);
    if (!state.currentConversationId && !state.showNewConversation && state.conversations?.length) {
      state.currentConversationId = state.conversations[0].id;
      state.chatMode = state.conversations[0].mode || state.chatMode;
      const latestRun = state.discoveryRuns.find((run) => run.conversationId === state.currentConversationId);
      if (latestRun?.strategyId && state.chatMode === "automatic_selection") state.chatStrategyId = latestRun.strategyId;
    }
    document.querySelector("#notice").textContent = state.providerStatus;
    document.querySelector("#notice").classList.add("show");
    render();
    state.discoveryRuns.filter((run) => run.status === "running").forEach((run) => watchAgentDiscovery(run.id));
  } catch (error) {
    notice.textContent = `无法读取本地研究空间：${error.message}。请确认本地服务仍在运行，然后刷新页面。`;
    notice.classList.add("show");
  }
}

async function watchAgentDiscovery(runId) {
  if (activeDiscoveryWatchers.has(runId)) return;
  activeDiscoveryWatchers.add(runId);
  try {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const data = await api("/api/bootstrap");
      Object.assign(state, data);
      document.querySelector("#notice").textContent = state.providerStatus;
      document.querySelector("#notice").classList.add("show");
      render();
      const run = state.discoveryRuns.find((item) => item.id === runId);
      if (!run || run.status !== "running") {
        if (run?.status === "needs_human_review") toast(`Agent 完成研究，形成 ${run.candidateCount || 0} 个待审核候选。`);
        else if (run?.status === "completed") toast("Agent 已完成竞品扫描，结果和来源已写入会话。");
        else if (run?.status === "no_candidates") toast("本次搜索未形成来源足够的候选；可以换一个策略再跑。", true);
        else if (run?.status === "failed") toast(run.message || "Agent 选品运行失败。", true);
        break;
      }
    }
  } catch (error) {
    toast(`Agent 运行状态暂时无法刷新：${error.message}`, true);
  } finally {
    activeDiscoveryWatchers.delete(runId);
  }
}

function render() {
  renderShell();
  if (state.view === "detail") renderDetail();
  else if (state.view === "profile") renderProfile();
  else if (state.view === "discovery") renderDiscovery();
  else if (state.view === "board") renderBoard();
  else if (state.view === "metrics") renderMetrics();
  else renderHome();
}

function renderShell() {
  const search = state.webSearch || {};
  const provider = state.llmProvider || {};
  const strategies = state.discoveryStrategies || [];
  const activeStrategy = strategies.find((item) => item.id === state.chatStrategyId);

  document.querySelector("#mode-switch").innerHTML = Object.entries(chatModes).map(([id, item]) => `
    <button class="${state.chatMode === id ? "active" : ""}" data-action="chat-mode" data-mode="${id}" title="${esc(item.description)}">
      <span>${esc(item.title)}</span><small>${esc(item.short)}</small>
    </button>`).join("");

  document.querySelector("#strategy-nav").innerHTML = strategies.map((item) => `
    <button class="strategy-item ${state.chatStrategyId === item.id ? "active" : ""}" data-action="select-chat-strategy" data-strategy="${esc(item.id)}">
      <span class="strategy-code">${esc(item.code)}</span>
      <span class="strategy-copy"><b>${esc(item.title)}</b><small>${esc(item.subtitle)}</small></span>
    </button>`).join("");

  document.querySelector("#topbar-status").innerHTML = `
    <span class="chip ${search.configured ? "" : "warn"}"><i class="dot"></i><span class="label">${search.configured ? "公开网页搜索已连接" : "搜索服务未配置"}</span></span>
    <span class="chip model"><i class="dot"></i><span class="label">${esc(provider.model || "MiniMax")}</span></span>`;

  document.querySelector("#side-status").innerHTML = `
    <div class="side-status-row"><i class="dot ${search.configured ? "" : "warn"}"></i><span>${search.configured ? "Agent 搜索可用" : "搜索未配置"}</span></div>
    <div class="side-status-row"><i class="dot ${provider.configured ? "" : "warn"}"></i><span>${esc(activeStrategy ? `当前策略 ${activeStrategy.code}` : "未选策略")}</span></div>
    <div class="side-status-row"><i class="dot off"></i><span>本地保存 · 不自动下单</span></div>`;

  document.querySelectorAll(".side-nav button").forEach((item) => {
    item.classList.toggle("active", state.view === item.dataset.action);
  });

  renderRecent();
}

function renderRecent() {
  const recent = (state.conversations || []).slice(0, 8);
  document.querySelector("#recent-list").innerHTML = recent.length ? recent.map((item) => `
    <button class="recent-link ${state.currentConversationId === item.id ? "active" : ""}" data-action="open-conversation" data-id="${esc(item.id)}" title="${esc(item.title)}">
      <span class="mode-tag">${esc((chatModes[item.mode]?.short) || "会话")}</span>${esc(item.title || "Agent 会话")}
    </button>`).join("") : `<span class="recent-empty">还没有会话，从一条策略开始。</span>`;
}

const chatModes = {
  automatic_selection: {
    title: "自动选品",
    short: "Agent 找品",
    description: "选一条策略，Agent 自主搜索、筛选并把候选交给你审核。",
    placeholder: "补充目标人群、价格带、市场或经营约束（可留空）",
    suggestions: ["价格带 1000-3000 日元", "面向独居与小家庭人群", "避开红海通用标品", "只看近一年出现的新需求"],
  },
  manual_selection: {
    title: "手动选品",
    short: "给商品/范围",
    description: "给一个商品或想法让 Agent 评估；给一个范围，让 Agent 在范围内挑出具体商品。",
    placeholder: "输入商品名称/链接，或描述人群、类目、市场、价格范围",
    suggestions: ["评估这个商品：便携宠物饮水器", "在爬宠用品里挑一个细分品", "价格带 3000-8000 日元", "只找日本站已经热卖的"],
  },
  competitor_analysis: {
    title: "手动竞品分析",
    short: "对标研究",
    description: "指定类目、市场或商品，Agent 搜索目标市场竞品并整理对照与来源。",
    placeholder: "例如：日本站便携宠物饮水器，分析 Amazon 与乐天的竞品",
    suggestions: ["日本站 便携宠物饮水器", "Amazon JP 桌面收纳类目", "乐天市场 户外炊具", "对比两个具体商品的定价与差评"],
  },
  price_gap: {
    title: "同品比价",
    short: "同配置差价",
    description: "给定一个商品型号，Agent 逐个平台找公开报价，只对同配置机型比较差价。",
    placeholder: "例如：OPPO K13s 12GB+256GB",
    suggestions: ["OPPO K13s", "小米 14 Ultra 16GB+512GB", "iPhone 16 128GB", "索尼 WH-1000XM5"],
  },
};

function activeConversation() {
  if (state.showNewConversation) return null;
  if (state.currentConversationId) return state.conversations.find((item) => item.id === state.currentConversationId) || null;
  return state.conversations[0] || null;
}

function renderCompetitorReport(run) {
  const report = run.report || {};
  const evidence = new Map((report.evidence || []).map((item) => [item.ref, item]));
  const rows = (report.competitors || []).map((item) => {
    const refs = (item.sourceRefs || []).map((ref) => evidence.get(ref)).filter(Boolean);
    const links = refs.map((source) => `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.ref)} ↗</a>${source.platformName ? `<small>${esc(source.platformName)}</small>` : ""}`).join("<br>");
    const channel = item.channel === "sourcing_reference" ? "国内货源对照" : "目标市场竞品";
    return `<tr><td><b>${esc(item.name)}</b><small>${esc(item.platform || "")}</small></td><td><span class="channel-pill ${item.channel === "sourcing_reference" ? "sourcing" : ""}">${channel}</span></td><td>${esc(item.positioning || "—")}</td><td>${esc(item.priceSignal || "未确认")}</td><td>${esc([...(item.differentiators || []), ...(item.weaknessSignals || [])].join("；") || "待补来源")}</td><td>${links || "—"}</td></tr>`;
  }).join("");
  const themes = (report.themes || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const gaps = (report.gaps || []).map((item) => `<li>${esc(item)}</li>`).join("");
  return `<section class="chat-result-card"><div class="chat-result-head"><b>竞品扫描结果</b><span>${esc(report.scope || "")}</span></div><p class="chat-result-summary">${esc(report.summary || run.summary || "")}</p>${rows ? `<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>竞品/平台</th><th>渠道</th><th>定位</th><th>价格线索</th><th>差异/反馈线索</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="inline-alert">目前没有足够的带来源竞品行。可以补充品牌、类目词或目标平台再运行。</div>`}${themes ? `<div class="report-list"><b>共性趋势</b><ul>${themes}</ul></div>` : ""}${gaps ? `<div class="report-list"><b>还需核实</b><ul>${gaps}</ul></div>` : ""}<small class="chat-result-foot">${Number(run.sourceCount || 0)} 条搜索线索 · ${esc(run.model || "MiniMax Agent")} · “国内货源对照”行的价格来自国内电商公开页面，不等于源头成本</small></section>`;
}

function priceGapMarkdown(run) {
  const report = run.report || {};
  const currency = report.currency || "CNY";
  const money = (value) => (value == null ? "—" : `${Number(value).toLocaleString("zh-CN")} ${currency}`);
  const lines = [];
  lines.push(`# 同品跨平台比价 · ${report.scope || ""}`);
  lines.push("");
  lines.push(report.summary || run.summary || "");
  lines.push("");
  lines.push("## 同配置分组");
  for (const group of report.groups || []) {
    lines.push("");
    lines.push(`### ${group.config || "未识别配置"}`);
    lines.push(group.comparable
      ? `同配置毛差价 ${money(group.grossGap)}（${(Number(group.grossGapPct || 0) * 100).toFixed(1)}%）· 最低 ${group.lowestPlatform} / 最高 ${group.highestPlatform}`
      : `不可对比：${(group.reasons || []).join("；")}`);
    for (const caveat of group.caveats || []) lines.push(`- 注意：${caveat}`);
    lines.push("");
    lines.push("| 平台 | 配置 | 可见报价 | 与最低价差价 | 促销信号 | 来源 |");
    lines.push("|---|---|---:|---:|---|---|");
    for (const row of group.rows || []) {
      lines.push(`| ${row.platform} | ${row.configKey || "配置未识别"} | ${money(row.priceLow)} | ${money(row.gapToLowest)} | ${(row.promoSignals || []).join("、") || "—"} | ${row.url} |`);
    }
    for (const pair of group.storePairs || []) {
      lines.push("");
      lines.push(`- 店间差价：${pair.sourcePlatform}（${pair.sourceSeller}）${money(pair.sourcePrice)} → ${pair.targetPlatform}（${pair.targetSeller}）${money(pair.targetPrice)}，差 ${money(pair.gap)}（${(Number(pair.gapPct || 0) * 100).toFixed(1)}%）`);
    }
  }
  const profit = report.platformProfit || {};
  if ((profit.rows || []).length) {
    lines.push("");
    lines.push("## 每家平台的单件贡献利润");
    lines.push("");
    lines.push("| 卖出平台 | 本平台报价 | 采购价基准 | 采购价 | 毛差价 | 贡献利润 |");
    lines.push("|---|---:|---|---:|---:|---:|");
    for (const row of profit.rows) {
      for (const scenario of row.scenarios || []) {
        lines.push(`| ${row.platform} | ${money(row.sellPrice)} | ${scenario.label} | ${money(scenario.purchasePrice)} | ${money(scenario.grossGap)} | ${scenario.contributionProfit == null ? "待补费用" : `${money(scenario.contributionProfit)}（${(Number(scenario.contributionMargin || 0) * 100).toFixed(1)}%）`} |`);
      }
    }
  }
  const history = report.historySummary || {};
  if ((history.series || []).length) {
    lines.push("");
    lines.push("## 历史观察");
    lines.push("");
    lines.push("| 平台 | 配置 | 最近报价 | 较上次 | 观察次数 |");
    lines.push("|---|---|---:|---:|---:|");
    for (const row of history.series) {
      const change = row.changePct == null ? "—" : `${(Number(row.changePct) * 100).toFixed(1)}%`;
      lines.push(`| ${row.platform} | ${row.config || "配置未识别"} | ${row.latestPrice == null ? "—" : `${Number(row.latestPrice).toLocaleString("zh-CN")} ${row.latestCurrency || ""}`} | ${change} | ${row.observations} |`);
    }
  }
  lines.push("");
  lines.push("## 仍需人工核实");
  for (const gap of report.gaps || []) lines.push(`- ${gap}`);
  lines.push("");
  lines.push("> 价格来自公开网页搜索摘要，未打开商品页核实；差价未扣佣金、广告、履约与退货，不是利润。本报告不构成采购建议。");
  return lines.join("\n");
}

function renderPriceGapReport(run) {
  const report = run.report || {};
  const groups = report.groups || [];
  const currency = report.currency || "CNY";
  const money = (value) => (value == null ? "—" : `${Number(value).toLocaleString("zh-CN")} ${currency}`);
  const groupBlocks = groups.map((group) => {
    const rows = (group.rows || []).map((row) => `<tr>
      <td><b>${esc(row.platform)}</b>${row.secondHand ? `<small>二手/个人定价</small>` : ""}</td>
      <td>${esc(row.configKey || "配置未识别")}${row.color ? ` · ${esc(row.color)}` : ""}</td>
      <td>${money(row.priceLow)}${row.priceHigh && row.priceHigh !== row.priceLow ? `<small>~ ${money(row.priceHigh)}</small>` : ""}${(row.promoSignals || []).length ? `<small class="promo-tag">疑似促销：${row.promoSignals.join("、")}</small>` : ""}</td>
      <td>${row.gapToLowest ? `${money(row.gapToLowest)}（${(Number(row.gapPct || 0) * 100).toFixed(1)}%）` : "—"}</td>
      <td>${row.url ? `<a href="${esc(row.url)}" target="_blank" rel="noopener noreferrer">来源 ↗</a>` : "—"}</td>
    </tr>`).join("");
    const head = group.comparable
      ? `<b>${esc(group.config || "未识别配置")}</b> · 同配置毛差价 ${money(group.grossGap)}（${(Number(group.grossGapPct || 0) * 100).toFixed(1)}%）· 最低 ${esc(group.lowestPlatform)} / 最高 ${esc(group.highestPlatform)}${group.arbDirection ? ` · 套利方向 ${esc(group.arbDirection)}` : ""}`
      : `<b>${esc(group.config || "未识别配置")}</b> · ${esc((group.reasons || []).join("；") || "不可对比")}`;
    const caveats = (group.caveats || []).length ? `<ul>${group.caveats.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : "";
    const sellers = (group.sellers || []).map((seller) => `<tr><td><b>${esc(seller.seller)}</b></td><td>${esc(seller.platform)}</td><td>${money(seller.priceLow)}</td><td>${seller.listingCount > 1 ? `${seller.listingCount} 个页面` : "1 个页面"}</td></tr>`).join("");
    const sellerBlock = sellers ? `<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>店铺 / 页面域名</th><th>平台</th><th>可见报价</th><th>页面数</th></tr></thead><tbody>${sellers}</tbody></table></div>` : "";
    const pairRows = (group.storePairs || []).map((pair) => `<tr><td>${esc(pair.sourcePlatform)}<small>${esc(pair.sourceSeller)}</small></td><td>${money(pair.sourcePrice)}${pair.sourcePromo ? "<small>疑似促销</small>" : ""}</td><td>${esc(pair.targetPlatform)}<small>${esc(pair.targetSeller)}</small></td><td>${money(pair.targetPrice)}${pair.targetPromo ? "<small>疑似促销</small>" : ""}</td><td>${money(pair.gap)}（${(Number(pair.gapPct || 0) * 100).toFixed(1)}%）</td></tr>`).join("");
    const pairBlock = pairRows ? `<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>低价一侧</th><th>报价</th><th>高价一侧</th><th>报价</th><th>店间差价</th></tr></thead><tbody>${pairRows}</tbody></table></div>` : "";
    const durableLine = group.durableGap != null ? `去除疑似促销页后，同配置差价 ${money(group.durableGap)}（${(Number(group.durableGapPct || 0) * 100).toFixed(1)}%）` : "没有足够的非促销页面，无法估算可持续差价";
    return `<div class="report-list"><b>${head}</b>${caveats}<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台</th><th>配置</th><th>可见报价</th><th>与同配置最低价差价</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table></div>${pairBlock ? `<div class="bottom-note"><span>ⓘ</span><span><strong>店间差价：</strong>左边是低价店铺，右边是高价店铺。${esc(durableLine)}。</span></div>${pairBlock}` : ""}${sellerBlock ? `<div class="bottom-note"><span>ⓘ</span><span><strong>同款店铺分布：</strong>同一配置在不同店铺的可见报价；最低价通常来自补贴或清库存动作，不等于可持续拿货价。</span></div>${sellerBlock}` : ""}</div>`;
  }).join("");
  const summaryRows = (report.platformSummary || []).map((item) => `<tr>
    <td><b>${esc(item.platform)}</b></td>
    <td>${item.status === "found" ? `${money(item.visiblePriceLow)}${item.visiblePriceHigh !== item.visiblePriceLow ? ` ~ ${money(item.visiblePriceHigh)}` : ""}` : "—"}</td>
    <td>${esc(item.config || "—")}</td>
    <td>${esc(item.note || "")}</td>
  </tr>`).join("");
  const gaps = (report.gaps || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const crossBorder = report.crossBorder ? `<div class="inline-alert">${esc(report.crossBorder.note)}</div>` : "";
  const historyBlocks = renderPriceHistory(report.history || []);
  const alertBlocks = renderPriceAlerts(report.historySummary?.alerts || []);
  const profitBlock = renderPlatformProfit(report.platformProfit || {});
  return `<section class="chat-result-card"><div class="chat-result-head"><b>同品跨平台比价</b><span>${esc(report.scope || "")}</span><button class="button-quiet" data-action="download-pricegap" data-id="${esc(run.id || "")}">导出 Markdown</button></div><p class="chat-result-summary">${esc(report.summary || run.summary || "")}</p>${crossBorder}${summaryRows ? `<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台</th><th>可见报价区间</th><th>配置</th><th>说明</th></tr></thead><tbody>${summaryRows}</tbody></table></div>` : ""}${groupBlocks || `<div class="inline-alert">没有找到带型号与价格的公开页面。可以换一个更通用的商品名再试。</div>`}${historyBlocks}${gaps ? `<div class="report-list"><b>还需核实</b><ul>${gaps}</ul></div>` : ""}<small class="chat-result-foot">${Number(run.sourceCount || 0)} 条可见报价 · 价格来自公开搜索摘要，未打开商品页核实 · 差价未扣佣金、广告、履约与退货，不是利润</small></section>`;
}

function renderPriceHistory(history) {
  if (!history.length) return "";
  const groups = new Map();
  for (const item of history) {
    const key = `${item.platform}|${item.config || ""}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const rows = [...groups.entries()].map(([key, items]) => {
    const sorted = [...items].sort((a, b) => String(a.observedAt).localeCompare(String(b.observedAt)));
    const latest = sorted[sorted.length - 1];
    const previous = sorted.length > 1 ? sorted[sorted.length - 2] : null;
    let change = "—";
    if (previous && previous.price && latest.price) {
      const delta = latest.price - previous.price;
      const pct = previous.price ? (delta / previous.price) * 100 : 0;
      change = `${delta >= 0 ? "+" : ""}${Number(delta).toLocaleString("zh-CN")} (${pct.toFixed(1)}%)`;
    }
    return `<tr><td><b>${esc(latest.platform)}</b><small>${esc(latest.config || "配置未识别")}</small></td><td>${latest.price == null ? "—" : `${Number(latest.price).toLocaleString("zh-CN")} ${esc(latest.currency || "")}`}</td><td>${esc(latest.observedAt || "")}</td><td>${esc(change)}</td><td>${sorted.length} 次观察</td></tr>`;
  }).join("");
  return `<div class="report-list"><b>历史观察（同一商品的公开页面报价记录）</b><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台</th><th>最近可见报价</th><th>观察日期</th><th>较上次</th><th>次数</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function renderPlatformProfit(profit) {
  const rows = (profit.rows || []).flatMap((row) => {
    const scenarios = row.scenarios || [];
    if (!scenarios.length) return [];
    return scenarios.map((scenario, index) => `<tr>
      <td>${index === 0 ? `<b>${esc(row.platform)}</b><small>${esc(row.config || "")}</small>${row.promo ? "<small class=\"promo-tag\">疑似促销价</small>" : ""}` : ""}</td>
      <td>${index === 0 ? `${Number(row.sellPrice).toLocaleString("zh-CN")} ${esc(row.currency || "")}` : ""}</td>
      <td>${esc(scenario.label)}<small>采购 ${Number(scenario.purchasePrice).toLocaleString("zh-CN")}</small></td>
      <td>${Number(scenario.grossGap).toLocaleString("zh-CN")}</td>
      <td>${scenario.contributionProfit == null ? `<span class="status-pill wait">待补费用</span>` : `${Number(scenario.contributionProfit).toLocaleString("zh-CN")} ${esc(row.currency || "")}<small>${(Number(scenario.contributionMargin || 0) * 100).toFixed(1)}%</small>`}</td>
    </tr>`);
  }).join("");
  if (!rows) return "";
  const missing = (profit.missingFields || []).length
    ? `<div class="inline-alert">还缺比价假设：${profit.missingFields.join("、")}。补齐后这里会给出每家平台的单件贡献利润。</div>`
    : "";
  return `<div class="report-list"><b>每家平台的单件贡献利润（三种采购价基准）</b>${missing}<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>卖出平台</th><th>本平台报价</th><th>采购价基准</th><th>毛差价</th><th>贡献利润</th></tr></thead><tbody>${rows}</tbody></table></div><small class="chat-result-foot">${esc(profit.note || "")}</small></div>`;
}

function renderPriceAlerts(alerts) {
  if (!alerts.length) return "";
  const rows = alerts.map((alert) => `<tr><td><b>${esc(alert.platform)}</b><small>${esc(alert.config || "配置未识别")}</small></td><td>${alert.direction}</td><td>${Number(alert.previousPrice).toLocaleString("zh-CN")} → ${Number(alert.latestPrice).toLocaleString("zh-CN")} ${esc(alert.latestCurrency || "")}</td><td>${esc(alert.note)}</td></tr>`).join("");
  return `<div class="report-list"><b>价格变化提醒（阈值 ±5%）</b><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台</th><th>方向</th><th>最近两次报价</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function renderChatRun(run) {
  if (!run) return "";
  if (run.status === "running") return `<section class="chat-run-progress"><div class="run-progress-label">${Number(run.progress || 0)}%</div><div class="run-progress"><i style="width:${Math.max(3, Math.min(100, Number(run.progress || 0)))}%"></i></div><b>${esc(run.message || "Agent 研究中…")}</b><small>${esc(run.stage || "planning")} · 研究完成后在这里展示来源和结果</small></section>`;
  if (run.status === "failed") return `<div class="chat-run-error">${esc(run.message || "Agent 运行失败。")}</div>`;
  if (run.mode === "competitor_analysis") return renderCompetitorReport(run);
  if (run.mode === "price_gap") return renderPriceGapReport(run);
  const candidates = (run.candidateIds || []).map((id) => state.candidates.find((item) => item.id === id)).filter(Boolean);
  return `<section class="chat-run-result"><div class="chat-result-head"><b>${run.status === "no_candidates" ? "本次未形成候选" : "待你审核的候选"}</b><span>${Number(run.sourceCount || 0)} 条来源线索 · ${esc(run.model || "MiniMax Agent")}</span></div><p>${esc(run.summary || run.message || "")}</p><div class="agent-candidate-grid">${candidates.map(renderAgentCandidate).join("") || `<div class="agent-empty"><h3>暂无可审商品</h3><p>Agent 没有找到来源足够的候选。你可以换策略或补充范围再试。</p></div>`}</div></section>`;
}

function renderChatMessage(message) {
  const run = state.discoveryRuns.find((item) => item.id === message.runId);
  const body = message.role === "user"
    ? `<div class="chat-bubble user-bubble">${esc(message.content)}</div>${run?.status === "running" ? renderChatRun(run) : ""}`
    : `<div class="chat-agent-row"><span class="chat-avatar">✳</span><div><div class="chat-bubble agent-bubble">${esc(message.content)}</div>${run ? renderChatRun(run) : ""}</div></div>`;
  return `<article class="chat-message chat-${esc(message.role)}">${body}<time>${fmtDate(message.createdAt)}</time></article>`;
}

function renderHome() {
  const mode = chatModes[state.chatMode] || chatModes.automatic_selection;
  const conversation = activeConversation();
  const strategies = state.discoveryStrategies || [];
  const messages = conversation?.messages?.map(renderChatMessage).join("") || `<div class="chat-agent-row welcome-row"><span class="chat-avatar">✳</span><div class="chat-bubble agent-bubble"><b>我是你的选品 Agent。</b><br>${esc(mode.description)}<br><small>我会搜索公开网页线索、保留来源，并把事实与推断分开展示。</small></div></div>`;
  const market = conversation ? (conversation.market || state.profile.market || "日本") : (state.chatMarket || state.profile.market || "日本");
  const platform = conversation ? (conversation.platform || state.profile.platform || "") : (state.chatPlatform || state.profile.platform || "");
  const marketChannels = salesPlatforms(market);
  const platformOptions = marketChannels.length
    ? marketChannels.map((item) => `<option value="${esc(item.name)}" ${item.name === platform ? "selected" : ""}>${esc(item.name)}</option>`).join("")
    : `<option value="${esc(platform)}">${esc(platform || "待确认")}</option>`;
  const activeStrategy = strategies.find((item) => item.id === state.chatStrategyId);
  content.innerHTML = `
    <div class="page-kicker">Agent research workspace</div>
    <div class="page-heading chat-page-heading"><div><h1>把选品研究交给 Agent</h1><p>先定切入策略，再看 Agent 按六步流程推进，最后由你审核候选。</p></div><div class="page-heading-actions"><button class="button-secondary" data-action="new-chat">＋ 新会话</button></div></div>
    ${renderStrategyLayer(strategies, activeStrategy)}
    ${renderFlowRail()}
    <section class="chat-workspace">
      <div class="chat-context"><div><span class="agent-live-dot ${state.webSearch?.configured ? "" : "offline"}"></span><b>${state.webSearch?.configured ? "Agent 已连接公开网页搜索" : "搜索服务未配置"}</b><small>${esc(state.webSearch?.message || "")}</small></div>${conversation ? `<span class="conversation-title">${esc(conversation.title || "Agent 会话")}</span>` : ""}</div>
      <div class="chat-transcript" id="chat-transcript">${messages}</div>
      <form id="chat-form" class="chat-composer">
        <div class="chat-composer-context"><label>目标市场<select name="market" data-change="chat-market">${marketOptions(market)}</select></label><label>优先平台<select name="platform">${platformOptions}</select></label>${state.chatMode === "automatic_selection" ? `<span class="selected-strategy-label">${esc(strategies.find((item) => item.id === state.chatStrategyId)?.title || "选择策略")}</span>` : ""}</div>
        ${state.chatMode === "price_gap" ? `<div class="sugg-chips"><label style="margin-right:10px">采购来源国<select name="sourcingCountry" style="margin-left:6px">${sourceMarketOptions(state.chatSourceCountry || state.profile.sourcingCountry || "中国")}</select></label><small style="margin-right:6px">参与比价的平台</small>${marketChannels.map((item) => `<label class="check-option" style="display:inline-flex;gap:5px;align-items:center"><input type="checkbox" name="platformIds" value="${esc(item.id)}" checked /><span>${esc(item.name)}</span></label>`).join("")}</div>` : ""}
        <div class="sugg-chips">${(mode.suggestions || []).map((text) => `<button type="button" data-action="use-suggestion" data-text="${esc(text)}">${esc(text)}</button>`).join("")}</div>
        <textarea name="message" rows="2" placeholder="${esc(mode.placeholder)}" ${state.chatMode === "automatic_selection" ? "" : "required"}></textarea>
        <div class="chat-composer-bottom"><small>${state.chatMode === "automatic_selection" ? "可以留空；策略会决定 Agent 的发现方向。" : state.chatMode === "price_gap" ? "填一个型号，Agent 逐个平台比同配置差价；差价不等于利润。" : "Agent 会根据你的描述自动规划搜索，不需要你先查好竞品。"} 搜索摘要需要点开来源核实。</small><button class="button-primary" type="submit" ${!state.webSearch?.configured ? "disabled" : ""}>${state.chatMode === "competitor_analysis" ? "开始竞品分析" : state.chatMode === "price_gap" ? "开始比价" : "发送给 Agent"} <span>↑</span></button></div>
      </form>
    </section>
    <div class="bottom-note"><span>ⓘ</span><span><strong>数据边界：</strong>公开网页搜索是发现线索，不等于实时趋势序列、平台销量、可购价或正式 MOQ 报价。Agent 不会自动下单。</span></div>
  `;
  const transcript = document.querySelector("#chat-transcript");
  if (transcript) transcript.scrollTop = transcript.scrollHeight;
}

function renderStrategyLayer(strategies, activeStrategy) {
  if (state.chatMode !== "automatic_selection") {
    const scope = {
      manual_selection: { title: "手动选品", lines: ["你给出：商品名称 / 链接，或人群、类目、市场、价格带等范围。", "Agent 负责：在范围内挑出具体商品并做完整研究，或对给定商品给出评估。"] },
      competitor_analysis: { title: "手动竞品分析", lines: ["你给出：类目、目标市场、平台，或具体商品 / 品牌。", "Agent 负责：盘点竞品集合、价格带、卖点与差评主题，输出带来源的对照。"] },
      price_gap: { title: "同品比价", lines: ["你给出：商品名称或型号（如 OPPO K13s）。", "Agent 负责：逐个平台找公开报价，只对同配置机型比较差价，并保留来源。"] },
    }[state.chatMode];
    return `<section class="layer"><div class="layer-head"><span class="layer-no">1</span><h2>研究范围</h2><p>${esc(scope.title)}：人给范围，Agent 出商品。</p></div>
      <div class="source-boundary-grid">${scope.lines.map((line) => `<article><p>${esc(line)}</p></article>`).join("")}</div></section>`;
  }
  return `<section class="layer"><div class="layer-head"><span class="layer-no">1</span><h2>选品切入策略</h2><p>策略是 Agent 的发现框架，不是机会保证；选一条开始。</p></div>
    <div class="strategy-grid">${strategies.map((item) => `
      <button class="strategy-card ${state.chatStrategyId === item.id ? "active" : ""}" data-action="select-chat-strategy" data-strategy="${esc(item.id)}">
        <span class="strategy-card-top"><span class="strategy-card-code">${esc(item.code)}</span><span class="state">${state.chatStrategyId === item.id ? "当前策略" : "点击切换"}</span></span>
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.subtitle)}。${esc(item.description)}</p>
        <span class="strategy-tags">${(item.tags || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</span>
      </button>`).join("")}</div>
    ${activeStrategy ? `<div class="bottom-note"><span>ⓘ</span><span><strong>${esc(activeStrategy.title)}的线索来源：</strong>${esc(activeStrategy.sourceHint || "公开网页搜索")}。这些是发现线索，不是实时趋势序列或平台销量数据。</span></div>` : ""}
  </section>`;
}

const flowStatusLabel = { completed: "已完成", partial: "部分完成", blocked: "待补数据", pending: "未开始", current: "进行中" };

function flowRailState() {
  const stages = state.workflowStages || [];
  const conversation = activeConversation();
  const run = (state.discoveryRuns || []).find((item) => item.conversationId === conversation?.id)
    || (state.discoveryRuns || [])[0];
  const runCandidates = run?.candidateIds?.map((id) => state.candidates.find((item) => item.id === id)).filter(Boolean) || [];
  const stageStates = runCandidates[0]?.workflowStages || [];
  const runningIndex = { planning: 0, demand: 0, candidate_generation: 1, market: 1, price_gap_search: 1, sourcing: 2, price_gap_compare: 2, validation: 2, validation_analysis: 3, human_review: 3 }[run?.status === "running" ? run.stage : ""];
  return stages.map((stage, index) => {
    const recorded = stageStates.find((item) => item.id === stage.id);
    let status = recorded?.status || "pending";
    if (run?.status === "running" && index === runningIndex) status = "current";
    else if (run?.status === "running" && index < runningIndex && status === "pending") status = "completed";
    return { ...stage, status, note: recorded?.note || "" };
  });
}

function renderFlowRail() {
  const steps = flowRailState();
  const current = steps.find((item) => item.status === "current");
  return `<section class="layer"><div class="layer-head"><span class="layer-no">2</span><h2>六步选品流程</h2><p>${current ? `Agent 正在推进：${esc(current.title)}` : "Agent 按证据逐步推进，缺数据时停在待补数据。"}</p></div>
    <div class="flow-rail-wrap"><div class="flow-rail">${steps.map((item) => `
      <div class="flow-step ${esc(item.status)}" title="${esc(item.note || item.description || "")}">
        <div class="t"><span class="n">${item.number}</span>${esc(item.title)}</div>
        <div class="s">${esc(flowStatusLabel[item.status] || item.status)}</div>
      </div>`).join("")}</div>
      <div class="flow-legend"><span><i class="done"></i>已完成</span><span><i class="cur"></i>进行中</span><span><i class="blk"></i>待补数据</span><span><i></i>未开始</span></div>
    </div></section>`;
}

function renderAgentCandidate(candidate) {
  const recommendation = candidate.agentResearch?.recommendation;
  const recommendationLabel = ({ keep: "进入人工审核", hold: "补证后审核", reject: "Agent 暂缓" })[recommendation] || "待 Agent 核验";
  const strategy = candidate.strategyTitle || (candidate.strategyId ? state.discoveryStrategies.find((item) => item.id === candidate.strategyId)?.title : "指定商品研究");
  const sourceCount = (candidate.evidence || []).length;
  const conclusion = candidate.agentResearch?.recommendationReason || candidate.agentDiscovery?.strategyFit || candidate.hypothesis;
  const sources = (candidate.evidence || []).slice(0, 3).map((item) => `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)} ↗</a>`).join("");
  const review = candidate.humanReview;
  const reviewLabel = ({ continue: "已通过初审 · 继续研究", hold: "已暂缓", reject: "已淘汰" })[review?.decision];
  const reviewControls = review
    ? `<span class="status-pill ${review.decision === "continue" ? "done" : review.decision === "reject" ? "bad" : "warn"}">${reviewLabel}</span>${review.reason ? `<span class="candidate-review-reason">理由：${esc(review.reason)}</span>` : ""}`
    : `<div class="candidate-review-actions"><button data-action="review-candidate" data-id="${esc(candidate.id)}" data-decision="continue">继续研究</button><button data-action="review-candidate" data-id="${esc(candidate.id)}" data-decision="hold">暂缓</button><button data-action="review-candidate" data-id="${esc(candidate.id)}" data-decision="reject">淘汰</button></div>`;
  return `<article class="agent-candidate"><div class="candidate-card-top"><span class="candidate-strategy">${esc(candidate.strategyCode ? `策略 ${candidate.strategyCode}` : "研究项目")} · ${esc(strategy || "")}</span><span class="status-pill ${recommendation === "keep" ? "done" : ""}">${recommendationLabel}</span></div><div class="candidate-card-title"><div class="product-icon">${categoryIcon(candidate.category)}</div><div><h3>${esc(candidate.name)}</h3><small>${esc(candidate.category)} · ${esc(candidate.market)} / ${esc(candidate.platform)}</small></div></div><p class="candidate-conclusion">${esc(conclusion || "等待 Agent 研究结果")}</p>${candidate.agentResearch ? `<div class="candidate-assessments"><div><small>市场核验</small><span>${esc(candidate.agentResearch.marketAssessment || "待核验")}</span></div><div><small>${esc(candidate.sourcingCountry || "中国")}货源</small><span>${esc(candidate.agentResearch.sourcingAssessment || "待核验")}</span></div><div><small>利润</small><span>${candidate.agentResearch.profitReadiness === "blocked" ? "数据不足，暂不估算" : "需复核来源"}</span></div></div>` : ""}<div class="candidate-card-meta"><span>${sourceCount} 条来源线索</span><span>信号置信度 ${esc(candidate.agentDiscovery?.confidence || "待定")}</span></div>${sources ? `<div class="candidate-source-links">${sources}</div>` : ""}<div class="candidate-card-actions"><button class="button-secondary" data-action="open" data-id="${esc(candidate.id)}">查看候选与证据</button>${reviewControls}</div></article>`;
}

const tabs = [
  ["overview", "研究概览"], ["tasks", "Agent 任务"], ["evidence", "证据来源"], ["profit", "利润模型"], ["report", "研究报告"],
];

function renderDetail() {
  const candidate = state.candidates.find((item) => item.id === state.selectedId);
  if (!candidate) { state.view = "home"; renderHome(); return; }
  const tabContent = {
    overview: renderOverview(candidate),
    tasks: renderTasks(candidate),
    evidence: renderEvidence(candidate),
    profit: renderProfit(candidate),
    report: renderReport(candidate),
  }[state.tab] || renderOverview(candidate);
  const scoreDone = candidate.scoreStatus?.complete;
  content.innerHTML = `
    <div class="page-kicker">Research detail / ${modeLabel(candidate.mode)}</div>
    <div class="detail-header">
      <button class="back-button" data-action="home" aria-label="返回机会工作台">←</button>
      <div class="detail-symbol">${categoryIcon(candidate.category)}</div>
      <div class="detail-title"><h1>${esc(candidate.name)}</h1><div class="detail-meta"><span>${esc(candidate.category)}</span><span>·</span><span>${esc(candidate.market)}</span><span>·</span><span>${esc(candidate.platform)}</span><span>·</span><span>更新于 ${fmtDate(candidate.updatedAt)}</span></div></div>
      <div class="detail-actions"><button class="button-quiet" data-action="edit-candidate">编辑</button>${candidate.priceGap ? `<button class="button-secondary" data-action="price-gap-pair" data-id="${esc(candidate.id)}">生成价差研究</button>` : ""}<button class="button-quiet" data-action="scan-price-gap" data-id="${esc(candidate.id)}">扫描同品比价</button>${candidate.priceGap ? `<button class="button-quiet" data-action="download-rfq">导出询价单</button>` : ""}${candidate.status === "ready_for_test" ? `<span class="status-pill done">可进入小测</span>` : `<button class="button-secondary" data-action="mark-ready" ${scoreDone ? "" : "title='四项独立判断完成后可标记'"}>标记可进入小测</button>`}${candidate.status === "paused" ? `<button class="button-quiet" data-action="resume">恢复研究</button>` : candidate.status === "researching" ? `<button class="button-quiet" data-action="pause">暂停</button>` : ""}<button class="button-quiet" data-action="archive">归档</button></div>
    </div>
    <nav class="detail-tabs">${tabs.map(([key, label]) => `<button class="detail-tab ${state.tab === key ? "active" : ""}" data-action="tab" data-tab="${key}">${label}${key === "evidence" && candidate.evidence?.length ? ` <span>${candidate.evidence.length}</span>` : ""}</button>`).join("")}</nav>
    ${tabContent}
  `;
}

function renderPriceGapContext(candidate) {
  const pairing = candidate.priceGap || {};
  const history = pairing.gapHistory || {};
  const series = history.series || [];
  const rows = series.map((row) => `<tr><td>${esc(row.date)}</td><td>${Number(row.sourcePrice).toLocaleString("zh-CN")}</td><td>${Number(row.targetPrice).toLocaleString("zh-CN")}</td><td>${Number(row.gap).toLocaleString("zh-CN")}（${(Number(row.gapPct || 0) * 100).toFixed(1)}%）</td></tr>`).join("");
  const median = history.medianGap == null ? "暂无历史可比记录" : `历史中位差价 ${Number(history.medianGap).toLocaleString("zh-CN")}（${history.observations} 次可比观察）`;
  return `<section class="panel"><div class="panel-head"><h3>价差背景</h3><small>公开页面报价记录，不是价格承诺</small></div>
    <p style="margin:0 0 8px;color:#77837a;font-size:9px;line-height:1.7">低价一侧：${esc(pairing.sourcePlatform || "")} ${esc(pairing.sourceVariant || "")} ${Number(pairing.sourcePrice || 0).toLocaleString("zh-CN")} ${esc(pairing.sourceCurrency || "")}；高价一侧：${esc(pairing.targetPlatform || "")} ${esc(pairing.targetVariant || "")} ${Number(pairing.targetPrice || 0).toLocaleString("zh-CN")} ${esc(pairing.targetCurrency || "")}。${esc(median)}。</p>
    ${pairing.gapContext ? `<div class="inline-alert">${esc(pairing.gapContext)}</div>` : ""}
    ${rows ? `<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>观察日期</th><th>低价侧报价</th><th>高价侧报价</th><th>当时差价</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="bottom-note"><span>ⓘ</span><span>这个商品还没有可比的两次观察，再跑一次比价就能看到历史差价区间。</span></div>`}
  </section>`;
}

function renderPurchaseOrderPanel(candidate) {
  const orders = (state.purchaseOrders || []).filter((item) => item.candidateId === candidate.id);
  if (!orders.length) {
    return `<section class="panel"><div class="panel-head"><h3>采购单草案</h3><small>由书面报价生成，你确认后再下单</small></div><div class="inline-alert">还没有采购单草案。先在询价单里录回书面报价，再从比较表生成草案。</div></section>`;
  }
  const statusOptions = ["草案", "已下单", "已发货", "已收货"];
  const rows = orders.map((po) => `<tr>
    <td><b>${esc(po.platform)}</b><small>${esc(po.seller)}</small></td>
    <td>${esc(po.product || "")}<small>${esc(po.spec || "")}</small></td>
    <td>${Number(po.quantity).toLocaleString("zh-CN")} 件</td>
    <td>${Number(po.unitPrice).toLocaleString("zh-CN")} ${esc(po.currency || "")}</td>
    <td>${Number(po.amount).toLocaleString("zh-CN")} ${esc(po.currency || "")}</td>
    <td>${esc(po.expectedArrival || "待确认")}</td>
    <td><select data-po-status="${esc(po.id)}">${statusOptions.map((status) => `<option ${po.status === status ? "selected" : ""}>${status}</option>`).join("")}</select></td>
  </tr>`).join("");
  const history = orders.flatMap((po) => (po.history || []).map((item) => `<li>${esc(item.at ? item.at.slice(0, 16).replace("T", " ") : "")} · ${esc(item.status)}${item.note ? ` · ${esc(item.note)}` : ""}</li>`)).join("");
  return `<section class="panel"><div class="panel-head"><h3>采购单草案</h3><small>系统不联系卖家、不付款、不发货</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>店铺</th><th>商品 / 规格</th><th>数量</th><th>单价</th><th>金额</th><th>预计到货</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>${history ? `<ul style="color:#9aa39b;font-size:9px;line-height:1.7">${history}</ul>` : ""}<div class="bottom-note"><span>ⓘ</span><span>草案只是给你核对与下单用的清单；状态变化由你手工记录。</span></div></section>`;
}

function renderRfqPanel(candidate) {
  const rfqs = (state.rfqs || []).filter((item) => item.candidateId === candidate.id);
  const pairing = candidate.priceGap || {};
  const createButton = pairing.product
    ? `<button class="button-secondary" data-action="create-rfq" data-id="${esc(candidate.id)}">生成询价单</button>`
    : "";
  if (!rfqs.length) {
    return `<section class="panel"><div class="panel-head"><h3>询价单与报价回收</h3><small>发给多家店铺，再把书面答复收回</small></div><div class="inline-alert">还没有询价单。${createButton}</div></section>`;
  }
  const blocks = rfqs.map((rfq) => {
    const targets = (rfq.targets || []).map((target) => {
      const quote = (rfq.quotes || []).find((item) => item.platform === target.platform && item.seller === target.seller);
      return `<tr>
        <td><b>${esc(target.platform)}</b><small>${esc(target.seller)}</small></td>
        <td>${target.referencePrice == null ? "—" : `${Number(target.referencePrice).toLocaleString("zh-CN")} ${esc(target.referenceCurrency || "")}`}</td>
        <td>${quote ? `<span class="status-pill done">已报价</span>` : `<span class="status-pill wait">${esc(target.status || "待报价")}</span>`}</td>
        <td>${quote ? `${quote.unitPrice == null ? "—" : Number(quote.unitPrice).toLocaleString("zh-CN")} ${esc(quote.currency || "")}<small>MOQ ${quote.moq ?? "—"} · 交期 ${quote.leadTimeDays ?? "—"} 天</small>` : "—"}</td>
        <td>${quote ? esc(quote.note || "") : "—"}</td>
        <td><button class="button-quiet" data-action="record-quote" data-rfq="${esc(rfq.id)}" data-platform="${esc(target.platform)}" data-seller="${esc(target.seller)}">${quote ? "修改报价" : "录入报价"}</button>${quote && quote.unitPrice != null ? `<button class="button-quiet" data-action="apply-quote" data-rfq="${esc(rfq.id)}" data-platform="${esc(target.platform)}" data-seller="${esc(target.seller)}">用作采购价</button>` : ""}</td>
      </tr>`;
    }).join("");
    const questions = (rfq.questions || []).map((item) => `<li>${esc(item)}</li>`).join("");
    const comparison = rfq.comparison || {};
    const comparisonRows = (comparison.rows || []).map((row) => `<tr>
      <td><b>${esc(row.platform)}</b><small>${esc(row.seller)}</small>${row.isCheapest ? `<small class="promo-tag">书面最低价</small>` : ""}${row.isSmallestMoq ? `<small class="promo-tag">最小起订</small>` : ""}${row.isFastest ? `<small class="promo-tag">最快交期</small>` : ""}</td>
      <td>${row.unitPrice == null ? "—" : `${Number(row.unitPrice).toLocaleString("zh-CN")} ${esc(row.currency || "")}`}</td>
      <td>${row.moq ?? "—"}</td>
      <td>${row.leadTimeDays == null ? "—" : `${row.leadTimeDays} 天`}</td>
      <td>${row.cashNeeded == null ? "—" : `${Number(row.cashNeeded).toLocaleString("zh-CN")} ${esc(row.currency || "")}`}</td>
      <td>${row.withinBudget === null ? "—" : row.withinBudget ? `<span class="status-pill done">在预算内</span>` : `<span class="status-pill wait">超预算</span>`}</td>
      <td>${esc(row.invoice || row.note || "—")}</td>
      <td>${row.unitPrice == null ? "" : `<button class="button-quiet" data-action="create-po" data-rfq="${esc(rfq.id)}" data-platform="${esc(row.platform)}" data-seller="${esc(row.seller)}">生成采购单</button>`}</td>
    </tr>`).join("");
    const comparisonBlock = comparisonRows ? `<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>店铺</th><th>书面单价</th><th>MOQ</th><th>交期</th><th>首单资金</th><th>预算对照</th><th>发票/备注</th></tr></thead><tbody>${comparisonRows}</tbody></table></div>${(comparison.notes || []).map((item) => `<li>${esc(item)}</li>`).join("")}` : "";
    const replyRate = comparison.replyRate == null ? "—" : `${Math.round(comparison.replyRate * 100)}%`;
    return `<div class="report-list"><b>${esc(rfq.title)} · ${esc(rfq.status || "")}</b><button class="button-quiet" data-action="export-rfq-batch" data-rfq="${esc(rfq.id)}">导出全部店铺</button>${rfq.status === "待发送" ? `<button class="button-quiet" data-action="mark-rfq-sent" data-rfq="${esc(rfq.id)}">标记已发送</button>` : ""}<small style="display:block;margin:6px 0">回复率 ${replyRate} · 截止日期 ${esc(rfq.dueDate || "未设置")} · 未回复 ${(comparison.missing || []).length} 家</small>${comparisonBlock}<div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>目标店铺</th><th>公开参考价</th><th>状态</th><th>书面报价</th><th>备注</th><th></th></tr></thead><tbody>${targets}</tbody></table></div><div class="bottom-note"><span>ⓘ</span><span>需要卖家书面回答：${questions ? "" : "无"}</span></div><ul style="color:#9aa39b;font-size:9px;line-height:1.7">${questions}</ul></div>`;
  }).join("");
  return `<section class="panel"><div class="panel-head"><h3>询价单与报价回收</h3><small>发给多家店铺，再把书面答复收回</small>${createButton}</div>${blocks}</section>`;
}

function rfqBatchMarkdown(rfq) {
  const lines = [`# 询价单 · ${rfq.title || ""}`, ""];
  lines.push(`- 目标配置：${rfq.spec || "—"}`);
  lines.push(`- 目标市场：${rfq.market || "—"}`);
  lines.push("");
  lines.push("## 需要书面回答的问题");
  (rfq.questions || []).forEach((question, index) => lines.push(`${index + 1}. ${question}`));
  lines.push("");
  for (const target of rfq.targets || []) {
    lines.push(`## ${target.platform} · ${target.seller}`);
    lines.push(`- 联系页面：${target.url || "—"}`);
    lines.push(`- 公开页面可见参考价（仅参考）：${target.referencePrice == null ? "—" : `${Number(target.referencePrice).toLocaleString("zh-CN")} ${target.referenceCurrency || ""}`}`);
    lines.push("");
    lines.push("请书面回复以上问题（单价 / 起订量与阶梯价 / 交期 / 发票 / 样品 / 发货地）：");
    lines.push("");
    lines.push("（答复栏）");
    lines.push("");
  }
  lines.push("> 本询价单由公开页面线索整理，仅用于询价沟通，不构成下单或付款指令。");
  return lines.join("\n");
}

function renderScalePlan(candidate) {
  const plan = candidate.scalePlan || {};
  if (!plan.complete) {
    return `<section class="panel"><div class="panel-head"><h3>扩量建议</h3><small>小测达标后才有测算</small></div><div class="inline-alert">${esc(plan.reason || "小测尚未达标，不生成扩量方案。")}</div></section>`;
  }
  const money = (value) => (value == null ? "—" : `${Number(value).toLocaleString("zh-CN")} ${plan.currency || ""}`);
  const rows = (plan.tiers || []).map((tier) => `<tr>
    <td><b>${esc(tier.label)}</b><small>日均 ${tier.dailyUnits} 件</small></td>
    <td>${tier.salesUnits} 件${tier.safetyUnits ? `<small>+ 安全库存 ${tier.safetyUnits} 件</small>` : ""}</td>
    <td>${tier.totalUnits} 件</td>
    <td>${money(tier.cashUsed)}</td>
    <td>${tier.withinBudget === null ? "—" : tier.withinBudget ? `<span class="status-pill done">在预算内</span>` : `<span class="status-pill wait">超预算</span>`}</td>
  </tr>`).join("");
  const risks = (plan.risks || []).map((item) => `<li>${esc(item)}</li>`).join("");
  return `<section class="panel"><div class="panel-head"><h3>扩量建议</h3><small>按小测实测日均销量测算</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>档位</th><th>铺货量</th><th>含安全库存</th><th>资金占用</th><th>预算对照</th></tr></thead><tbody>${rows}</tbody></table></div><ul style="color:#9aa39b;font-size:9px;line-height:1.7">${risks}</ul><div class="bottom-note"><span>ⓘ</span><span>${esc(plan.note || "")}</span></div></section>`;
}

function renderTimeline(candidate) {
  const timeline = candidate.timeline || [];
  if (!timeline.length) return "";
  const rows = timeline.map((item) => `<tr><td>${esc(item.at ? fmtDate(item.at) : "")}</td><td>${esc(item.title || item.stageId || "")}</td><td>${esc(item.from || "")} → ${esc(item.to || "")}</td><td>${esc(item.note || "")}</td></tr>`).join("");
  return `<section class="panel"><div class="panel-head"><h3>阶段时间线</h3><small>每一步何时被谁推进的记录</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>时间</th><th>阶段</th><th>状态变化</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function renderPilotPanel(candidate) {
  const pilot = candidate.pilot || {};
  const review = candidate.pilotReview || {};
  const metrics = review.metrics || {};
  const verdictLabel = { "达标": "达标", "质量不达标": "质量不达标", "成本不成立": "成本不成立", "待补数据": "待补数据" }[review.verdict || ""] || "待补数据";
  const field = (name, label, unit, step = "0.01") => `<div class="field"><label for="pilot-${name}">${esc(label)} <span style="font-weight:400;color:#9ca59e">(${esc(unit)})</span></label><input id="pilot-${name}" name="${name}" type="number" min="0" step="${step}" value="${pilot[name] == null ? "" : esc(pilot[name])}" /></div>`;
  const findings = (review.findings || []).map((item) => `<li>${esc(item)}</li>`).join("");
  return `<section class="panel"><div class="panel-head"><h3>小测记录</h3><small>录入真实订单与投放数据，系统只按你设的门槛判断</small></div>
    <form id="pilot-form" class="form-grid">
      ${field("windowDays", "测试窗口", "天", "1")}
      ${field("spend", "投放花费", "销售币种")}
      ${field("clicks", "点击/访问", "次", "1")}
      ${field("orders", "订单数", "单", "1")}
      ${field("refunds", "退款单数", "单", "1")}
      ${field("revenue", "销售额", "销售币种")}
      ${field("netProfit", "净利润", "销售币种")}
      <div class="field full"><div class="form-actions"><button type="submit" class="button-secondary">保存并判断</button></div></div>
    </form>
    <div class="profit-result" style="margin-top:12px"><div class="profit-metric"><span>结论</span><b class="${review.verdict === "达标" ? "" : "negative"}">${esc(verdictLabel)}</b></div><div class="profit-metric"><span>建议</span><b>${esc(review.recommendation || "待补数据")}</b></div><div class="profit-metric"><span>退款率</span><b>${metrics.refundRate == null ? "—" : `${(metrics.refundRate * 100).toFixed(1)}%`}</b></div><div class="profit-metric"><span>净利率</span><b>${metrics.netMargin == null ? "—" : `${(metrics.netMargin * 100).toFixed(1)}%`}</b></div></div>
    ${findings ? `<ul style="color:#9aa39b;font-size:9px;line-height:1.7">${findings}</ul>` : ""}
    ${renderCalibration(candidate)}
  </section>`;
}

function renderCalibration(candidate) {
  const suggestions = candidate.calibration || [];
  if (!suggestions.length) return "";
  const rows = suggestions.map((item) => `<tr><td>${esc(item.label)}</td><td>${item.current == null ? "未填" : esc(item.current)}</td><td>${esc(item.suggested)}</td><td>${esc(item.note || "")}</td><td>${item.field === "measuredNetMargin" ? "仅对照" : `<button class="button-quiet" data-action="apply-calibration" data-field="${esc(item.field)}" data-value="${esc(item.suggested)}">应用到利润假设</button>`}</td></tr>`).join("");
  return `<div class="report-list"><b>用实测值校准利润假设</b><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>项目</th><th>当前假设</th><th>实测值</th><th>说明</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function renderSellerComparison(candidate) {
  const pairing = candidate.priceGap || {};
  if (!pairing.product) return "";
  const observations = (state.priceObservations || []).filter((item) => item.product === pairing.product && item.config === pairing.sourceVariant);
  if (!observations.length) return "";
  const groups = new Map();
  for (const item of observations) {
    const key = `${item.platform}|${item.url}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const rows = [...groups.entries()].map(([, items]) => {
    const sorted = [...items].sort((a, b) => String(b.observedAt).localeCompare(String(a.observedAt)));
    const latest = sorted[0];
    const seller = (latest.url || "").replace(/^https?:\/\/([^/]+).*$/, "$1");
    return `<tr><td><b>${esc(latest.platform || "未知平台")}</b><small>${esc(seller)}</small></td><td>${latest.price == null ? "—" : `${Number(latest.price).toLocaleString("zh-CN")} ${esc(latest.currency || "")}`}</td><td>${esc(latest.observedAt || "")}</td><td>${sorted.length} 次</td><td>${latest.url ? `<a href="${esc(latest.url)}" target="_blank" rel="noopener noreferrer">页面 ↗</a>` : "—"}</td><td><button class="button-quiet" data-action="mark-supplier" data-platform="${esc(latest.platform || "")}" data-seller="${esc(seller)}" data-url="${esc(latest.url || "")}" data-price="${esc(latest.price ?? "")}" data-currency="${esc(latest.currency || "")}" data-product="${esc(latest.product || "")}">标记货源</button></td></tr>`;
  }).sort((a, b) => a.localeCompare(b)).join("");
  return `<section class="panel"><div class="panel-head"><h3>采购比价表</h3><small>同一商品与配置在各个页面见过的报价</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台 / 店铺</th><th>最近可见报价</th><th>观察日期</th><th>次数</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table></div><div class="bottom-note"><span>ⓘ</span><span>这些是页面报价记录，不等于可下单价；起订量、交期与发票仍需向卖家确认。</span></div></section>`;
}

function renderCandidateScenarios(candidate) {
  const rows = candidate.priceScenarios.map((scenario) => `<tr><td>${esc(scenario.label)}</td><td>${Number(scenario.purchasePrice).toLocaleString("zh-CN")}</td><td>${Number(scenario.grossGap).toLocaleString("zh-CN")}</td><td>${scenario.contributionProfit == null ? `<span class="status-pill wait">待补费用</span>` : `${Number(scenario.contributionProfit).toLocaleString("zh-CN")}`}</td><td>${esc(scenario.note || "")}</td></tr>`).join("");
  return `<section class="panel"><div class="panel-head"><h3>三档采购价基准</h3><small>同一配置，不同拿货价下的单件贡献利润</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>采购价基准</th><th>采购价</th><th>毛差价</th><th>贡献利润</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function renderPurchaseChecklist(candidate) {
  const items = candidate.purchaseChecklist || [];
  const rows = items.map((item) => {
    const tickable = item.id !== "price_evidence";
    const status = item.done ? "已确认" : item.status;
    return `<tr><td>${tickable ? `<label class="check-option" style="display:inline-flex;gap:6px;align-items:flex-start"><input type="checkbox" data-check="checklist" data-item="${esc(item.id)}" ${item.done ? "checked" : ""} /><span>${esc(item.item)}</span></label>` : esc(item.item)}</td><td><span class="status-pill ${item.done || item.status === "已有线索" ? "done" : "wait"}">${esc(status)}</span></td><td>${esc(item.note || "")}</td></tr>`;
  }).join("");
  const done = items.filter((item) => item.id !== "price_evidence" && item.done).length;
  const total = items.filter((item) => item.id !== "price_evidence").length;
  return `<section class="panel"><div class="panel-head"><h3>采购待办清单</h3><small>除价格外都需要你向卖家确认 · 已确认 ${done}/${total}</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>事项</th><th>状态</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div><div class="bottom-note"><span>ⓘ</span><span>逐项确认后，“寻找货源”阶段会自动推进；MOQ、交期与发票仍以卖家书面答复为准。</span></div></section>`;
}

function scoreRows(candidate) {
  const evidenceDimensions = {
    potential: ["demand", "competition", "margin"],
    resource_fit: ["sourcing", "operations", "margin"],
    risk: ["sourcing", "operations", "compliance"],
    confidence: Object.keys(state.evidenceDimensionLabels),
  };
  return Object.entries(state.scoreLabels).map(([key, metadata]) => {
    const score = candidate.scores?.[key] || {};
    const allowed = evidenceDimensions[key] || [];
    const choices = (candidate.evidence || []).filter((item) => allowed.includes(item.dimension) && item.url && item.observedAt);
    const selected = new Set(score.evidenceIds || []);
    return `<div class="score-assessment"><div class="score-assessment-head"><b>${esc(metadata.label)}</b><span>${esc(metadata.direction)}</span></div><div class="score-assessment-value">${score.value == null ? "待评估" : `${Number(score.value).toFixed(1)} / 5`}</div>${score.rationale ? `<p class="score-rationale-saved">${esc(score.rationale)}</p>` : ""}<div class="score-edit-grid"><label>评分<select id="score-value-${esc(key)}"><option value="">选择 1–5</option>${[1,2,3,4,5].map((n) => `<option value="${n}" ${Number(score.value) === n ? "selected" : ""}>${n}</option>`).join("")}</select></label><label>关联来源<select id="score-evidence-${esc(key)}" multiple size="${Math.min(Math.max(choices.length, 2), 4)}" aria-label="${esc(metadata.label)}关联来源">${choices.map((item) => `<option value="${esc(item.id)}" ${selected.has(item.id) ? "selected" : ""}>${esc(item.title)} · ${esc(item.observedAt)}</option>`).join("")}</select></label></div><label class="score-rationale-label">判断依据<textarea id="score-rationale-${esc(key)}" rows="2" placeholder="简述当前评分依据">${esc(score.rationale || "")}</textarea></label><div class="dimension-actions"><button class="text-link" data-action="evidence-tab">管理证据</button><button class="text-link" data-action="save-score" data-score="${esc(key)}">保存这项判断</button></div></div>`;
  }).join("");
}

function confidenceRows(candidate) {
  const confidence = candidate.confidence || {};
  return Object.entries(state.evidenceDimensionLabels).map(([key, label]) => {
    const item = confidence[key] || { count: 0, status: "missing" };
    return `<div class="confidence-line"><span>${esc(label)}</span><span>${item.count} 条 · ${evidenceStatusLabel(item.status)}</span></div>`;
  }).join("");
}

function renderOverview(candidate) {
  const scoreDone = candidate.scoreStatus?.complete;
  const statusNote = candidate.agentDiscovery
    ? [candidate.agentDiscovery.productIdea, candidate.agentDiscovery.strategyFit, candidate.agentDiscovery.signal].filter(Boolean).join("\n\n")
    : candidate.hypothesis || "Agent 尚未生成研究假设。";
  const workflow = (candidate.workflowStages || []).map((item) => `<div class="candidate-workflow-step"><span class="workflow-status workflow-${esc(item.status)}">${item.status === "completed" ? "✓" : item.status === "blocked" ? "Ⅱ" : item.number}</span><div><b>${esc(item.title)}</b><p>${esc(item.note || "等待 Agent 推进")}</p></div></div>`).join("");
  const research = candidate.agentResearch;
  return `
    <div class="detail-grid">
      <div>
        <section class="panel"><div class="panel-head"><div><h3>Agent 选品判断</h3><small>${esc(candidate.strategyTitle || "指定商品研究")}${candidate.agentDiscovery?.confidence ? ` · 信号置信度 ${esc(candidate.agentDiscovery.confidence)}` : ""}</small></div><span class="status-pill">${statusLabel(candidate.status)}</span></div><div class="hypothesis-box">${esc(statusNote)}</div>${research ? `<div class="agent-final-review"><b>Agent 初筛：${({ keep: "进入人工审核", hold: "补证后审核", reject: "暂缓" })[research.recommendation] || "待审核"}</b><p>${esc(research.recommendationReason)}</p></div>` : ""}<div style="display:flex;gap:7px;margin-top:11px"><button class="button-secondary" data-action="tab" data-tab="evidence">查看来源证据</button><button class="button-quiet" data-action="tab" data-tab="tasks">查看 Agent 任务 →</button></div></section>
        ${candidate.priceGap ? renderPriceGapContext(candidate) : ""}
        ${candidate.priceScenarios?.length ? renderCandidateScenarios(candidate) : ""}
        ${renderSellerComparison(candidate)}
        ${renderPilotPanel(candidate)}
        ${renderRfqPanel(candidate)}
        ${renderPurchaseOrderPanel(candidate)}
        ${renderScalePlan(candidate)}
        ${renderTimeline(candidate)}
        ${candidate.purchaseChecklist?.length ? renderPurchaseChecklist(candidate) : ""}
        ${workflow ? `<section class="panel"><div class="panel-head"><h3>六步自动研究进度</h3><small>缺少授权或真实交易数据时明确停在待核验</small></div><div class="candidate-workflow">${workflow}</div></section>` : ""}
        ${research ? `<section class="panel"><div class="panel-head"><h3>市场与供应链初筛</h3><small>搜索摘要 · 待人工核对页面</small></div><div class="agent-assessments-detail"><div><b>${esc(candidate.market || "日本")}市场</b><p>${esc(research.marketAssessment)}</p></div><div><b>${esc(candidate.sourcingCountry || "中国")}货源</b><p>${esc(research.sourcingAssessment)}</p></div><div><b>利润准备度</b><p>${research.profitReadiness === "blocked" ? "当前搜索结果不足以验证商品价格与完整费用，因此不计算利润。" : "部分价格线索仍需核实。"}</p></div>${research.gaps?.length ? `<div><b>尚待补齐</b><ul>${research.gaps.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}</div></section>` : ""}
        <section class="panel"><div class="panel-head"><h3>Agent 研究队列</h3><small>${candidate.tasks.length} 项专业任务</small></div><div class="task-list">${candidate.tasks.slice(0, 5).map(renderTaskRow).join("")}</div><div style="display:flex;justify-content:flex-end;margin-top:10px"><button class="text-link" data-action="tab" data-tab="tasks">查看全部任务 →</button></div></section>
        <section class="panel"><div class="panel-head"><h3>利润模型</h3><small>JPY · 用户输入假设</small></div>${candidate.profit?.complete ? `<div class="profit-result"><div class="profit-metric"><span>单件贡献利润</span><b class="${candidate.profit.contributionProfit < 0 ? "negative" : ""}">¥${fmtYen(candidate.profit.contributionProfit)}</b></div><div class="profit-metric"><span>贡献利润率</span><b class="${candidate.profit.contributionMargin < 0 ? "negative" : ""}">${(candidate.profit.contributionMargin * 100).toFixed(1)}%</b></div><div class="profit-metric"><span>盈亏平衡广告成本</span><b>¥${fmtYen(candidate.profit.breakEvenAdCost)}</b></div></div>` : `<div class="inline-alert">利润假设未填完整。补齐售价、采购价、汇率和履约成本后才会计算。</div>`}<div style="display:flex;justify-content:flex-end;margin-top:10px"><button class="text-link" data-action="tab" data-tab="profit">编辑利润假设 →</button></div></section>
      </div>
      <aside>
        <section class="panel"><div class="panel-head"><h3>四项独立判断</h3><small>分别查看，不合并成总分</small></div><div class="independent-score-note">机会潜力、资源匹配、风险水平和结论置信度各自保留分数、理由与证据。</div>${scoreRows(candidate)}</section>
        <section class="panel"><div class="panel-head"><h3>证据覆盖</h3><small>${candidate.evidence.length} 条来源</small></div>${confidenceRows(candidate)}<div style="display:flex;justify-content:flex-end;margin-top:11px"><button class="text-link" data-action="tab" data-tab="evidence">管理来源 →</button></div></section>
        <section class="panel"><div class="panel-head"><h3>资源匹配提示</h3><small>不影响基础机会分</small></div><p style="margin:0;color:#77837a;font-size:8px;line-height:1.6">${esc(candidate.resourceFit)}</p><button class="text-link" style="margin-top:10px" data-action="profile">调整经营资源 →</button></section>
      </aside>
    </div>
  `;
}

function renderTaskRow(task) {
  const icon = ({ discovery: "✳", demand: "⌕", competition: "▤", sourcing: "⌂", economics: "¥", operations: "↗", compliance: "◈", synthesis: "✧" })[task.agentKey] || "·";
  const required = (task.requiredEvidence || []).join(" · ");
  const result = task.result;
  const action = state.llmProvider?.configured
    ? `<button class="button-secondary agent-run-button" data-action="run-agent" data-task-id="${esc(task.id)}">${result ? "重新分析" : "MiniMax 分析"}</button>`
    : "";
  const draft = result?.text
    ? `<div class="agent-draft"><div class="agent-draft-head"><b>MiniMax 分析草稿 · 待人工复核</b><small>${esc(result.model)} · ${fmtDate(result.createdAt)}</small></div><div>${esc(result.text)}</div><small>基于 ${result.evidenceIds?.length || 0} 条已录入证据；模型没有访问外部商品平台。</small></div>`
    : "";
  return `<div class="task-row"><div class="task-mark">${icon}</div><div class="task-copy"><b>${esc(task.title)}</b><p>${esc(task.question)}<br><span style="color:#9aa49b">所需材料：${esc(required)}</span></p>${draft}</div><div class="task-controls"><span class="task-status ${taskStatusClass(task.status)}">${taskStatusLabel(task.status)}</span>${action}</div></div>`;
}

function renderTasks(candidate) {
  const providerMessage = state.llmProvider?.configured && state.webSearch?.configured
    ? `已连接 ${esc(state.llmProvider.provider)} 与公开网页搜索。`
    : "检查 MiniMax 模型、Web Search 和本机 Pi Agent 配置。";
  const checklistBlock = (candidate.purchaseChecklist || []).length
    ? `<section class="panel"><div class="panel-head"><h3>人工待办（采购）</h3><small>勾选后同步推进六步流程</small></div>${renderPurchaseChecklist(candidate)}</section>`
    : "";
  return `<div class="detail-grid"><div><section class="panel"><div class="panel-head"><h3>专业 Agent 任务</h3><small>主控 Agent 自动拆解与汇总</small></div><div class="task-list">${candidate.tasks.map(renderTaskRow).join("")}</div></section>${checklistBlock}</div><aside><section class="panel"><div class="panel-head"><h3>Agent 工作边界</h3></div><p style="color:#79847b;font-size:9px;line-height:1.7;margin:0">${providerMessage} Agent 先自动搜索公开网页并整理来源，任务按钮可基于候选资料补充专项分析。搜索摘要不是完整商品数据；平台 API、正式报价和真实交易结果仍需对应授权或订单数据。模型输出均为待审分析，不会自动下单。</p></section><section class="panel"><div class="panel-head"><h3>证据要求</h3></div><p style="color:#79847b;font-size:9px;line-height:1.7;margin:0">每个候选保留来源 URL、搜索式和摘要。商品规格、价格、销量、MOQ、交期和利润要在对应平台页面或正式报价中复核。</p><button class="button-secondary" style="margin-top:12px" data-action="tab" data-tab="evidence">查看来源</button></section></aside></div>`;
}

function evidenceForm(candidate, selectedDimension = "demand") {
  return `
    <form id="evidence-form" class="form-grid">
      <div class="field"><label for="ev-dimension">研究维度</label><select id="ev-dimension" name="dimension" required>${Object.entries(state.evidenceDimensionLabels).map(([key, label]) => `<option value="${key}" ${selectedDimension === key ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></div>
      <div class="field"><label for="ev-type">来源类型</label><select id="ev-type" name="sourceType"><option>Amazon 日本站</option><option>供应商/采购</option><option>官方规则</option><option>趋势/消费者</option><option>成本报价</option><option>其他</option></select></div>
      <div class="field"><label for="ev-title">来源名称</label><input id="ev-title" name="title" placeholder="例如：商品页面、官方规则名称" required /></div>
      <div class="field"><label for="ev-date">采集日期</label><input id="ev-date" name="observedAt" type="date" value="${new Date().toISOString().slice(0, 10)}" required /></div>
      <div class="field full"><label for="ev-url">来源链接</label><input id="ev-url" name="url" type="url" placeholder="https://..." required /></div>
      <div class="field full"><label for="ev-note">摘录与研究备注</label><textarea id="ev-note" name="note" placeholder="记录能从来源直接支持的内容，并区分推断与事实。" required></textarea></div>
      <p class="form-help">录入来源不会自动验证页面内容或来源独立性。来源链接和采集日期是评分的最低门槛。</p>
      <div class="field full"><div class="form-actions"><button type="submit" class="button-primary">保存来源</button></div></div>
    </form>
  `;
}

function renderEvidence(candidate) {
  const items = [...candidate.evidence].sort((a, b) => b.observedAt.localeCompare(a.observedAt));
  return `<div class="detail-grid"><div><section class="panel"><div class="panel-head"><h3>来源证据</h3><small>只代表已记录来源，不代表自动核实</small></div>${items.length ? items.map((item) => `<article class="evidence-card"><div class="evidence-title"><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)} ↗</a><time>${esc(item.observedAt)}</time></div><div class="source-type">${esc(state.evidenceDimensionLabels[item.dimension] || item.dimension)} · ${esc(item.sourceType)}</div><p>${esc(item.note)}</p></article>`).join("") : `<div class="table-empty" style="padding:25px 10px"><div class="empty-icon">↗</div><h3>还没有来源证据</h3><p>先记录你找到的市场页面、竞品、供应商、成本或官方规则来源。</p></div>`}</section></div><aside><section class="panel"><div class="panel-head"><h3>添加证据</h3><small>来源可复查</small></div>${evidenceForm(candidate)}</section><section class="panel"><div class="panel-head"><h3>证据状态说明</h3></div><div class="confidence-line"><span>待补来源</span><span>尚未记录有效 URL</span></div><div class="confidence-line"><span>单一来源</span><span>有 1 条有效来源</span></div><div class="confidence-line"><span>多来源</span><span>2 条以上，仍需确认独立性</span></div><p style="font-size:8px;color:#929b94;line-height:1.6;margin:10px 0 0">来源数量不是可靠性评级。确认页面真实性、采集范围和来源独立性仍由研究者负责。</p></section></aside></div>`;
}

const unifiedProfitFields = [
  ["sellingPrice", "销售端报价", "元 / 件", ""],
  ["sellingCurrency", "销售端币种", "CNY / JPY", "currency"],
  ["purchasePrice", "采购端报价", "元 / 件", ""],
  ["purchaseCurrency", "采购端币种", "CNY / JPY", "currency"],
  ["fxRate", "汇率（采购币种→销售币种）", "销售币种 / 采购币种", ""],
  ["inboundCost", "头程与入仓", "销售币种 / 件", ""],
  ["dutyTaxCost", "关税及进口税费", "销售币种 / 件", ""],
  ["referralRate", "平台佣金率", "%", "percent"],
  ["adCost", "广告成本预估", "销售币种 / 件", ""],
  ["fulfillmentCost", "履约与运费", "销售币种 / 件", ""],
  ["returnReserveRate", "退货/损耗预留", "%", "percent"],
  ["otherCost", "其他单件成本", "销售币种 / 件", ""],
  ["batchQty", "首批备货数量", "件", ""],
  ["cashCycleDays", "现金回本周期", "天", ""],
];

const percentProfitFields = new Set(["referralRate", "returnReserveRate"]);
const currencyProfitFields = new Set(["sellingCurrency", "purchaseCurrency"]);

function renderProfit(candidate) {
  const values = candidate.assumptions || {};
  const result = candidate.profit || { complete: false };
  const fallbackCurrency = candidate.market === "中国" ? "CNY" : "JPY";
  const input = ([key, label, unit, type]) => {
    const value = values[key];
    if (type === "currency") {
      const current = String(value || fallbackCurrency).toUpperCase();
      return `<div class="field"><label for="pf-${key}">${esc(label)} <span style="font-weight:400;color:#9ca59e">(${esc(unit)})</span></label><select id="pf-${key}" name="${key}"><option value="CNY" ${current === "CNY" ? "selected" : ""}>CNY 元</option><option value="JPY" ${current === "JPY" ? "selected" : ""}>JPY 日元</option></select></div>`;
    }
    return `<div class="field"><label for="pf-${key}">${esc(label)} <span style="font-weight:400;color:#9ca59e">(${esc(unit)})</span></label><input id="pf-${key}" name="${key}" type="number" min="0" step="${type === "percent" ? "0.1" : "0.01"}" value="${value == null ? "" : type === "percent" ? Number(value) * 100 : esc(value)}" placeholder="${type === "percent" ? "输入百分比" : "输入假设值"}" /></div>`;
  };
  const money = (value) => (result.currency === "CNY" ? `¥${Number(value).toLocaleString("zh-CN")}` : `¥${fmtYen(value)}`);
  return `<div class="detail-grid"><div><section class="panel"><div class="panel-head"><h3>单件利润假设</h3><small>同币种差价与跨境差价共用这一套字段</small></div><form id="profit-form" class="form-grid">
    ${unifiedProfitFields.map(input).join("")}
    <div class="field"><label for="pf-fxSource">汇率来源（页面或日期）</label><input id="pf-fxSource" name="fxSource" type="text" value="${esc(values.fxSource || "")}" placeholder="例如：银行页面 2026-11-15" /></div>
    <div class="field"><label for="pf-costSource">费用来源（平台规则页或你的记录）</label><input id="pf-costSource" name="costSource" type="text" value="${esc(values.costSource || "")}" placeholder="例如：京东商家后台费率截图" /></div>
    <p class="form-help">两端币种相同（同市场平台间差价）时不用填汇率、头程与关税；币种不同时这三项必须填。佣金和退货预留按百分比录入；缺任何必要输入都只显示“待核验”，系统不会用 0 代替。</p>
    <div class="field full"><div class="form-actions"><button type="submit" class="button-primary">保存并计算</button></div></div>
  </form></section></div><aside>
    <section class="panel"><div class="panel-head"><h3>利润情景</h3><small>${result.currency || fallbackCurrency} / 单件</small></div>${values.fxSource || values.costSource ? `<div class="confidence-line"><span>汇率来源</span><span>${esc(values.fxSource || "未记录")}</span></div><div class="confidence-line"><span>费用来源</span><span>${esc(values.costSource || "未记录")}</span></div>` : `<div class="confidence-line"><span>汇率与费用来源</span><span>未记录</span></div>`}${result.complete ? `<div class="profit-result"><div class="profit-metric"><span>贡献利润</span><b class="${result.contributionProfit < 0 ? "negative" : ""}">${money(result.contributionProfit)}</b></div><div class="profit-metric"><span>贡献利润率</span><b class="${result.contributionMargin < 0 ? "negative" : ""}">${(result.contributionMargin * 100).toFixed(1)}%</b></div><div class="profit-metric"><span>盈亏平衡广告成本</span><b>${money(result.breakEvenAdCost)}</b></div></div><div class="profit-costs">${Object.entries(result.costs).map(([key, value]) => `<div class="cost-row"><span>${esc(key)}</span><b>${money(value)}</b></div>`).join("")}</div>` : `<div class="inline-alert">补齐必要输入后展示计算结果；${result.missingFields?.length ? `当前缺：${result.missingFields.join("、")}。` : "关税和其他成本请核实或明确填入。"}</div>`}</section>
    ${renderBatchPlan(candidate)}
    <section class="panel"><div class="panel-head"><h3>计算公式</h3></div><p style="font-size:8px;line-height:1.7;color:#7f8981;margin:0">售价 − 采购成本（跨币种时按手动汇率换算）− 头程 − 关税及进口税费 − 平台佣金 − 广告 − 履约与运费 − 退货预留 − 其他成本。</p><p style="font-size:8px;line-height:1.7;color:#9aa39b;margin:8px 0 0">单件贡献利润情景；未录入的固定费用、汇兑波动和税务处理仍需人工复核。</p></section>
  </aside></div>`;
}

function rfqMarkdown(candidate) {
  const pairing = candidate.priceGap || {};
  const plan = candidate.scalePlan || {};
  const checklist = candidate.purchaseChecklist || [];
  const lines = [];
  lines.push(`# 询价单 · ${candidate.name}`);
  lines.push("");
  lines.push(`- 目标市场 / 平台：${candidate.market || ""} / ${candidate.platform || ""}`);
  lines.push(`- 采购来源国：${candidate.sourcingCountry || ""}`);
  lines.push(`- 目标配置：${pairing.sourceVariant || ""}${pairing.sourceColor ? ` · ${pairing.sourceColor}` : ""}`);
  lines.push(`- 询价渠道（公开页面线索）：${pairing.sourcePlatform || ""}${pairing.sourceSeller ? ` · ${pairing.sourceSeller}` : ""}`);
  lines.push(`- 公开页面可见参考价：${pairing.sourcePrice ?? "—"} ${pairing.sourceCurrency || ""}（仅作参考，不是承诺价）`);
  if (plan.complete && (plan.tiers || []).length) {
    lines.push(`- 预计销量速度：小测实测日均 ${plan.dailyUnits} 件`);
    lines.push(`- 备货量参考：${plan.tiers.map((tier) => `${tier.label} ${tier.totalUnits} 件`).join("；")}`);
  }
  lines.push("");
  lines.push("## 需要卖家书面回答的问题");
  const questions = checklist
    .filter((item) => item.id !== "price_evidence")
    .map((item, index) => `${index + 1}. ${item.item}（${item.note || ""}）`);
  lines.push(...questions);
  lines.push("");
  lines.push("## 备注");
  lines.push("- 本询价单由公开页面线索整理，仅用于询价沟通，不构成下单或付款指令。");
  lines.push("- 规格、包装、MOQ、阶梯价、交期、发票与验货要求以卖家书面答复为准。");
  return lines.join("\n");
}

function renderBatchPlan(candidate) {
  const plan = candidate.batchPlan || {};
  const profit = candidate.profit || {};
  if (!plan.complete) {
    return `<section class="panel"><div class="panel-head"><h3>首批与周转</h3><small>由你设定，不预测销量</small></div><div class="inline-alert">填入首批数量与现金回本周期，这里会算出首批占用资金、每日需售出件数与每日/每月贡献利润。</div></section>`;
  }
  const money = (value) => (value == null ? "—" : `${Number(value).toLocaleString("zh-CN")} ${plan.currency || ""}`);
  return `<section class="panel"><div class="panel-head"><h3>首批与周转</h3><small>由你设定，不预测销量</small></div><div class="profit-result"><div class="profit-metric"><span>首批占用资金</span><b>${money(plan.cashUsed)}</b></div><div class="profit-metric"><span>每日需售出</span><b>${plan.unitsPerDay} 件</b></div><div class="profit-metric"><span>每日贡献利润</span><b class="${(plan.dailyContribution || 0) < 0 ? "negative" : ""}">${money(plan.dailyContribution)}</b></div></div><div class="profit-costs"><div class="cost-row"><span>30 天贡献利润</span><b>${money(plan.monthlyContribution)}</b></div></div><p style="font-size:8px;color:#929b94;line-height:1.6;margin:10px 0 0">${esc(plan.note || "")} 首批 ${plan.batchQty} 件 / ${plan.cashCycleDays} 天回本。</p></section>`;
}

function reportMarkdown(candidate) {
  const rows = Object.entries(candidate.scores || {}).map(([key, value]) => {
    const refs = (value.evidenceIds || []).map((id) => candidate.evidence.find((item) => item.id === id)?.title).filter(Boolean).join("、");
    return `| ${value.label} | ${value.value ?? "待评估"} / 5 | ${value.direction} | ${value.rationale || "待补判断依据"} | ${refs || "待关联来源"} |`;
  }).join("\n");
  const evidenceFor = (dimension) => {
    const items = candidate.evidence.filter((item) => item.dimension === dimension);
    return items.length ? items.map((item) => `- [${item.title}](${item.url})（采集于 ${item.observedAt}）：${item.note}`).join("\n") : "- 待补来源证据。";
  };
  const evidenceDimensions = new Set(candidate.evidence.map((item) => item.dimension));
  const gaps = Object.entries(state.evidenceDimensionLabels).filter(([key]) => !evidenceDimensions.has(key)).map(([key, label]) => `- ${label}：暂无来源，保持待验证`);
  const missingScores = candidate.scoreStatus?.missingScores || Object.keys(state.scoreLabels);
  const profitLines = [];
  if (candidate.profit?.complete) {
    profitLines.push(`单件贡献利润：${Number(candidate.profit.contributionProfit).toLocaleString("zh-CN")} ${candidate.profit.currency || ""}；贡献利润率：${(candidate.profit.contributionMargin * 100).toFixed(1)}%。`);
  } else {
    profitLines.push("利润模型尚未填完。");
  }
  if (candidate.profit?.missingFields?.length) profitLines.push(`仍缺：${candidate.profit.missingFields.join("、")}。`);
  if (candidate.assumptions?.fxSource) profitLines.push(`汇率来源：${candidate.assumptions.fxSource}。`);
  if (candidate.assumptions?.costSource) profitLines.push(`费用来源：${candidate.assumptions.costSource}。`);
  if (candidate.batchPlan?.complete) profitLines.push(`首批 ${candidate.batchPlan.batchQty} 件约占用 ${Number(candidate.batchPlan.cashUsed).toLocaleString("zh-CN")} ${candidate.batchPlan.currency || ""}，需每日售出 ${candidate.batchPlan.unitsPerDay} 件才能在 ${candidate.batchPlan.cashCycleDays} 天回本。`);
  const profit = profitLines.join("\n");
  const tasks = candidate.tasks.map((task) => `- ${task.title}：${taskStatusLabel(task.status)}${task.result?.text ? `\n\n${task.result.text}` : ""}`).join("\n");
  const workflow = (candidate.workflowStages || []).map((stage) => `- ${stage.number}. ${stage.title}：${stage.status}。${stage.note || ""}`).join("\n");
  const marketName = candidate.market || "日本";
  const sourceCountry = candidate.sourcingCountry || "中国";
  const timeline = (candidate.timeline || []).length
    ? `\n\n## 阶段时间线\n\n${candidate.timeline.map((item) => `- ${item.at ? item.at.slice(0, 16).replace("T", " ") : ""} · ${item.title || item.stageId}：${item.from || ""} → ${item.to || ""}${item.note ? `（${item.note}）` : ""}`).join("\n")}`
    : "";
  const purchaseChecklist = (candidate.purchaseChecklist || []).length
    ? `\n\n## 采购待办清单\n\n${candidate.purchaseChecklist.map((item) => `- [${item.status}] ${item.item}${item.note ? `（${item.note}）` : ""}`).join("\n")}`
    : "";
  const validationPlan = [
    `- [ ] 需求：${evidenceDimensions.has("demand") ? "复核来源时间范围与目标顾客相关性，再确认需求是否持续。" : `从授权的${marketName}市场来源收集需求信号，并记录来源和采集日期。`}`,
    `- [ ] 竞品：${evidenceDimensions.has("competition") ? "抽样比较同类商品的价格、差评主题和品牌集中度。" : `记录${marketName}站同类商品页面、定价和评论痛点。`}`,
    `- [ ] 供应链：${evidenceDimensions.has("sourcing") ? "向多家供应商核实 MOQ、样品、交期、包装规格和书面报价。" : `获取至少一条可追溯的${sourceCountry}供应链线索，并询问 MOQ、样品和交期。`}`,
    `- [ ] 利润：${candidate.profit?.complete ? "用已核实的 FBA、佣金、物流和退货数据替换假设，再做保守情景。" : "补齐售价、采购价、汇率、头程、FBA、佣金、广告与退货预留。"}`,
    `- [ ] 合规与运营：确认商品规格、运输限制、平台限制、进口要求、认证和知识产权事项。`,
    `- [ ] 决策：人工复核来源和风险后，再决定小批量测试或暂停；本系统不自动采购。`,
  ].join("\n");
  return `# ${candidate.name}\n\n- 类目：${candidate.category}\n- 市场/平台：${candidate.market} / ${candidate.platform}\n- 选品策略：${candidate.strategyTitle || "指定商品研究"}\n- 研究模式：${modeLabel(candidate.mode)}\n- 状态：${statusLabel(candidate.status)}\n- 更新时间：${candidate.updatedAt}\n\n## 研究假设\n\n${candidate.hypothesis || "待补充"}\n\n## Agent 六步工作流\n\n${workflow || "- 尚无自动选品运行记录。"}\n\n## 四项独立判断\n\n系统不生成加权总分；风险水平单独展示，分数越高代表风险越高。\n\n| 指标 | 分数 | 方向 | 判断依据 | 关联来源 |\n|---|---:|---|---|---|\n${rows || "| 尚无独立判断 | — | — | — | — |"}\n\n未完成指标：${missingScores.length ? missingScores.map((key) => state.scoreLabels[key]?.label || key).join("、") : "无"}\n\n## ${esc(candidate.market || "日本")}市场需求\n\n${evidenceFor("demand")}\n\n## ${esc(candidate.market || "日本")}站竞品\n\n${evidenceFor("competition")}\n\n## ${esc(candidate.sourcingCountry || "中国")}供应链线索\n\n${evidenceFor("sourcing")}\n\n## 利润情景\n\n${profit}\n\n${evidenceFor("margin")}\n\n## 运营与履约\n\n${evidenceFor("operations")}\n\n## 合规与知识产权待确认\n\n${evidenceFor("compliance")}\n\n## 证据缺口\n\n${gaps.length ? gaps.join("\n") : "- 六类研究领域均已记录来源，来源真实性和独立性仍需人工复核。"}${purchaseChecklist}${timeline}\n\n## 下一步小批量验证计划\n\n${validationPlan}\n\n## Agent 任务状态\n\n${tasks}\n\n## 限制\n\n来源数量不代表来源已核实；汇率、费用和市场数据需要人工确认。合规内容只用于提示审查方向。本报告不构成采购建议。\n`;
}

function renderReport(candidate) {
  const markdown = reportMarkdown(candidate);
  return `<section class="panel"><div class="panel-head"><div><h3>研究报告预览</h3><small style="display:block;margin-top:5px">${esc(candidate.name)} · 更新于 ${fmtDate(candidate.updatedAt)}</small></div><button class="button-secondary" data-action="download-report">导出 Markdown</button></div><div class="hypothesis-box" style="white-space:pre-wrap;font:9px/1.75 'DM Sans','Noto Sans SC',sans-serif;max-height:620px;overflow:auto">${esc(markdown)}</div><div class="bottom-note"><span>ⓘ</span><span>报告摘要只整理当前录入信息；证据状态和利润假设仍需要人工复核。</span></div></section>`;
}

function renderProfile() {
  const profile = state.profile || {};
  const market = profile.market || "日本";
  const sourceCountry = profile.sourcingCountry || "中国";
  const sourcingOptions = sourcingPlatforms(sourceCountry);
  const gapCosts = (profile.priceGapAssumptions && typeof profile.priceGapAssumptions === "object") ? profile.priceGapAssumptions : { referralRate: "", adCost: "", fulfillmentCost: "", returnReserveRate: "" };
  const pilotLimits = (profile.pilotThresholds && typeof profile.pilotThresholds === "object") ? profile.pilotThresholds : { minOrders: 30, maxRefundRate: 0.05, minNetMargin: 0.1 };
  const marketChannels = salesPlatforms(market);
  const platformOptions = marketChannels.length
    ? marketChannels.map((item) => `<option value="${esc(item.name)}" ${item.name === profile.platform ? "selected" : ""}>${esc(item.name)}</option>`).join("")
    : `<option value="${esc(profile.platform || "")}">${esc(profile.platform || "待确认")}</option>`;
  const chosenSources = Array.isArray(profile.sourcingPlatforms) ? profile.sourcingPlatforms : [];
  content.innerHTML = `
    <div class="page-kicker">Operating profile</div>
    <div class="page-heading"><div><h1>经营资源画像</h1><p>用它判断机会是否适合当前资源；画像不会调整基础机会评分。</p></div></div>
    <div class="resource-layout"><section class="panel"><div class="panel-head"><h3>当前经营条件</h3><small>字段可留空</small></div><form id="profile-form" class="form-grid">
      <div class="field"><label>目标市场</label><select name="market" data-change="profile-market">${marketOptions(market)}</select></div>
      <div class="field"><label>优先平台</label><select name="platform">${platformOptions}</select></div>
      <div class="field"><label>采购来源国家</label><select name="sourcingCountry">${sourceMarketOptions(sourceCountry)}</select></div>
      <div class="field full"><label>优先货源平台（Agent 按此规划货源搜索式）</label>
        <div class="check-grid">${sourcingOptions.map((item) => `<label class="check-option"><input type="checkbox" name="sourcingPlatforms" value="${esc(item.id)}" ${chosenSources.includes(item.id) ? "checked" : ""} /><span><b>${esc(item.name)}</b><small>${esc(item.note)}</small></span></label>`).join("")}</div>
      </div>
      <div class="field"><label>履约方式</label><input name="fulfillment" value="${esc(profile.fulfillment || "")}" placeholder="例如：日本 FBA" /></div>
      <div class="field"><label>可投入预算 (CNY)</label><input name="budgetCny" type="number" min="0" value="${profile.budgetCny == null ? "" : esc(profile.budgetCny)}" placeholder="可不填写" /></div>
      <div class="field"><label>风险偏好</label><select name="riskTolerance"><option value="" ${!profile.riskTolerance ? "selected" : ""}>未设置</option><option ${profile.riskTolerance === "低风险" ? "selected" : ""}>低风险</option><option ${profile.riskTolerance === "中等风险" ? "selected" : ""}>中等风险</option><option ${profile.riskTolerance === "较高风险" ? "selected" : ""}>较高风险</option></select></div>
      <div class="field"><label>库存承受度</label><select name="inventoryTolerance"><option value="" ${!profile.inventoryTolerance ? "selected" : ""}>未设置</option><option ${profile.inventoryTolerance === "不囤货" ? "selected" : ""}>不囤货</option><option ${profile.inventoryTolerance === "允许少量囤货" ? "selected" : ""}>允许少量囤货</option><option ${profile.inventoryTolerance === "可批量备货" ? "selected" : ""}>可批量备货</option></select></div>
      <div class="field"><label>供应链能力</label><input name="sourcingCapabilities" value="${esc(profile.sourcingCapabilities || "")}" placeholder="工厂、验货、定制等" /></div>
      <div class="field full"><label>小测门槛（判断真实订单是否达标）</label>
        <div class="form-grid">
          <div class="field"><label>最少订单量</label><input name="pilotMinOrders" type="number" min="0" value="${esc(pilotLimits.minOrders ?? "")}" /></div>
          <div class="field"><label>最高退款率（%）</label><input name="pilotMaxRefundRate" type="number" min="0" step="0.1" value="${esc(pilotLimits.maxRefundRate === "" || pilotLimits.maxRefundRate === undefined ? "" : Number(pilotLimits.maxRefundRate) * 100)}" /></div>
          <div class="field"><label>最低净利率（%）</label><input name="pilotMinNetMargin" type="number" min="0" step="0.1" value="${esc(pilotLimits.minNetMargin === "" || pilotLimits.minNetMargin === undefined ? "" : Number(pilotLimits.minNetMargin) * 100)}" /></div>
        </div>
      </div>
      <div class="field full"><label class="check-option" style="display:inline-flex;gap:7px;align-items:flex-start"><input type="checkbox" name="avoidRejected" ${profile.avoidRejected ? "checked" : ""} /><span><b>把淘汰理由回喂给 Agent</b><small>开启后，下一轮自动选品会带上你过去淘汰商品时写下的原因，作为“不要再来这类”的提示；理由原文不改写。</small></span></label></div>
      <div class="field full"><label>比价假设（把毛差价换算成单件贡献利润）</label>
        <div class="form-grid">
          <div class="field"><label>平台佣金率（%）</label><input name="priceGapReferralRate" type="number" min="0" step="0.1" value="${esc(gapCosts.referralRate === "" ? "" : Number(gapCosts.referralRate) * 100)}" placeholder="例如 5" /></div>
          <div class="field"><label>广告成本（每件）</label><input name="priceGapAdCost" type="number" min="0" step="0.01" value="${esc(gapCosts.adCost === "" ? "" : gapCosts.adCost)}" placeholder="例如 20" /></div>
          <div class="field"><label>履约与运费（每件）</label><input name="priceGapFulfillmentCost" type="number" min="0" step="0.01" value="${esc(gapCosts.fulfillmentCost === "" ? "" : gapCosts.fulfillmentCost)}" placeholder="例如 15" /></div>
          <div class="field"><label>退货/损耗预留（%）</label><input name="priceGapReturnReserveRate" type="number" min="0" step="0.1" value="${esc(gapCosts.returnReserveRate === "" ? "" : Number(gapCosts.returnReserveRate) * 100)}" placeholder="例如 2" /></div>
        </div>
      </div>
      ${marketChannels.length ? `<div class="field full"><label>各平台佣金率覆盖（可选，留空则用上面的默认值）</label>
        <div class="check-grid">${marketChannels.map((item) => `<label class="check-option"><input type="number" min="0" step="0.1" name="priceGapCommission_${esc(item.id)}" value="${gapCosts.byPlatform && gapCosts.byPlatform[item.id] && gapCosts.byPlatform[item.id].referralRate !== undefined && gapCosts.byPlatform[item.id].referralRate !== "" ? esc(Number(gapCosts.byPlatform[item.id].referralRate) * 100) : ""}" placeholder="默认" /><span><b>${esc(item.name)}</b><small>平台佣金率 %</small></span></label>`).join("")}</div>
      </div>` : ""}
      <p class="form-help">目标市场决定 Agent 去哪些销售平台核对竞品与价格带；货源平台决定 Agent 去找哪些源头页面。未勾选时使用该来源国的默认集合。这些平台当前只提供公开页面线索，正式报价与 MOQ 仍需另行授权或人工确认。比价假设只在你填写后用于换算单件贡献利润，留空即显示“待补费用”。</p>
      <div class="field full"><div class="form-actions"><button type="submit" class="button-primary">保存经营画像</button></div></div>
    </form></section><aside class="resource-aside"><div class="hero-overline">Scoring policy</div><h3>画像做适配，不替你做决定。</h3><p>基础机会分用于比较市场、利润和风险证据。你的预算、备货能力和风险偏好单独显示，帮助你理解“机会不错”是否等于“现在适合做”。</p><ul><li>${esc(market)}销售 · ${esc(profile.platform || "待确认平台")} 优先</li><li>${esc(sourceCountry)}采购 · ${esc(profile.fulfillment || "履约待确认")}</li><li>预算示例 ¥100,000 CNY，可修改或清空</li><li>目标市场可切到中国或海外，平台清单随市场变化</li></ul></aside></div>
  `;
}

async function renderMetrics() {
  content.innerHTML = `<div class="page-kicker">Retrospective</div><div class="page-heading"><div><h1>复盘小结</h1><p>本地工作台的处理过程统计，不是市场表现或商业结果。</p></div><button class="button-quiet" data-action="home">返回工作台</button></div><div id="metrics-body"><div class="inline-alert">正在统计本地记录…</div></div>`;
  try {
    const metrics = await api("/api/metrics");
    const body = document.querySelector("#metrics-body");
    if (!body) return;
    const strategyRows = Object.entries(metrics.candidates.byStrategy || {}).map(([name, bucket]) => `<tr><td>${esc(name)}</td><td>${bucket.total}</td><td>${bucket.continue || 0}</td><td>${bucket.hold || 0}</td><td>${bucket.reject || 0}</td></tr>`).join("");
    const reasonRows = (metrics.reviews.rejectReasons || []).map((item) => `<tr><td>${esc(item.reason)}</td><td>${item.count}</td></tr>`).join("");
    const statusRows = Object.entries(metrics.candidates.byStatus || {}).map(([name, count]) => `<tr><td>${esc(name)}</td><td>${count}</td></tr>`).join("");
    body.innerHTML = `
      <div class="source-boundary-grid">
        <article><b>候选与运行</b><p>候选 ${metrics.candidates.total} 个 · 运行 ${metrics.runs.total} 次（比价 ${metrics.runs.priceGapRuns} 次）· 失败 ${metrics.runs.failed} 次。</p></article>
        <article><b>证据覆盖</b><p>来源 ${metrics.evidence.totalSources} 条 · 平均每候选 ${metrics.evidence.avgSourcesPerCandidate} 条 · ${metrics.evidence.candidatesWithoutSources} 个候选没有来源。</p></article>
        <article><b>人工审核</b><p>继续 ${metrics.reviews.continue} · 暂缓 ${metrics.reviews.hold} · 淘汰 ${metrics.reviews.reject}。</p></article>
        <article><b>小测与货源</b><p>有真实小测数据 ${metrics.pilot.withData} 个（达标 ${metrics.pilot.passed}）· 货源档案 ${metrics.suppliers.total} 家。</p></article>
        <article><b>询价回收</b><p>询价单 ${metrics.rfqs?.total || 0} 张 · 目标店铺 ${metrics.rfqs?.targets || 0} 家 · 收到书面报价 ${metrics.rfqs?.quotes || 0} 条。</p></article>
        <article><b>采购单草案</b><p>共 ${metrics.purchaseOrders?.total || 0} 张 · ${Object.entries(metrics.purchaseOrders?.byStatus || {}).map(([key, value]) => `${key} ${value}`).join(" / ") || "暂无"}。</p></article>
      </div>
      <section class="panel"><div class="panel-head"><h3>按策略的候选与审核结果</h3><small>看哪条策略产出的候选更容易被留下</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>策略</th><th>候选</th><th>继续</th><th>暂缓</th><th>淘汰</th></tr></thead><tbody>${strategyRows || `<tr><td colspan="5">暂无数据</td></tr>`}</tbody></table></div></section>
      <section class="panel"><div class="panel-head"><h3>淘汰理由分布</h3><small>下一轮发现可以避开这些坑</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>理由</th><th>次数</th></tr></thead><tbody>${reasonRows || `<tr><td colspan="2">还没有淘汰记录</td></tr>`}</tbody></table></div></section>
      <section class="panel"><div class="panel-head"><h3>候选状态分布</h3></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>状态</th><th>数量</th></tr></thead><tbody>${statusRows || `<tr><td colspan="2">暂无数据</td></tr>`}</tbody></table></div></section>
      <div class="bottom-note"><span>ⓘ</span><span>${esc(metrics.note || "")}</span></div>`;
  } catch (error) {
    const body = document.querySelector("#metrics-body");
    if (body) body.innerHTML = `<div class="inline-alert">读取统计失败：${esc(error.message)}</div>`;
  }
}

function boardBuckets() {
  const buckets = {
    "待我决策": [],
    "缺数据": [],
    "可扩量": [],
    "暂缓": [],
    "已淘汰": [],
  };
  for (const candidate of state.candidates || []) {
    const review = candidate.humanReview || {};
    if (review.decision === "reject" || candidate.status === "archived") {
      buckets["已淘汰"].push(candidate);
      continue;
    }
    if (candidate.status === "paused") {
      buckets["暂缓"].push(candidate);
      continue;
    }
    if (candidate.pilotReview?.verdict === "达标") {
      buckets["可扩量"].push(candidate);
      continue;
    }
    const gaps = (candidate.workflowStages || []).filter((stage) => stage.status === "blocked" || stage.status === "partial");
    if (gaps.length) buckets["缺数据"].push(candidate);
    else buckets["待我决策"].push(candidate);
  }
  return buckets;
}

function renderBoard() {
  const buckets = boardBuckets();
  const markets = [...new Set((state.candidates || []).map((item) => item.market).filter(Boolean))];
  const filter = state.boardMarket || "";
  const filtered = {};
  for (const [name, items] of Object.entries(buckets)) {
    filtered[name] = filter ? items.filter((item) => (item.market || "") === filter) : items;
  }
  const block = (title, hint, items) => `<section class="panel"><div class="panel-head"><div><h3>${esc(title)}</h3><small>${esc(hint)}</small></div><span class="connector-count">${items.length}</span></div>${items.length ? `<div class="discovery-run-list">${items.map((candidate) => `<article class="discovery-run"><div class="discovery-run-head"><div><span class="run-type">${esc(candidate.category || "研究项目")} · ${esc(candidate.market || "")} / ${esc(candidate.platform || "")}</span><h3>${esc(candidate.name)}</h3></div><time>${fmtDate(candidate.updatedAt)}</time></div><p class="run-signal-note">${esc(boardNote(candidate))}</p><div class="run-signal-metrics"><span>${esc(candidate.status || "")}</span><span>${(candidate.evidence || []).length} 条来源</span><span>${(candidate.workflowStages || []).filter((stage) => stage.status === "completed").length}/6 步完成</span></div><div class="candidate-card-actions"><button class="button-secondary" data-action="open" data-id="${esc(candidate.id)}">查看候选</button></div></article>`).join("")}</div>` : `<div class="table-empty" style="padding:22px"><p>${esc(title)}里暂时没有内容。</p></div>`}</section>`;
  content.innerHTML = `
    <div class="page-kicker">Decision board</div>
    <div class="page-heading"><div><h1>决策看板</h1><p>按“需要你做什么”排列候选：待决策、缺数据、可扩量、暂缓与已淘汰。</p></div><button class="button-quiet" data-action="home">返回工作台</button></div>
    <div class="chat-composer-context" style="margin-bottom:12px"><label>按市场筛选<select name="boardMarket" data-change="board-market"><option value="">全部市场</option>${markets.map((market) => `<option value="${esc(market)}" ${filter === market ? "selected" : ""}>${esc(market)}</option>`).join("")}</select></label></div>
    ${block("待我决策", "证据齐了、等你继续研究或淘汰的候选", filtered["待我决策"])}
    ${block("缺数据", "流程里还有待补数据或被阻塞的阶段", filtered["缺数据"])}
    ${block("可扩量", "小测已达标，可以看备货测算", filtered["可扩量"])}
    ${block("暂缓", "你主动挂起的候选", filtered["暂缓"])}
    ${block("已淘汰", "已归档或被淘汰的候选，理由留档", filtered["已淘汰"])}
  `;
}

function boardNote(candidate) {
  const review = candidate.humanReview || {};
  if (review.decision === "reject") return `淘汰理由：${review.reason || "未填写"}`;
  if (candidate.status === "paused") return `暂缓原因：${review.reason || "未填写"}`;
  const gaps = (candidate.workflowStages || []).filter((stage) => stage.status === "blocked" || stage.status === "partial");
  if (gaps.length) return gaps.map((stage) => `${stage.title}：${stage.note || stage.status}`).join("；");
  return candidate.hypothesis || "证据已齐，等待你的审核动作。";
}

function renderDiscovery() {
  const webSearch = state.webSearch || {};
  const statusLabel = { web_search: "公开搜索线索", manual_source: "人工记录", restricted_api: "权限受限", authorization_required: "官方 API 待授权" };
  const roleOrder = ["demand", "sales", "sourcing"];
  const targetMarket = state.profile?.market || "日本";
  const roleGroups = roleOrder.map((role) => {
    const items = (state.connectors || []).filter((item) => platformRoles(item).includes(role));
    if (!items.length) return "";
    const cards = items.map((item) => `
      <article class="connector-card">
        <div class="connector-card-head"><b>${esc(item.platform)}</b><span class="connector-state ${item.status === "web_search" ? "ok" : item.status === "authorization_required" ? "wait" : ""}">${esc(statusLabel[item.status] || item.status)}</span></div>
        <p>${esc(item.access)}</p>
        <small>${esc(item.note)}</small>
        <div class="connector-channel"><span>授权通道</span>${esc(item.authorizationChannel || "—")}</div>
        <div class="connector-channel"><span>市场归属</span>${esc([item.salesMarket, item.supplyMarket].filter(Boolean).join(" / ") || "—")}</div>
      </article>`).join("");
    const title = { demand: "需求与内容侧", sales: "销售市场侧", sourcing: "货源侧（供给来源）" }[role];
    const hint = {
      demand: "用来判断需求是否真的出现：相对热度、内容热度与反复出现的抱怨。热度不等于成交。",
      sales: `用来验证市场：竞品数量、价格带、评价主题与市场空位。当前目标市场是${targetMarket}。`,
      sourcing: "用来寻找货源与成本：产业带、工厂线索与电商挂牌价。零售挂牌价不等于可复制的源头成本。",
    }[role];
    return `<section class="panel"><div class="panel-head"><div><h3>${title}</h3><small>${hint}</small></div><span class="connector-count">${items.length} 个平台</span></div><div class="connector-grid">${cards}</div></section>`;
  }).join("");
  const runs = state.discoveryRuns.slice(0, 20).map((run) => {
    const strategy = state.discoveryStrategies.find((item) => item.id === run.strategyId);
    const modeTitle = ({ automatic_selection: "自动选品", manual_selection: "手动选品", competitor_analysis: "手动竞品分析" })[run.mode];
    const status = run.status === "running" ? `运行中 · ${Number(run.progress || 0)}%` : run.status === "needs_human_review" ? "待审核" : run.status === "no_candidates" ? "无候选" : run.status === "failed" ? "失败" : run.status === "needs_product_match_review" ? "待核对同款" : run.status === "needs_cost_input" ? "待补费用" : run.status === "needs_manual_validation" ? "待人工复核" : "已完成";
    const matchBlock = run.trigger === "price_gap_pair" && run.matching ? `<div class="report-list"><b>同款校验：${esc(run.matching.status)}</b><ul>${(run.matching.reasons || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>${run.estimate ? `<p>按你填的汇率与费用估算：单件贡献利润 ${Number(run.estimate.estimatedContribution).toLocaleString("zh-CN")} ${esc(run.estimate.currency || "")}（${(Number(run.estimate.estimatedContributionMargin || 0) * 100).toFixed(1)}%）。</p>` : `<p>${esc(run.estimateError || "费用未填齐，未计算贡献利润。")}</p>`}</div>` : "";
    return `<article class="discovery-run"><div class="discovery-run-head"><div><span class="run-type">${esc(strategy ? `策略 ${strategy.code} · ${strategy.title}` : modeTitle || (run.trigger === "price_gap_pair" ? "同款价差研究" : "发现运行"))}</span><h3>${esc(run.title || "Agent 研究运行")}</h3></div><time>${fmtDate(run.createdAt)}</time></div><p class="run-signal-note">${esc(run.message || run.summary || "等待任务开始")}</p><div class="run-signal-metrics"><span>${status}</span><span>${Number(run.candidateCount || 0)} 个候选</span><span>${Number(run.sourceCount || 0)} 条搜索结果</span></div>${matchBlock}</article>`;
  }).join("");
  content.innerHTML = `
    <div class="page-kicker">Sources & agent runs</div>
    <div class="page-heading"><div><h1>Agent 搜索与数据源</h1><p>Agent 自动搜索公开网页；官方平台 API 在获得对应权限后再接入。</p></div><button class="button-quiet" data-action="home">返回 Agent 选品</button></div>
    <section class="agent-intro"><div><span class="agent-live-dot ${webSearch.configured ? "" : "offline"}"></span><b>${webSearch.configured ? "MiniMax Web Search 已连接" : "MiniMax Web Search 未配置"}</b><p>${esc(webSearch.message || "检查本机 MiniMax 配置。")}</p></div><span class="agent-model">${esc(state.llmProvider?.model || "MiniMax")}</span></section>
    <section class="source-boundary-grid"><article><b>现在可用</b><p>Agent 可按策略搜索 Google Trends、TikTok、抖音、快手、Amazon.co.jp、乐天、Yahoo! Shopping、1688、淘宝、京东、拼多多等公开页面线索，并附来源 URL 与采集时间。</p></article><article><b>仍需官方授权</b><p>趋势序列、平台热销榜、商品详情/实时价格、账号数据、直播与投放数据、MOQ 与阶梯报价等，需要各平台官方应用与对应接口权限。</p></article></section>
    ${roleGroups}
    <section class="panel"><div class="panel-head"><div><h3>Agent 选品运行记录</h3><small>每次扫描保留策略、运行状态、结果数量与时间</small></div></div><div class="discovery-run-list">${runs || `<div class="table-empty" style="padding:22px"><h3>暂无 Agent 运行</h3><p>回到工作台，从四大策略中启动一次自动选品。</p></div>`}</div></section>
    <div class="bottom-note"><span>ⓘ</span><span>仅 MiniMax Web Search 产生公开搜索摘要；系统不模拟登录、不绕过访问限制，也不会把摘要称作销量或真实成交价。</span></div>
  `;
}

function renderLeadLibrary(observations) {
  if (!observations.length) return "";
  const groups = new Map();
  for (const item of observations) {
    const key = `${item.platform || "未知平台"}|${(item.url || "").replace(/^https?:\/\/([^/]+).*$/, "$1")}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const rows = [...groups.entries()].map(([key, items]) => {
    const sorted = [...items].sort((a, b) => String(b.observedAt).localeCompare(String(a.observedAt)));
    const latest = sorted[0];
    const products = [...new Set(sorted.map((item) => item.product).filter(Boolean))];
    return `<tr><td><b>${esc(latest.platform || "未知平台")}</b><small>${esc(products.slice(0, 2).join("、"))}</small></td><td>${latest.price == null ? "—" : `${Number(latest.price).toLocaleString("zh-CN")} ${esc(latest.currency || "")}`}</td><td>${esc(latest.observedAt || "")}</td><td>${sorted.length} 次观察</td><td>${latest.url ? `<a href="${esc(latest.url)}" target="_blank" rel="noopener noreferrer">页面 ↗</a>` : "—"}</td></tr>`;
  }).join("");
  return `<section class="panel"><div class="panel-head"><h3>货源线索库</h3><small>比价时见过的页面与店铺，按最近一次报价排序</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台 / 商品</th><th>最近可见报价</th><th>观察日期</th><th>次数</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table></div><div class="bottom-note"><span>ⓘ</span><span>线索库只是“某个页面在某天显示过这个价”，不是供应商能力、MOQ 或可稳定拿货的证明。</span></div></section>`;
}

function renderRfqArchive(rfqs) {
  if (!rfqs.length) {
    return `<section class="panel"><div class="panel-head"><h3>询价单</h3><small>发给谁、问了什么、回了什么</small></div><div class="table-empty" style="padding:22px"><h3>还没有询价单</h3><p>在候选详情里点「生成询价单」，可以把同一商品同时问多家店铺。</p></div></section>`;
  }
  const rows = rfqs.map((rfq) => `<tr>
    <td><b>${esc(rfq.title || rfq.product || "")}</b><small>${esc(rfq.spec || "")}</small></td>
    <td>${(rfq.targets || []).length} 家</td>
    <td>${(rfq.quotes || []).length} 条回复</td>
    <td>${rfq.comparison?.replyRate == null ? "—" : `${Math.round(rfq.comparison.replyRate * 100)}%`}</td>
    <td>${esc(rfq.dueDate || "未设置")}</td>
    <td><span class="status-pill ${rfq.status === "已齐" ? "done" : "wait"}">${esc(rfq.status || "")}</span></td>
    <td>${esc(rfq.createdAt ? fmtDate(rfq.createdAt) : "")}</td>
  </tr>`).join("");
  return `<section class="panel"><div class="panel-head"><h3>询价单</h3><small>发给谁、问了什么、回了什么</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>商品 / 规格</th><th>目标店铺</th><th>收到回复</th><th>状态</th><th>创建日期</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function renderSupplierArchive(suppliers) {
  if (!suppliers.length) {
    return `<section class="panel"><div class="panel-head"><h3>货源档案</h3><small>标记过的店铺与跟进状态</small></div><div class="table-empty" style="padding:22px"><h3>还没有标记货源</h3><p>在采购比价表或货源线索库里点“标记货源”，就会在这里建立一份店铺档案。</p></div></section>`;
  }
  const completeness = (item) => {
    const history = item.quoteHistory || [];
    if (!history.length) return "—";
    const last = history[history.length - 1];
    const fields = ["unitPrice", "moq", "leadTimeDays", "invoice", "note"];
    const filled = fields.filter((key) => last[key] !== null && last[key] !== undefined && last[key] !== "").length;
    return `${Math.round((filled / fields.length) * 100)}%`;
  };
  const rows = suppliers.map((item) => `<tr>
    <td><b>${esc(item.platform || "")}</b><small>${esc(item.seller || "")}</small></td>
    <td>${esc((item.products || []).join("、") || "—")}</td>
    <td>${item.lastPrice == null ? "—" : `${Number(item.lastPrice).toLocaleString("zh-CN")} ${esc(item.lastCurrency || "")}`}<small>${esc(item.lastObservedAt || "")}</small></td>
    <td>${(item.quoteHistory || []).length} 条报价</td>
    <td>${completeness(item)}</td>
    <td><select data-supplier-status="${esc(item.id)}">${["待联系", "已联系", "已报价", "已排除"].map((status) => `<option ${item.status === status ? "selected" : ""}>${status}</option>`).join("")}</select></td>
    <td><input data-supplier-note="${esc(item.id)}" value="${esc(item.note || "")}" placeholder="备注：MOQ、报价、时效…" /></td>
    <td>${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">页面 ↗</a>` : "—"}</td>
  </tr>`).join("");
  return `<section class="panel"><div class="panel-head"><h3>货源档案</h3><small>只是店铺笔记，不是已验证的供应商</small></div><div class="competitor-table-wrap"><table class="competitor-table"><thead><tr><th>平台 / 店铺</th><th>涉及商品</th><th>最近可见价</th><th>状态</th><th>备注</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table></div><div class="bottom-note"><span>ⓘ</span><span>档案里的价格是页面观察值；MOQ、交期、发票与报价必须由你向卖家确认后写进备注。</span></div></section>`;
}

function showNewModal() {
  modalRoot.innerHTML = `
    <div class="modal-backdrop" data-action="backdrop-close"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="new-title">
      <div class="modal-head"><div><h2 id="new-title">指定商品研究</h2><p>这是补充入口。常规选品从首页四大策略启动，由 Agent 自主发现商品。</p></div><button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>
      <form id="create-form">
        <input type="hidden" name="mode" value="specified_product" />
        <div class="form-grid">
          <div class="field full"><label for="create-name">研究名称 / 商品名称</label><input id="create-name" name="name" placeholder="例如：可折叠的玄关收纳架" /></div>
          <div class="field"><label for="create-category">研究品类</label><select id="create-category" name="category">${state.categories.map((item) => `<option>${esc(item)}</option>`).join("")}</select></div>
          <div class="field"><label for="create-market">目标市场</label><select id="create-market" name="market">${marketOptions(state.profile?.market || "日本")}</select></div>
          <div class="field"><label for="create-platform">优先平台</label><input id="create-platform" name="platform" value="${esc(state.profile?.platform || "Amazon.co.jp")}" /></div>
          <div class="field"><label for="create-fulfillment">履约方式</label><input id="create-fulfillment" name="fulfillment" value="日本 FBA" /></div>
          <div class="field full"><label for="create-hypothesis">需求或研究假设</label><textarea id="create-hypothesis" name="hypothesis" placeholder="目标人群、遇到的问题、想验证的差异化或商品链接。"></textarea></div>
        </div>
        <div class="modal-note">Agent 会依据当前商品和已有证据生成待复核研究草稿。要让 Agent 自主找品，请回首页选择痛点、审美、长尾或供应链策略。</div>
        <div class="form-actions"><button type="button" class="button-secondary" data-action="close-modal">取消</button><button type="submit" class="button-primary">创建研究任务</button></div>
      </form>
    </section></div>
  `;
}

function showEditModal(candidate) {
  modalRoot.innerHTML = `
    <div class="modal-backdrop" data-action="backdrop-close"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="edit-title">
      <div class="modal-head"><div><h2 id="edit-title">编辑研究条件</h2><p>修改研究范围或假设；已有来源和评分会保留。</p></div><button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>
      <form id="edit-form"><div class="form-grid">
        <div class="field full"><label for="edit-name">研究名称</label><input id="edit-name" name="name" value="${esc(candidate.name)}" required /></div>
        <div class="field"><label for="edit-category">研究品类</label><select id="edit-category" name="category">${state.categories.map((item) => `<option ${candidate.category === item ? "selected" : ""}>${esc(item)}</option>`).join("")}</select></div>
        <div class="field"><label for="edit-market">目标市场</label><input id="edit-market" name="market" value="${esc(candidate.market)}" /></div>
        <div class="field"><label for="edit-platform">销售平台</label><input id="edit-platform" name="platform" value="${esc(candidate.platform)}" /></div>
        <div class="field"><label for="edit-fulfillment">履约方式</label><input id="edit-fulfillment" name="fulfillment" value="${esc(candidate.fulfillment)}" /></div>
        <div class="field full"><label for="edit-hypothesis">需求或研究假设</label><textarea id="edit-hypothesis" name="hypothesis">${esc(candidate.hypothesis)}</textarea></div>
      </div><div class="form-actions"><button type="button" class="button-secondary" data-action="close-modal">取消</button><button type="submit" class="button-primary">保存修改</button></div></form>
    </section></div>
  `;
}

function showReviewModal(candidate, decision) {
  const labels = { continue: "继续研究", hold: "暂缓", reject: "淘汰" };
  const hints = {
    continue: "记录一条继续理由，Agent 会在下一轮补充这个方向的证据。",
    hold: "写清楚缺什么，候选会留在待办里等你补数据。",
    reject: "淘汰需要写明原因；原因会进入评估记录，用来改进下一轮发现。",
  };
  const reasonChips = {
    continue: ["来源足够，继续核验货源", "需求信号明确，补竞品数据", "市场空位成立，进入利润核算"],
    hold: ["缺正式报价", "需求证据不足", "规格不匹配，需重新选品"],
    reject: ["红海标品，无差异化", "需求是伪趋势", "资质/合规风险高", "成本结构不成立"],
  }[decision] || [];
  modalRoot.innerHTML = `
    <div class="modal-backdrop" data-action="backdrop-close"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="review-title">
      <div class="modal-head"><div><h2 id="review-title">${esc(labels[decision])}：${esc(candidate.name)}</h2><p>${esc(hints[decision])}</p></div><button type="button" class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div>
      <form id="review-form" data-id="${esc(candidate.id)}" data-decision="${esc(decision)}">
        <div class="field"><label for="review-reason">审核理由${decision === "reject" ? "（必填）" : "（可选）"}</label>
          <textarea id="review-reason" name="reason" rows="3" placeholder="例如：需求只在短视频里出现，缺少日本站同类商品的持续销量证据。" ${decision === "reject" ? "required" : ""}></textarea></div>
        <div class="sugg-chips" style="margin-top:10px">${reasonChips.map((reason) => `<button type="button" data-action="review-reason-chip" data-reason="${esc(reason)}">${esc(reason)}</button>`).join("")}</div>
        <div class="modal-note">审核结果只写入本地记录，不会下单、付款或联系供应商。</div>
        <div class="form-actions"><button type="button" class="button-secondary" data-action="close-modal">取消</button><button type="submit" class="button-primary">确认${esc(labels[decision])}</button></div>
      </form>
    </section></div>
  `;
}

function closeModal() { modalRoot.innerHTML = ""; }

function currentCandidate() { return state.candidates.find((item) => item.id === state.selectedId); }

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  try {
    if (action === "backdrop-close" && event.target !== button) return;
    if (action === "home") { state.view = "home"; state.selectedId = null; render(); return; }
    if (action === "chat-mode") { state.view = "home"; state.chatMode = button.dataset.mode; state.currentConversationId = null; state.showNewConversation = true; render(); return; }
    if (action === "new-chat") { state.view = "home"; state.currentConversationId = null; state.showNewConversation = true; render(); return; }
    if (action === "open-conversation") {
      const conversation = state.conversations.find((item) => item.id === button.dataset.id);
      if (conversation) {
        state.view = "home"; state.currentConversationId = conversation.id; state.chatMode = conversation.mode || "automatic_selection"; state.showNewConversation = false;
        const latestRun = state.discoveryRuns.find((run) => run.conversationId === conversation.id);
        if (latestRun?.strategyId && state.chatMode === "automatic_selection") state.chatStrategyId = latestRun.strategyId;
        render();
      }
      return;
    }
    if (action === "select-chat-strategy") {
      state.chatStrategyId = button.dataset.strategy;
      if (state.chatMode !== "automatic_selection") {
        state.chatMode = "automatic_selection";
        state.currentConversationId = null;
        state.showNewConversation = true;
      }
      render();
      return;
    }
    if (action === "use-suggestion") {
      const textarea = document.querySelector("#chat-form textarea[name='message']");
      if (textarea) {
        textarea.value = button.dataset.text || "";
        textarea.focus();
      }
      return;
    }
    if (action === "discovery") { state.view = "discovery"; state.selectedId = null; render(); return; }
    if (action === "board") { state.view = "board"; state.selectedId = null; render(); return; }
    if (action === "metrics") { state.view = "metrics"; state.selectedId = null; render(); return; }
    if (action === "new") { showNewModal(); return; }
    if (action === "edit-candidate") { const candidate = currentCandidate(); if (candidate) showEditModal(candidate); return; }
    if (action === "close-modal" || action === "backdrop-close") { closeModal(); return; }
    if (action === "profile") { state.view = "profile"; render(); return; }
    if (action === "create-rfq") {
      const candidate = state.candidates.find((item) => item.id === button.dataset.id);
      if (!candidate) return;
      const pairing = candidate.priceGap || {};
      const observations = (state.priceObservations || []).filter((item) => item.product === pairing.product);
      const targets = [];
      const seen = new Set();
      for (const item of observations) {
        const seller = (item.url || "").replace(/^https?:\/\/([^/]+).*$/, "$1");
        const key = `${item.platform}|${seller}`;
        if (!seller || seen.has(key)) continue;
        seen.add(key);
        targets.push({ platform: item.platform, seller, url: item.url, price: item.price, currency: item.currency });
      }
      if (!targets.length) {
        toast("还没有这个商品的店铺记录，先跑一次比价或标记货源。", true);
        return;
      }
      const questions = (candidate.purchaseChecklist || []).filter((item) => item.id !== "price_evidence").map((item) => item.item);
      const dueDate = window.prompt("回复截止日期（YYYY-MM-DD，可留空）", "") || "";
      const created = await api("/api/rfqs", {
        method: "POST",
        body: JSON.stringify({
          candidateId: candidate.id,
          product: pairing.product,
          spec: pairing.sourceVariant || "",
          market: candidate.market,
          currency: pairing.sourceCurrency || "",
          targets,
          questions,
        }),
      });
      await refresh();
      state.view = "detail";
      render();
      toast(`询价单已创建，含 ${created.targets.length} 家店铺。`);
      return;
    }
    if (action === "apply-quote") {
      const body = {
        platform: button.dataset.platform,
        seller: button.dataset.seller,
        applyToCandidate: true,
      };
      const rfq = (state.rfqs || []).find((item) => item.id === button.dataset.rfq);
      const quote = (rfq?.quotes || []).find((item) => item.platform === button.dataset.platform && item.seller === button.dataset.seller);
      if (!quote) { toast("这条报价还没有记录。", true); return; }
      body.unitPrice = quote.unitPrice;
      body.moq = quote.moq;
      body.leadTimeDays = quote.leadTimeDays;
      body.currency = quote.currency;
      body.note = quote.note;
      await api(`/api/rfqs/${encodeURIComponent(button.dataset.rfq)}/quotes`, { method: "POST", body: JSON.stringify(body) });
      await refresh();
      state.view = "detail";
      render();
      toast("已把书面报价回填为采购价，并记入来源证据。");
      return;
    }
    if (action === "record-quote") {
      const rfqId = button.dataset.rfq;
      const platform = button.dataset.platform;
      const seller = button.dataset.seller;
      const unitPrice = window.prompt(`${platform} · ${seller} 的书面单价（留空表示未报价）`, "");
      if (unitPrice === null) return;
      const moq = window.prompt("起订量（MOQ）", "");
      if (moq === null) return;
      const leadTime = window.prompt("交期（天）", "");
      if (leadTime === null) return;
      const note = window.prompt("备注（发票、样品、付款方式等）", "") || "";
      await api(`/api/rfqs/${encodeURIComponent(rfqId)}/quotes`, {
        method: "POST",
        body: JSON.stringify({
          platform,
          seller,
          unitPrice: unitPrice === "" ? null : Number(unitPrice),
          moq: moq === "" ? null : Number(moq),
          leadTimeDays: leadTime === "" ? null : Number(leadTime),
          currency: state.profile?.market === "中国" ? "CNY" : "JPY",
          note,
        }),
      });
      await refresh();
      state.view = "detail";
      render();
      toast("报价已记录，并同步到货源档案。");
      return;
    }
    if (action === "create-po") {
      const rfq = (state.rfqs || []).find((item) => item.id === button.dataset.rfq);
      const quote = (rfq?.quotes || []).find((item) => item.platform === button.dataset.platform && item.seller === button.dataset.seller);
      if (!quote) { toast("没有找到这家店的书面报价。", true); return; }
      const quantity = window.prompt(`采购数量（建议不小于 MOQ ${quote.moq ?? "—"}）`, String(quote.moq ?? 50));
      if (quantity === null) return;
      const payment = window.prompt("付款方式 / 账期（可留空）", "") || "";
      const note = window.prompt("备注（验货要求、到货地址等，可留空）", "") || "";
      const created = await api("/api/purchase-orders", {
        method: "POST",
        body: JSON.stringify({
          candidateId: rfq.candidateId || "",
          rfqId: rfq.id,
          platform: quote.platform,
          seller: quote.seller,
          product: rfq.product,
          spec: rfq.spec,
          quantity: Number(quantity),
          unitPrice: quote.unitPrice,
          currency: quote.currency,
          leadTimeDays: quote.leadTimeDays,
          paymentTerms: payment,
          note,
        }),
      });
      await refresh();
      state.view = "detail";
      render();
      toast(`采购单草案已生成：${Number(created.quantity)} 件 · ${Number(created.amount).toLocaleString("zh-CN")} ${created.currency}。`);
      return;
    }
    if (action === "mark-rfq-sent") {
      await api(`/api/rfqs/${encodeURIComponent(button.dataset.rfq)}`, { method: "PATCH", body: JSON.stringify({ sent: true }) });
      await refresh();
      state.view = "detail";
      render();
      toast("已标记为发送；后续录回的报价会继续累计。");
      return;
    }
    if (action === "export-rfq-batch") {
      const rfq = (state.rfqs || []).find((item) => item.id === button.dataset.rfq);
      if (!rfq) { toast("没有找到这个询价单。", true); return; }
      const blob = new Blob([rfqBatchMarkdown(rfq)], { type: "text/markdown;charset=utf-8" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${(rfq.title || "rfq").replace(/[\\/:*?"<>|]/g, "-")}-询价单.md`;
      link.click();
      URL.revokeObjectURL(link.href);
      toast("询价单已导出（每家店铺一节，可分开发送）。");
      return;
    }
    if (action === "download-rfq") {
      const candidate = currentCandidate();
      if (!candidate) return;
      const blob = new Blob([rfqMarkdown(candidate)], { type: "text/markdown;charset=utf-8" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${(candidate.name || "rfq").replace(/[\\/:*?"<>|]/g, "-")}-询价单.md`;
      link.click();
      URL.revokeObjectURL(link.href);
      toast("询价单已导出；发给卖家前请自行核对规格与数量。");
      return;
    }
    if (action === "apply-calibration") {
      const candidate = currentCandidate();
      if (!candidate) return;
      const assumptions = { ...(candidate.assumptions || {}) };
      const value = Number(button.dataset.value);
      if (button.dataset.field === "returnReserveRate") assumptions.returnReserveRate = value;
      else if (button.dataset.field === "adCost") assumptions.adCost = value;
      await api(`/api/candidates/${encodeURIComponent(candidate.id)}`, { method: "PATCH", body: JSON.stringify({ assumptions }) });
      await refresh();
      state.view = "detail";
      render();
      toast("利润假设已按实测值更新。");
      return;
    }
    if (action === "mark-supplier") {
      const created = await api("/api/suppliers", {
        method: "POST",
        body: JSON.stringify({
          platform: button.dataset.platform,
          seller: button.dataset.seller,
          url: button.dataset.url,
          price: button.dataset.price === "" ? null : Number(button.dataset.price),
          currency: button.dataset.currency,
          product: button.dataset.product,
        }),
      });
      await refresh();
      render();
      toast(`已把 ${created.platform} · ${created.seller} 加入货源档案。`);
      return;
    }
    if (action === "scan-price-gap") {
      const candidate = state.candidates.find((item) => item.id === button.dataset.id);
      if (!candidate) return;
      const product = candidate.priceGap?.product || candidate.name;
      const response = await api("/api/agent/chat", {
        method: "POST",
        body: JSON.stringify({
          mode: "price_gap",
          message: product,
          market: candidate.market || state.profile.market,
          sourcingCountry: candidate.sourcingCountry || state.profile.sourcingCountry,
          conversationId: "",
        }),
      });
      state.view = "home";
      state.currentConversationId = response.conversationId;
      state.showNewConversation = false;
      state.chatMode = "price_gap";
      await refresh();
      toast(`正在扫描 ${product} 的跨平台报价。`);
      watchAgentDiscovery(response.run.id);
      return;
    }
    if (action === "price-gap-pair") {
      const created = await api("/api/discovery", { method: "POST", body: JSON.stringify({ trigger: "price_gap_pair", candidateId: button.dataset.id }) });
      await refresh();
      state.view = "discovery";
      render();
      const matching = created.matching || {};
      if (matching.status === "exact_match" || matching.status === "probable_match") {
        toast(created.estimate ? "规格匹配通过，贡献利润已按你填的假设估算。" : "规格匹配通过，但费用未填齐，请补齐汇率与成本后重算。");
      } else {
        toast("两侧规格/包装不一致，按规则不给出价差结论，请先核对商品页。");
      }
      return;
    }
    if (action === "open") { state.selectedId = button.dataset.id; state.view = "detail"; state.tab = "overview"; render(); return; }
    if (action === "review-candidate") {
      const candidate = state.candidates.find((item) => item.id === button.dataset.id);
      if (candidate) showReviewModal(candidate, button.dataset.decision);
      return;
    }
    if (action === "review-reason-chip") {
      const textarea = document.querySelector("#review-form textarea[name='reason']");
      if (textarea) { textarea.value = button.dataset.reason || ""; textarea.focus(); }
      return;
    }
    if (action === "filter") { state.filter = button.dataset.filter; renderHome(); return; }
    if (action === "tab") { state.tab = button.dataset.tab; renderDetail(); return; }
    if (action === "evidence-tab") { state.tab = "evidence"; renderDetail(); const select = document.querySelector("#ev-dimension"); if (select) select.value = button.dataset.dim || "demand"; return; }
    if (action === "run-agent-discovery") {
      button.disabled = true;
      button.textContent = "正在启动 Agent…";
      const run = await api("/api/agent/discovery", { method: "POST", body: JSON.stringify({ strategyId: button.dataset.strategy }) });
      state.view = "home";
      await refresh();
      toast("Agent 已开始搜索趋势、市场与货源；完成后会通知你审核候选。", false);
      watchAgentDiscovery(run.id);
      return;
    }
    if (action === "run-agent") {
      const taskId = button.dataset.taskId;
      button.disabled = true;
      button.textContent = "分析中…";
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}/tasks/${encodeURIComponent(taskId)}/run`, { method: "POST", body: "{}" });
      await refresh(); state.view = "detail"; state.tab = "tasks"; render(); toast("MiniMax 分析已保存为待复核草稿。"); return;
    }
    if (action === "save-score") {
      const key = button.dataset.score;
      const value = document.querySelector(`#score-value-${CSS.escape(key)}`)?.value;
      const rationale = document.querySelector(`#score-rationale-${CSS.escape(key)}`)?.value.trim();
      const evidenceIds = [...(document.querySelector(`#score-evidence-${CSS.escape(key)}`)?.selectedOptions || [])].map((option) => option.value);
      if (!value || !rationale || !evidenceIds.length) { toast("请选择分数、关联来源并填写判断依据。", true); return; }
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}/scores`, { method: "POST", body: JSON.stringify({ key, value, rationale, evidenceIds }) });
      await refresh(); state.view = "detail"; toast("独立判断已保存；系统不会合并成总分。"); return;
    }
    if (action === "promote-run") {
      const created = await api(`/api/discovery/${encodeURIComponent(button.dataset.id)}/candidate`, { method: "POST", body: "{}" });
      await refresh(); state.selectedId = created.id; state.view = "detail"; state.tab = "overview"; render(); toast("机会已转入研究池，原始来源已关联。"); return;
    }
    if (action === "archive") {
      if (!window.confirm("将这项研究移入归档？原型会保留数据。")) return;
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}`, { method: "DELETE" });
      state.view = "home"; state.selectedId = null; await refresh(); toast("研究已归档。"); return;
    }
    if (action === "mark-ready") {
      const candidate = currentCandidate();
      if (!candidate?.scoreStatus?.complete) { toast("四项独立判断都需要先关联来源并保存。", true); return; }
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}`, { method: "PATCH", body: JSON.stringify({ status: "ready_for_test" }) });
      await refresh(); state.view = "detail"; toast("已标记为可进入小测，请继续人工复核报告。"); return;
    }
    if (action === "pause" || action === "resume") {
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}`, { method: "PATCH", body: JSON.stringify({ status: action === "pause" ? "paused" : "researching" }) });
      await refresh(); state.view = "detail"; toast(action === "pause" ? "研究已暂停。" : "研究已恢复。"); return;
    }
    if (action === "download-pricegap") {
      const run = (state.discoveryRuns || []).find((item) => item.id === button.dataset.id);
      if (!run || !run.report) { toast("这份比价结果已经不在本地记录里。", true); return; }
      const blob = new Blob([priceGapMarkdown(run)], { type: "text/markdown;charset=utf-8" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${(run.report.scope || "price-gap").replace(/[\\/:*?"<>|]/g, "-")}.md`;
      link.click();
      URL.revokeObjectURL(link.href);
      toast("比价报告已导出；价格仍需点开来源核实。");
      return;
    }
    if (action === "download-report") {
      const candidate = currentCandidate();
      const blob = new Blob([reportMarkdown(candidate)], { type: "text/markdown;charset=utf-8" });
      const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${(candidate.name || "research").replace(/[\\/:*?"<>|]/g, "-")}-research.md`; link.click(); URL.revokeObjectURL(link.href); return;
    }
  } catch (error) {
    if (action === "run-agent" && button.isConnected) {
      button.disabled = false;
      button.textContent = "MiniMax 分析";
    }
    if (action === "run-agent-discovery" && button.isConnected) {
      button.disabled = false;
      button.textContent = "让 Agent 按此策略找品";
    }
    toast(error.message, true);
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.matches("form")) return;
  event.preventDefault();
  const data = new FormData(form);
  try {
    if (form.id === "chat-form") {
      const message = String(data.get("message") || "").trim();
      if (state.chatMode !== "automatic_selection" && !message) { toast("先描述商品、研究范围或竞品对象。", true); return; }
      const strategyId = state.chatStrategyId || state.discoveryStrategies[0]?.id;
      if (state.chatMode === "automatic_selection" && !strategyId) { toast("暂时没有可用的选品策略。", true); return; }
      const response = await api("/api/agent/chat", {
        method: "POST",
        body: JSON.stringify({
          mode: state.chatMode,
          strategyId,
          message,
          market: data.get("market"),
          platform: data.get("platform"),
          platformIds: data.getAll("platformIds").map(String),
          sourcingCountry: data.get("sourcingCountry") || undefined,
          conversationId: state.showNewConversation ? "" : state.currentConversationId || "",
        }),
      });
      state.view = "home";
      state.currentConversationId = response.conversationId;
      state.showNewConversation = false;
      await refresh();
      toast("Agent 已接收任务，正在搜索并整理结果。");
      watchAgentDiscovery(response.run.id);
      return;
    }
    if (form.id === "create-form") {
      const payload = Object.fromEntries(data.entries());
      payload.mode = data.get("mode") || "specified_product";
      if (payload.mode === "discovery" && !payload.name) payload.name = `待发现机会：${payload.category}`;
      const created = await api("/api/candidates", { method: "POST", body: JSON.stringify(payload) });
      closeModal(); await refresh(); state.selectedId = created.id; state.view = "detail"; state.tab = "overview"; render(); toast("研究任务已创建，证据收集尚未开始。"); return;
    }
    if (form.id === "tiktok-form") {
      const raw = Object.fromEntries(data.entries());
      const metrics = {};
      if (raw.views) metrics.views = Number(raw.views);
      if (raw.engagement) metrics.engagement = Number(raw.engagement);
      const signal = {
        ...raw,
        metrics,
        source: "TikTok / 用户记录",
      };
      delete signal.views; delete signal.engagement;
      const created = await api("/api/discovery", { method: "POST", body: JSON.stringify({ trigger: "tiktok_trend", signal }) });
      await refresh(); state.view = "discovery"; render(); toast(`趋势线索已保存：${created.title}`); return;
    }
    if (form.id === "price-gap-form") {
      const raw = Object.fromEntries(data.entries());
      const listing = (side) => ({
        platform: raw[`${side}Platform`],
        market: side === "source" ? "中国" : "日本",
        title: raw[`${side}Title`],
        url: raw[`${side}Url`],
        price: Number(raw[`${side}Price`]),
        currency: side === "source" ? "CNY" : "JPY",
        observedAt: raw[`${side}ObservedAt`],
        brand: raw[`${side}Brand`],
        model: raw[`${side}Model`],
        variant: raw[`${side}Variant`],
        packCount: raw[`${side}PackCount`],
        identifiers: raw[`${side}Gtin`] ? { gtin: raw[`${side}Gtin`] } : {},
      });
      const assumptions = {
        sourceCurrencyToJpy: Number(raw.sourceCurrencyToJpy),
        fxObservedAt: raw.fxObservedAt,
        inboundJpy: Number(raw.inboundJpy || 0),
        dutyTaxJpy: Number(raw.dutyTaxJpy || 0),
        fulfillmentJpy: Number(raw.fulfillmentJpy || 0),
        referralRate: Number(raw.referralRate || 0) / 100,
        adCostJpy: Number(raw.adCostJpy || 0),
        returnReserveRate: Number(raw.returnReserveRate || 0) / 100,
        otherCostJpy: Number(raw.otherCostJpy || 0),
      };
      const created = await api("/api/discovery", { method: "POST", body: JSON.stringify({ trigger: "cross_market_price_gap", sourceListing: listing("source"), targetListing: listing("target"), assumptions }) });
      await refresh(); state.view = "discovery"; render(); toast(created.estimate ? "同品匹配完成，价差与单件贡献利润已估算。" : "商品匹配证据不足，已保存供人工复核。"); return;
    }
    if (form.id === "edit-form") {
      const payload = Object.fromEntries(data.entries());
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}`, { method: "PATCH", body: JSON.stringify(payload) });
      closeModal(); await refresh(); state.view = "detail"; render(); toast("研究条件已更新。"); return;
    }
    if (form.id === "review-form") {
      const decision = form.dataset.decision;
      const reason = String(data.get("reason") || "").trim();
      if (decision === "reject" && !reason) { toast("淘汰需要写明原因。", true); return; }
      const labels = { continue: "继续研究", hold: "暂缓", reject: "淘汰" };
      await api(`/api/candidates/${encodeURIComponent(form.dataset.id)}/review`, { method: "POST", body: JSON.stringify({ decision, reason }) });
      closeModal();
      await refresh();
      toast(`已记录人工审核：${labels[decision]}`);
      return;
    }
    if (form.id === "evidence-form") {
      const payload = Object.fromEntries(data.entries());
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}/evidence`, { method: "POST", body: JSON.stringify(payload) });
      await refresh(); state.view = "detail"; state.tab = "evidence"; render(); toast("来源已保存；其真实性仍需人工复核。"); return;
    }
    if (form.id === "profit-form") {
      const assumptions = {};
      for (const [key] of unifiedProfitFields) {
        const raw = String(data.get(key) || "").trim();
        if (raw === "") continue;
        if (currencyProfitFields.has(key)) assumptions[key] = raw.toUpperCase();
        else if (key.endsWith("Source")) assumptions[key] = raw;
        else assumptions[key] = percentProfitFields.has(key) ? Number(raw) / 100 : Number(raw);
      }
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}`, { method: "PATCH", body: JSON.stringify({ assumptions }) });
      await refresh(); state.view = "detail"; state.tab = "profit"; render(); toast("利润假设已更新。"); return;
    }
    if (form.id === "pilot-form") {
      const pilot = {};
      for (const key of ["windowDays", "spend", "clicks", "orders", "refunds", "revenue", "netProfit"]) {
        const raw = String(data.get(key) || "").trim();
        if (raw !== "") pilot[key] = Number(raw);
      }
      await api(`/api/candidates/${encodeURIComponent(state.selectedId)}`, { method: "PATCH", body: JSON.stringify({ pilot }) });
      await refresh();
      state.view = "detail";
      render();
      toast("小测数据已保存，结论按你设的门槛判定。");
      return;
    }
    if (form.id === "profile-form") {
      const profile = Object.fromEntries(data.entries());
      profile.budgetCny = profile.budgetCny === "" ? null : Number(profile.budgetCny);
      profile.avoidRejected = data.get("avoidRejected") === "on";
      const pilotThresholds = {};
      const minOrders = String(data.get("pilotMinOrders") || "").trim();
      const maxRefund = String(data.get("pilotMaxRefundRate") || "").trim();
      const minMargin = String(data.get("pilotMinNetMargin") || "").trim();
      pilotThresholds.minOrders = minOrders === "" ? "" : Number(minOrders);
      pilotThresholds.maxRefundRate = maxRefund === "" ? "" : Number(maxRefund) / 100;
      pilotThresholds.minNetMargin = minMargin === "" ? "" : Number(minMargin) / 100;
      profile.pilotThresholds = pilotThresholds;
      profile.sourcingPlatforms = data.getAll("sourcingPlatforms").map(String);
      const gapAssumptions = {};
      const referral = String(data.get("priceGapReferralRate") || "").trim();
      const adCost = String(data.get("priceGapAdCost") || "").trim();
      const fulfillment = String(data.get("priceGapFulfillmentCost") || "").trim();
      const returnRate = String(data.get("priceGapReturnReserveRate") || "").trim();
      gapAssumptions.referralRate = referral === "" ? "" : Number(referral) / 100;
      gapAssumptions.adCost = adCost === "" ? "" : Number(adCost);
      gapAssumptions.fulfillmentCost = fulfillment === "" ? "" : Number(fulfillment);
      gapAssumptions.returnReserveRate = returnRate === "" ? "" : Number(returnRate) / 100;
      const byPlatform = {};
      for (const [key, value] of data.entries()) {
        if (!key.startsWith("priceGapCommission_")) continue;
        const platformId = key.replace("priceGapCommission_", "");
        const raw = String(value).trim();
        if (raw === "") continue;
        byPlatform[platformId] = { referralRate: Number(raw) / 100 };
      }
      gapAssumptions.byPlatform = byPlatform;
      profile.priceGapAssumptions = gapAssumptions;
      await api("/api/profile", { method: "PATCH", body: JSON.stringify(profile) });
      await refresh(); state.view = "profile"; render(); toast("经营画像已保存；基础机会分不变。"); return;
    }
  } catch (error) { toast(error.message, true); }
});

document.addEventListener("change", async (event) => {
  const target = event.target;
  if (target.dataset && target.dataset.check === "checklist") {
    const candidate = currentCandidate();
    if (!candidate) return;
    const updates = (candidate.purchaseChecklist || []).map((item) => ({
      id: item.id,
      done: item.id === target.dataset.item ? target.checked : !!item.done,
    }));
    try {
      await api(`/api/candidates/${encodeURIComponent(candidate.id)}`, { method: "PATCH", body: JSON.stringify({ purchaseChecklist: updates }) });
      await refresh();
      state.view = "detail";
      render();
      toast("采购待办已更新，六步流程状态同步推进。");
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  if (target.dataset && target.dataset.poStatus) {
    try {
      await api(`/api/purchase-orders/${encodeURIComponent(target.dataset.poStatus)}`, { method: "PATCH", body: JSON.stringify({ status: target.value }) });
      await refresh();
      state.view = "detail";
      render();
      toast("采购单状态已记录。");
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  if (target.dataset && target.dataset.supplierNote) {
    try {
      await api(`/api/suppliers/${encodeURIComponent(target.dataset.supplierNote)}`, { method: "PATCH", body: JSON.stringify({ note: target.value }) });
      await refresh();
      render();
      toast("货源备注已保存。");
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  if (!(target instanceof HTMLSelectElement)) return;
  if (target.dataset.change === "chat-market") {
    state.chatMarket = target.value;
    state.chatPlatform = "";
    renderHome();
    return;
  }
  if (target.dataset.supplierStatus) {
    try {
      await api(`/api/suppliers/${encodeURIComponent(target.dataset.supplierStatus)}`, { method: "PATCH", body: JSON.stringify({ status: target.value }) });
      await refresh();
      render();
      toast("货源状态已更新。");
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  if (target.dataset.change === "board-market") {
    state.boardMarket = target.value;
    renderBoard();
    return;
  }
  if (target.dataset.change === "profile-market") {
    // Preview the platform list of the new market without saving yet.
    state.profile = { ...state.profile, market: target.value };
    renderProfile();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && modalRoot.innerHTML) closeModal();
  const target = event.target.closest?.(".candidate-row");
  if (target && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault(); state.selectedId = target.dataset.id; state.view = "detail"; state.tab = "overview"; render();
  }
});

refresh();

