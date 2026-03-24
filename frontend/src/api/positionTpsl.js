//// frontend/src/api/positionTpsl.js

function buildErrorMessage(status, body) {
  const detail = body?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  if (detail && typeof detail === "object") {
    const code = detail.code ? String(detail.code) : "";
    const message = detail.message ? String(detail.message) : "";
    const merged = [code, message].filter(Boolean).join(": ");
    if (merged) return merged;
  }
  return `TP/SL update failed (status=${status})`;
}

export async function updatePositionTpsl({
  overlayId,
  field,
  price,
}) {
  const id = String(overlayId || "").trim();
  if (!id) {
    throw new Error("overlayId is required");
  }
  if (field !== "tp" && field !== "sl") {
    throw new Error("field must be tp or sl");
  }
  const numericPrice = Number(price);
  if (!Number.isFinite(numericPrice) || numericPrice <= 0) {
    throw new Error("price must be a positive number");
  }

  const res = await fetch(`/api/positions/${encodeURIComponent(id)}/tpsl`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      field,
      price: numericPrice,
    }),
  });

  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    throw new Error(buildErrorMessage(res.status, body));
  }

  return body;
}
