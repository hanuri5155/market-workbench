import GlassSurface from "./glass/GlassSurface";
import LiveStreamIndicator from "./LiveStreamIndicator";
import StatusBadge from "./StatusBadge";

function getPriceTone(ticker) {
  if (ticker?.direction === "up") return "positive";
  if (ticker?.direction === "down") return "negative";
  return "neutral";
}

export default function MarketPulseHeader({
  ticker,
  activeTf,
  source = "bot_http",
  label = "BTCUSDT",
}) {
  const priceTone = getPriceTone(ticker);

  return (
    <GlassSurface
      as="section"
      level="command"
      interactive
      className="ui-v3-market-command"
      aria-label="Market command header"
    >
      <div className="ui-v3-market-command-title">
        <span className="ui-v3-kicker">Liquid Trading Command Center</span>
        <h1>{label} structure command</h1>
        <p>Live candle structure, decision bands, and stream trust in one focused workspace.</p>
      </div>

      <div className="ui-v3-market-command-price">
        <div className="ui-v3-symbol-row">
          <StatusBadge tone="neutral">{label}</StatusBadge>
          <StatusBadge tone="muted">{activeTf}m</StatusBadge>
        </div>
        <strong className={`ui-v3-hero-price is-${priceTone}`}>
          {ticker?.formattedPrice || "--"}
        </strong>
        <span>Current live candle reference</span>
      </div>

      <LiveStreamIndicator ticker={ticker} source={source} />
    </GlassSurface>
  );
}
