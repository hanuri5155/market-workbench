// 타임프레임 라벨
const TF_LABEL_MAP = {
  "60": "1h",
  "240": "4h",
  "1440": "1d",
};

// 숫자 판별
export function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// 타임프레임 표시 문자열
export function formatTfButtonLabel(tf) {
  const tfStr = String(tf);
  return TF_LABEL_MAP[tfStr] ?? `${tfStr}m`;
}

// 차트 period 변환
export function tfToPeriod(tf) {
  const n = Number(tf);
  if (!Number.isFinite(n) || n <= 0) {
    return { span: 15, type: "minute" };
  }

  if (n % 1440 === 0) {
    return { span: n / 1440, type: "day" };
  }

  if (n >= 60 && n % 60 === 0) {
    return { span: n / 60, type: "hour" };
  }

  return { span: n, type: "minute" };
}

// 타임프레임 문자열 변환
export function periodToTf(period) {
  if (!period) return "15";
  if (period.type === "day") {
    const span = Number(period.span);
    return String((Number.isFinite(span) && span > 0 ? span : 1) * 1440);
  }
  if (period.type === "hour") {
    return String(period.span * 60);
  }
  if (period.type === "minute") {
    return String(period.span);
  }
  return "15";
}

// JSON 응답 파싱
export async function safeJson(res, label) {
  const ct = res.headers.get("content-type") || "";
  const raw = await res.text();

  if (!ct.includes("application/json")) {
    console.error(
      `[CandleChart] ${label}: JSON이 아닌 응답입니다.`,
      res.status,
      raw.slice(0, 200)
    );
    return null;
  }

  try {
    return JSON.parse(raw);
  } catch (error) {
    console.error(
      `[CandleChart] ${label}: JSON 파싱 실패`,
      error,
      raw.slice(0, 200)
    );
    return null;
  }
}

// 서버 캔들 정규화
export function mapServerCandle(candle) {
  return {
    timestamp: Number(candle.start),
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  };
}

// 메인 pane 식별
export function resolveMainPaneId(chart) {
  if (!chart || typeof chart.getPaneOptions !== "function") return "candle_pane";
  try {
    const panes = chart.getPaneOptions();
    if (Array.isArray(panes) && panes[0]?.id) {
      return panes[0].id;
    }
  } catch {
    // ignore
  }
  return "candle_pane";
}

// 차트 timestamp 투영
export function projectTsToChart(dataList, ts, mode = "nearest") {
  if (!Array.isArray(dataList) || dataList.length === 0) return null;
  const n = dataList.length;

  const first = Number(dataList[0]?.timestamp);
  const last = Number(dataList[n - 1]?.timestamp);
  if (!Number.isFinite(first) || !Number.isFinite(last)) return null;

  if (ts <= first) return first;
  if (ts >= last) return last;

  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const value = Number(dataList[mid].timestamp);
    if (value < ts) lo = mid + 1;
    else hi = mid;
  }

  const right = Number(dataList[lo]?.timestamp);
  const left = Number(dataList[Math.max(0, lo - 1)]?.timestamp);

  if (right === ts) {
    return right;
  }

  if (mode === "floor") return left;
  if (mode === "ceil") return right;
  return Math.abs(ts - left) <= Math.abs(right - ts) ? left : right;
}
