import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { DashboardScreen } from "./features/dashboard/DashboardScreen";
import { BacktestScreen } from "./features/backtest/BacktestScreen";
import { HistoryScreen } from "./features/history/HistoryScreen";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<DashboardScreen />} />
        <Route path="/backtest" element={<BacktestScreen />} />
        <Route path="/history" element={<HistoryScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
