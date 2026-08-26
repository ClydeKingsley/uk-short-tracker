(() => {
  "use strict";

  /**
   * Short Tracker frontend API contract (canonical payloads)
   * --------------------------------------------------------
   * GET /api/status
   *   -> { ok, status?, last_sync_at?, security_count?, default_security_id?,
   *        sources?: { short?: { name?, latest_date? }, price?: { name?, latest_date? } } }
   *
   * GET /api/securities?q=<text>
   *   -> { items: [{ id, name, ticker?, isin?, market?, price_symbol? }] }
   *
   * GET /api/rankings/current?page_size=2000
   *   -> { items: [{ rank, security_id, name, isin?, ticker?, price_symbol?,
   *                   short_percent?|aggregate_percent?|aggregate_bp?, position_date, age_days? }],
   *        total, as_of_date?, source?, fetched_at_utc?|last_sync_at?, methodology?, coverage? }
   *
   * GET /api/security/{id}
   *   -> { security: { id, name, ticker?, isin?, market?, price_symbol?, currency? },
   *        last_sync_at?, sources? }
   *
   * GET /api/security/{id}/short-series
   *   -> { items: [{ date, legacy_percent: number|null, ansp_percent: number|null }],
   *        legacy?: [{ date, value }],
   *        ansp?: [{ date, value, position_date?, interval_end?: date|null,
   *                  is_current?: boolean, chart_date_basis?, first_published_on? }],
   *        source?: { name? }, latest_date? }
   *      A normalized alternative [{ date, value, regime: "legacy"|"ansp" }] is accepted.
   *
   * GET /api/security/{id}/prices
   *   -> { items: [{ date, close }], symbol?, currency?: "GBP"|"GBp"|..., source?: { name? },
   *        latest_date? }
   *
   * GET /api/price-search?q=<text>
   *   -> { items: [{ symbol, name, exchange?, currency? }] }
   *
   * POST /api/security/{id}/price-symbol  JSON { symbol }
   *   -> { ok, security? }
   *
   * POST /api/sync
   *   -> { ok, status?: "complete"|"queued"|"in_progress", last_sync_at? }
   *
   * GET /api/settings
   * PUT /api/settings  JSON { auto_sync: { enabled, interval_hours } }
   *
   * GET /api/update/status
   * POST /api/update/check  JSON { force }
   *
   * The normalizers below also accept common wrappers (`data`, `results`, `series`,
   * `securities`, `prices`, `positions`) and common field aliases. Dates should be ISO-8601.
   */
  const API_CONTRACT = Object.freeze({
    status: "GET /api/status",
    securities: "GET /api/securities?q=",
    rankings: "GET /api/rankings/current?page_size=2000",
    security: "GET /api/security/{id}",
    shortSeries: "GET /api/security/{id}/short-series",
    prices: "GET /api/security/{id}/prices",
    priceSearch: "GET /api/price-search?q=",
    priceSymbol: "POST /api/security/{id}/price-symbol",
    sync: "POST /api/sync",
    settings: "GET|PUT /api/settings",
    updateStatus: "GET /api/update/status",
    updateCheck: "POST /api/update/check",
  });
  window.SHORT_TRACKER_API_CONTRACT = API_CONTRACT;

  const REGIME_SWITCH = Date.UTC(2026, 6, 13);
  const DAY = 86_400_000;
  const DEFAULT_RANGE = "1Y";
  const API_ROOT = "/api";
  const LANGUAGE_STORAGE_KEY = "short-tracker-language";
  const DISMISSED_UPDATE_STORAGE_KEY = "short-tracker-dismissed-update";
  const STATUS_POLL_INTERVAL = 30_000;
  const SUPPORTED_LANGUAGES = new Set(["zh-CN", "en-GB"]);
  const COPY = window.SHORT_TRACKER_COPY || {};

  function readStoredLanguage() {
    try {
      const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
      return SUPPORTED_LANGUAGES.has(stored) ? stored : "zh-CN";
    } catch {
      return "zh-CN";
    }
  }

  class ApiError extends Error {
    constructor(message, status = 0, payload = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  const ids = [
    "languageSwitch",
    "securitySearch",
    "securitySearchInput",
    "securitySearchResults",
    "searchSpinner",
    "searchClear",
    "connectionStatus",
    "connectionLabel",
    "connectionBanner",
    "connectionMessage",
    "retryStatusButton",
    "syncButton",
    "settingsButton",
    "updateBanner",
    "updateBannerTitle",
    "updateBannerDetail",
    "openReleaseButton",
    "dismissUpdateButton",
    "securityEmpty",
    "rankingsView",
    "rankingsLoading",
    "rankingsError",
    "rankingsErrorMessage",
    "retryRankingsButton",
    "rankingsEmpty",
    "rankingsContent",
    "rankingSnapshotHeadline",
    "rankingSnapshotOrb",
    "rankingAsOfDate",
    "rankingSnapshotUpdated",
    "rankingSource",
    "rankingCoverage",
    "rankingTotalBadge",
    "rankingsBarChart",
    "rankingAxisMax",
    "rankingSearchInput",
    "rankingPageSize",
    "rankingTable",
    "rankingTableBody",
    "rankingTableSummary",
    "rankingPreviousPage",
    "rankingNextPage",
    "rankingPageStatus",
    "rankingPageNumber",
    "backToRankingsButton",
    "securityWorkspace",
    "focusSearchButton",
    "securityMarket",
    "currentRegimeBadge",
    "securityName",
    "securityTicker",
    "securityIsin",
    "securityPriceSymbol",
    "freshnessHeadline",
    "freshnessOrb",
    "shortSourceChip",
    "priceSourceChip",
    "lastUpdatedText",
    "openPriceDialogButton",
    "kpiShort",
    "kpiShortMeta",
    "kpiChange",
    "kpiChangeMeta",
    "kpiPrice",
    "kpiPriceMeta",
    "kpiCoverage",
    "kpiCoverageMeta",
    "rangeSelector",
    "resetViewButton",
    "chartViewport",
    "trackerChart",
    "chartTooltip",
    "tooltipDate",
    "tooltipLegacyRow",
    "tooltipLegacy",
    "tooltipAnspRow",
    "tooltipAnsp",
    "tooltipAnspAudit",
    "tooltipAnspEffective",
    "tooltipAnspPositionDate",
    "tooltipAnspIntervalEnd",
    "tooltipAnspDateBasis",
    "tooltipAnspFirstPublished",
    "tooltipPriceRow",
    "tooltipPrice",
    "chartLoading",
    "chartEmpty",
    "chartEmptyMessage",
    "emptyPriceButton",
    "chartError",
    "chartErrorMessage",
    "retrySecurityButton",
    "chartSummary",
    "priceDialog",
    "priceDialogForm",
    "priceDialogSecurity",
    "priceSearchInput",
    "priceSearchSpinner",
    "priceSearchResults",
    "settingsDialog",
    "settingsForm",
    "settingsCloseButton",
    "settingsCancelButton",
    "settingsSaveButton",
    "autoSyncEnabled",
    "autoSyncInterval",
    "settingsSyncState",
    "settingsLastSuccess",
    "settingsNextCheck",
    "currentVersionChip",
    "softwareUpdateHeadline",
    "softwareUpdateDetail",
    "checkUpdateButton",
    "settingsOpenReleaseButton",
    "toastRegion",
  ];

  const dom = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

  const state = {
    language: readStoredLanguage(),
    status: null,
    settings: null,
    updateStatus: null,
    updateChecking: false,
    releaseUrl: "",
    observedSyncRunning: false,
    observedLastSyncAt: null,
    statusPollTimer: null,
    settingsDirty: false,
    connectionState: { kind: "loading", labelKey: "connection.connecting", detailKey: "" },
    activeView: "rankings",
    rankingsState: "loading",
    rankings: [],
    rankingsMeta: {},
    rankingFilter: "",
    rankingSort: { key: "rank", direction: "asc" },
    rankingPage: 1,
    rankingPageSize: 25,
    rankingController: null,
    selectedSecurity: null,
    shortSeries: [],
    prices: [],
    shortMeta: {},
    priceMeta: {},
    currency: "GBP",
    currentRange: DEFAULT_RANGE,
    searchResults: [],
    activeSearchIndex: -1,
    searchController: null,
    loadController: null,
    priceSearchController: null,
    priceSearchItems: [],
    priceSearchPlaceholderKey: "priceDialog.minChars",
    loadSequence: 0,
    lastSearchTerm: null,
    chart: null,
    chartState: "loading",
  };

  let dateFormatter;
  let shortDateFormatter;
  let monthFormatter;
  let yearFormatter;
  let percentFormatter;
  let integerFormatter;

  function t(key, variables = {}) {
    const pair = COPY[key];
    const index = state.language === "en-GB" ? 1 : 0;
    const template = Array.isArray(pair) ? pair[index] ?? pair[0] : key;
    return String(template).replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(variables, name) ? String(variables[name]) : match
    ));
  }

  function configureFormatters() {
    const locale = state.language;
    dateFormatter = new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      timeZone: "UTC",
    });
    shortDateFormatter = new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "2-digit",
      timeZone: "UTC",
    });
    monthFormatter = new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "short",
      timeZone: "UTC",
    });
    yearFormatter = new Intl.DateTimeFormat(locale, {
      year: "numeric",
      timeZone: "UTC",
    });
    percentFormatter = new Intl.NumberFormat(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    integerFormatter = new Intl.NumberFormat(locale);
  }

  function applyStaticTranslations() {
    document.documentElement.lang = state.language;
    document.title = t("page.title");
    document.querySelector('meta[name="description"]')?.setAttribute("content", t("page.description"));

    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n);
    });
    const translatedAttributes = [
      ["i18nPlaceholder", "placeholder"],
      ["i18nAriaLabel", "aria-label"],
      ["i18nTitle", "title"],
      ["i18nTooltip", "data-tooltip"],
    ];
    for (const [datasetKey, attribute] of translatedAttributes) {
      document.querySelectorAll(`[data-${datasetKey.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}]`).forEach((element) => {
        element.setAttribute(attribute, t(element.dataset[datasetKey]));
      });
    }
    dom.languageSwitch.querySelectorAll("button[data-language]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.language === state.language));
    });
  }

  function refreshLocalizedView() {
    if (state.rankings.length) renderRankings();
    if (state.rankingsState === "error") setRankingsState("error", t("error.rankingUnavailable"));
    if (state.selectedSecurity) {
      updateSecurityHeader(state.selectedSecurity);
      updateFreshness();
      const bounds = state.chart?.getViewBounds();
      updateKpis(bounds?.start, bounds?.end);
    }
    if (!dom.securitySearchResults.hidden) {
      renderSecurityResults(state.searchResults, dom.securitySearchInput.value.trim());
    }
    if (dom.priceDialog.open) {
      renderPriceResults(state.priceSearchItems, t(state.priceSearchPlaceholderKey));
      if (state.selectedSecurity) setText(dom.priceDialogSecurity, t("security.dialogFor", { name: state.selectedSecurity.name }));
    }
    const connection = state.connectionState;
    setConnectionState(connection.kind, connection.labelKey, connection.detailKey, connection.variables);
    if (state.chartState === "empty") {
      setChartState("empty", t(state.selectedSecurity?.priceSymbol ? "chart.emptyMatched" : "chart.emptyUnmatched"));
    } else if (state.chartState === "error") {
      setChartState("error", t("chart.errorDetail"));
    }
    renderAutomationStatus();
    renderUpdateStatus();
    state.chart?.refreshLanguage();
    dom.toastRegion.replaceChildren();
  }

  function setLanguage(language, { persist = true, rerender = true } = {}) {
    state.language = SUPPORTED_LANGUAGES.has(language) ? language : "zh-CN";
    if (persist) {
      try {
        window.localStorage.setItem(LANGUAGE_STORAGE_KEY, state.language);
      } catch {
        // The app remains usable when private browsing or policy blocks storage.
      }
    }
    configureFormatters();
    applyStaticTranslations();
    if (rerender) refreshLocalizedView();
  }

  function firstDefined(object, keys, fallback = null) {
    if (!object || typeof object !== "object") return fallback;
    for (const key of keys) {
      if (object[key] !== undefined && object[key] !== null && object[key] !== "") {
        return object[key];
      }
    }
    return fallback;
  }

  function toNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    const normalized = String(value).replace(/[,%\s]/g, "");
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function toOptionalBoolean(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value === 1 ? true : value === 0 ? false : null;
    const normalized = String(value).trim().toLowerCase();
    if (["true", "yes", "y", "1"].includes(normalized)) return true;
    if (["false", "no", "n", "0"].includes(normalized)) return false;
    return null;
  }

  function toTime(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") {
      const millis = value < 10_000_000_000 ? value * 1000 : value;
      return Number.isFinite(millis) ? millis : null;
    }
    if (value instanceof Date) return value.getTime();
    const text = String(value).trim();
    const parsed = /^\d{4}-\d{2}-\d{2}$/.test(text)
      ? Date.parse(`${text}T00:00:00Z`)
      : Date.parse(text);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function isoDate(time) {
    if (!Number.isFinite(time)) return "";
    return new Date(time).toISOString().slice(0, 10);
  }

  function formatDate(time) {
    return Number.isFinite(time) ? dateFormatter.format(new Date(time)) : "—";
  }

  function formatAnspDateBasis(value) {
    const translationKeys = {
      initial_ansp_scope_and_constituent_position_date: "chart.auditBasisInitial",
      previous_became_historical_date: "chart.auditBasisPreviousHistoric",
    };
    if (!value) return "—";
    return t(translationKeys[value] || "chart.auditBasisOther");
  }

  function formatDateTime(value) {
    const time = toTime(value);
    if (!Number.isFinite(time)) return "—";
    return new Intl.DateTimeFormat(state.language, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(time));
  }

  function formatCompactDate(time) {
    return Number.isFinite(time) ? shortDateFormatter.format(new Date(time)) : "—";
  }

  function formatPercent(value, suffix = "%") {
    return Number.isFinite(value) ? `${percentFormatter.format(value)}${suffix}` : "—";
  }

  function formatCurrency(value, currency = "GBP") {
    if (!Number.isFinite(value)) return "—";
    const unit = String(currency || "GBP").trim();
    if (/^(GBp|GBX|p)$/i.test(unit)) {
      return `${new Intl.NumberFormat(state.language, {
        minimumFractionDigits: value < 10 ? 2 : 0,
        maximumFractionDigits: 2,
      }).format(value)}p`;
    }
    try {
      return new Intl.NumberFormat(state.language, {
        style: "currency",
        currency: unit.toUpperCase(),
        minimumFractionDigits: value < 10 ? 2 : 0,
        maximumFractionDigits: value < 10 ? 2 : 0,
      }).format(value);
    } catch {
      return `${new Intl.NumberFormat(state.language, { maximumFractionDigits: 2 }).format(value)} ${unit}`;
    }
  }

  function formatAxisPrice(value, currency) {
    if (!Number.isFinite(value)) return "—";
    if (/^(GBp|GBX|p)$/i.test(String(currency || ""))) {
      return `${formatCompactNumber(value)}p`;
    }
    const symbol = { GBP: "£", USD: "$", EUR: "€" }[String(currency || "").toUpperCase()] || "";
    return `${symbol}${formatCompactNumber(value)}`;
  }

  function formatCompactNumber(value) {
    const abs = Math.abs(value);
    if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}b`;
    if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
    if (abs >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
    if (abs >= 100) return value.toFixed(0);
    if (abs >= 10) return value.toFixed(1);
    return value.toFixed(2);
  }

  function formatRelativeDate(time) {
    if (!Number.isFinite(time)) return t("common.unknown");
    const now = Date.now();
    const days = Math.max(0, Math.floor((now - time) / DAY));
    if (days === 0) return t("relative.today");
    if (days === 1) return t("relative.oneDayAgo");
    if (days < 30) return t("relative.daysAgo", { count: integerFormatter.format(days) });
    return formatDate(time);
  }

  function formatDuration(start, end) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
    const totalMonths = Math.max(0, Math.round((end - start) / (DAY * 30.4375)));
    const years = Math.floor(totalMonths / 12);
    const months = totalMonths % 12;
    const yearText = years ? t(years === 1 ? "duration.year" : "duration.years", { count: integerFormatter.format(years) }) : "";
    const monthText = months ? t(months === 1 ? "duration.month" : "duration.months", { count: integerFormatter.format(months) }) : "";
    if (years && months) return `${yearText} ${monthText}`;
    if (years) return yearText;
    if (months) return monthText;
    const days = Math.max(1, Math.round((end - start) / DAY));
    return t(days === 1 ? "duration.day" : "duration.days", { count: integerFormatter.format(days) });
  }

  function sourceName(source, fallback) {
    if (typeof source === "string" && source.trim()) return source.trim();
    return firstDefined(source, ["name", "label", "provider", "source"], fallback);
  }

  function debounce(fn, wait = 240) {
    let timer = null;
    const wrapped = (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
    wrapped.cancel = () => {
      clearTimeout(timer);
      timer = null;
    };
    return wrapped;
  }

  function isAbort(error) {
    return error?.name === "AbortError";
  }

  const API_ERROR_KEYS = Object.freeze({
    sync_running: "error.syncRunning",
    service_closing: "error.serviceClosing",
    invalid_settings: "error.invalidRequest",
    security_not_found: "error.securityNotFound",
    price_symbol_unresolved: "error.priceUnavailable",
    price_provider_error: "error.priceUnavailable",
    price_history_empty: "error.priceUnavailable",
    price_search_error: "error.priceUnavailable",
    local_only: "error.localOnly",
    invalid_sort: "error.invalidRequest",
    invalid_order: "error.invalidRequest",
    invalid_page: "error.invalidRequest",
    invalid_page_size: "error.invalidRequest",
    invalid_symbol: "error.invalidRequest",
    invalid_identifier: "error.invalidRequest",
    invalid_query: "error.invalidRequest",
    invalid_pagination: "error.invalidRequest",
    invalid_force: "error.invalidRequest",
    unsupported_media_type: "error.invalidRequest",
    not_found: "error.notFound",
    internal_error: "error.server",
    upstream_data_error: "error.server",
  });

  function errorMessage(error, fallback = t("error.requestFailed")) {
    if (!error) return fallback;
    if (error instanceof ApiError && error.payload) {
      const direct = firstDefined(error.payload, ["message", "detail"], null);
      const nested = error.payload.error;
      const code = String(firstDefined(nested, ["code"], firstDefined(error.payload, ["code"], ""))).toLowerCase();
      if (API_ERROR_KEYS[code]) return t(API_ERROR_KEYS[code]);
      const candidate = String(direct || firstDefined(nested, ["message", "detail"], null) || error.message || "");
      if (state.language === "en-GB" && /[\u3400-\u9fff]/u.test(candidate)) return fallback;
      return candidate || fallback;
    }
    const candidate = String(error.message || "");
    if (state.language === "en-GB" && /[\u3400-\u9fff]/u.test(candidate)) return fallback;
    return candidate || fallback;
  }

  async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

    let response;
    try {
      response = await fetch(`${API_ROOT}${path}`, { ...options, headers });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError(t("error.cannotConnect"), 0, null);
    }

    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    try {
      payload = contentType.includes("json") ? await response.json() : await response.text();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      const message =
        (payload && typeof payload === "object" && firstDefined(payload, ["message", "error", "detail"])) ||
        t("error.http", { status: response.status });
      throw new ApiError(String(message), response.status, payload);
    }
    return payload;
  }

  function findArray(payload, keys = []) {
    if (Array.isArray(payload)) return payload;
    if (!payload || typeof payload !== "object") return [];
    for (const key of keys) {
      if (Array.isArray(payload[key])) return payload[key];
    }
    for (const wrapper of ["data", "result", "payload"]) {
      const value = payload[wrapper];
      if (Array.isArray(value)) return value;
      if (value && typeof value === "object") {
        for (const key of keys) {
          if (Array.isArray(value[key])) return value[key];
        }
      }
    }
    return [];
  }

  function unwrapObject(payload, keys = []) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return {};
    for (const key of keys) {
      if (payload[key] && typeof payload[key] === "object" && !Array.isArray(payload[key])) return payload[key];
    }
    if (payload.data && typeof payload.data === "object" && !Array.isArray(payload.data)) {
      for (const key of keys) {
        if (payload.data[key] && typeof payload.data[key] === "object" && !Array.isArray(payload.data[key])) {
          return payload.data[key];
        }
      }
      return payload.data;
    }
    return payload;
  }

  function normalizeSecurity(raw, fallback = {}) {
    const source = { ...fallback, ...(raw || {}) };
    const id = firstDefined(source, ["id", "security_id", "securityId", "slug", "isin", "ticker", "symbol"]);
    if (id === null || id === undefined || id === "") return null;
    const name = firstDefined(source, ["name", "company_name", "companyName", "issuer", "display_name"], String(id));
    return {
      id: String(id),
      name: String(name),
      ticker: String(firstDefined(source, ["ticker", "epic", "short_ticker", "symbol"], "—")),
      isin: String(firstDefined(source, ["isin", "ISIN"], "—")),
      market: String(firstDefined(source, ["market", "exchange", "venue", "market_name"], "UK market")),
      priceSymbol: String(firstDefined(source, ["price_symbol", "priceSymbol", "mapped_symbol", "yahoo_symbol"], "")),
      currency: String(firstDefined(source, ["currency", "price_currency"], fallback.currency || "GBP")),
      shortSource: firstDefined(source, ["short_source", "shortSource"], fallback.shortSource || null),
      priceSource: firstDefined(source, ["price_source", "priceSource"], fallback.priceSource || null),
      lastSyncAt: firstDefined(source, ["last_sync_at", "lastSyncAt", "updated_at"], fallback.lastSyncAt || null),
    };
  }

  function normalizeSecurityList(payload) {
    return findArray(payload, ["items", "securities", "results", "matches"])
      .map((item) => normalizeSecurity(item))
      .filter(Boolean);
  }

  function normalizeRankings(payload) {
    const items = findArray(payload, ["items", "rankings", "results", "records"]);
    return items
      .map((raw, index) => {
        const nestedSecurity = raw?.security && typeof raw.security === "object" ? raw.security : {};
        const security = normalizeSecurity({
          ...nestedSecurity,
          ...raw,
          id: firstDefined(raw, ["security_id", "securityId", "id"], firstDefined(nestedSecurity, ["id", "security_id"])),
          name: firstDefined(raw, ["name", "security_name", "issuer", "company_name"], firstDefined(nestedSecurity, ["name", "issuer"])),
        });
        if (!security) return null;

        let shortPercent = toNumber(firstDefined(raw, [
          "short_percent",
          "aggregate_percent",
          "aggregated_percent",
          "ansp_percent",
          "current_short_percent",
          "value",
        ]));
        if (shortPercent === null) {
          const basisPoints = toNumber(firstDefined(raw, ["aggregate_bp", "short_bp", "ansp_bp"]));
          if (basisPoints !== null) shortPercent = basisPoints / 100;
        }
        if (!Number.isFinite(shortPercent)) return null;

        const positionTime = toTime(firstDefined(raw, [
          "position_date",
          "as_of_date",
          "date",
          "latest_position_date",
        ]));
        const suppliedAge = toNumber(firstDefined(raw, ["age_days", "position_age_days", "days_old"]));
        const positionDateInFuture = Boolean(firstDefined(raw, ["position_date_in_future"], false));
        const ageDays = positionDateInFuture
          ? null
          : suppliedAge !== null
            ? Math.max(0, Math.round(suppliedAge))
            : Number.isFinite(positionTime) && positionTime <= Date.now()
              ? Math.max(0, Math.floor((Date.now() - positionTime) / DAY))
              : null;

        return {
          rank: Math.max(1, Math.round(toNumber(firstDefined(raw, ["rank", "ranking", "position_rank"])) || index + 1)),
          security,
          shortPercent,
          positionTime,
          positionDate: Number.isFinite(positionTime) ? isoDate(positionTime) : "",
          ageDays,
          positionDateInFuture,
        };
      })
      .filter(Boolean);
  }

  function normalizeRankingsMeta(payload, itemCount) {
    const root = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
    const meta = root.meta && typeof root.meta === "object" ? root.meta : {};
    const coverage = root.coverage && typeof root.coverage === "object" ? root.coverage : {};
    const total = toNumber(firstDefined(root, ["total", "count", "total_count"], firstDefined(meta, ["total", "count"], itemCount)));
    const sourceTotal = toNumber(firstDefined(root, ["source_total"], firstDefined(meta, ["source_total"], total)));
    return {
      total: Number.isFinite(total) ? Math.max(0, Math.round(total)) : itemCount,
      sourceTotal: Number.isFinite(sourceTotal) ? Math.max(0, Math.round(sourceTotal)) : itemCount,
      sourceLimit: toNumber(firstDefined(root, ["source_limit"], firstDefined(meta, ["source_limit"], null))),
      sourceTruncated: Boolean(firstDefined(root, ["source_truncated"], firstDefined(meta, ["source_truncated"], false))),
      asOfDate: toTime(firstDefined(root, ["as_of_date", "latest_date", "data_through"], firstDefined(meta, ["as_of_date", "latest_date"], null))),
      source: sourceName(firstDefined(root, ["source_name", "source", "provider"], firstDefined(meta, ["source_name", "source", "provider"], null)), "FCA ANSP"),
      fetchedAt: toTime(firstDefined(
        root,
        ["fetched_at_utc", "fetched_at", "last_sync_at", "updated_at"],
        firstDefined(meta, ["fetched_at_utc", "last_sync_at"], firstDefined(coverage, ["last_checked_at", "activated_at", "imported_at"], null)),
      )),
      methodology: firstDefined(root, ["methodology"], firstDefined(meta, ["methodology"], null)),
      coverage: firstDefined(root, ["coverage"], firstDefined(meta, ["coverage"], null)),
    };
  }

  function normalizeShortSeries(payload) {
    const direct = findArray(payload, ["items", "series", "positions", "short_series", "records"]);
    const forced = [];
    const containers = [payload, payload?.data].filter((item) => item && typeof item === "object");
    for (const container of containers) {
      for (const key of ["legacy", "individual", "legacy_positions", "individual_positions"]) {
        if (Array.isArray(container[key])) forced.push(...container[key].map((item) => ({ ...item, __regime: "legacy" })));
      }
      for (const key of ["ansp", "aggregate", "aggregated", "ansp_positions", "aggregate_positions"]) {
        if (Array.isArray(container[key])) forced.push(...container[key].map((item) => ({ ...item, __regime: "ansp" })));
      }
    }

    // The flattened `items` collection is retained for older servers, while the
    // regime-specific collections carry richer ANSP interval metadata. Process
    // them afterwards so those authoritative fields win when a date is repeated.
    const rawItems = forced.length ? [...direct, ...forced] : direct;
    const byDate = new Map();
    for (const raw of rawItems) {
      const time = toTime(firstDefined(raw, ["date", "position_date", "as_of_date", "as_of", "timestamp", "time"]));
      if (!Number.isFinite(time)) continue;

      let legacy = toNumber(firstDefined(raw, [
        "legacy_percent",
        "legacy_pct",
        "legacy",
        "individual_percent",
        "legacy_value",
      ]));
      let ansp = toNumber(firstDefined(raw, [
        "ansp_percent",
        "ansp_pct",
        "ansp",
        "aggregate_percent",
        "aggregated_percent",
        "aggregate_value",
      ]));
      const generic = toNumber(firstDefined(raw, [
        "value",
        "percent",
        "percentage",
        "net_short_position",
        "net_short_percent",
        "short_percent",
        "position",
      ]));
      const regimeText = String(firstDefined(raw, ["__regime", "regime", "type", "disclosure_type"], "")).toLowerCase();
      if (generic !== null && legacy === null && ansp === null) {
        if (/legacy|individual|old|named/.test(regimeText)) legacy = generic;
        else if (/ansp|aggregat|new/.test(regimeText)) ansp = generic;
        else if (time < REGIME_SWITCH) legacy = generic;
        else ansp = generic;
      }
      if (legacy === null && ansp === null) continue;

      const existing = byDate.get(time) || {
        time,
        date: isoDate(time),
        legacy: null,
        ansp: null,
        anspIntervalEnd: null,
        anspIntervalEndDate: null,
        anspIsCurrent: null,
        anspPositionTime: null,
        anspPositionDate: null,
        anspChartDateBasis: null,
        anspFirstPublishedTime: null,
        anspFirstPublishedDate: null,
      };
      if (legacy !== null) existing.legacy = legacy;
      if (ansp !== null) {
        existing.ansp = ansp;
        const intervalEnd = toTime(firstDefined(raw, [
          "interval_end",
          "intervalEnd",
          "became_historical_date",
          "date_became_historical",
          "end_date",
          "valid_to",
        ]));
        const isCurrent = toOptionalBoolean(firstDefined(raw, ["is_current", "isCurrent", "current", "active"]));
        const positionTime = toTime(firstDefined(raw, [
          "position_date",
          "positionDate",
          "constituent_position_date",
          "raw_position_date",
        ]));
        const chartDateBasis = firstDefined(raw, ["chart_date_basis", "chartDateBasis", "date_basis"]);
        const firstPublishedTime = toTime(firstDefined(raw, [
          "first_published_on",
          "firstPublishedOn",
          "first_publication_date",
        ]));
        if (Number.isFinite(intervalEnd)) {
          existing.anspIntervalEnd = intervalEnd;
          existing.anspIntervalEndDate = isoDate(intervalEnd);
          if (isCurrent === null) existing.anspIsCurrent = false;
        }
        if (isCurrent !== null) existing.anspIsCurrent = isCurrent;
        if (isCurrent === true) {
          existing.anspIntervalEnd = null;
          existing.anspIntervalEndDate = null;
        }
        if (Number.isFinite(positionTime)) {
          existing.anspPositionTime = positionTime;
          existing.anspPositionDate = isoDate(positionTime);
        }
        if (chartDateBasis) existing.anspChartDateBasis = String(chartDateBasis);
        if (Number.isFinite(firstPublishedTime)) {
          existing.anspFirstPublishedTime = firstPublishedTime;
          existing.anspFirstPublishedDate = isoDate(firstPublishedTime);
        }
      }
      byDate.set(time, existing);
    }
    return [...byDate.values()].sort((a, b) => a.time - b.time);
  }

  function normalizePrices(payload) {
    const items = findArray(payload, ["items", "series", "prices", "history", "records", "results"]);
    const byDate = new Map();
    for (const raw of items) {
      const time = toTime(firstDefined(raw, ["date", "price_date", "trading_date", "timestamp", "time"]));
      const close = toNumber(firstDefined(raw, ["close", "adj_close", "adjusted_close", "price", "value"]));
      if (!Number.isFinite(time) || !Number.isFinite(close)) continue;
      byDate.set(time, { time, date: isoDate(time), close });
    }
    return [...byDate.values()].sort((a, b) => a.time - b.time);
  }

  function normalizePriceSearch(payload) {
    return findArray(payload, ["items", "results", "matches", "symbols"])
      .map((raw) => {
        const symbol = firstDefined(raw, ["symbol", "ticker", "code", "price_symbol"]);
        if (!symbol) return null;
        return {
          symbol: String(symbol),
          name: String(firstDefined(raw, ["name", "company_name", "description", "longname", "shortname"], symbol)),
          exchange: String(firstDefined(raw, ["exchange", "market", "venue", "exchDisp"], "")),
          currency: String(firstDefined(raw, ["currency", "price_currency"], "")),
        };
      })
      .filter(Boolean);
  }

  function normalizeMeta(payload, fallbackSource) {
    const root = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
    const nestedMeta = root.meta && typeof root.meta === "object" ? root.meta : {};
    const source = firstDefined(root, ["source", "provider"], firstDefined(nestedMeta, ["source", "provider"], null));
    return {
      source: sourceName(source, fallbackSource),
      latestDate: toTime(firstDefined(root, ["latest_date", "latestDate", "as_of_date", "data_through"], firstDefined(nestedMeta, ["latest_date", "latestDate", "as_of_date"], null))),
      updatedAt: toTime(firstDefined(root, ["last_sync_at", "updated_at", "fetched_at", "fetched_at_utc"], firstDefined(nestedMeta, ["last_sync_at", "updated_at", "fetched_at", "fetched_at_utc"], null))),
      symbol: String(firstDefined(root, ["symbol", "price_symbol"], firstDefined(nestedMeta, ["symbol", "price_symbol"], ""))),
      currency: String(firstDefined(root, ["currency", "price_currency"], firstDefined(nestedMeta, ["currency", "price_currency"], ""))),
    };
  }

  function setText(element, value) {
    if (element) element.textContent = value ?? "—";
  }

  function setKpisLoading(loading) {
    for (const element of [dom.kpiShort, dom.kpiChange, dom.kpiPrice, dom.kpiCoverage]) {
      element.classList.toggle("is-loading", loading);
      if (loading) element.textContent = t("common.loading");
    }
  }

  function showToast(title, detail = "", type = "success", timeout = 4200) {
    const toast = document.createElement("div");
    toast.className = `toast${type === "error" ? " is-error" : ""}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");

    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = type === "error" ? "!" : "✓";

    const copy = document.createElement("div");
    copy.className = "toast-copy";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const body = document.createElement("span");
    body.textContent = detail;
    copy.append(heading, body);

    const close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.setAttribute("aria-label", t("common.closeNotification"));
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());

    toast.append(icon, copy, close);
    dom.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), timeout);
  }

  function setChartState(kind, message = "") {
    state.chartState = kind;
    dom.chartLoading.hidden = kind !== "loading";
    dom.chartEmpty.hidden = kind !== "empty";
    dom.chartError.hidden = kind !== "error";
    if (kind === "empty" && message) setText(dom.chartEmptyMessage, message);
    if (kind === "error" && message) setText(dom.chartErrorMessage, message);
    dom.trackerChart.toggleAttribute("aria-busy", kind === "loading");
  }

  function newestTime(items, field = "time") {
    if (!items.length) return null;
    return items.reduce((latest, item) => Math.max(latest, item[field] || -Infinity), -Infinity);
  }

  function oldestTime(items, field = "time") {
    if (!items.length) return null;
    return items.reduce((earliest, item) => Math.min(earliest, item[field] || Infinity), Infinity);
  }

  function buildShortIntervals(items, field, effectiveEnd = field === "legacy" ? REGIME_SWITCH : Infinity) {
    const points = items
      .filter((item) => Number.isFinite(item[field]) && Number.isFinite(item.time))
      .sort((a, b) => a.time - b.time);

    return points.map((point, index) => {
      const nextTime = points[index + 1]?.time ?? Infinity;
      let intervalEnd = nextTime;
      let isCurrent = null;

      if (field === "ansp") {
        isCurrent = point.anspIsCurrent;
        if (isCurrent === true) intervalEnd = Infinity;
        else if (Number.isFinite(point.anspIntervalEnd)) intervalEnd = Math.min(point.anspIntervalEnd, nextTime);
        else if (!Number.isFinite(nextTime) && isCurrent === false) intervalEnd = point.time;
      }

      intervalEnd = Math.min(intervalEnd, effectiveEnd);
      return {
        time: point.time,
        value: point[field],
        intervalEnd,
        isCurrent,
        point,
      };
    }).filter((interval) => interval.intervalEnd > interval.time);
  }

  function latestShortPoint(items, start = -Infinity, end = Infinity) {
    const target = Number.isFinite(end) ? end : newestTime(items);
    if (!Number.isFinite(target) || target < start) return null;
    for (const field of ["ansp", "legacy"]) {
      const match = valueAtOrBefore(items, target, field);
      if (match && match.intervalEnd > start) return { ...match, regime: field };
    }
    return null;
  }

  function firstShortPoint(items, start = -Infinity, end = Infinity, regime = null) {
    const fields = regime ? [regime] : ["legacy", "ansp"];
    const matches = fields.flatMap((field) => buildShortIntervals(items, field)
      .filter((interval) => interval.intervalEnd > start && interval.time <= end)
      .map((interval) => ({ ...interval, regime: field })));
    matches.sort((a, b) => a.time - b.time);
    return matches[0] || null;
  }

  function lastPricePoint(items, start = -Infinity, end = Infinity) {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item.time >= start && item.time <= end && Number.isFinite(item.close)) return item;
    }
    return null;
  }

  function setConnectionState(kind, labelKey, detailKey = "", variables = {}) {
    state.connectionState = { kind, labelKey, detailKey, variables };
    dom.connectionStatus.classList.remove("is-loading", "is-online", "is-offline");
    dom.connectionStatus.classList.add(
      kind === "online" ? "is-online" : kind === "offline" ? "is-offline" : "is-loading",
    );
    setText(dom.connectionLabel, t(labelKey, variables));
    dom.connectionBanner.hidden = kind !== "offline";
    if (detailKey) setText(dom.connectionMessage, t(detailKey, variables));
  }

  function setAppView(view, { focus = false } = {}) {
    const next = view === "security" ? "security" : "rankings";
    state.activeView = next;
    dom.securityEmpty.hidden = true;
    dom.rankingsView.hidden = next !== "rankings";
    dom.securityWorkspace.hidden = next !== "security";
    if (focus) {
      const target = next === "rankings" ? dom.rankingsView : dom.securityWorkspace;
      target.setAttribute("tabindex", "-1");
      window.setTimeout(() => target.focus({ preventScroll: true }), 0);
    }
  }

  function setWorkspaceVisible(visible) {
    setAppView(visible ? "security" : "rankings");
  }

  function showRankings({ updateUrl = true, focus = true } = {}) {
    setAppView("rankings", { focus });
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.delete("security");
      window.history.replaceState({ view: "rankings" }, "", url);
    }
  }

  function updateRangeButtons(range) {
    state.currentRange = range || state.currentRange;
    for (const button of dom.rangeSelector.querySelectorAll("button[data-range]")) {
      button.setAttribute("aria-pressed", String(button.dataset.range === state.currentRange));
    }
  }

  function updateSecurityHeader(security) {
    setText(dom.securityName, security?.name || "—");
    setText(dom.securityTicker, security?.ticker || "—");
    setText(dom.securityIsin, security?.isin || "—");
    const market = security?.market;
    setText(dom.securityMarket, !market || /^(UK|英国市场)$/i.test(market) ? t("security.ukMarket") : market);
    setText(dom.securityPriceSymbol, security?.priceSymbol || t("common.unmatched"));
    setText(dom.currentRegimeBadge, t(Date.now() >= REGIME_SWITCH ? "security.currentAnsp" : "security.currentLegacy"));
    setText(dom.priceDialogSecurity, security ? t("security.dialogFor", { name: security.name }) : t("priceDialog.subtitle"));
  }

  function updateFreshness() {
    const shortLatest = state.shortMeta.latestDate || newestTime(state.shortSeries);
    const priceLatest = state.priceMeta.latestDate || newestTime(state.prices);
    const latestDates = [shortLatest, priceLatest].filter(Number.isFinite);
    const oldestLatest = latestDates.length ? Math.min(...latestDates) : null;
    const syncTime =
      toTime(state.selectedSecurity?.lastSyncAt) ||
      state.shortMeta.updatedAt ||
      state.priceMeta.updatedAt ||
      toTime(firstDefined(state.status, ["last_sync_at", "lastSyncAt", "updated_at"]));

    dom.freshnessOrb.classList.remove("is-loading", "is-fresh", "is-stale", "is-empty");
    if (!latestDates.length) {
      setText(dom.freshnessHeadline, t("provenance.noSeries"));
      dom.freshnessOrb.classList.add("is-empty");
    } else {
      const ageDays = Math.max(0, Math.floor((Date.now() - oldestLatest) / DAY));
      setText(
        dom.freshnessHeadline,
        t("provenance.headline", {
          shortDate: shortLatest ? formatDate(shortLatest) : t("common.noRecord"),
          priceDate: priceLatest ? formatDate(priceLatest) : t("common.noRecord"),
        }),
      );
      dom.freshnessOrb.classList.add(ageDays > 7 ? "is-stale" : "is-fresh");
    }

    const statusSources = state.status?.sources || {};
    const shortSource =
      state.shortMeta.source ||
      sourceName(state.selectedSecurity?.shortSource, t("source.fca")) ||
      sourceName(statusSources.short, t("source.fca"));
    const priceSource =
      state.priceMeta.source ||
      sourceName(state.selectedSecurity?.priceSource, t("common.unmatched")) ||
      sourceName(statusSources.price, t("common.unmatched"));
    const localizedShortSource = ["FCA 公开披露", "FCA public disclosures"].includes(shortSource) ? t("source.fca") : shortSource;
    const localizedPriceSource = ["每日行情", "Daily prices"].includes(priceSource)
      ? t("source.dailyPrices")
      : ["未匹配", "Not matched"].includes(priceSource)
        ? t("common.unmatched")
        : priceSource;
    setText(dom.shortSourceChip, t("provenance.shortSource", { source: localizedShortSource || t("source.fca") }));
    setText(dom.priceSourceChip, t("provenance.priceSource", { source: localizedPriceSource || t("common.unmatched") }));
    setText(dom.lastUpdatedText, t("provenance.lastSync", { time: syncTime ? formatRelativeDate(syncTime) : "—" }));
  }

  function pointsInRange(items, start, end) {
    return items.filter((item) => item.time >= start && item.time <= end);
  }

  function updateKpis(start, end) {
    const dataStart = Math.min(oldestTime(state.shortSeries) ?? Infinity, oldestTime(state.prices) ?? Infinity);
    const dataEnd = Math.max(newestTime(state.shortSeries) ?? -Infinity, newestTime(state.prices) ?? -Infinity);
    const viewStart = Number.isFinite(start) ? start : dataStart;
    const viewEnd = Number.isFinite(end) ? end : dataEnd;
    const latestShort = latestShortPoint(state.shortSeries, viewStart, viewEnd);
    const latestPrice = lastPricePoint(state.prices, viewStart, viewEnd);

    for (const element of [dom.kpiShort, dom.kpiChange, dom.kpiPrice, dom.kpiCoverage]) {
      element.classList.remove("is-loading");
    }

    if (latestShort) {
      setText(dom.kpiShort, formatPercent(latestShort.value));
      setText(
        dom.kpiShortMeta,
        t(latestShort.regime === "ansp" ? "kpi.anspMeta" : "kpi.legacyMeta", { date: formatDate(latestShort.time) }),
      );
    } else {
      setText(dom.kpiShort, "—");
      setText(dom.kpiShortMeta, t("kpi.noShortRange"));
    }

    dom.kpiChange.classList.remove("is-positive", "is-negative");
    if (latestShort) {
      const first = firstShortPoint(state.shortSeries, viewStart, viewEnd, latestShort.regime);
      const crossesRegime = viewStart < REGIME_SWITCH && viewEnd >= REGIME_SWITCH;
      if (first && first.time !== latestShort.time && !crossesRegime) {
        const change = latestShort.value - first.value;
        const formattedChange = `${change > 0 ? "+" : ""}${percentFormatter.format(change)}`;
        setText(dom.kpiChange, t(Math.abs(change) === 1 ? "kpi.percentagePoint" : "kpi.percentagePoints", { value: formattedChange }));
        dom.kpiChange.classList.add(change > 0 ? "is-positive" : change < 0 ? "is-negative" : "");
        setText(dom.kpiChangeMeta, t("kpi.dateRange", { start: formatDate(first.time), end: formatDate(latestShort.time) }));
      } else if (crossesRegime) {
        setText(dom.kpiChange, t("kpi.regimeChange"));
        setText(dom.kpiChangeMeta, t("kpi.regimeNoCompare"));
      } else {
        setText(dom.kpiChange, "—");
        setText(dom.kpiChangeMeta, t("kpi.insufficient"));
      }
    } else {
      setText(dom.kpiChange, "—");
      setText(dom.kpiChangeMeta, t("kpi.rangeBasis"));
    }

    if (latestPrice) {
      setText(dom.kpiPrice, formatCurrency(latestPrice.close, state.currency));
      setText(dom.kpiPriceMeta, t("kpi.priceMeta", {
        symbol: state.selectedSecurity?.priceSymbol || state.priceMeta.symbol || t("common.dailyClose"),
        date: formatDate(latestPrice.time),
      }));
    } else {
      setText(dom.kpiPrice, "—");
      setText(dom.kpiPriceMeta, t(state.selectedSecurity?.priceSymbol ? "kpi.noPriceRange" : "kpi.noPriceSymbol"));
    }

    const rangeShort = pointsInRange(state.shortSeries, viewStart, viewEnd);
    const rangePrices = pointsInRange(state.prices, viewStart, viewEnd);
    const coverageStart = Math.min(oldestTime(rangeShort) ?? Infinity, oldestTime(rangePrices) ?? Infinity);
    const coverageEnd = Math.max(newestTime(rangeShort) ?? -Infinity, newestTime(rangePrices) ?? -Infinity);
    if (Number.isFinite(coverageStart) && Number.isFinite(coverageEnd)) {
      setText(dom.kpiCoverage, formatDuration(coverageStart, coverageEnd));
      setText(dom.kpiCoverageMeta, t("kpi.coverageCounts", {
        positions: integerFormatter.format(rangeShort.length),
        days: integerFormatter.format(rangePrices.length),
      }));
    } else {
      setText(dom.kpiCoverage, "—");
      setText(dom.kpiCoverageMeta, t("kpi.noSeries"));
    }

    const regimeText = t(latestShort?.regime === "ansp" ? "regime.ansp" : latestShort ? "regime.legacy" : "regime.none");
    setText(
      dom.chartSummary,
      t("chart.summary", {
        security: state.selectedSecurity?.name || t("common.currentSecurity"),
        regime: regimeText,
        short: latestShort ? formatPercent(latestShort.value) : t("common.unknown"),
        price: latestPrice ? formatCurrency(latestPrice.close, state.currency) : t("common.unknown"),
        start: formatDate(viewStart),
        end: formatDate(viewEnd),
      }),
    );
  }

  function setRankingsState(kind, message = "") {
    state.rankingsState = kind;
    dom.rankingsLoading.hidden = kind !== "loading";
    dom.rankingsError.hidden = kind !== "error";
    dom.rankingsEmpty.hidden = kind !== "empty";
    dom.rankingsContent.hidden = kind !== "ready";
    dom.rankingsView.toggleAttribute("aria-busy", kind === "loading");
    if (kind === "error" && message) setText(dom.rankingsErrorMessage, message);
  }

  function updateRankingSnapshot() {
    const latestPosition = state.rankingsMeta.asOfDate || newestTime(state.rankings, "positionTime");
    const snapshotTime = state.rankingsMeta.fetchedAt || toTime(firstDefined(state.status, ["last_sync_at", "lastSyncAt"]));
    const loaded = state.rankings.length;
    const total = state.rankingsMeta.sourceTotal ?? state.rankingsMeta.total ?? loaded;

    setText(dom.rankingAsOfDate, latestPosition ? formatDate(latestPosition) : "—");
    setText(dom.rankingSnapshotUpdated, snapshotTime ? `${formatDate(snapshotTime)} · ${formatRelativeDate(snapshotTime)}` : t("common.notProvided"));
    setText(dom.rankingSource, state.rankingsMeta.source || "FCA ANSP");
    setText(
      dom.rankingCoverage,
      state.rankingsMeta.sourceTruncated
        ? t("ranking.coverageTruncated", { loaded: integerFormatter.format(loaded), total: integerFormatter.format(total) })
        : total === loaded
          ? t("ranking.coverageExact", { loaded: integerFormatter.format(loaded), total: integerFormatter.format(total) })
          : t("ranking.coverageLoaded", { loaded: integerFormatter.format(loaded), total: integerFormatter.format(total) }),
    );
    setText(dom.rankingTotalBadge, t("ranking.totalBadge", { total: integerFormatter.format(total) }));

    dom.rankingSnapshotOrb.classList.remove("is-loading", "is-fresh", "is-stale", "is-empty", "is-error");
    if (snapshotTime) {
      const snapshotAge = Math.max(0, Math.floor((Date.now() - snapshotTime) / DAY));
      dom.rankingSnapshotOrb.classList.add(snapshotAge > 7 ? "is-stale" : "is-fresh");
      setText(dom.rankingSnapshotHeadline, t("ranking.checkedAt", { time: formatRelativeDate(snapshotTime) }));
    } else {
      dom.rankingSnapshotOrb.classList.add("is-empty");
      setText(dom.rankingSnapshotHeadline, t("ranking.checkedNoTime"));
    }
  }

  function renderRankingBars() {
    dom.rankingsBarChart.replaceChildren();
    const top = [...state.rankings]
      .sort((a, b) => b.shortPercent - a.shortPercent || a.rank - b.rank)
      .slice(0, 8);
    const rawMax = Math.max(0, ...top.map((item) => item.shortPercent));
    const axisMax = Math.max(0.5, Math.ceil(rawMax * 2) / 2);
    setText(dom.rankingAxisMax, formatPercent(axisMax));

    const fragment = document.createDocumentFragment();
    for (const item of top) {
      const row = document.createElement("div");
      row.className = "ranking-bar-item";
      row.setAttribute("role", "listitem");

      const button = document.createElement("button");
      button.type = "button";
      button.className = "ranking-bar-button";
      const positionDate = Number.isFinite(item.positionTime) ? formatDate(item.positionTime) : item.positionDate || t("common.unknown");
      button.setAttribute("aria-label", t("ranking.itemAria", {
        rank: integerFormatter.format(item.rank),
        name: item.security.name,
        percent: formatPercent(item.shortPercent),
        date: positionDate,
      }));

      const rank = document.createElement("span");
      rank.className = "ranking-bar-rank";
      rank.textContent = `#${item.rank}`;

      const label = document.createElement("span");
      label.className = "ranking-bar-label";
      const name = document.createElement("strong");
      name.textContent = item.security.name;
      name.title = item.security.name;
      const meta = document.createElement("span");
      meta.textContent = [item.security.ticker !== "—" ? item.security.ticker : "", positionDate].filter(Boolean).join(" · ");
      label.append(name, meta);

      const track = document.createElement("span");
      track.className = "ranking-bar-track";
      const fill = document.createElement("span");
      fill.className = "ranking-bar-fill";
      fill.style.setProperty("--bar-size", `${Math.max(0, Math.min(100, (item.shortPercent / axisMax) * 100))}%`);
      track.append(fill);

      const value = document.createElement("span");
      value.className = "ranking-bar-value";
      value.textContent = formatPercent(item.shortPercent);
      button.append(rank, label, track, value);
      button.addEventListener("click", () => selectSecurity(item.security));
      row.append(button);
      fragment.append(row);
    }
    dom.rankingsBarChart.append(fragment);
  }

  function rankingSearchText(item) {
    return [
      item.security.name,
      item.security.ticker,
      item.security.isin,
      item.security.priceSymbol,
    ].join(" ").toLocaleLowerCase(state.language);
  }

  function getFilteredRankings() {
    const filter = state.rankingFilter.trim().toLocaleLowerCase(state.language);
    const filtered = filter
      ? state.rankings.filter((item) => rankingSearchText(item).includes(filter))
      : [...state.rankings];
    const { key, direction } = state.rankingSort;
    const multiplier = direction === "desc" ? -1 : 1;
    return filtered.sort((a, b) => {
      let result = 0;
      if (key === "name") result = a.security.name.localeCompare(b.security.name, state.language, { numeric: true, sensitivity: "base" });
      else {
        const left = a[key];
        const right = b[key];
        if (!Number.isFinite(left) && !Number.isFinite(right)) result = 0;
        else if (!Number.isFinite(left)) return 1;
        else if (!Number.isFinite(right)) return -1;
        else result = left - right;
      }
      return result === 0 ? a.rank - b.rank : result * multiplier;
    });
  }

  function updateRankingSortHeaders() {
    for (const heading of dom.rankingTable.querySelectorAll("th[data-sort-column]")) {
      const active = heading.dataset.sortColumn === state.rankingSort.key;
      heading.setAttribute("aria-sort", active ? (state.rankingSort.direction === "asc" ? "ascending" : "descending") : "none");
    }
  }

  function createRankingTableRow(item) {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.setAttribute("role", "link");
    const localizedPositionDate = Number.isFinite(item.positionTime) ? formatDate(item.positionTime) : item.positionDate || t("common.unknown");
    row.setAttribute("aria-label", t("ranking.itemAria", {
      rank: integerFormatter.format(item.rank),
      name: item.security.name,
      percent: formatPercent(item.shortPercent),
      date: localizedPositionDate,
    }));

    const rankCell = document.createElement("td");
    rankCell.className = "ranking-rank";
    rankCell.textContent = `#${item.rank}`;

    const companyCell = document.createElement("td");
    companyCell.className = "ranking-company-cell";
    const companyName = document.createElement("strong");
    companyName.textContent = item.security.name;
    companyName.title = item.security.name;
    const identity = document.createElement("span");
    identity.textContent = [
      item.security.ticker !== "—" ? item.security.ticker : "",
      item.security.isin !== "—" ? item.security.isin : "",
    ].filter(Boolean).join(" · ") || "—";
    companyCell.append(companyName, identity);

    const percentCell = document.createElement("td");
    percentCell.className = "numeric-column ranking-percent";
    percentCell.textContent = formatPercent(item.shortPercent);

    const dateCell = document.createElement("td");
    dateCell.className = "ranking-date";
    dateCell.textContent = Number.isFinite(item.positionTime) ? formatDate(item.positionTime) : item.positionDate || "—";

    const ageCell = document.createElement("td");
    ageCell.className = "numeric-column";
    const age = document.createElement("span");
    age.className = `position-age${Number.isFinite(item.ageDays) && item.ageDays > 90 ? " is-earlier" : ""}`;
    age.textContent = Number.isFinite(item.ageDays)
      ? t(item.ageDays === 1 ? "ranking.ageOneDay" : "ranking.ageDays", { count: integerFormatter.format(item.ageDays) })
      : "—";
    if (Number.isFinite(item.ageDays) && item.ageDays > 90) {
      age.title = t("ranking.oldDateTitle");
    }
    ageCell.append(age);

    row.append(rankCell, companyCell, percentCell, dateCell, ageCell);
    const open = () => selectSecurity(item.security);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    return row;
  }

  function renderRankingTable() {
    const filtered = getFilteredRankings();
    const totalPages = Math.max(1, Math.ceil(filtered.length / state.rankingPageSize));
    state.rankingPage = Math.max(1, Math.min(state.rankingPage, totalPages));
    const startIndex = (state.rankingPage - 1) * state.rankingPageSize;
    const pageItems = filtered.slice(startIndex, startIndex + state.rankingPageSize);
    dom.rankingTableBody.replaceChildren();

    if (!pageItems.length) {
      const row = document.createElement("tr");
      row.className = "ranking-table-empty-row";
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.textContent = t("ranking.noFilterResults");
      row.append(cell);
      dom.rankingTableBody.append(row);
    } else {
      const fragment = document.createDocumentFragment();
      pageItems.forEach((item) => fragment.append(createRankingTableRow(item)));
      dom.rankingTableBody.append(fragment);
    }

    const shownStart = filtered.length ? startIndex + 1 : 0;
    const shownEnd = filtered.length ? startIndex + pageItems.length : 0;
    setText(
      dom.rankingTableSummary,
      state.rankingFilter
        ? t("ranking.filteredSummary", { all: integerFormatter.format(state.rankings.length), filtered: integerFormatter.format(filtered.length) })
        : t("ranking.loadedSummary", { count: integerFormatter.format(state.rankings.length) }),
    );
    setText(dom.rankingPageStatus, t("ranking.pageStatus", {
      start: integerFormatter.format(shownStart),
      end: integerFormatter.format(shownEnd),
      total: integerFormatter.format(filtered.length),
    }));
    setText(dom.rankingPageNumber, t("ranking.pageNumber", {
      page: integerFormatter.format(state.rankingPage),
      pages: integerFormatter.format(totalPages),
    }));
    dom.rankingPreviousPage.disabled = state.rankingPage <= 1;
    dom.rankingNextPage.disabled = state.rankingPage >= totalPages;
    updateRankingSortHeaders();
  }

  function renderRankings() {
    updateRankingSnapshot();
    renderRankingBars();
    renderRankingTable();
  }

  async function loadRankings({ silent = false } = {}) {
    state.rankingController?.abort();
    const controller = new AbortController();
    state.rankingController = controller;
    if (!silent) setRankingsState("loading");
    try {
      const payload = await apiFetch("/rankings/current?page_size=2000", { signal: controller.signal });
      if (controller.signal.aborted) return;
      const rankings = normalizeRankings(payload);
      state.rankings = rankings;
      state.rankingsMeta = normalizeRankingsMeta(payload, rankings.length);
      if (!state.rankingsMeta.asOfDate) state.rankingsMeta.asOfDate = newestTime(rankings, "positionTime");
      state.rankingPage = 1;
      renderRankings();
      setRankingsState(rankings.length ? "ready" : "empty");
    } catch (error) {
      if (isAbort(error)) return;
      dom.rankingSnapshotOrb.classList.remove("is-loading", "is-fresh", "is-stale", "is-empty");
      dom.rankingSnapshotOrb.classList.add("is-error");
      setText(dom.rankingSnapshotHeadline, t("ranking.snapshotFailed"));
      if (silent && state.rankings.length) {
        showToast(t("ranking.refreshFailed"), errorMessage(error), "error", 6000);
      } else {
        setRankingsState("error", errorMessage(error, t("error.rankingUnavailable")));
      }
    }
  }

  function closeSecurityResults() {
    dom.securitySearchResults.hidden = true;
    dom.securitySearchInput.setAttribute("aria-expanded", "false");
    dom.securitySearchInput.setAttribute("aria-activedescendant", "");
    state.activeSearchIndex = -1;
  }

  function setActiveSearchIndex(index) {
    const options = [...dom.securitySearchResults.querySelectorAll('[role="option"]')];
    if (!options.length) return;
    const next = ((index % options.length) + options.length) % options.length;
    state.activeSearchIndex = next;
    options.forEach((option, optionIndex) => {
      const active = optionIndex === next;
      option.classList.toggle("is-active", active);
      option.setAttribute("aria-selected", String(active));
    });
    dom.securitySearchInput.setAttribute("aria-activedescendant", options[next].id);
    options[next].scrollIntoView({ block: "nearest" });
  }

  function renderSecurityResults(items, term = "") {
    dom.securitySearchResults.replaceChildren();
    state.searchResults = items;
    state.activeSearchIndex = -1;

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "autocomplete-message";
      empty.textContent = t(term ? "search.noMatch" : "search.noSecurities");
      dom.securitySearchResults.append(empty);
    } else {
      items.slice(0, 12).forEach((security, index) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "autocomplete-option";
        option.id = `security-option-${index}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.dataset.securityId = security.id;

        const monogram = document.createElement("span");
        monogram.className = "option-monogram";
        monogram.setAttribute("aria-hidden", "true");
        monogram.textContent = (security.ticker !== "—" ? security.ticker : security.name).replace(/[^A-Za-z0-9\u4e00-\u9fff]/g, "").slice(0, 3).toUpperCase() || "UK";

        const identity = document.createElement("span");
        identity.className = "option-copy";
        const name = document.createElement("strong");
        name.textContent = security.name;
        const meta = document.createElement("span");
        meta.textContent = [security.ticker !== "—" ? security.ticker : "", security.isin !== "—" ? security.isin : ""]
          .filter(Boolean)
          .join(" · ");
        identity.append(name, meta);

        const market = document.createElement("span");
        market.className = "option-market";
        market.textContent = security.market || "UK";
        option.append(monogram, identity, market);
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => selectSecurity(security));
        dom.securitySearchResults.append(option);
      });
    }

    dom.securitySearchResults.hidden = false;
    dom.securitySearchInput.setAttribute("aria-expanded", "true");
  }

  async function searchSecurities(term, { open = true } = {}) {
    state.searchController?.abort();
    const controller = new AbortController();
    state.searchController = controller;
    state.lastSearchTerm = term;
    dom.securitySearch.classList.add("is-searching");
    try {
      const payload = await apiFetch(`/securities?q=${encodeURIComponent(term)}`, { signal: controller.signal });
      if (controller.signal.aborted || state.lastSearchTerm !== term) return [];
      const items = normalizeSecurityList(payload);
      if (open) renderSecurityResults(items, term);
      return items;
    } catch (error) {
      if (isAbort(error)) return [];
      if (open) {
        dom.securitySearchResults.replaceChildren();
        const message = document.createElement("div");
        message.className = "autocomplete-message is-error";
        message.textContent = errorMessage(error, t("error.searchUnavailable"));
        dom.securitySearchResults.append(message);
        dom.securitySearchResults.hidden = false;
        dom.securitySearchInput.setAttribute("aria-expanded", "true");
      }
      return [];
    } finally {
      if (state.searchController === controller) dom.securitySearch.classList.remove("is-searching");
    }
  }

  const debouncedSecuritySearch = debounce((term) => searchSecurities(term), 220);

  function handleSecuritySearchInput() {
    const term = dom.securitySearchInput.value.trim();
    dom.searchClear.hidden = !term;
    if (!term) {
      debouncedSecuritySearch.cancel();
      searchSecurities("");
      return;
    }
    debouncedSecuritySearch(term);
  }

  function handleSecuritySearchKeydown(event) {
    const isOpen = !dom.securitySearchResults.hidden;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!isOpen) searchSecurities(dom.securitySearchInput.value.trim());
      else setActiveSearchIndex(state.activeSearchIndex + 1);
    } else if (event.key === "ArrowUp" && isOpen) {
      event.preventDefault();
      setActiveSearchIndex(state.activeSearchIndex - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const currentTerm = dom.securitySearchInput.value.trim();
      const selected = state.lastSearchTerm === currentTerm
        ? state.searchResults[state.activeSearchIndex] || state.searchResults[0]
        : null;
      if (selected) selectSecurity(selected);
      else searchSecurities(currentTerm);
    } else if (event.key === "Escape") {
      closeSecurityResults();
    }
  }

  function readDismissedUpdate() {
    try {
      return window.localStorage.getItem(DISMISSED_UPDATE_STORAGE_KEY) || "";
    } catch {
      return "";
    }
  }

  function renderAutomationStatus() {
    const automatic = state.status?.auto_sync || state.settings?.auto_sync || {};
    const enabled = automatic.enabled !== false;
    const running = automatic.running === true || state.status?.sync?.running === true;
    const closing = automatic.closing === true || state.status?.sync?.closing === true;
    const failed = Boolean(automatic.last_error);

    if (!state.settingsDirty) {
      dom.autoSyncEnabled.checked = enabled;
      const interval = Number(automatic.interval_hours || 6);
      dom.autoSyncInterval.value = [6, 12, 24].includes(interval) ? String(interval) : "6";
      dom.autoSyncInterval.disabled = !enabled;
    }

    const stateKey = closing
      ? "settings.stateClosing"
      : running
        ? "settings.stateRunning"
        : failed
          ? "settings.stateFailed"
          : enabled
            ? "settings.stateEnabled"
            : "settings.stateDisabled";
    setText(dom.settingsSyncState, t(stateKey));
    setText(dom.settingsLastSuccess, formatDateTime(automatic.last_success_at));
    setText(dom.settingsNextCheck, enabled ? formatDateTime(automatic.next_check_at) : "—");
  }

  function normalizeUpdateStatus(payload) {
    const raw = unwrapObject(payload, ["update", "result"]);
    return payload && typeof payload === "object"
      ? { ...payload, ...(raw !== payload && typeof raw === "object" ? raw : {}) }
      : {};
  }

  function renderUpdateStatus() {
    const update = state.updateStatus || {};
    const currentVersion = String(update.current_version || state.status?.version || "—");
    setText(dom.currentVersionChip, currentVersion === "—" ? "v—" : `v${currentVersion.replace(/^v/i, "")}`);

    if (state.updateChecking) {
      setText(dom.softwareUpdateHeadline, t("update.checking"));
      setText(dom.softwareUpdateDetail, t("update.checkDetail"));
      dom.checkUpdateButton.disabled = true;
      return;
    }
    dom.checkUpdateButton.disabled = false;

    const status = String(update.status || "not_checked");
    const release = update.release && typeof update.release === "object" ? update.release : null;
    const version = String(release?.version || "").replace(/^v/i, "");
    state.releaseUrl = status === "update_available" ? String(release?.release_url || "") : "";

    let headlineKey = "update.notChecked";
    let detailKey = "update.checkDetail";
    let variables = {};
    if (status === "disabled") {
      headlineKey = "update.disabled";
      detailKey = "update.disabledDetail";
    } else if (status === "up_to_date") {
      headlineKey = "update.current";
      detailKey = "update.currentDetail";
      variables = { version: currentVersion === "—" ? "—" : `v${currentVersion.replace(/^v/i, "")}` };
    } else if (status === "update_available" && version && state.releaseUrl) {
      headlineKey = "update.available";
      detailKey = "update.availableDetail";
      variables = { version: `v${version}` };
    } else if (status === "unavailable") {
      headlineKey = "update.unavailable";
      detailKey = "update.unavailableDetail";
    }

    setText(dom.softwareUpdateHeadline, t(headlineKey, variables));
    setText(dom.softwareUpdateDetail, t(detailKey, variables));
    dom.settingsOpenReleaseButton.hidden = !state.releaseUrl;

    const dismissed = readDismissedUpdate();
    const showBanner = Boolean(state.releaseUrl && version && dismissed !== version);
    dom.updateBanner.hidden = !showBanner;
    if (showBanner) {
      setText(dom.updateBannerTitle, t("update.available", { version: `v${version}` }));
      setText(dom.updateBannerDetail, t("update.availableDetail"));
      dom.updateBanner.dataset.version = version;
    }
  }

  async function openExternalRelease() {
    if (!state.releaseUrl) return;
    let url;
    try {
      url = new URL(state.releaseUrl);
    } catch {
      return;
    }
    if (url.protocol !== "https:" || url.hostname !== "github.com") return;
    try {
      if (window.pywebview?.api?.open_external) {
        await window.pywebview.api.open_external(url.href);
      } else {
        window.open(url.href, "_blank", "noopener,noreferrer");
      }
    } catch {
      window.open(url.href, "_blank", "noopener,noreferrer");
    }
  }

  async function loadSettings() {
    const payload = await apiFetch("/settings");
    state.settings = payload && typeof payload === "object" ? payload : {};
    state.settingsDirty = false;
    renderAutomationStatus();
    return state.settings;
  }

  async function openSettings() {
    state.settingsDirty = false;
    if (!dom.settingsDialog.open) dom.settingsDialog.showModal();
    await Promise.allSettled([loadSettings(), loadUpdateStatus()]);
  }

  async function saveSettings(event) {
    event.preventDefault();
    dom.settingsSaveButton.disabled = true;
    try {
      const payload = await apiFetch("/settings", {
        method: "PUT",
        body: JSON.stringify({
          auto_sync: {
            enabled: dom.autoSyncEnabled.checked,
            interval_hours: Number(dom.autoSyncInterval.value),
          },
        }),
      });
      state.settings = payload && typeof payload === "object" ? payload : {};
      state.settingsDirty = false;
      await loadStatus({ quiet: true });
      renderAutomationStatus();
      dom.settingsDialog.close("saved");
      showToast(t("settings.saved"), t("settings.footer"));
    } catch (error) {
      showToast(t("settings.saveFailed"), errorMessage(error), "error", 6500);
    } finally {
      dom.settingsSaveButton.disabled = false;
    }
  }

  async function loadUpdateStatus() {
    try {
      const payload = await apiFetch("/update/status");
      state.updateStatus = normalizeUpdateStatus(payload);
    } catch {
      state.updateStatus = { status: "unavailable", current_version: state.status?.version || "" };
    }
    renderUpdateStatus();
    return state.updateStatus;
  }

  async function checkForUpdates({ force = false } = {}) {
    if (state.updateChecking) return state.updateStatus;
    state.updateChecking = true;
    renderUpdateStatus();
    try {
      const payload = await apiFetch("/update/check", {
        method: "POST",
        body: JSON.stringify({ force: Boolean(force) }),
      });
      state.updateStatus = normalizeUpdateStatus(payload);
    } catch {
      state.updateStatus = { status: "unavailable", current_version: state.status?.version || "" };
    } finally {
      state.updateChecking = false;
      renderUpdateStatus();
    }
    return state.updateStatus;
  }

  function normalizeStatus(payload) {
    const raw = unwrapObject(payload, ["status"]);
    return payload && typeof payload === "object"
      ? { ...payload, ...(raw !== payload && typeof raw === "object" ? raw : {}) }
      : {};
  }

  async function loadStatus({ autoSelect = false, quiet = false } = {}) {
    const previousRunning = state.observedSyncRunning;
    const previousLastSyncAt = state.observedLastSyncAt;
    if (!quiet) setConnectionState("loading", "connection.connecting");
    try {
      const payload = await apiFetch("/status");
      state.status = normalizeStatus(payload);
      if (state.rankings.length) updateRankingSnapshot();
      const statusText = String(firstDefined(state.status, ["status", "state"], "ready")).toLowerCase();
      const syncing = Boolean(state.status?.sync?.running) || /queue|sync|progress|running/.test(statusText);
      const lastSyncAt = String(firstDefined(state.status, ["last_sync_at", "lastSyncAt"], "") || "");
      state.observedSyncRunning = syncing;
      state.observedLastSyncAt = lastSyncAt;
      setConnectionState(syncing ? "loading" : "online", syncing ? "connection.syncing" : "connection.online");
      renderAutomationStatus();
      if (autoSelect && !state.selectedSecurity) {
        const params = new URLSearchParams(window.location.search);
        const requestedId = params.get("security") || firstDefined(state.status, ["default_security_id", "defaultSecurityId"]);
        if (requestedId) {
          await loadSecurity(String(requestedId), null, { updateUrl: false });
        } else {
          const items = await searchSecurities("", { open: false });
          if (items[0]) await selectSecurity(items[0]);
        }
      }
      const backgroundSyncCompleted = quiet && (
        (previousRunning && !syncing) ||
        (Boolean(previousLastSyncAt) && Boolean(lastSyncAt) && previousLastSyncAt !== lastSyncAt)
      );
      if (backgroundSyncCompleted) {
        await loadRankings({ silent: state.activeView === "security" });
        if (state.activeView === "security" && state.selectedSecurity) {
          await loadSecurity(state.selectedSecurity.id, state.selectedSecurity, { updateUrl: false });
        }
        showToast(t("sync.complete"), t("sync.reloadedDetail"));
      }
      return state.status;
    } catch (error) {
      if (!quiet) {
        state.status = null;
        setConnectionState("offline", "connection.offline", "connection.errorDetail");
      }
      return null;
    }
  }

  function mergeSecurityPayload(payload, seed) {
    const rawSecurity = unwrapObject(payload, ["security", "item", "result"]);
    const root = payload && typeof payload === "object" ? payload : {};
    return normalizeSecurity(rawSecurity, {
      ...(seed || {}),
      lastSyncAt: firstDefined(root, ["last_sync_at", "lastSyncAt", "updated_at"], seed?.lastSyncAt),
      shortSource: firstDefined(root, ["short_source", "shortSource"], seed?.shortSource),
      priceSource: firstDefined(root, ["price_source", "priceSource"], seed?.priceSource),
    });
  }

  async function selectSecurity(security) {
    closeSecurityResults();
    dom.securitySearchInput.value = security.name || security.ticker || "";
    dom.searchClear.hidden = !dom.securitySearchInput.value;
    await loadSecurity(security.id, security);
  }

  async function loadSecurity(id, seed = null, { updateUrl = true } = {}) {
    if (!id) return;
    state.loadController?.abort();
    const controller = new AbortController();
    const sequence = ++state.loadSequence;
    state.loadController = controller;

    const fallback = normalizeSecurity(seed || { id, name: String(id) }) || { id: String(id), name: String(id) };
    state.selectedSecurity = fallback;
    state.shortSeries = [];
    state.prices = [];
    state.shortMeta = {};
    state.priceMeta = {};
    state.currency = fallback.currency || "GBP";
    setWorkspaceVisible(true);
    updateSecurityHeader(fallback);
    setKpisLoading(true);
    setChartState("loading");
    state.chart?.setData([], [], { currency: state.currency });
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("security", String(id));
      const currentId = new URLSearchParams(window.location.search).get("security");
      const method = currentId === String(id) ? "replaceState" : "pushState";
      window.history[method]({ view: "security", security: String(id) }, "", url);
    }

    const base = `/security/${encodeURIComponent(id)}`;
    const requests = await Promise.allSettled([
      apiFetch(base, { signal: controller.signal }),
      apiFetch(`${base}/short-series`, { signal: controller.signal }),
      apiFetch(`${base}/prices`, { signal: controller.signal }),
    ]);
    if (controller.signal.aborted || sequence !== state.loadSequence) return;

    const [detailResult, shortResult, priceResult] = requests;
    if (detailResult.status === "fulfilled") {
      state.selectedSecurity = mergeSecurityPayload(detailResult.value, fallback) || fallback;
    }
    if (shortResult.status === "fulfilled") {
      state.shortSeries = normalizeShortSeries(shortResult.value);
      state.shortMeta = normalizeMeta(shortResult.value, t("source.fca"));
    }
    if (priceResult.status === "fulfilled") {
      state.prices = normalizePrices(priceResult.value);
      state.priceMeta = normalizeMeta(priceResult.value, state.selectedSecurity?.priceSymbol ? t("source.dailyPrices") : t("common.unmatched"));
    }

    const priceSymbol =
      state.selectedSecurity?.priceSymbol || state.priceMeta.symbol || firstDefined(priceResult.value, ["symbol", "price_symbol"], "");
    if (state.selectedSecurity && priceSymbol) state.selectedSecurity.priceSymbol = String(priceSymbol);
    state.currency = state.priceMeta.currency || state.selectedSecurity?.currency || "GBP";

    updateSecurityHeader(state.selectedSecurity);
    dom.securitySearchInput.value = state.selectedSecurity?.name || "";
    dom.searchClear.hidden = !dom.securitySearchInput.value;
    updateFreshness();
    state.chart?.setData(state.shortSeries, state.prices, { currency: state.currency });
    state.chart?.setRange(state.currentRange, { announce: false });

    const failed = [shortResult, priceResult].filter((result) => result.status === "rejected");
    const hasData = state.shortSeries.length || state.prices.length;
    if (hasData) {
      setChartState("ready");
      if (failed.length) {
        const missingKey = shortResult.status === "rejected" ? "chart.positions" : "chart.prices";
        showToast(t("chart.partialMissingTitle", { missing: t(missingKey) }), errorMessage(failed[0].reason), "error", 6000);
      }
    } else if (failed.length === 2) {
      setChartState("error", errorMessage(failed[0].reason, t("error.seriesFailed")));
    } else if (failed.length === 1) {
      const availableType = t(failed[0] === shortResult ? "chart.prices" : "chart.positions");
      setChartState("error", t("chart.availableMissing", { available: availableType, error: errorMessage(failed[0].reason) }));
    } else {
      setChartState("empty", t(state.selectedSecurity?.priceSymbol ? "chart.emptyMatched" : "chart.emptyUnmatched"));
    }

    const bounds = state.chart?.getViewBounds();
    updateKpis(bounds?.start, bounds?.end);
    if (detailResult.status === "rejected" && !hasData) {
      showToast(t("security.detailFailed"), errorMessage(detailResult.reason), "error");
    }
  }

  async function syncData() {
    if (dom.syncButton.disabled) return;
    dom.syncButton.disabled = true;
    dom.syncButton.classList.add("is-syncing");
    setConnectionState("loading", "connection.syncing");
    try {
      const payload = await apiFetch("/sync", { method: "POST", body: JSON.stringify({}) });
      const status = String(firstDefined(payload, ["status", "state"], "complete")).toLowerCase();
      const queued = Boolean(payload?.accepted || payload?.sync?.running) || /queue|progress|running|sync/.test(status);
      showToast(
        t(queued ? "sync.submitted" : "sync.complete"),
        t(queued ? "sync.submittedDetail" : "sync.completeDetail"),
      );
      if (queued) await waitForSyncCompletion();
      else await loadStatus();
      await loadRankings({ silent: state.activeView === "security" });
      if (state.activeView === "security" && state.selectedSecurity) {
        await loadSecurity(state.selectedSecurity.id, state.selectedSecurity, { updateUrl: false });
      }
    } catch (error) {
      setConnectionState("offline", "connection.syncFailed", "error.requestFailed");
      showToast(t("sync.failedToast"), errorMessage(error), "error", 6500);
    } finally {
      dom.syncButton.disabled = false;
      dom.syncButton.classList.remove("is-syncing");
    }
  }

  function waitDelay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function waitForSyncCompletion(timeout = 180_000) {
    const started = Date.now();
    let consecutiveErrors = 0;
    while (Date.now() - started < timeout) {
      await waitDelay(1_200);
      try {
        const payload = await apiFetch("/status");
        state.status = normalizeStatus(payload);
        consecutiveErrors = 0;
        const sync = state.status?.sync || {};
        if (sync.running) {
          setConnectionState("loading", "connection.syncing");
          continue;
        }
        if (sync.last_error) throw new ApiError(t("error.server"), 500, { error: { code: "internal_error" } });
        setConnectionState("online", "connection.online");
        showToast(t("sync.complete"), t("sync.reloadedDetail"));
        return state.status;
      } catch (error) {
        consecutiveErrors += 1;
        if (consecutiveErrors >= 3) throw error;
      }
    }
    setConnectionState("loading", "connection.syncBackground");
    showToast(t("sync.stillRunning"), t("sync.stillRunningDetail"), "success", 7000);
    return null;
  }

  function openPriceDialog() {
    if (!state.selectedSecurity) return;
    setText(dom.priceDialogSecurity, t("security.dialogFor", { name: state.selectedSecurity.name }));
    dom.priceSearchInput.value = state.selectedSecurity.priceSymbol || state.selectedSecurity.ticker.replace("—", "");
    const placeholderKey = dom.priceSearchInput.value.trim().length < 2 ? "priceDialog.minChars" : "priceDialog.searching";
    renderPriceResults([], t(placeholderKey), placeholderKey);
    if (typeof dom.priceDialog.showModal === "function") dom.priceDialog.showModal();
    else dom.priceDialog.setAttribute("open", "");
    window.setTimeout(() => {
      dom.priceSearchInput.focus();
      if (dom.priceSearchInput.value.trim().length >= 2) searchPrices(dom.priceSearchInput.value.trim());
    }, 0);
  }

  function renderPriceResults(items, placeholder = t("priceDialog.noMatch"), placeholderKey = "priceDialog.noMatch") {
    state.priceSearchItems = items;
    state.priceSearchPlaceholderKey = placeholderKey;
    dom.priceSearchResults.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "dialog-placeholder";
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "⌁";
      empty.append(icon, document.createTextNode(placeholder));
      dom.priceSearchResults.append(empty);
      return;
    }

    for (const item of items.slice(0, 16)) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "price-option";
      option.setAttribute("role", "option");

      const symbol = document.createElement("strong");
      symbol.className = "price-symbol";
      symbol.textContent = item.symbol;
      const main = document.createElement("span");
      main.className = "price-option-copy";
      const detail = document.createElement("strong");
      detail.textContent = item.name;
      const venue = document.createElement("span");
      venue.textContent = [item.exchange, item.currency].filter(Boolean).join(" · ") || t("common.priceSymbol");
      main.append(detail, venue);

      const action = document.createElement("span");
      action.className = "price-option-action";
      action.textContent = t("common.select");
      option.append(symbol, main, action);
      option.addEventListener("click", () => savePriceSymbol(item));
      dom.priceSearchResults.append(option);
    }
  }

  async function searchPrices(term) {
    state.priceSearchController?.abort();
    const cleaned = term.trim();
    if (cleaned.length < 2) {
      dom.priceSearchInput.closest(".dialog-search-row")?.classList.remove("is-searching");
      renderPriceResults([], t("priceDialog.minChars"), "priceDialog.minChars");
      return;
    }
    const controller = new AbortController();
    state.priceSearchController = controller;
    dom.priceSearchInput.closest(".dialog-search-row")?.classList.add("is-searching");
    try {
      const payload = await apiFetch(`/price-search?q=${encodeURIComponent(cleaned)}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      renderPriceResults(normalizePriceSearch(payload));
    } catch (error) {
      if (!isAbort(error)) renderPriceResults([], errorMessage(error, t("error.priceSearchUnavailable")), "error.priceSearchUnavailable");
    } finally {
      if (state.priceSearchController === controller) dom.priceSearchInput.closest(".dialog-search-row")?.classList.remove("is-searching");
    }
  }

  const debouncedPriceSearch = debounce((term) => searchPrices(term), 240);

  async function savePriceSymbol(item) {
    if (!state.selectedSecurity) return;
    const buttons = dom.priceSearchResults.querySelectorAll("button");
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const payload = await apiFetch(`/security/${encodeURIComponent(state.selectedSecurity.id)}/price-symbol`, {
        method: "POST",
        body: JSON.stringify({ symbol: item.symbol }),
      });
      const returned = normalizeSecurity(unwrapObject(payload, ["security", "item"]), state.selectedSecurity);
      state.selectedSecurity = returned || { ...state.selectedSecurity };
      state.selectedSecurity.priceSymbol = String(firstDefined(payload, ["symbol", "price_symbol"], item.symbol));
      if (item.currency) state.selectedSecurity.currency = item.currency;
      if (dom.priceDialog.open) dom.priceDialog.close("saved");
      showToast(t("priceDialog.saved"), t("priceDialog.reloading", { symbol: item.symbol }));
      await loadSecurity(state.selectedSecurity.id, state.selectedSecurity, { updateUrl: false });
    } catch (error) {
      buttons.forEach((button) => { button.disabled = false; });
      showToast(t("priceDialog.saveFailed"), errorMessage(error), "error", 6500);
    }
  }

  function lowerBound(items, target, accessor = (item) => item.time) {
    let low = 0;
    let high = items.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (accessor(items[middle]) < target) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function nearestItem(items, target) {
    if (!items.length) return null;
    const index = lowerBound(items, target);
    if (index <= 0) return items[0];
    if (index >= items.length) return items[items.length - 1];
    return target - items[index - 1].time <= items[index].time - target ? items[index - 1] : items[index];
  }

  function valueAtOrBefore(items, target, field) {
    const intervals = buildShortIntervals(items, field);
    for (let index = intervals.length - 1; index >= 0; index -= 1) {
      const interval = intervals[index];
      if (interval.time <= target && target < interval.intervalEnd) return interval;
    }
    return null;
  }

  class TrackerChart {
    constructor(canvas, tooltip, options = {}) {
      this.canvas = canvas;
      this.tooltip = tooltip;
      this.viewport = canvas.parentElement;
      this.ctx = canvas.getContext("2d");
      this.shortSeries = [];
      this.prices = [];
      this.observationTimes = [];
      this.currency = "GBP";
      this.dataStart = Date.now() - 365 * DAY;
      this.dataEnd = Date.now();
      this.viewStart = this.dataStart;
      this.viewEnd = this.dataEnd;
      this.hoverTime = null;
      this.drag = null;
      this.layout = null;
      this.frame = null;
      this.lastWidth = 0;
      this.lastHeight = 0;
      this.onViewChange = options.onViewChange || (() => {});
      this.onRangeChange = options.onRangeChange || (() => {});
      this.colors = {
        grid: "#e8e9e5",
        gridStrong: "#d4d7d5",
        text: "#788294",
        ink: "#17243b",
        legacy: "#d89332",
        ansp: "#dc4f69",
        price: "#2384b8",
        marker: "#e0574f",
        surface: "#ffffff",
      };

      this.boundPointerMove = (event) => this.handlePointerMove(event);
      this.boundPointerDown = (event) => this.handlePointerDown(event);
      this.boundPointerUp = (event) => this.handlePointerUp(event);
      this.boundPointerLeave = () => this.handlePointerLeave();
      this.boundWheel = (event) => this.handleWheel(event);
      this.boundKeydown = (event) => this.handleKeydown(event);
      this.boundDoubleClick = () => this.setRange(state.currentRange);

      canvas.addEventListener("pointermove", this.boundPointerMove);
      canvas.addEventListener("pointerdown", this.boundPointerDown);
      canvas.addEventListener("pointerup", this.boundPointerUp);
      canvas.addEventListener("pointercancel", this.boundPointerUp);
      canvas.addEventListener("pointerleave", this.boundPointerLeave);
      canvas.addEventListener("wheel", this.boundWheel, { passive: false });
      canvas.addEventListener("keydown", this.boundKeydown);
      canvas.addEventListener("dblclick", this.boundDoubleClick);
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(canvas);
      this.resize();
    }

    setData(shortSeries, prices, options = {}) {
      this.shortSeries = [...(shortSeries || [])].sort((a, b) => a.time - b.time);
      this.prices = [...(prices || [])].sort((a, b) => a.time - b.time);
      this.currency = options.currency || this.currency || "GBP";
      const intervalEnds = this.shortSeries
        .map((item) => item.anspIntervalEnd)
        .filter(Number.isFinite);
      const currentTime = this.shortSeries.some((item) => item.anspIsCurrent === true)
        ? Date.now()
        : null;
      this.observationTimes = [...new Set([
        ...this.shortSeries.map((item) => item.time),
        ...intervalEnds,
        ...(Number.isFinite(currentTime) ? [currentTime] : []),
        ...this.prices.map((item) => item.time),
      ])].sort((a, b) => a - b);
      if (this.observationTimes.length) {
        this.dataStart = this.observationTimes[0];
        this.dataEnd = this.observationTimes[this.observationTimes.length - 1];
        if (this.dataStart === this.dataEnd) {
          this.dataStart -= DAY;
          this.dataEnd += DAY;
        }
      } else {
        this.dataEnd = Date.now();
        this.dataStart = this.dataEnd - 365 * DAY;
      }
      this.hoverTime = null;
      this.hideTooltip();
      this.requestDraw();
    }

    refreshLanguage() {
      this.canvas.setAttribute("aria-label", t("canvas.rangeAria", {
        start: formatDate(this.viewStart),
        end: formatDate(this.viewEnd),
      }));
      if (!this.tooltip.hidden && Number.isFinite(this.hoverTime)) this.showTooltip(this.hoverTime);
      this.requestDraw();
    }

    getViewBounds() {
      return { start: this.viewStart, end: this.viewEnd };
    }

    rangeStart(range, end) {
      const date = new Date(end);
      if (range === "YTD") return Date.UTC(date.getUTCFullYear(), 0, 1);
      const months = { "1M": 1, "3M": 3, "6M": 6 }[range];
      if (months) {
        const result = new Date(end);
        result.setUTCMonth(result.getUTCMonth() - months);
        return result.getTime();
      }
      const years = { "1Y": 1, "3Y": 3, "5Y": 5 }[range];
      if (years) {
        const result = new Date(end);
        result.setUTCFullYear(result.getUTCFullYear() - years);
        return result.getTime();
      }
      return this.dataStart;
    }

    setRange(range, { announce = true } = {}) {
      const key = range || DEFAULT_RANGE;
      const end = this.dataEnd;
      const rawStart = this.rangeStart(key, end);
      const start = key === "MAX" ? this.dataStart : Math.max(this.dataStart, rawStart);
      this.setView(start, end, { source: key, announce });
      if (announce) this.onRangeChange(key);
    }

    setView(start, end, { source = "custom", announce = true } = {}) {
      if (!Number.isFinite(start) || !Number.isFinite(end)) return;
      const fullSpan = Math.max(DAY, this.dataEnd - this.dataStart);
      const minSpan = Math.min(fullSpan, 7 * DAY);
      let nextStart = Math.min(start, end - minSpan);
      let nextEnd = Math.max(end, start + minSpan);
      let span = Math.min(fullSpan, Math.max(minSpan, nextEnd - nextStart));
      if (nextStart < this.dataStart) {
        nextStart = this.dataStart;
        nextEnd = nextStart + span;
      }
      if (nextEnd > this.dataEnd) {
        nextEnd = this.dataEnd;
        nextStart = nextEnd - span;
      }
      nextStart = Math.max(this.dataStart, nextStart);
      nextEnd = Math.min(this.dataEnd, nextEnd);
      if (nextEnd <= nextStart) {
        nextStart = this.dataStart;
        nextEnd = this.dataEnd;
      }
      this.viewStart = nextStart;
      this.viewEnd = nextEnd;
      this.requestDraw();
      this.onViewChange(this.viewStart, this.viewEnd, source);
      this.canvas.setAttribute("aria-label", t("canvas.rangeAria", {
        start: formatDate(this.viewStart),
        end: formatDate(this.viewEnd),
      }));
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const width = Math.max(320, Math.round(rect.width));
      const height = Math.max(420, Math.round(rect.height));
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (width === this.lastWidth && height === this.lastHeight && this.canvas.width === Math.round(width * dpr)) return;
      this.lastWidth = width;
      this.lastHeight = height;
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.requestDraw();
    }

    requestDraw() {
      if (this.frame !== null) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.draw();
      });
    }

    calculateLayout() {
      const width = this.lastWidth || this.canvas.clientWidth;
      const height = this.lastHeight || this.canvas.clientHeight;
      const compact = width < 640;
      const left = compact ? 49 : 65;
      const right = compact ? 18 : 58;
      const top = compact ? 28 : 34;
      const bottom = compact ? 42 : 47;
      const gap = compact ? 48 : 54;
      const plotWidth = Math.max(100, width - left - right);
      const usable = Math.max(220, height - top - bottom - gap);
      const upperHeight = usable * 0.58;
      return {
        width,
        height,
        left,
        right,
        plotWidth,
        plotRight: left + plotWidth,
        upper: { top, bottom: top + upperHeight, height: upperHeight },
        lower: { top: top + upperHeight + gap, bottom: top + usable + gap, height: usable - upperHeight },
      };
    }

    xForTime(time) {
      const span = Math.max(1, this.viewEnd - this.viewStart);
      return this.layout.left + ((time - this.viewStart) / span) * this.layout.plotWidth;
    }

    timeForX(x) {
      const ratio = Math.max(0, Math.min(1, (x - this.layout.left) / this.layout.plotWidth));
      return this.viewStart + ratio * (this.viewEnd - this.viewStart);
    }

    getShortBounds() {
      const values = [];
      for (const field of ["legacy", "ansp"]) {
        for (const interval of buildShortIntervals(this.shortSeries, field)) {
          if (interval.intervalEnd > this.viewStart && interval.time <= this.viewEnd) values.push(interval.value);
        }
      }
      if (!values.length) return { min: 0, max: 1, hasData: false };
      const maxValue = Math.max(...values, 0.25);
      const magnitude = 10 ** Math.floor(Math.log10(maxValue));
      const candidates = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude);
      const niceMax = candidates.find((value) => value >= maxValue * 1.12) || Math.ceil(maxValue / magnitude) * magnitude;
      return { min: 0, max: Math.max(0.5, niceMax), hasData: true };
    }

    getPriceBounds() {
      const visible = this.prices.filter((item) => item.time >= this.viewStart && item.time <= this.viewEnd);
      if (!visible.length) return { min: 0, max: 1, hasData: false };
      let min = Math.min(...visible.map((item) => item.close));
      let max = Math.max(...visible.map((item) => item.close));
      const rawSpan = max - min;
      const pad = rawSpan > 0 ? rawSpan * 0.12 : Math.max(Math.abs(max) * 0.08, 1);
      min -= pad;
      max += pad;
      if (min < 0 && Math.min(...visible.map((item) => item.close)) >= 0) min = 0;
      return { min, max, hasData: true };
    }

    yForValue(value, bounds, panel) {
      const span = Math.max(Number.EPSILON, bounds.max - bounds.min);
      return panel.bottom - ((value - bounds.min) / span) * panel.height;
    }

    draw() {
      if (!this.ctx || !this.lastWidth || !this.lastHeight) return;
      const ctx = this.ctx;
      this.layout = this.calculateLayout();
      const { width, height, upper, lower } = this.layout;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = this.colors.surface;
      ctx.fillRect(0, 0, width, height);

      const shortBounds = this.getShortBounds();
      const priceBounds = this.getPriceBounds();
      this.drawPanelBackground(upper);
      this.drawPanelBackground(lower);
      this.drawGrid(shortBounds, priceBounds);
      this.drawPanelLabels();
      this.drawRegimeMarker();

      ctx.save();
      ctx.beginPath();
      ctx.rect(this.layout.left, upper.top, this.layout.plotWidth, upper.height);
      ctx.clip();
      this.drawStepSeries("legacy", this.colors.legacy, shortBounds, upper, Math.min(this.viewEnd, REGIME_SWITCH));
      this.drawStepSeries("ansp", this.colors.ansp, shortBounds, upper);
      ctx.restore();

      ctx.save();
      ctx.beginPath();
      ctx.rect(this.layout.left, lower.top, this.layout.plotWidth, lower.height);
      ctx.clip();
      this.drawPriceSeries(priceBounds, lower);
      ctx.restore();

      if (!shortBounds.hasData) this.drawPanelEmpty(upper, t("canvas.noShort"));
      if (!priceBounds.hasData) this.drawPanelEmpty(lower, t("canvas.noPrice"));
      this.drawCrosshair(shortBounds, priceBounds);
    }

    drawPanelBackground(panel) {
      const ctx = this.ctx;
      ctx.fillStyle = "#fbfbf9";
      ctx.fillRect(this.layout.left, panel.top, this.layout.plotWidth, panel.height);
      ctx.strokeStyle = this.colors.gridStrong;
      ctx.lineWidth = 1;
      ctx.strokeRect(this.layout.left + 0.5, panel.top + 0.5, this.layout.plotWidth - 1, panel.height - 1);
    }

    drawGrid(shortBounds, priceBounds) {
      const ctx = this.ctx;
      const { upper, lower, left, plotRight } = this.layout;
      ctx.save();
      ctx.lineWidth = 1;
      ctx.font = '10px "Cascadia Mono", Consolas, monospace';
      ctx.fillStyle = this.colors.text;

      for (let index = 0; index <= 4; index += 1) {
        const ratio = index / 4;
        const yUpper = upper.bottom - ratio * upper.height;
        const yLower = lower.bottom - ratio * lower.height;
        ctx.strokeStyle = this.colors.grid;
        ctx.beginPath();
        ctx.moveTo(left, yUpper + 0.5);
        ctx.lineTo(plotRight, yUpper + 0.5);
        ctx.moveTo(left, yLower + 0.5);
        ctx.lineTo(plotRight, yLower + 0.5);
        ctx.stroke();

        const shortValue = shortBounds.min + ratio * (shortBounds.max - shortBounds.min);
        const priceValue = priceBounds.min + ratio * (priceBounds.max - priceBounds.min);
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(`${formatCompactNumber(shortValue)}%`, left - 9, yUpper);
        if (priceBounds.hasData) ctx.fillText(formatAxisPrice(priceValue, this.currency), left - 9, yLower);
      }

      const span = this.viewEnd - this.viewStart;
      const tickCount = this.layout.width < 560 ? 3 : this.layout.width < 840 ? 5 : 7;
      for (let index = 0; index < tickCount; index += 1) {
        const ratio = tickCount === 1 ? 0 : index / (tickCount - 1);
        const time = this.viewStart + ratio * span;
        const x = left + ratio * this.layout.plotWidth;
        ctx.strokeStyle = this.colors.grid;
        ctx.beginPath();
        ctx.moveTo(x + 0.5, upper.top);
        ctx.lineTo(x + 0.5, lower.bottom);
        ctx.stroke();
        ctx.fillStyle = this.colors.text;
        ctx.textBaseline = "top";
        ctx.textAlign = index === 0 ? "left" : index === tickCount - 1 ? "right" : "center";
        const label = span > 4 * 365 * DAY
          ? yearFormatter.format(new Date(time))
          : span > 150 * DAY
            ? monthFormatter.format(new Date(time))
            : formatCompactDate(time);
        ctx.fillText(label, x, lower.bottom + 13);
      }
      ctx.restore();
    }

    drawPanelLabels() {
      const ctx = this.ctx;
      ctx.save();
      ctx.font = '600 10px "Aptos", "Segoe UI", sans-serif';
      ctx.textBaseline = "bottom";
      ctx.textAlign = "left";
      ctx.fillStyle = this.colors.ink;
      ctx.fillText(t("canvas.shortPanel"), this.layout.left, this.layout.upper.top - 9);
      ctx.fillText(t("canvas.pricePanel", { currency: this.currency || "—" }), this.layout.left, this.layout.lower.top - 9);
      ctx.restore();
    }

    drawRegimeMarker() {
      if (REGIME_SWITCH < this.viewStart || REGIME_SWITCH > this.viewEnd) return;
      const ctx = this.ctx;
      const x = this.xForTime(REGIME_SWITCH);
      ctx.save();
      ctx.strokeStyle = this.colors.marker;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x + 0.5, this.layout.upper.top);
      ctx.lineTo(x + 0.5, this.layout.lower.bottom);
      ctx.stroke();
      ctx.setLineDash([]);

      const label = t(this.layout.width < 560 ? "canvas.newRegimeCompact" : "canvas.newRegime");
      ctx.font = '600 9px "Cascadia Mono", Consolas, monospace';
      const padding = 6;
      const boxWidth = ctx.measureText(label).width + padding * 2;
      const boxX = Math.max(this.layout.left, Math.min(x + 7, this.layout.plotRight - boxWidth));
      const boxY = this.layout.upper.top + 7;
      ctx.fillStyle = "rgba(255, 240, 237, 0.96)";
      ctx.fillRect(boxX, boxY, boxWidth, 21);
      ctx.strokeStyle = "rgba(224, 87, 79, 0.24)";
      ctx.strokeRect(boxX + 0.5, boxY + 0.5, boxWidth - 1, 20);
      ctx.fillStyle = this.colors.marker;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(label, boxX + padding, boxY + 11);
      ctx.restore();
    }

    drawStepSeries(field, color, bounds, panel, effectiveEnd = this.viewEnd, effectiveStart = this.viewStart) {
      const start = Math.max(this.viewStart, effectiveStart);
      const end = Math.min(this.viewEnd, effectiveEnd);
      if (end < start) return;
      const intervals = buildShortIntervals(this.shortSeries, field, effectiveEnd)
        .map((interval) => ({
          ...interval,
          visibleStart: Math.max(start, interval.time),
          visibleEnd: Math.min(end, interval.intervalEnd),
        }))
        .filter((interval) => interval.visibleEnd > interval.visibleStart);
      if (!intervals.length) return;

      const ctx = this.ctx;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();
      let previous = null;
      for (const interval of intervals) {
        const startX = this.xForTime(interval.visibleStart);
        const endX = this.xForTime(interval.visibleEnd);
        const y = this.yForValue(interval.value, bounds, panel);
        if (previous && previous.visibleEnd === interval.visibleStart) {
          ctx.lineTo(startX, this.yForValue(previous.value, bounds, panel));
          ctx.lineTo(startX, y);
        } else {
          ctx.moveTo(startX, y);
        }
        ctx.lineTo(endX, y);
        previous = interval;
      }
      ctx.stroke();
      ctx.restore();
    }

    drawPriceSeries(bounds, panel) {
      const visible = this.prices.filter((item) => item.time >= this.viewStart && item.time <= this.viewEnd);
      if (!visible.length) return;
      const ctx = this.ctx;
      const gradient = ctx.createLinearGradient(0, panel.top, 0, panel.bottom);
      gradient.addColorStop(0, "rgba(35, 132, 184, 0.18)");
      gradient.addColorStop(1, "rgba(35, 132, 184, 0.015)");
      ctx.save();
      ctx.beginPath();
      visible.forEach((item, index) => {
        const x = this.xForTime(item.time);
        const y = this.yForValue(item.close, bounds, panel);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      const last = visible[visible.length - 1];
      const first = visible[0];
      ctx.lineTo(this.xForTime(last.time), panel.bottom);
      ctx.lineTo(this.xForTime(first.time), panel.bottom);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();

      ctx.beginPath();
      visible.forEach((item, index) => {
        const x = this.xForTime(item.time);
        const y = this.yForValue(item.close, bounds, panel);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = this.colors.price;
      ctx.lineWidth = 1.8;
      ctx.lineJoin = "round";
      ctx.stroke();

      const latest = visible[visible.length - 1];
      const latestX = this.xForTime(latest.time);
      const latestY = this.yForValue(latest.close, bounds, panel);
      ctx.fillStyle = this.colors.surface;
      ctx.beginPath();
      ctx.arc(latestX, latestY, 4.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = this.colors.price;
      ctx.lineWidth = 2.2;
      ctx.stroke();

      const label = formatAxisPrice(latest.close, this.currency);
      ctx.font = '600 9px "Cascadia Mono", Consolas, monospace';
      const labelWidth = ctx.measureText(label).width + 11;
      const labelX = Math.min(this.layout.plotRight - labelWidth - 3, latestX + 7);
      const labelY = Math.max(panel.top + 3, Math.min(panel.bottom - 21, latestY - 10));
      ctx.fillStyle = "rgba(35, 132, 184, 0.92)";
      ctx.fillRect(labelX, labelY, labelWidth, 19);
      ctx.fillStyle = "#fff";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(label, labelX + 5.5, labelY + 9.5);
      ctx.restore();
    }

    drawPanelEmpty(panel, label) {
      const ctx = this.ctx;
      ctx.save();
      ctx.fillStyle = this.colors.text;
      ctx.font = '10px "Aptos", "Segoe UI", sans-serif';
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(label, this.layout.left + this.layout.plotWidth / 2, panel.top + panel.height / 2);
      ctx.restore();
    }

    tooltipValues(time) {
      const legacy = time < REGIME_SWITCH ? valueAtOrBefore(this.shortSeries, time, "legacy") : null;
      const ansp = valueAtOrBefore(this.shortSeries, time, "ansp");
      const price = nearestItem(this.prices, time);
      const maxPriceDistance = 5 * DAY;
      return {
        legacy: legacy?.value ?? null,
        ansp: ansp?.value ?? null,
        anspInterval: ansp,
        price: price && Math.abs(price.time - time) <= maxPriceDistance ? price : null,
      };
    }

    drawCrosshair(shortBounds, priceBounds) {
      if (!Number.isFinite(this.hoverTime) || this.hoverTime < this.viewStart || this.hoverTime > this.viewEnd) return;
      const ctx = this.ctx;
      const values = this.tooltipValues(this.hoverTime);
      const x = this.xForTime(this.hoverTime);
      ctx.save();
      ctx.strokeStyle = "rgba(23, 36, 59, 0.38)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(x + 0.5, this.layout.upper.top);
      ctx.lineTo(x + 0.5, this.layout.lower.bottom);
      ctx.stroke();
      ctx.setLineDash([]);

      const drawDot = (value, bounds, panel, color) => {
        if (!Number.isFinite(value)) return;
        const y = this.yForValue(value, bounds, panel);
        ctx.fillStyle = this.colors.surface;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
      };
      drawDot(values.legacy, shortBounds, this.layout.upper, this.colors.legacy);
      drawDot(values.ansp, shortBounds, this.layout.upper, this.colors.ansp);
      if (values.price) drawDot(values.price.close, priceBounds, this.layout.lower, this.colors.price);
      ctx.restore();
    }

    showTooltip(time, pointerX = null, pointerY = null) {
      this.hoverTime = Math.max(this.viewStart, Math.min(this.viewEnd, time));
      const values = this.tooltipValues(this.hoverTime);
      setText(dom.tooltipDate, formatDate(this.hoverTime));
      setText(dom.tooltipLegacy, formatPercent(values.legacy));
      setText(dom.tooltipAnsp, formatPercent(values.ansp));
      setText(dom.tooltipPrice, values.price ? formatCurrency(values.price.close, this.currency) : "—");
      dom.tooltipLegacyRow.hidden = !Number.isFinite(values.legacy);
      dom.tooltipAnspRow.hidden = !Number.isFinite(values.ansp);
      dom.tooltipAnspAudit.hidden = !values.anspInterval;
      if (values.anspInterval) {
        const interval = values.anspInterval;
        const point = interval.point || {};
        const intervalEnd = Number.isFinite(point.anspIntervalEnd)
          ? formatDate(point.anspIntervalEnd)
          : interval.isCurrent === true
            ? t("chart.auditCurrentOpen")
            : Number.isFinite(interval.intervalEnd)
              ? formatDate(interval.intervalEnd)
              : t("chart.auditOpen");
        setText(dom.tooltipAnspEffective, formatDate(interval.time));
        setText(dom.tooltipAnspPositionDate, formatDate(point.anspPositionTime));
        setText(dom.tooltipAnspIntervalEnd, intervalEnd);
        setText(dom.tooltipAnspDateBasis, formatAnspDateBasis(point.anspChartDateBasis));
        setText(dom.tooltipAnspFirstPublished, formatDate(point.anspFirstPublishedTime));
      }
      dom.tooltipPriceRow.hidden = !values.price;
      this.tooltip.hidden = false;

      const x = Number.isFinite(pointerX) ? pointerX : this.xForTime(this.hoverTime);
      const y = Number.isFinite(pointerY) ? pointerY : this.layout.upper.top + 18;
      const tooltipWidth = this.tooltip.offsetWidth || 195;
      const tooltipHeight = this.tooltip.offsetHeight || 120;
      const left = x + tooltipWidth + 22 > this.layout.width ? x - tooltipWidth - 14 : x + 14;
      const top = Math.max(8, Math.min(this.layout.height - tooltipHeight - 8, y - 18));
      this.tooltip.style.left = `${Math.max(8, left)}px`;
      this.tooltip.style.top = `${top}px`;
      this.requestDraw();
    }

    hideTooltip() {
      this.tooltip.hidden = true;
      this.hoverTime = null;
      this.requestDraw();
    }

    eventPoint(event) {
      const rect = this.canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    insidePlot(point) {
      return point.x >= this.layout.left && point.x <= this.layout.plotRight && point.y >= this.layout.upper.top && point.y <= this.layout.lower.bottom;
    }

    handlePointerDown(event) {
      if (!this.observationTimes.length || !this.layout) return;
      const point = this.eventPoint(event);
      if (!this.insidePlot(point)) return;
      this.drag = { pointerId: event.pointerId, x: point.x, start: this.viewStart, end: this.viewEnd, moved: false };
      this.canvas.setPointerCapture(event.pointerId);
      this.viewport.classList.add("is-dragging");
    }

    handlePointerMove(event) {
      if (!this.layout || !this.observationTimes.length) return;
      const point = this.eventPoint(event);
      if (this.drag && this.drag.pointerId === event.pointerId) {
        const delta = point.x - this.drag.x;
        if (Math.abs(delta) > 2) this.drag.moved = true;
        const shift = -(delta / this.layout.plotWidth) * (this.drag.end - this.drag.start);
        this.setView(this.drag.start + shift, this.drag.end + shift, { source: "pan", announce: false });
        this.hideTooltip();
        return;
      }
      if (!this.insidePlot(point)) {
        this.hideTooltip();
        return;
      }
      this.showTooltip(this.timeForX(point.x), point.x, point.y);
    }

    handlePointerUp(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      const point = this.eventPoint(event);
      const moved = this.drag.moved;
      this.drag = null;
      this.viewport.classList.remove("is-dragging");
      if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
      if (!moved && this.insidePlot(point)) this.showTooltip(this.timeForX(point.x), point.x, point.y);
    }

    handlePointerLeave() {
      if (!this.drag) this.hideTooltip();
    }

    handleWheel(event) {
      if (!this.observationTimes.length || !this.layout) return;
      event.preventDefault();
      const point = this.eventPoint(event);
      const anchor = this.timeForX(Math.max(this.layout.left, Math.min(this.layout.plotRight, point.x)));
      const factor = Math.exp(Math.max(-1, Math.min(1, event.deltaY * 0.0015)));
      this.zoomAt(factor, anchor);
    }

    zoomAt(factor, anchor = (this.viewStart + this.viewEnd) / 2) {
      const span = this.viewEnd - this.viewStart;
      const nextSpan = span * factor;
      const ratio = span ? (anchor - this.viewStart) / span : 0.5;
      const start = anchor - nextSpan * ratio;
      const end = start + nextSpan;
      this.setView(start, end, { source: "zoom", announce: false });
    }

    handleKeydown(event) {
      if (!this.observationTimes.length) return;
      const visibleTimes = this.observationTimes.filter((time) => time >= this.viewStart && time <= this.viewEnd);
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (!visibleTimes.length) return;
        let index = Number.isFinite(this.hoverTime)
          ? Math.max(0, Math.min(visibleTimes.length - 1, lowerBound(visibleTimes, this.hoverTime, (value) => value)))
          : visibleTimes.length - 1;
        if (event.key === "ArrowLeft") index = Math.max(0, index - 1);
        if (event.key === "ArrowRight") index = Math.min(visibleTimes.length - 1, index + 1);
        if (event.key === "Home") index = 0;
        if (event.key === "End") index = visibleTimes.length - 1;
        this.showTooltip(visibleTimes[index]);
      } else if (["+", "=", "Add"].includes(event.key)) {
        event.preventDefault();
        this.zoomAt(0.72, this.hoverTime || (this.viewStart + this.viewEnd) / 2);
      } else if (["-", "_", "Subtract"].includes(event.key)) {
        event.preventDefault();
        this.zoomAt(1.38, this.hoverTime || (this.viewStart + this.viewEnd) / 2);
      } else if (event.key === "PageUp" || event.key === "PageDown") {
        event.preventDefault();
        const direction = event.key === "PageUp" ? -1 : 1;
        const shift = (this.viewEnd - this.viewStart) * 0.2 * direction;
        this.setView(this.viewStart + shift, this.viewEnd + shift, { source: "pan", announce: false });
      } else if (event.key === "Escape") {
        this.hideTooltip();
      }
    }
  }

  function bindEvents() {
    dom.languageSwitch.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-language]");
      if (!button || button.dataset.language === state.language) return;
      setLanguage(button.dataset.language);
    });

    dom.securitySearch.addEventListener("submit", (event) => {
      event.preventDefault();
      const currentTerm = dom.securitySearchInput.value.trim();
      const selected = state.lastSearchTerm === currentTerm
        ? state.searchResults[state.activeSearchIndex] || state.searchResults[0]
        : null;
      if (selected) selectSecurity(selected);
      else searchSecurities(currentTerm);
    });
    dom.securitySearchInput.addEventListener("input", handleSecuritySearchInput);
    dom.securitySearchInput.addEventListener("keydown", handleSecuritySearchKeydown);
    dom.securitySearchInput.addEventListener("focus", () => {
      if (state.searchResults.length) renderSecurityResults(state.searchResults, dom.securitySearchInput.value.trim());
      else searchSecurities(dom.securitySearchInput.value.trim());
    });
    dom.searchClear.addEventListener("click", () => {
      dom.securitySearchInput.value = "";
      dom.searchClear.hidden = true;
      dom.securitySearchInput.focus();
      searchSecurities("");
    });
    document.addEventListener("pointerdown", (event) => {
      if (!dom.securitySearch.contains(event.target)) closeSecurityResults();
    });

    dom.focusSearchButton.addEventListener("click", () => dom.securitySearchInput.focus());
    dom.retryStatusButton.addEventListener("click", () => {
      loadStatus();
      if (state.activeView === "rankings") loadRankings();
    });
    dom.syncButton.addEventListener("click", syncData);
    dom.settingsButton.addEventListener("click", openSettings);
    dom.settingsCloseButton.addEventListener("click", () => dom.settingsDialog.close("cancel"));
    dom.settingsCancelButton.addEventListener("click", () => dom.settingsDialog.close("cancel"));
    dom.settingsForm.addEventListener("submit", saveSettings);
    dom.autoSyncEnabled.addEventListener("change", () => {
      state.settingsDirty = true;
      dom.autoSyncInterval.disabled = !dom.autoSyncEnabled.checked;
    });
    dom.autoSyncInterval.addEventListener("change", () => { state.settingsDirty = true; });
    dom.checkUpdateButton.addEventListener("click", () => checkForUpdates({ force: true }));
    dom.openReleaseButton.addEventListener("click", openExternalRelease);
    dom.settingsOpenReleaseButton.addEventListener("click", openExternalRelease);
    dom.dismissUpdateButton.addEventListener("click", () => {
      const version = dom.updateBanner.dataset.version || "";
      try {
        if (version) window.localStorage.setItem(DISMISSED_UPDATE_STORAGE_KEY, version);
      } catch {
        // Dismissal remains session-only when storage is unavailable.
      }
      dom.updateBanner.hidden = true;
    });
    dom.settingsDialog.addEventListener("click", (event) => {
      if (event.target === dom.settingsDialog) dom.settingsDialog.close("cancel");
    });
    dom.retryRankingsButton.addEventListener("click", () => loadRankings());
    dom.backToRankingsButton.addEventListener("click", () => showRankings({ updateUrl: true, focus: true }));
    dom.retrySecurityButton.addEventListener("click", () => {
      if (state.selectedSecurity) loadSecurity(state.selectedSecurity.id, state.selectedSecurity, { updateUrl: false });
    });

    dom.rankingSearchInput.addEventListener("input", () => {
      state.rankingFilter = dom.rankingSearchInput.value;
      state.rankingPage = 1;
      renderRankingTable();
    });
    dom.rankingPageSize.addEventListener("change", () => {
      state.rankingPageSize = Number(dom.rankingPageSize.value) === 50 ? 50 : 25;
      state.rankingPage = 1;
      renderRankingTable();
    });
    dom.rankingTable.querySelector("thead").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-ranking-sort]");
      if (!button) return;
      const key = button.dataset.rankingSort;
      if (state.rankingSort.key === key) {
        state.rankingSort.direction = state.rankingSort.direction === "asc" ? "desc" : "asc";
      } else {
        state.rankingSort = {
          key,
          direction: key === "shortPercent" || key === "positionTime" ? "desc" : "asc",
        };
      }
      state.rankingPage = 1;
      renderRankingTable();
    });
    dom.rankingPreviousPage.addEventListener("click", () => {
      if (state.rankingPage <= 1) return;
      state.rankingPage -= 1;
      renderRankingTable();
      dom.rankingTable.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    dom.rankingNextPage.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil(getFilteredRankings().length / state.rankingPageSize));
      if (state.rankingPage >= totalPages) return;
      state.rankingPage += 1;
      renderRankingTable();
      dom.rankingTable.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    dom.rangeSelector.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-range]");
      if (!button) return;
      const range = button.dataset.range;
      updateRangeButtons(range);
      state.chart?.setRange(range);
    });
    dom.resetViewButton.addEventListener("click", () => {
      updateRangeButtons(state.currentRange || DEFAULT_RANGE);
      state.chart?.setRange(state.currentRange || DEFAULT_RANGE);
    });

    for (const button of [dom.openPriceDialogButton, dom.emptyPriceButton]) {
      button.addEventListener("click", openPriceDialog);
    }
    dom.priceSearchInput.addEventListener("input", () => debouncedPriceSearch(dom.priceSearchInput.value));
    dom.priceSearchInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
    dom.priceDialog.addEventListener("close", () => {
      debouncedPriceSearch.cancel();
      state.priceSearchController?.abort();
      dom.priceSearchInput.closest(".dialog-search-row")?.classList.remove("is-searching");
    });
    dom.priceDialog.addEventListener("click", (event) => {
      if (event.target === dom.priceDialog) dom.priceDialog.close("cancel");
    });

    window.addEventListener("popstate", () => {
      const id = new URLSearchParams(window.location.search).get("security");
      if (id) {
        if (id !== state.selectedSecurity?.id || state.activeView !== "security") loadSecurity(id, null, { updateUrl: false });
        else setAppView("security");
      } else {
        showRankings({ updateUrl: false, focus: true });
      }
    });
    window.addEventListener("online", () => {
      loadStatus();
      if (state.activeView === "rankings") loadRankings();
    });
    window.addEventListener("offline", () => setConnectionState("offline", "connection.networkOffline", "connection.browserOfflineDetail"));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadStatus({ quiet: true });
    });
    window.addEventListener("beforeunload", () => {
      if (state.statusPollTimer) window.clearInterval(state.statusPollTimer);
      state.searchController?.abort();
      state.loadController?.abort();
      state.priceSearchController?.abort();
      state.rankingController?.abort();
    });
  }

  async function init() {
    setLanguage(state.language, { persist: false, rerender: false });
    state.chart = new TrackerChart(dom.trackerChart, dom.chartTooltip, {
      onViewChange(start, end, source) {
        updateKpis(start, end);
        if (source === "pan" || source === "zoom") {
          for (const button of dom.rangeSelector.querySelectorAll("button[data-range]")) {
            button.setAttribute("aria-pressed", "false");
          }
        }
      },
      onRangeChange(range) {
        updateRangeButtons(range);
      },
    });
    bindEvents();
    updateRangeButtons(DEFAULT_RANGE);
    const requestedId = new URLSearchParams(window.location.search).get("security");
    if (requestedId) {
      setAppView("security");
      window.history.replaceState({ view: "security", security: requestedId }, "", window.location.href);
    } else {
      showRankings({ updateUrl: true, focus: false });
    }
    try {
      const tasks = [loadStatus(), loadRankings({ silent: Boolean(requestedId) })];
      if (requestedId) tasks.push(loadSecurity(requestedId, null, { updateUrl: false }));
      await Promise.allSettled(tasks);
      await checkForUpdates({ force: false });
      state.statusPollTimer = window.setInterval(() => {
        if (document.visibilityState === "visible") loadStatus({ quiet: true });
      }, STATUS_POLL_INTERVAL);
    } catch (error) {
      setConnectionState("offline", "connection.initialFailed", "error.requestFailed");
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
