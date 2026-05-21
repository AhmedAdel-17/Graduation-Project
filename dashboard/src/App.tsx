import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { HomeScreen } from "./features/home/HomeScreen";
import { BacktestScreen } from "./features/backtest/BacktestScreen";
import { HistoryScreen } from "./features/history/HistoryScreen";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<HomeScreen />} />
        <Route path="/backtest" element={<BacktestScreen />} />
        <Route path="/history" element={<HistoryScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
