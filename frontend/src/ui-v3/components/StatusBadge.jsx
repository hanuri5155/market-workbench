export default function StatusBadge({
  children,
  tone = "neutral",
  pulse = false,
  className = "",
}) {
  const classes = [
    "ui-v3-status-badge",
    `ui-v3-status-badge--${tone}`,
    pulse ? "is-pulsing" : "",
    className,
  ].filter(Boolean).join(" ");

  return <span className={classes}>{children}</span>;
}
