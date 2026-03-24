export default function TrendLine({ tone = "positive" }) {
  const stroke = tone === "negative" ? "var(--ui-v3-negative)" : "var(--ui-v3-positive)";

  return (
    <div className={`ui-v3-trend ui-v3-trend--${tone}`} aria-hidden="true">
      <svg viewBox="0 0 620 180" preserveAspectRatio="none">
        <path className="ui-v3-trend-grid" d="M0 45H620M0 90H620M0 135H620M124 0V180M248 0V180M372 0V180M496 0V180" />
        <path
          className="ui-v3-trend-area"
          d="M0 132 C54 118 78 144 130 110 C178 80 224 104 270 76 C326 42 360 92 414 62 C478 26 528 54 620 28 L620 180 L0 180 Z"
          fill={stroke}
        />
        <path
          d="M0 132 C54 118 78 144 130 110 C178 80 224 104 270 76 C326 42 360 92 414 62 C478 26 528 54 620 28"
          fill="none"
          stroke={stroke}
          strokeWidth="4"
          strokeLinecap="round"
        />
      </svg>
    </div>
  );
}
