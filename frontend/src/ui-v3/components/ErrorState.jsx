export default function ErrorState({ title = "Unable to load", detail }) {
  return (
    <div className="ui-v3-state ui-v3-state--error">
      <strong>{title}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}
