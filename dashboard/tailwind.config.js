/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "Cairo",
          "Noto Sans Arabic",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      colors: {
        ink: {
          950: "#F0F4F8",
          900: "#FFFFFF",
          800: "#F8FAFC",
          700: "#F1F5F9",
          600: "#E2E8F0",
          500: "#CBD5E1",
        },
        line: {
          DEFAULT: "#E2E8F0",
          strong: "#CBD5E1",
        },
        fg: {
          DEFAULT: "#0F172A",
          muted: "#475569",
          subtle: "#94A3B8",
        },
        brand: {
          50: "#EFF6FF",
          100: "#DBEAFE",
          400: "#2563EB",
          500: "#1D4ED8",
          600: "#1E40AF",
          700: "#1E3A8A",
        },
        up: "#16A34A",
        down: "#DC2626",
        accent: "#7C3AED",
        nosignal: "#B45309",
        parsefail: "#C026D3",
        regime: {
          calm: "#16A34A",
          elevated: "#B45309",
          stressed: "#DC2626",
        },
      },
      boxShadow: {
        card: "0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04)",
        glow: "0 0 0 1px rgba(37,99,235,0.2), 0 4px 12px -2px rgba(37,99,235,0.12)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(2px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-1000px 0" },
          "100%": { backgroundPosition: "1000px 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 200ms ease-out",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
