import StatusBadge from "./StatusBadge";

const statusMap = {
  live: { tone: "positive", label: "Live" },
  connecting: { tone: "warning", label: "Connecting" },
  waiting: { tone: "warning", label: "Waiting" },
  disabled: { tone: "muted", label: "Disabled" },
};

export default function ConnectionIndicator({ status, source = "bot_http" }) {
  const mapped = statusMap[status] || statusMap.waiting;

  return (
    <div className="ui-v3-connection-indicator" aria-label={`Connection ${mapped.label}`}>
      <StatusBadge tone={mapped.tone} pulse={status === "live"}>
        <span className="ui-v3-live-dot" aria-hidden="true" />
        {mapped.label}
      </StatusBadge>
      <StatusBadge tone="source">{source}</StatusBadge>
    </div>
  );
}
