import { useEffect, useMemo, useRef, useState } from "react";

const api = {
  async getDashboard() {
    const response = await fetch("/api/dashboard");
    if (!response.ok) throw new Error(`Dashboard request failed: ${response.status}`);
    return response.json();
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
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
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

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const next = await api.getDashboard();
      setData(next);
      if (!formsInitialized.current) {
        setItemsText(next.items_text || "");
        setProxiesText(next.proxies_text || "");
        setConfigDraft(next.config || {});
        setUseProxies(Boolean(next.dashboard?.proxies_enabled));
        formsInitialized.current = true;
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
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
      await refresh();
      if (view === "history") {
        await refreshHistory();
      }
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (view === "history") {
      refreshHistory();
    }
  }, [view]);

  const metrics = useMemo(() => {
    if (!data) return [];
    const d = data.dashboard;
    return [
      { label: "Bot", value: d.bot_state, detail: data.status.started_at || "not started", state: d.bot_state_class },
      {
        label: "Buyer session",
        value: d.buyer_session.active === true ? "ACTIVE" : d.buyer_session.active === false ? "INACTIVE" : "UNKNOWN",
        detail: `${d.buyer_session.login} / ${d.buyer_session.error || d.buyer_session.source}`,
        state: stateClass(d.buyer_session.active),
      },
      {
        label: "Parser session",
        value: d.parser_session.active === true ? "ACTIVE" : d.parser_session.active === false ? "INACTIVE" : "UNKNOWN",
        detail: `${d.parser_session.login} / ${d.parser_session.error || d.parser_session.source}`,
        state: stateClass(d.parser_session.active),
      },
      {
        label: "Balance",
        value: formatValue(d.buyer_session.wallet_balance, " RUB"),
        detail: "buyer wallet",
        state: d.buyer_session.wallet_balance ? "ok" : "idle",
      },
      { label: "Tracked", value: d.tracked_count, detail: `${d.proxy_count} proxies`, state: d.tracked_count ? "ok" : "warn" },
      { label: "Purchases", value: d.purchase_count, detail: `${d.recent_purchase_count} visible`, state: d.purchase_count ? "ok" : "idle" },
      { label: "Sticker prices", value: d.sticker_price_count, detail: `${d.recent_sticker_price_count} recent rows`, state: d.sticker_price_count ? "ok" : "warn" },
      { label: "Checked", value: d.recent_checked_count, detail: "debug listings", state: d.recent_checked_count ? "ok" : "idle" },
    ];
  }, [data]);

  if (loading && !data) {
    return <div className="boot">Loading RareItemsBot dashboard...</div>;
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>RareItemsBot</h1>
          <p>Steam market control dashboard</p>
        </div>
        <div className="top-actions">
          <button className="ghost" onClick={refresh}>Refresh</button>
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
            data={data}
            metrics={metrics}
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
  data,
  metrics,
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
          {(data?.dashboard.runtime.steps || []).map((step) => (
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
          {(data?.recent_checked_items || []).length === 0 && <p className="empty">No checked listings yet.</p>}
          {(data?.recent_checked_items || []).map((item) => (
            <CheckedItemCard item={item} compact key={`${item.listing_id}-${item.checked_at}`} />
          ))}
        </div>
      </section>

      <section className="two-col">
        <div className="panel">
          <h2>Recent purchases</h2>
          <PurchaseGrid purchases={data?.recent_purchases || []} />
        </div>
        <div className="panel">
          <h2>Sticker prices</h2>
          <DataTable
            rows={data?.recent_sticker_prices || []}
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
            {(data?.config_fields || []).map((field) => (
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

function PurchaseGrid({ purchases }) {
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

function DataTable({ rows, columns }) {
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
