import { useEffect, useMemo, useRef, useState } from "react";

const api = {
  async getDashboard() {
    const response = await fetch("/api/dashboard");
    if (!response.ok) throw new Error(`Dashboard request failed: ${response.status}`);
    return response.json();
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

function formatValue(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (Number.isFinite(number)) return `${number.toFixed(2).replace(/\.?0+$/, "")}${suffix}`;
  return `${value}${suffix}`;
}

function stateClass(value) {
  if (value === true || value === "ok" || value === "success") return "ok";
  if (value === false || value === "error" || value === "danger") return "danger";
  if (value === "active" || value === "starting") return "warn";
  return "idle";
}

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
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

  async function runAction(action) {
    setError("");
    try {
      const result = await action();
      setMessage(result.message || "Done");
      if (result.config) {
        setConfigDraft(result.config);
      }
      await refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);

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

      {(message || error) && (
        <div className={`notice ${error ? "danger" : "ok"}`}>{error || message}</div>
      )}

      <main>
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
          </div>
          <div className="checked-grid">
            {(data?.recent_checked_items || []).length === 0 && <p className="empty">No checked listings yet.</p>}
            {(data?.recent_checked_items || []).map((item) => (
              <article className={`checked-card ${item.profitable ? "profitable" : ""}`} key={item.listing_id}>
                <h3>{item.item_name}</h3>
                <div className="kv">
                  <span>Listing</span><b>{item.listing_id}</b>
                  <span>Price</span><b>{formatValue(item.price, " RUB")}</b>
                  <span>Stickers</span><b>{formatValue(item.stickers_price, " RUB")}</b>
                  <span>Float</span><b>{formatValue(item.float_value)}</b>
                  <span>Pattern</span><b>{item.pattern_template || "-"}</b>
                </div>
                <div className="chips">
                  {(item.stickers || []).slice(0, 6).map((sticker, index) => (
                    <span className="chip" key={`${sticker.name}-${index}`}>{sticker.name} / {formatValue(sticker.price)}</span>
                  ))}
                  {(item.stickers || []).length === 0 && <span className="chip">no stickers</span>}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="two-col">
          <div className="panel">
            <h2>Recent purchases</h2>
            <DataTable
              rows={data?.recent_purchases || []}
              columns={[
                ["date", "Date"],
                ["item_name", "Item"],
                ["price", "Price"],
                ["stickers_price", "Stickers"],
              ]}
            />
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
      </main>
    </div>
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
