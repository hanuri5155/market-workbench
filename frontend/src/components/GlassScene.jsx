//// frontend/src/components/GlassScene.jsx
import { useState, useEffect } from "react";
import "./GlassScene.css";

/**
 * Props:
 * - visible: boolean
 * - type: 'rounded' | 'pill' | 'circle' (기본 'rounded')
 * - borderRadius: number (기본 32)
 * - children: ReactNode
 */
export default function GlassScene({
  visible = true,
  type = "rounded",
  borderRadius = 32,
  className = "",
  style,
  children,
  onClick,           // 외부 onClick 핸들러 전달용
  initialOn = false, // 외부에서 전달받는 ON/OFF 상태 (DB 기반)
}) {
  // 클릭 애니메이션 상태
  const [isPressing, setIsPressing] = useState(false);

  // circle 버튼의 ON/OFF 상태
  const [isOn, setIsOn] = useState(initialOn);

  // 외부 enable_trading 값과 내부 isOn 동기화
  useEffect(() => {
    if (type === "circle") {
      setIsOn(initialOn);
    }
  }, [initialOn, type]);

  const typeClass =
    type === "pill" ? "glass-surface--pill" :
    type === "circle" ? "glass-surface--circle" : "";

  //  클릭 시 호출할 핸들러 (OFF → ON 일 때만 bounce)
  const handleClick = (e) => {
    // 바깥에서 넘겨준 onClick도 그대로 호출
    if (typeof onClick === "function") {
      onClick(e);
    }

    // circle 타입일 때만 ON/OFF 토글 + 애니메이션 제어
    if (type === "circle") {
      setIsOn((prev) => {
        const next = !prev;       // 토글 후 상태

        //  OFF(false) → ON(true) 로 바뀌는 순간에만 클릭 애니메이션 실행
        if (!prev && next) {
          setIsPressing(true);
        }

        return next;
      });
    }
  };

  // 애니메이션 끝나면 상태 초기화
  const handleAnimationEnd = () => {
    if (type === "circle") {
      setIsPressing(false);
    }
  };

  const cn = [
    "scene",
    visible ? "is-visible" : "",
    "glass-surface",
    typeClass,
    isPressing && type === "circle" ? "glass-surface--circle-press" : "",
    isOn && type === "circle" ? "glass-surface--circle-on" : "",
    className || "",
  ].filter(Boolean).join(" ");

  const radiusStyle =
    type === "rounded"
      ? { borderRadius }
      : {};

  return (
    <section
      className={cn}
      data-visible={visible ? "1" : "0"}
      aria-hidden={visible ? "false" : "true"}
      style={{ ...radiusStyle, ...(style || {}) }}
      onClick={handleClick}            // 클릭 시 bounce 시작
      onAnimationEnd={handleAnimationEnd} // 끝나면 상태 초기화
    >
      {children}
    </section>
  );
}
