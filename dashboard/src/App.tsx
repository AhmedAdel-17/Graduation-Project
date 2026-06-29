import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { HomeScreen } from "./features/home/HomeScreen";
import { BacktestScreen } from "./features/backtest/BacktestScreen";
import { HistoryScreen } from "./features/history/HistoryScreen";
import { InvestorPage } from "./features/investor/InvestorPage";
import { MonitoringPage } from "./features/monitoring/MonitoringPage";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<HomeScreen />} />
        <Route path="/backtest" element={<BacktestScreen />} />
        <Route path="/history" element={<HistoryScreen />} />
        <Route path="/decisions" element={<InvestorPage />} />
        {/* Legacy route redirect */}
        <Route path="/investor" element={<Navigate to="/decisions" replace />} />
        <Route path="/monitoring" element={<MonitoringPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
