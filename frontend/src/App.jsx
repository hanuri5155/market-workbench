import { useLocation } from "react-router-dom";
import "./styles/layout.css";
import OtpGate from "./components/OtpGate";
import UiPreviewApp from "./ui-v2/UiPreviewApp";
import UiV3App from "./ui-v3/UiV3App";

export default function App() {
  const location = useLocation();
  const isUiPreviewRoute = location.pathname.startsWith("/ui-preview");
  const isUiV2Route = location.pathname.startsWith("/ui-v2");

  if (isUiPreviewRoute) {
    return (
      <div id="app-root">
        <UiPreviewApp basePath="/ui-preview" preview />
      </div>
    );
  }

  if (isUiV2Route) {
    return (
      <div id="app-root">
        <OtpGate />
        <div className="ui-v2-authenticated-shell">
          <UiPreviewApp basePath="/ui-v2" preview={false} />
        </div>
      </div>
    );
  }

  return (
    <div id="app-root">
      <OtpGate />
      <UiV3App />
    </div>
  );
}
