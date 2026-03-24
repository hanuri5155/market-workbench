export default function SectionPanel({
  title,
  eyebrow,
  action,
  children,
  className = "",
}) {
  const classes = ["ui-v3-section-panel", className].filter(Boolean).join(" ");

  return (
    <section className={classes}>
      {(title || eyebrow || action) ? (
        <div className="ui-v3-section-head">
          <div>
            {eyebrow ? <span>{eyebrow}</span> : null}
            {title ? <h2>{title}</h2> : null}
          </div>
          {action ? <div className="ui-v3-section-action">{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
