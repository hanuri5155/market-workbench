// frontend/src/ui-v2/hooks/useLiveTicker.js

import { useEffect, useMemo, useRef, useState } from "react";

const DEFAULT_SYMBOL = "BTCUSDT";
const DEFAULT_TF = "15";
const REST_POLL_INTERVAL_MS = 5000;
const REST_TIMEOUT_MS = 3500;

function normalizeCandle(raw) {
  if (!raw || typeof raw !== "object") return null;
  const candle = raw.candle && typeof raw.candle === "object" ? raw.candle : raw;
  const close = Number(candle.close);
  if (!Number.isFinite(close) || close <= 0) return null;

  const start = Number(candle.start);
  const end = Number(candle.end);

  return {
    close,
    start: Number.isFinite(start) ? start : null,
    end: Number.isFinite(end) ? end : null,
    confirm: Boolean(candle.confirm),
  };
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return "--";
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export default function useLiveTicker({
  symbol = DEFAULT_SYMBOL,
  tf = DEFAULT_TF,
  enabled = true,
} = {}) {
  const [state, setState] = useState({
    price: null,
    previousPrice: null,
    updatedAt: null,
    source: "idle",
    status: enabled ? "connecting" : "disabled",
  });
  const lastPriceRef = useRef(null);

  useEffect(() => {
    if (!enabled) {
      setState((prev) => ({ ...prev, status: "disabled" }));
      return undefined;
    }

    let disposed = false;
    let pollTimer = null;
    let ws = null;

    const applyPrice = (price, source) => {
      if (!Number.isFinite(price) || disposed) return;
      const previousPrice = lastPriceRef.current;
      lastPriceRef.current = price;
      setState({
        price,
        previousPrice,
        updatedAt: Date.now(),
        source,
        status: "live",
      });
    };

    const fetchLatest = async (source = "rest") => {
      if (disposed) return;

      let timeoutId = null;
      let fetchOptions = undefined;
      try {
        if (typeof AbortController === "function") {
          const controller = new AbortController();
          timeoutId = setTimeout(() => controller.abort(), REST_TIMEOUT_MS);
          fetchOptions = { signal: controller.signal };
        }

        const response = await fetch(`/api/candles/latest/${tf}`, fetchOptions);
        if (!response.ok) {
          throw new Error(`latest candle ${response.status}`);
        }

        const candle = normalizeCandle(await response.json());
        if (candle) {
          applyPrice(candle.close, source);
        }
      } catch {
        if (!disposed && lastPriceRef.current == null) {
          setState((prev) => ({ ...prev, status: "waiting", source: "rest" }));
        }
      } finally {
        if (timeoutId) clearTimeout(timeoutId);
      }
    };

    const connectWs = () => {
      if (disposed || typeof WebSocket !== "function") return;

      try {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(`${protocol}//${window.location.host}/ws/chart-candles`);

        ws.onopen = () => {
          if (disposed || ws?.readyState !== WebSocket.OPEN) return;
          setState((prev) => ({
            ...prev,
            status: prev.price == null ? "connecting" : "live",
            source: "ws",
          }));
          ws.send(JSON.stringify({ type: "subscribe", symbol, tf }));
        };

        ws.onmessage = (event) => {
          if (disposed) return;
          let message = null;
          try {
            message = JSON.parse(event.data);
          } catch {
            return;
          }

          const latest =
            message?.type === "candle_subscription_ack"
              ? message.latest
              : message?.type === "candle_update" || message?.type === "candle_reconcile"
                ? message
                : null;
          const candle = normalizeCandle(latest?.candle ?? latest);
          if (candle) {
            applyPrice(candle.close, "ws");
          }
        };

        ws.onerror = () => {
          if (!disposed && lastPriceRef.current == null) {
            setState((prev) => ({ ...prev, status: "waiting", source: "ws" }));
          }
        };

        ws.onclose = () => {
          if (!disposed && lastPriceRef.current == null) {
            setState((prev) => ({ ...prev, status: "waiting", source: "ws" }));
          }
        };
      } catch {
        if (!disposed && lastPriceRef.current == null) {
          setState((prev) => ({ ...prev, status: "waiting", source: "ws" }));
        }
      }
    };

    // 상단 티커는 차트 WS를 우선 사용하고, WS가 늦거나 끊긴 경우 최신 캔들 REST로 폴백한다.
    // 이 덕분에 API/봇/브라우저 연결 흐름을 한눈에 확인하면서도 빈 가격 표시를 오래 방치하지 않는다.
    setState((prev) => ({ ...prev, status: "connecting" }));
    fetchLatest("rest");
    pollTimer = setInterval(() => fetchLatest("rest"), REST_POLL_INTERVAL_MS);
    connectWs();

    return () => {
      disposed = true;
      if (pollTimer) clearInterval(pollTimer);
      if (ws) {
        try {
          ws.close();
        } catch {
          // ignore
        }
      }
    };
  }, [enabled, symbol, tf]);

  const direction = useMemo(() => {
    if (!Number.isFinite(state.price) || !Number.isFinite(state.previousPrice)) {
      return "flat";
    }
    if (state.price > state.previousPrice) return "up";
    if (state.price < state.previousPrice) return "down";
    return "flat";
  }, [state.price, state.previousPrice]);

  return {
    ...state,
    direction,
    formattedPrice: formatPrice(state.price),
    symbol,
    tf,
  };
}
