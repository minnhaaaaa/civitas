import { AuditView } from "./AuditView";
import { LandingPage } from "./LandingPage";

export function App() {
  return window.location.pathname.startsWith("/audit/") ? <AuditView /> : <LandingPage />;
}
