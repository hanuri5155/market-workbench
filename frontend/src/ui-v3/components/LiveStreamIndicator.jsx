import StatusBadge from "./StatusBadge";

function getFreshness(updatedAt) {
  if (!updatedAt) {
    return { label: "Awaiting tick", detail: "No candle update yet", tone: "warning" };
  }

  const ageSeconds = Math.max(0, Math.round((Date.now() - updatedAt) / 1000));
  if (ageSeconds <= 8) {
    return { label: "Fresh", detail: `${ageSeconds}s ago`, tone: "positive" };
  }
  if (ageSeconds <= 30) {
    return { label: "Watching", detail: `${ageSeconds}s ago`, tone: "warning" };
  }
  return { label: "Stale", detail: `${ageSeconds}s ago`, tone: "warning" };
}

export default function LiveStreamIndicator({
  ticker,
  source = "bot_http",
  compact = false,
  showSource = false,
}) {
  const status = ticker?.status || "waiting";
  const freshness = getFreshness(ticker?.updatedAt);
  const isLive = status === "live";

  return (
    <div className={`ui-v3-live-stream${compact ? " is-compact" : ""}`}>
      <div className="ui-v3-live-stream-core" data-state={isLive ? "live" : "waiting"}>
        <span />
      </div>
      <div>
        <strong>{isLive ? "Live market feed" : "Preparing live feed"}</strong>
        <span>{freshness.label} - {freshness.detail}</span>
      </div>
      {showSource ? (
        <StatusBadge tone={freshness.tone} pulse={isLive}>
          {source}
        </StatusBadge>
      ) : null}
    </div>
  );
}
