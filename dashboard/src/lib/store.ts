import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { User } from "@/types/api";

const noopStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

const safeStorage = () =>
  typeof window === "undefined" ? (noopStorage as any) : window.localStorage;

interface AuthState {
  token: string | null;
  user: User | null;
  setAuth: (token: string, user: User) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      logout: () => set({ token: null, user: null }),
    }),
    { name: "ta_auth", storage: createJSONStorage(safeStorage) },
  ),
);

interface UIState {
  sidebarCollapsed: boolean;
  theme: "dark" | "light";
  toggleSidebar: () => void;
  setTheme: (t: "dark" | "light") => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      theme: "dark",
      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setTheme: (t) => set({ theme: t }),
    }),
    { name: "ta_ui", storage: createJSONStorage(safeStorage) },
  ),
);
