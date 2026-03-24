import { useMemo, useState } from "react";
import { useZoneNotifications } from "../../contexts/ZoneNotificationContext";
import GlassSurface from "./glass/GlassSurface";
import MotionButton from "./motion/MotionButton";

function asNumber(value) {
  const next = Number(value);
  return Number.isFinite(next) ? next : null;
}

function formatPrice(value) {
  const number = asNumber(value);
  if (number == null) return "--";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(number);
}

function parsePercent(value) {
  if (typeof value !== "string") return null;
  const next = Number(value.replace("%", ""));
  return Number.isFinite(next) ? next : null;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function getZoneModel(item, livePrice) {
  const entry = asNumber(item.entryPrice);
  const stop = asNumber(item.stopPrice);
  const current = asNumber(livePrice ?? item.currentPriceAtBuild);
  const side = item.side === "Short" ? "Short" : "Long";
  const low = Math.min(entry ?? 0, stop ?? 0);
  const high = Math.max(entry ?? 0, stop ?? 0);
  const width = Math.max(0, high - low);
  const riskPercent = parsePercent(item.percentageText);
  const padding = Math.max(width * 1.4, (current || entry || high || 1) * 0.003);
  const domainLow = Math.max(0, low - padding);
  const domainHigh = high + padding;
  const domainWidth = Math.max(1, domainHigh - domainLow);
  const currentPct = current == null ? null : clamp(((current - domainLow) / domainWidth) * 100, 0, 100);
  const entryPct = entry == null ? 0 : clamp(((entry - domainLow) / domainWidth) * 100, 0, 100);
  const stopPct = stop == null ? 0 : clamp(((stop - domainLow) / domainWidth) * 100, 0, 100);
  const rangeLeft = Math.min(entryPct, stopPct);
  const rangeWidth = Math.max(3, Math.abs(entryPct - stopPct));
  const inside = current != null && current >= low && current <= high;
  const distance = current == null
    ? null
    : current < low
      ? low - current
      : current > high
        ? current - high
        : 0;
  const distancePct = current && distance != null ? (distance / current) * 100 : null;
  const nearThreshold = Math.max(riskPercent ?? 0.2, 0.2);

  let state = "Watching distance";
  let detail = current == null ? "Current price pending" : `${distancePct?.toFixed(2) ?? "0.00"}% from zone`;
  if (inside) {
    state = "Inside decision band";
    detail = "Price is within the visible structure range";
  } else if (distancePct != null && distancePct <= nearThreshold) {
    state = "Approaching zone";
    detail = `${distancePct.toFixed(2)}% outside the band`;
  }

  return {
    side,
    entry,
    stop,
    current,
    riskPercent,
    entryPct,
    stopPct,
    currentPct,
    rangeLeft,
    rangeWidth,
    state,
    detail,
  };
}

function SignalIntentBadge({ side }) {
  const tone = side === "Short" ? "negative" : "positive";
  return <span className={`ui-v3-signal-badge ui-v3-signal-badge--${tone}`}>{side.toUpperCase()} SETUP</span>;
}

function RiskBandMeter({ model }) {
  return (
    <div className="ui-v3-risk-meter" aria-hidden="true">
      <div
        className={`ui-v3-risk-meter-band is-${model.side.toLowerCase()}`}
        style={{ left: `${model.rangeLeft}%`, width: `${model.rangeWidth}%` }}
      />
      <span className="ui-v3-risk-edge ui-v3-risk-edge--entry" style={{ left: `${model.entryPct}%` }} />
      <span className="ui-v3-risk-edge ui-v3-risk-edge--stop" style={{ left: `${model.stopPct}%` }} />
      {model.currentPct != null ? (
        <span className="ui-v3-risk-marker" style={{ left: `${model.currentPct}%` }} />
      ) : null}
    </div>
  );
}

function StructureZoneCard({ item, currentPrice, hovered, setHoveredBoxId, onSelect }) {
  const model = useMemo(() => getZoneModel(item, currentPrice), [item, currentPrice]);
  const sideClass = model.side.toLowerCase();
  const timeframe = item.timeframe ? `${item.timeframe}M` : "TF";

  function handleKeyDown(event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(item);
    }
  }

  return (
    <article
      className={`ui-v3-zone-card is-${sideClass}${hovered ? " is-hovered" : ""}`}
      onMouseEnter={() => setHoveredBoxId(item.id)}
      onMouseLeave={() => setHoveredBoxId(null)}
      onFocus={() => setHoveredBoxId(item.id)}
      onBlur={() => setHoveredBoxId(null)}
      onClick={() => onSelect(item)}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="button"
      aria-label={`${model.side} structure zone ${timeframe}`}
    >
      <div className="ui-v3-zone-card-top">
        <SignalIntentBadge side={model.side} />
        <span className="ui-v3-timeframe-pill">{timeframe}</span>
      </div>

      <div className="ui-v3-zone-price-row">
        <div>
          <span>Entry edge</span>
          <strong>{formatPrice(model.entry)}</strong>
        </div>
        <div>
          <span>Invalidation</span>
          <strong>{formatPrice(model.stop)}</strong>
        </div>
      </div>

      <RiskBandMeter model={model} />

      <div className="ui-v3-zone-card-bottom">
        <div>
          <span>{model.state}</span>
          <strong>{model.detail}</strong>
        </div>
        <div className="ui-v3-risk-chip">
          <span>Risk band</span>
          <strong>{item.percentageText || "--"}</strong>
        </div>
      </div>
    </article>
  );
}

function ZoneDetailSheet({ item, currentPrice, onClose }) {
  const model = useMemo(() => item ? getZoneModel(item, currentPrice) : null, [item, currentPrice]);
  if (!item || !model) return null;

  return (
    <div className="ui-v3-zone-sheet" role="dialog" aria-modal="false" aria-label="Structure zone detail">
      <div className="ui-v3-zone-sheet-handle" aria-hidden="true" />
      <div className="ui-v3-zone-sheet-head">
        <div>
          <SignalIntentBadge side={model.side} />
          <h3>{model.side} decision band</h3>
        </div>
        <MotionButton type="button" onClick={onClose}>Close</MotionButton>
      </div>
      <div className="ui-v3-zone-sheet-grid">
        <div>
          <span>Entry edge</span>
          <strong>{formatPrice(model.entry)}</strong>
        </div>
        <div>
          <span>Invalidation edge</span>
          <strong>{formatPrice(model.stop)}</strong>
        </div>
        <div>
          <span>Risk band</span>
          <strong>{item.percentageText || "--"}</strong>
        </div>
        <div>
          <span>Current relation</span>
          <strong>{model.state}</strong>
        </div>
      </div>
      <RiskBandMeter model={model} />
      <p>{model.detail}. This is a read-only structure interpretation from existing chart data.</p>
    </div>
  );
}

export default function StructureZoneDeck({ currentPrice }) {
  const { items, hoveredBoxId, setHoveredBoxId } = useZoneNotifications();
  const visibleItems = Array.isArray(items) ? items.slice(0, 6) : [];
  const [selectedItem, setSelectedItem] = useState(null);

  return (
    <GlassSurface as="section" level="signal" interactive className="ui-v3-zone-deck" aria-label="Structure zones">
      <div className="ui-v3-zone-deck-head">
        <div>
          <span className="ui-v3-kicker">Structure Zones</span>
          <h2>Signal decision bands</h2>
        </div>
        <span className="ui-v3-zone-count">{visibleItems.length}</span>
      </div>

      {visibleItems.length ? (
        <div className="ui-v3-zone-list">
          {visibleItems.map((item) => (
            <StructureZoneCard
              key={item.id}
              item={item}
              currentPrice={currentPrice}
              hovered={hoveredBoxId === item.id}
              setHoveredBoxId={setHoveredBoxId}
              onSelect={setSelectedItem}
            />
          ))}
        </div>
      ) : (
        <div className="ui-v3-zone-empty">
          <strong>No visible decision zones</strong>
          <span>Move or zoom the chart to bring active structure bands into view.</span>
        </div>
      )}
      <ZoneDetailSheet
        item={selectedItem}
        currentPrice={currentPrice}
        onClose={() => setSelectedItem(null)}
      />
    </GlassSurface>
  );
}
