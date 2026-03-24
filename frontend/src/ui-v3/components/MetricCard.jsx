export default function MetricCard({
  label,
  value,
  unit,
  detail,
  tone = "neutral",
  compact = false,
}) {
  const classes = [
    "ui-v3-metric-card",
    `ui-v3-metric-card--${tone}`,
    compact ? "ui-v3-metric-card--compact" : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={classes}>
      <div className="ui-v3-metric-head">
        <span className="ui-v3-metric-label">{label}</span>
        {unit ? <span className="ui-v3-metric-unit">{unit}</span> : null}
      </div>
      <strong className="ui-v3-metric-value">
        {value}
      </strong>
      {detail ? <span className="ui-v3-metric-detail">{detail}</span> : null}
    </div>
  );
}
