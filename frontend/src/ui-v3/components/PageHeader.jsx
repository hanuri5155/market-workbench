import StatusBadge from "./StatusBadge";

export default function PageHeader({
  eyebrow,
  title,
  detail,
  status,
  tone = "neutral",
}) {
  return (
    <section className="ui-v3-page-header">
      <div>
        {eyebrow ? <span className="ui-v3-kicker">{eyebrow}</span> : null}
        <h1>{title}</h1>
        {detail ? <p>{detail}</p> : null}
      </div>
      {status ? <StatusBadge tone={tone}>{status}</StatusBadge> : null}
    </section>
  );
}
