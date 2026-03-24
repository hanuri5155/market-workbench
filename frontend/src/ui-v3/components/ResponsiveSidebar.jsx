import ConnectionIndicator from "./ConnectionIndicator";
import MetricCard from "./MetricCard";
import SectionPanel from "./SectionPanel";
import StatusBadge from "./StatusBadge";

function formatUpdateTime(value) {
  if (!Number.isFinite(value)) return "--";
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(value);
}

function getPriceTone(direction) {
  if (direction === "up") return "positive";
  if (direction === "down") return "negative";
  return "neutral";
}

function getConnectionCopy(status) {
  if (status === "live") return "Receiving candle updates";
  if (status === "connecting") return "Opening chart stream";
  if (status === "disabled") return "Stream disabled";
  return "Waiting for first live tick";
}

export default function ResponsiveSidebar({
  ticker,
  activeTf,
  activeSource = "bot_http",
}) {
  const priceTone = getPriceTone(ticker.direction);
  const lastUpdate = formatUpdateTime(ticker.updatedAt);
  const candleState = ticker.status === "live" ? "Realtime" : "Pending";

  return (
    <aside className="ui-v3-sidebar" aria-label="Market context">
      <SectionPanel
        eyebrow="Market Context"
        title="BTCUSDT"
        action={<ConnectionIndicator status={ticker.status} source={activeSource} />}
      >
        <div className="ui-v3-price-block">
          <span>Current price</span>
          <strong className={`ui-v3-price-value is-${priceTone}`}>
            {ticker.formattedPrice}
          </strong>
          <small>{getConnectionCopy(ticker.status)}</small>
        </div>

        <div className="ui-v3-metric-grid">
          <MetricCard
            label="Interval"
            value={`${activeTf}m`}
            detail="Chart-selected"
            compact
          />
          <MetricCard
            label="Last update"
            value={lastUpdate}
            detail={ticker.source === "ws" ? "WebSocket" : ticker.source}
            compact
          />
          <MetricCard
            label="Recent candle"
            value={candleState}
            detail={ticker.status === "live" ? "Update received" : "No tick yet"}
            tone={ticker.status === "live" ? "positive" : "warning"}
            compact
          />
          <MetricCard
            label="Reconnect"
            value={ticker.status === "waiting" ? "Standby" : "Auto"}
            detail="Client-managed"
            compact
          />
        </div>
      </SectionPanel>

      <SectionPanel eyebrow="Read-only Operations" title="Runtime">
        <div className="ui-v3-runtime-list">
          <div>
            <span>Browser fanout</span>
            <StatusBadge tone="source">{activeSource}</StatusBadge>
          </div>
          <div>
            <span>Chart stream</span>
            <StatusBadge tone={ticker.status === "live" ? "positive" : "warning"}>
              /ws/chart-candles
            </StatusBadge>
          </div>
          <div>
            <span>Migration state</span>
            <StatusBadge tone="muted">Pending</StatusBadge>
          </div>
        </div>
      </SectionPanel>
    </aside>
  );
}
