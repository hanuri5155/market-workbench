## backend/core/state/state_snapshot.py

import json, time, threading
from core.state import shared_state
from core.utils.log_utils import log

def start_state_snapshot_writer(path="/tmp/shared_state.json", interval=0.25):
    def loop():
        while True:
            try:
                snap = {
                    "ts": time.time(),
                    "latest_price_map": shared_state.latest_price_map,
                    "bbands_map": shared_state.bbands_map,
                    "last_confirmed_candle": shared_state.last_confirmed_candle,
                }
                with open(path, "w") as f:
                    json.dump(snap, f, ensure_ascii=False, separators=(",", ":"))
            except Exception as e:
                log(f"⚠️ snapshot write error: {e}")
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    log(f"📝 snapshot writer started → {path} (every {interval}s)")
