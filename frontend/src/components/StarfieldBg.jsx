//// frontend/src/components/StarfieldBg.jsx
import { useMemo } from "react";
import "./StarfieldBg.css";

/**
 * Parallax Starfield Background (CSS-only 애니메이션 + 런타임 좌표 생성)
 * - small/medium/big: 레이어별 별 개수 (원본: 700/200/100)
 * - areaW, areaH: 별이 생성될 가상 캔버스 크기 (원본: 2000x2000px)
 */
function genStars(n, w = 2000, h = 2000) {
  const arr = new Array(n).fill(0).map(() => {
    const x = Math.floor(Math.random() * w);
    const y = Math.floor(Math.random() * h);
    return `${x}px ${y}px #FFF`;
  });
  return arr.join(", ");
}

export default function StarfieldBg({
  small = 700,
  medium = 200,
  big = 100,
  areaW = 2000,
  areaH = 2000,
}) {
  // 원본과 동일한 밀도/영역으로 box-shadow 좌표를 생성
  const starsS = useMemo(() => genStars(small, areaW, areaH), [small, areaW, areaH]);
  const starsM = useMemo(() => genStars(medium, areaW, areaH), [medium, areaW, areaH]);
  const starsB = useMemo(() => genStars(big, areaW, areaH), [big, areaW, areaH]);

  const layers = [
    { size: "s", shadow: starsS },
    { size: "m", shadow: starsM },
    { size: "b", shadow: starsB },
  ];

  return (
    <div className="starfield" aria-hidden="true">
      {layers.flatMap(({ size, shadow }) => [
        <div key={`${size}-base`} className={`stars stars--${size}`} style={{ boxShadow: shadow }} />,
        <div
          key={`${size}-clone`}
          className={`stars stars--${size} stars--clone`}
          style={{ boxShadow: shadow }}
        />,
      ])}
    </div>
  );
}
