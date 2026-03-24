// Structure Zone 활성화 상태와 진입가 보정값을 저장하기 위함
export async function saveZoneStateToServer(payload) {
  try {
    const res = await fetch("/api/zones/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.error(
        "[ZoneStateAPI] saveZoneStateToServer 응답 코드:",
        res.status
      );
    }
  } catch (e) {
    console.error("[ZoneStateAPI] saveZoneStateToServer 에러:", e);
  }
}
