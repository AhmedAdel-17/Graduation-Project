import { Routes, Route, Navigate } from "react-router-dom";
import { PredictionPage } from "./features/prediction/PredictionPage";
import { BacktestPage } from "./features/backtest/BacktestPage";
import { AdvancedBacktestPage } from "./features/advanced/AdvancedBacktestPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PredictionPage />} />
      <Route path="/backtest" element={<BacktestPage />} />
      <Route path="/backtest/advanced" element={<AdvancedBacktestPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
