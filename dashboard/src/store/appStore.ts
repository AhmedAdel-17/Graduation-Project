import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface AppState {
  selectedTicker: string;
  initialCapital: number;
  setSelectedTicker: (t: string) => void;
  setInitialCapital: (v: number) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      selectedTicker: "COMI.CA",
      initialCapital: 1_000_000,
      setSelectedTicker: (selectedTicker) => set({ selectedTicker }),
      setInitialCapital: (initialCapital) => set({ initialCapital }),
    }),
    { name: "egx-dashboard-state" }
  )
);
