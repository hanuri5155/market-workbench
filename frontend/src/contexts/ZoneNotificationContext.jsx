import { createContext, useContext, useMemo, useState, useCallback } from "react";

// 차트와 알림 패널이 같은 목록과 hover 상태를 공유하기 위함
const ZoneNotificationContext = createContext(null);

export function ZoneNotificationProvider({ children }) {
  const [items, setItems] = useState([]);
  const [hoveredBoxId, setHoveredBoxId] = useState(null);

  const toggleBoxActive = useCallback((boxId) => {
    if (
      typeof window !== "undefined" &&
      typeof window.__toggleZoneActiveById === "function"
    ) {
      window.__toggleZoneActiveById(boxId);
    }
  }, []);

  const value = useMemo(
    () => ({
      items,
      setItems,
      hoveredBoxId,
      setHoveredBoxId,
      toggleBoxActive,
    }),
    [items, hoveredBoxId, toggleBoxActive]
  );

  return (
    <ZoneNotificationContext.Provider value={value}>
      {children}
    </ZoneNotificationContext.Provider>
  );
}

export function useZoneNotifications() {
  const ctx = useContext(ZoneNotificationContext);
  if (ctx === null) {
    throw new Error(
      "useZoneNotifications 훅은 ZoneNotificationProvider 안에서만 사용할 수 있습니다."
    );
  }
  return ctx;
}
