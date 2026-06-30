import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface AppState {
  selectedTicker: string;
  initialCapital: number;
  activeProfileId: string;
  setSelectedTicker: (t: string) => void;
  setInitialCapital: (v: number) => void;
  setActiveProfileId: (id: string) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      selectedTicker: "COMI.CA",
      initialCapital: 1_000_000,
      activeProfileId: "shadow-tester",
      setSelectedTicker: (selectedTicker) => set({ selectedTicker }),
      setInitialCapital: (initialCapital) => set({ initialCapital }),
      setActiveProfileId: (activeProfileId) => set({ activeProfileId }),
    }),
    { name: "egx-dashboard-state" }
  )
);
