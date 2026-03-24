// frontend/src/ui-v2/components/primitives.jsx

import { useId } from "react";

export function Panel({
  children,
  className = "",
  variant = "glass",
  interactive = false,
}) {
  const classes = [
    "ui-v2-panel",
    `ui-v2-panel--${variant}`,
    interactive ? "ui-v2-panel--interactive" : "",
    className,
  ].filter(Boolean).join(" ");

  return <section className={classes}>{children}</section>;
}

export function Badge({ children, tone = "neutral", pulse = false }) {
  return (
    <span className={`ui-v2-badge ui-v2-badge--${tone}${pulse ? " is-pulsing" : ""}`}>
      {children}
    </span>
  );
}

export function Button({ children, variant = "secondary", className = "", ...props }) {
  return (
    <button className={`ui-v2-button ui-v2-button--${variant} ${className}`.trim()} {...props}>
      {children}
    </button>
  );
}

export function Switch({ checked = false, label, detail, disabled = false }) {
  return (
    <div className="ui-v2-switch-row">
      <div>
        <div className="ui-v2-switch-label">{label}</div>
        {detail ? <div className="ui-v2-switch-detail">{detail}</div> : null}
      </div>
      <button
        type="button"
        className={`ui-v2-switch${checked ? " is-on" : ""}`}
        aria-pressed={checked}
        disabled={disabled}
      >
        <span />
      </button>
    </div>
  );
}

export function StatCard({ label, value, unit, tone = "primary", delta }) {
  return (
    <div className={`ui-v2-stat ui-v2-stat--${tone}`}>
      <div className="ui-v2-stat-label">{label}</div>
      <div className="ui-v2-stat-value">
        {value}
        {unit ? <span>{unit}</span> : null}
      </div>
      {delta ? <div className="ui-v2-stat-delta">{delta}</div> : null}
    </div>
  );
}

export function SegmentedControl({ options, value }) {
  return (
    <div className="ui-v2-segmented" role="tablist" aria-label="Workspace options">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          className={option === value ? "is-active" : ""}
          aria-pressed={option === value}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

export function DataTable({ columns, rows, getTone }) {
  return (
    <div className="ui-v2-table-wrap">
      <table className="ui-v2-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.id ?? `${row.strategy ?? row.symbol}-${index}`}>
              {columns.map((column) => {
                const tone = getTone?.(row, column.key);
                return (
                  <td key={column.key} className={tone ? `is-${tone}` : ""}>
                    {row[column.key]}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Sparkline({ tone = "red" }) {
  const gradientId = `ui-v2-spark-${useId().replace(/:/g, "")}`;
  const points = "0,62 34,45 70,54 104,22 138,34 172,18 210,28 248,10 286,16";

  return (
    <svg className={`ui-v2-sparkline ui-v2-sparkline--${tone}`} viewBox="0 0 286 72" role="img" aria-label="Performance curve">
      <defs>
        <linearGradient id={gradientId} x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.15" />
          <stop offset="42%" stopColor="currentColor" stopOpacity="0.72" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="1" />
        </linearGradient>
      </defs>
      <polyline points={points} fill="none" stroke={`url(#${gradientId})`} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points={`${points} 286,72 0,72`} fill="currentColor" opacity="0.08" />
    </svg>
  );
}

export function SkeletonChart() {
  return (
    <div className="ui-v2-chart-skeleton" aria-hidden="true">
      <div className="ui-v2-chart-skeleton-grid" />
      <svg viewBox="0 0 900 360" preserveAspectRatio="none">
        <path d="M0 246 C90 220 112 270 186 228 C250 190 284 202 336 164 C412 108 462 156 520 122 C604 72 646 98 708 72 C786 38 820 80 900 48" />
        <path className="ui-v2-chart-skeleton-loss" d="M0 292 C88 274 130 300 210 266 C284 232 320 246 390 210 C486 160 536 204 602 176 C704 132 786 144 900 108" />
      </svg>
      <div className="ui-v2-chart-candles">
        {Array.from({ length: 44 }).map((_, index) => (
          <span
            key={index}
            className={index % 5 === 0 || index % 7 === 0 ? "is-red" : ""}
            style={{ "--h": `${34 + ((index * 17) % 74)}px` }}
          />
        ))}
      </div>
    </div>
  );
}
