import CandleChart from "../../components/CandleChart";
import { ZoneNotificationProvider } from "../../contexts/ZoneNotificationContext";
import GlassSurface from "../components/glass/GlassSurface";
import StructureZoneDeck from "../components/StructureZoneDeck";

export default function ChartWorkspace({
  ticker,
  onTimeframeChange,
}) {
  return (
    <ZoneNotificationProvider>
      <div className="ui-v3-page ui-v3-page--cockpit">
        <section className="ui-v3-cockpit-grid" aria-label="Live trading cockpit">
          <GlassSurface level="stage" interactive className="ui-v3-chart-stage">
            <div className="ui-v3-chart-shell ui-v3-chart-shell--premium">
              <div className="ui-v3-live-chart ui-v3-live-chart--premium">
                <CandleChart
                  isActive
                  onTimeframeChange={onTimeframeChange}
                />
              </div>
            </div>
          </GlassSurface>

          <aside className="ui-v3-decision-rail" aria-label="Decision context">
            <StructureZoneDeck currentPrice={ticker.price} />
          </aside>
        </section>
      </div>
    </ZoneNotificationProvider>
  );
}
