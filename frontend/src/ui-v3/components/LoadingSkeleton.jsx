export default function LoadingSkeleton({ label = "Preparing chart" }) {
  return (
    <div className="ui-v3-loading-skeleton" aria-label={label}>
      <div className="ui-v3-loading-grid" />
      <div className="ui-v3-loading-tags" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </div>
      <div className="ui-v3-loading-line ui-v3-loading-line--primary" />
      <div className="ui-v3-loading-line ui-v3-loading-line--secondary" />
      <div className="ui-v3-loading-card" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="ui-v3-loading-bars" aria-hidden="true">
        {Array.from({ length: 42 }).map((_, index) => (
          <span
            key={index}
            className={index % 4 === 0 || index % 9 === 0 ? "is-negative" : ""}
            style={{ "--bar-h": `${30 + ((index * 19) % 86)}px` }}
          />
        ))}
      </div>
    </div>
  );
}
