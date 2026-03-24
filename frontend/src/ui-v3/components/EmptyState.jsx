export default function EmptyState({ title = "No data", detail }) {
  return (
    <div className="ui-v3-state ui-v3-state--empty">
      <strong>{title}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}
