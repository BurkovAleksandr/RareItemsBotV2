import { useEffect, useMemo, useRef, useState } from "react";

const api = {
  async getJson(path, label) {
    const response = await fetch(path);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `${label} request failed: ${response.status}`);
    return data;
  },
  async getDashboardSummary() {
    return this.getJson("/api/dashboard/summary", "Dashboard summary");
  },
  async getRuntime() {
    return this.getJson("/api/dashboard/runtime", "Runtime");
  },
  async getSessions() {
    return this.getJson("/api/dashboard/sessions", "Sessions");
  },
  async getRecentChecked() {
    return this.getJson("/api/dashboard/recent-checked", "Recent checked");
  },
  async getRecentPurchases() {
    return this.getJson("/api/dashboard/recent-purchases", "Recent purchases");
  },
  async getStickerPrices() {
    return this.getJson("/api/dashboard/sticker-prices", "Sticker prices");
  },
  async getConfig() {
    return this.getJson("/api/dashboard/config", "Config");
  },
  async getTrackedItems() {
    return this.getJson("/api/dashboard/tracked-items", "Tracked items");
  },
  async getProxies() {
    return this.getJson("/api/dashboard/proxies", "Proxies");
  },
  async getCheckedItems(filters) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") {
        params.set(key, value);
      }
    });
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`/api/checked-items${suffix}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Checked items request failed: ${response.status}`);
    return data;
  },
  async post(path) {
    const response = await fetch(path, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.message || `Request failed: ${response.status}`);
    return data;
  },
  async put(path, body) {
    const response = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.message || `Request failed: ${response.status}`);
    return data;
  },
};

const DEFAULT_HISTORY_FILTERS = {
  date_from: "",
  date_to: "",
  min_stickers_price: "",
  max_stickers_price: "",
  min_item_price: "",
  max_item_price: "",
  has_streak: "",
  limit: "",
};

function formatValue(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (Number.isFinite(number)) return `${number.toFixed(2).replace(/\.?0+$/, "")}${suffix}`;
  return `${value}${suffix}`;
}

function formatRatio(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "-";
  return `${number.toFixed(2).replace(/\.?0+$/, "")}x`;
}

function stateClass(value) {
  if (value === true || value === "ok" || value === "success") return "ok";
  if (value === false || value === "error" || value === "danger") return "danger";
  if (value === "active" || value === "starting") return "warn";
  return "idle";
}

function dateBoundary(value, endOfDay = false) {
  if (!value) return "";
  return `${value} ${endOfDay ? "23:59:59" : "00:00:00"}`;
}

function buildHistoryQuery(filters) {
  return {
    date_from: dateBoundary(filters.date_from),
    date_to: dateBoundary(filters.date_to, true),
    min_stickers_price: filters.min_stickers_price,
    max_stickers_price: filters.max_stickers_price,
    min_item_price: filters.min_item_price,
    max_item_price: filters.max_item_price,
    has_streak: filters.has_streak,
    limit: filters.limit,
  };
}

function stickerSlot(sticker, index) {
  return sticker.slot ?? sticker.slot_index ?? sticker.position ?? index + 1;
}

function App() {
  const [view, setView] = useState("dashboard");
  const [summary, setSummary] = useState(null);
  const [sessions, setSessions] = useState({});
  const [runtime, setRuntime] = useState({ steps: [] });
  const [recentCheckedItems, setRecentCheckedItems] = useState([]);
  const [recentPurchases, setRecentPurchases] = useState([]);
  const [recentStickerPrices, setRecentStickerPrices] = useState([]);
  const [configFields, setConfigFields] = useState([]);
  const [sectionLoading, setSectionLoading] = useState({});
  const [historyLoading, setHistoryLoading] = useState(false);
  const [checkedHistory, setCheckedHistory] = useState({ items: [], count: 0 });
  const [historyFilters, setHistoryFilters] = useState(DEFAULT_HISTORY_FILTERS);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [itemsText, setItemsText] = useState("");
  const [proxiesText, setProxiesText] = useState("");
  const [configDraft, setConfigDraft] = useState({});
  const [itemsMode, setItemsMode] = useState("replace");
  const [expandExteriors, setExpandExteriors] = useState(false);
  const [useProxies, setUseProxies] = useState(false);
  const formsInitialized = useRef(false);

  async function loadSection(name, request, apply) {
    setSectionLoading((current) => ({ ...current, [name]: true }));
    try {
      const payload = await request();
      apply(payload);
      return payload;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setSectionLoading((current) => ({ ...current, [name]: false }));
    }
  }

  function refreshSummary() {
    return loadSection("summary", () => api.getDashboardSummary(), setSummary);
  }

  function refreshRuntime() {
    return loadSection("runtime", () => api.getRuntime(), (payload) => setRuntime(payload.runtime || { steps: [] }));
  }

  function refreshSessions() {
    return loadSection("sessions", () => api.getSessions(), (payload) => setSessions(payload.sessions || {}));
  }

  function refreshRecentChecked() {
    return loadSection("recentChecked", () => api.getRecentChecked(), (payload) => setRecentCheckedItems(payload.items || []));
  }

  function refreshRecentPurchases() {
    return loadSection("recentPurchases", () => api.getRecentPurchases(), (payload) => setRecentPurchases(payload.items || []));
  }

  function refreshStickerPrices() {
    return loadSection("stickerPrices", () => api.getStickerPrices(), (payload) => setRecentStickerPrices(payload.rows || []));
  }

  function refreshConfig() {
    return loadSection("config", () => api.getConfig(), (payload) => {
      setConfigFields(payload.config_fields || []);
      setConfigDraft(payload.config || {});
    });
  }

  function refreshTrackedItems() {
    return loadSection("trackedItems", () => api.getTrackedItems(), (payload) => {
      setItemsText(payload.items_text || "");
    });
  }

  function refreshProxies() {
    return loadSection("proxies", () => api.getProxies(), (payload) => {
      setProxiesText(payload.proxies_text || "");
      setUseProxies(Boolean(payload.proxies_enabled));
    });
  }

  async function refreshAll({ includeForms = false } = {}) {
    setError("");
    const requests = [
      refreshSummary(),
      refreshRuntime(),
      refreshSessions(),
      refreshRecentChecked(),
      refreshRecentPurchases(),
      refreshStickerPrices(),
    ];

    if (includeForms || !formsInitialized.current) {
      requests.push(refreshConfig(), refreshTrackedItems(), refreshProxies());
      formsInitialized.current = true;
    }

    await Promise.all(requests);
  }

  async function refreshHistory(filters = historyFilters) {
    setHistoryLoading(true);
    setError("");
    try {
      setCheckedHistory(await api.getCheckedItems(buildHistoryQuery(filters)));
    } catch (err) {
      setError(err.message);
    } finally {
      setHistoryLoading(false);
    }
  }

  async function runAction(action) {
    setError("");
    try {
      const result = await action();
      setMessage(result.message || "Done");
      if (result.config) {
        setConfigDraft(result.config);
      }
      await Promise.all([
        refreshSummary(),
        refreshRuntime(),
        refreshSessions(),
        refreshRecentChecked(),
        refreshRecentPurchases(),
        refreshStickerPrices(),
      ]);
      if (view === "history") {
        await refreshHistory();
      }
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refreshAll();
    const timer = setInterval(() => {
      refreshSummary();
      refreshRuntime();
      refreshSessions();
      refreshRecentChecked();
      refreshRecentPurchases();
      refreshStickerPrices();
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (view === "history") {
      refreshHistory();
    }
  }, [view]);

  const metrics = useMemo(() => {
    if (!summary) return [];
    const d = summary.dashboard;
    const buyerSession = sessions.buyer_session || {};
    const parserSession = sessions.parser_session || {};
    return [
      { label: "Bot", value: d.bot_state, detail: summary.status.started_at || "not started", state: d.bot_state_class },
      {
        label: "Buyer session",
        value: buyerSession.active === true ? "ACTIVE" : buyerSession.active === false ? "INACTIVE" : "UNKNOWN",
        detail: `${buyerSession.login || "-"} / ${buyerSession.error || buyerSession.source || "loading"}`,
        state: stateClass(buyerSession.active),
      },
      {
        label: "Parser session",
        value: parserSession.active === true ? "ACTIVE" : parserSession.active === false ? "INACTIVE" : "UNKNOWN",
        detail: `${parserSession.login || "-"} / ${parserSession.error || parserSession.source || "loading"}`,
        state: stateClass(parserSession.active),
      },
      {
        label: "Balance",
        value: formatValue(buyerSession.wallet_balance, " RUB"),
        detail: "buyer wallet",
        state: buyerSession.wallet_balance ? "ok" : "idle",
      },
      { label: "Tracked", value: d.tracked_count, detail: `${d.proxy_count} proxies`, state: d.tracked_count ? "ok" : "warn" },
      { label: "Purchases", value: d.purchase_count, detail: `${recentPurchases.length} visible`, state: d.purchase_count ? "ok" : "idle" },
      { label: "Sticker prices", value: d.sticker_price_count, detail: `${recentStickerPrices.length} recent rows`, state: d.sticker_price_count ? "ok" : "warn" },
      { label: "Checked", value: recentCheckedItems.length, detail: "debug listings", state: recentCheckedItems.length ? "ok" : "idle" },
    ];
  }, [summary, sessions, recentCheckedItems.length, recentPurchases.length, recentStickerPrices.length]);

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>RareItemsBot</h1>
          <p>Steam market control dashboard</p>
        </div>
        <div className="top-actions">
          <button className="ghost" onClick={() => refreshAll({ includeForms: true })}>Refresh</button>
          <button onClick={() => runAction(() => api.post("/api/bot/start"))}>Start</button>
          <button className="danger" onClick={() => runAction(() => api.post("/api/bot/stop"))}>Stop</button>
        </div>
      </header>

      <nav className="view-tabs" aria-label="Dashboard sections">
        <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>Dashboard</button>
        <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>Checked history</button>
      </nav>

      {(message || error) && (
        <div className={`notice ${error ? "danger" : "ok"}`}>{error || message}</div>
      )}

      <main>
        {view === "dashboard" ? (
          <DashboardView
            metrics={metrics}
            runtime={runtime}
            recentCheckedItems={recentCheckedItems}
            recentPurchases={recentPurchases}
            recentStickerPrices={recentStickerPrices}
            configFields={configFields}
            sectionLoading={sectionLoading}
            configDraft={configDraft}
            setConfigDraft={setConfigDraft}
            itemsText={itemsText}
            setItemsText={setItemsText}
            itemsMode={itemsMode}
            setItemsMode={setItemsMode}
            expandExteriors={expandExteriors}
            setExpandExteriors={setExpandExteriors}
            proxiesText={proxiesText}
            setProxiesText={setProxiesText}
            useProxies={useProxies}
            setUseProxies={setUseProxies}
            runAction={runAction}
            setView={setView}
          />
        ) : (
          <HistoryView
            filters={historyFilters}
            setFilters={setHistoryFilters}
            history={checkedHistory}
            loading={historyLoading}
            onApply={() => refreshHistory()}
            onReset={() => {
              setHistoryFilters(DEFAULT_HISTORY_FILTERS);
              refreshHistory(DEFAULT_HISTORY_FILTERS);
            }}
          />
        )}
      </main>
    </div>
  );
}

function DashboardView({
  metrics,
  runtime,
  recentCheckedItems,
  recentPurchases,
  recentStickerPrices,
  configFields,
  sectionLoading,
  configDraft,
  setConfigDraft,
  itemsText,
  setItemsText,
  itemsMode,
  setItemsMode,
  expandExteriors,
  setExpandExteriors,
  proxiesText,
  setProxiesText,
  useProxies,
  setUseProxies,
  runAction,
  setView,
}) {
  return (
    <>
      <section className="metric-grid">
        {metrics.length === 0 && (
          <article className="metric idle">
            <span>Dashboard</span>
            <strong>Loading</strong>
            <small>summary request</small>
          </article>
        )}
        {metrics.map((metric) => (
          <article className={`metric ${metric.state}`} key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
            <small>{metric.detail}</small>
          </article>
        ))}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Startup checkpoints</h2>
        </div>
        <div className="timeline">
          {sectionLoading.runtime && (runtime.steps || []).length === 0 && <p className="empty">Loading checkpoints...</p>}
          {!sectionLoading.runtime && (runtime.steps || []).length === 0 && <p className="empty">No checkpoints yet.</p>}
          {(runtime.steps || []).map((step) => (
            <details className={`checkpoint ${step.status}`} key={step.id}>
              <summary><span className="dot" />{step.label}</summary>
              <p>{step.detail || step.status}</p>
              <div className="events">
                {(step.events || []).map((event, index) => (
                  <div key={`${event.at}-${index}`}>{event.at} / {event.status} / {event.message}</div>
                ))}
              </div>
            </details>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Latest checked listings</h2>
          <button className="ghost" onClick={() => setView("history")}>Open history</button>
        </div>
        <div className="checked-grid">
          {sectionLoading.recentChecked && recentCheckedItems.length === 0 && <p className="empty">Loading checked listings...</p>}
          {!sectionLoading.recentChecked && recentCheckedItems.length === 0 && <p className="empty">No checked listings yet.</p>}
          {recentCheckedItems.map((item) => (
            <CheckedItemCard item={item} compact key={`${item.listing_id}-${item.checked_at}`} />
          ))}
        </div>
      </section>

      <section className="two-col">
        <div className="panel">
          <h2>Recent purchases</h2>
          <PurchaseGrid purchases={recentPurchases} loading={sectionLoading.recentPurchases} />
        </div>
        <div className="panel">
          <h2>Sticker prices</h2>
          <DataTable
            rows={recentStickerPrices}
            loading={sectionLoading.stickerPrices}
            columns={[
              ["name", "Sticker"],
              ["price", "Price"],
              ["updated_at", "Updated"],
            ]}
          />
        </div>
      </section>

      <section className="two-col">
        <form className="panel config-panel" onSubmit={(event) => {
          event.preventDefault();
          runAction(() => api.put("/api/config", { config: configDraft }));
        }}>
          <div className="panel-head">
            <h2>Config</h2>
            <button type="submit">Save config</button>
          </div>
          <div className="config-grid">
            {sectionLoading.config && configFields.length === 0 && <p className="empty">Loading config...</p>}
            {configFields.map((field) => (
              <ConfigField
                field={field}
                value={configDraft[field.name]}
                onChange={(value) => setConfigDraft((current) => ({ ...current, [field.name]: value }))}
                key={field.name}
              />
            ))}
          </div>
        </form>

        <form className="panel editor" onSubmit={(event) => {
          event.preventDefault();
          runAction(() => api.put("/api/items", { items_text: itemsText, mode: itemsMode, expand_exteriors: expandExteriors }));
        }}>
          <div className="panel-head">
            <h2>Tracked items</h2>
            <select value={itemsMode} onChange={(event) => setItemsMode(event.target.value)}>
              <option value="replace">Replace</option>
              <option value="append">Append</option>
            </select>
          </div>
          <textarea value={itemsText} onChange={(event) => setItemsText(event.target.value)} />
          <label className="check"><input type="checkbox" checked={expandExteriors} onChange={(event) => setExpandExteriors(event.target.checked)} /> Expand CS2 exteriors</label>
          <button type="submit">Save items</button>
        </form>

        <form className="panel editor" onSubmit={(event) => {
          event.preventDefault();
          runAction(() => api.put("/api/proxies", { proxies_text: proxiesText, use_proxies: useProxies }));
        }}>
          <div className="panel-head">
            <h2>Proxies</h2>
            <label className="switch"><input type="checkbox" checked={useProxies} onChange={(event) => setUseProxies(event.target.checked)} /> Enabled</label>
          </div>
          <textarea value={proxiesText} onChange={(event) => setProxiesText(event.target.value)} />
          <button type="submit">Save proxies</button>
        </form>
      </section>
    </>
  );
}

function HistoryView({ filters, setFilters, history, loading, onApply, onReset }) {
  function updateFilter(name, value) {
    setFilters((current) => ({ ...current, [name]: value }));
  }

  return (
    <section className="history-view">
      <form className="panel filters-panel" onSubmit={(event) => {
        event.preventDefault();
        onApply();
      }}>
        <div className="panel-head">
          <div>
            <h2>Checked items history</h2>
            <p className="subtle">{history.count} listings loaded</p>
          </div>
          <div className="history-actions">
            <button type="button" className="ghost" onClick={onReset}>Reset</button>
            <button type="submit">Apply filters</button>
          </div>
        </div>
        <div className="filters-grid">
          <label>
            <span>From date</span>
            <input type="date" value={filters.date_from} onChange={(event) => updateFilter("date_from", event.target.value)} />
          </label>
          <label>
            <span>To date</span>
            <input type="date" value={filters.date_to} onChange={(event) => updateFilter("date_to", event.target.value)} />
          </label>
          <label>
            <span>Sticker price from</span>
            <input type="number" step="0.01" value={filters.min_stickers_price} onChange={(event) => updateFilter("min_stickers_price", event.target.value)} />
          </label>
          <label>
            <span>Sticker price to</span>
            <input type="number" step="0.01" value={filters.max_stickers_price} onChange={(event) => updateFilter("max_stickers_price", event.target.value)} />
          </label>
          <label>
            <span>Item price from</span>
            <input type="number" step="0.01" value={filters.min_item_price} onChange={(event) => updateFilter("min_item_price", event.target.value)} />
          </label>
          <label>
            <span>Item price to</span>
            <input type="number" step="0.01" value={filters.max_item_price} onChange={(event) => updateFilter("max_item_price", event.target.value)} />
          </label>
          <label>
            <span>Streak</span>
            <select value={filters.has_streak} onChange={(event) => updateFilter("has_streak", event.target.value)}>
              <option value="">All</option>
              <option value="true">Only streaks</option>
              <option value="false">No streak</option>
            </select>
          </label>
          <label>
            <span>Limit</span>
            <input type="number" min="1" max="50000" step="1" placeholder="all" value={filters.limit} onChange={(event) => updateFilter("limit", event.target.value)} />
          </label>
        </div>
      </form>

      <div className="history-grid">
        {loading && <p className="empty">Loading checked items...</p>}
        {!loading && history.items.length === 0 && <p className="empty">No checked listings match these filters.</p>}
        {!loading && history.items.map((item) => (
          <CheckedItemCard item={item} key={`${item.listing_id}-${item.checked_at}`} />
        ))}
      </div>
    </section>
  );
}

function CheckedItemCard({ item, compact = false }) {
  const stickers = item.stickers || [];
  const visibleStickers = compact ? stickers.slice(0, 6) : stickers;
  const streak = item.streak || {};

  return (
    <article className={`checked-card ${item.profitable ? "profitable" : ""} ${streak.has_streak ? "has-streak" : ""}`}>
      <div className="checked-title">
        <h3>
          {item.market_url ? (
            <a href={item.market_url} target="_blank" rel="noreferrer">{item.item_name}</a>
          ) : item.item_name}
        </h3>
        <span className={`streak-badge ${streak.has_streak ? "active" : ""}`}>
          {streak.has_streak ? `Streak ${streak.count}` : "No streak"}
        </span>
      </div>
      <div className="checked-meta">
        <span>{item.checked_at || "-"}</span>
        <span>Listing {item.listing_id || "-"}</span>
      </div>
      {streak.has_streak && (
        <div className="streak-line">
          <b>{streak.name}</b>
          <span>{formatValue(streak.sum_price, " RUB")} total / {formatValue(streak.single_price, " RUB")} each</span>
        </div>
      )}
      <div className="kv checked-kv">
        <span>Buy price</span><b>{formatValue(item.price, " RUB")}</b>
        <span>Stickers</span><b>{formatValue(item.stickers_price, " RUB")}</b>
        <span>Sticker / buy</span><b className="ratio">{formatRatio(item.stickers_to_price_ratio)}</b>
        <span>Float</span><b>{formatValue(item.float_value)}</b>
        <span>Pattern</span><b>{item.pattern_template || "-"}</b>
      </div>
      <div className="chips">
        {visibleStickers.map((sticker, index) => (
          <a
            className="chip sticker-chip"
            href={sticker.market_url || undefined}
            target="_blank"
            rel="noreferrer"
            key={`${sticker.name}-${index}`}
          >
            <span className="slot">#{stickerSlot(sticker, index)}</span>
            <span>{sticker.name || "unknown sticker"}</span>
            <b>{formatValue(sticker.price, " RUB")}</b>
          </a>
        ))}
        {compact && stickers.length > visibleStickers.length && (
          <span className="chip">+{stickers.length - visibleStickers.length} more</span>
        )}
        {stickers.length === 0 && <span className="chip">no stickers</span>}
      </div>
    </article>
  );
}

function PurchaseGrid({ purchases, loading = false }) {
  if (loading && !purchases.length) return <p className="empty">Loading purchases...</p>;
  if (!purchases.length) return <p className="empty">No purchase attempts yet.</p>;
  return (
    <div className="purchase-grid">
      {purchases.map((purchase, index) => (
        <PurchaseCard purchase={purchase} key={`${purchase.listing_id}-${purchase.date}-${index}`} />
      ))}
    </div>
  );
}

function PurchaseCard({ purchase }) {
  const success = Boolean(purchase.success);
  const stickers = purchase.stickers || [];
  return (
    <article className={`purchase-card ${success ? "success" : "failed"}`}>
      <div className="checked-title">
        <h3>
          {purchase.market_url ? (
            <a href={purchase.market_url} target="_blank" rel="noreferrer">{purchase.item_name}</a>
          ) : purchase.item_name}
        </h3>
        <span className={`purchase-badge ${success ? "success" : "failed"}`}>
          {success ? "Bought" : "Failed"}
        </span>
      </div>
      <div className="checked-meta">
        <span>{purchase.date || "-"}</span>
        <span>Listing {purchase.listing_id || "-"}</span>
      </div>
      <div className="kv checked-kv">
        <span>Buy price</span><b>{formatValue(purchase.price, " RUB")}</b>
        <span>Stickers</span><b>{formatValue(purchase.stickers_price, " RUB")}</b>
      </div>
      <div className="chips purchase-stickers">
        {stickers.map((sticker, index) => (
          <a
            className="chip sticker-chip"
            href={sticker.market_url || undefined}
            target="_blank"
            rel="noreferrer"
            key={`${sticker.name}-${index}`}
          >
            <span className="slot">#{stickerSlot(sticker, index)}</span>
            <span>{sticker.name || "unknown sticker"}</span>
            <b>{formatValue(sticker.price, " RUB")}</b>
          </a>
        ))}
        {stickers.length === 0 && <span className="chip">no stickers</span>}
      </div>
      {!success && (
        <div className="purchase-error">
          {purchase.error || "Purchase failed without an error message."}
        </div>
      )}
    </article>
  );
}

function ConfigField({ field, value, onChange }) {
  if (field.type === "checkbox") {
    return (
      <label className="config-check">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{field.label}</span>
      </label>
    );
  }

  return (
    <label className="config-field">
      <span>{field.label}</span>
      <input
        type={field.type === "password" ? "password" : field.type}
        step={field.type === "number" ? "0.01" : undefined}
        value={value ?? ""}
        placeholder={field.secret ? "unchanged" : ""}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function DataTable({ rows, columns, loading = false }) {
  if (loading && !rows.length) return <p className="empty">Loading rows...</p>;
  if (!rows.length) return <p className="empty">No rows yet.</p>;
  return (
    <table>
      <thead>
        <tr>{columns.map(([, label]) => <th key={label}>{label}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={row.listing_id || row.name || index}>
            {columns.map(([key]) => <td key={key}>{row[key] || "-"}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default App;
