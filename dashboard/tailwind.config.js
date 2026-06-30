/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
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
          950: "#071525",
          900: "#FFFFFF",
          800: "#F7FBFA",
          700: "#EEF7F6",
          600: "#D7E7EA",
          500: "#B8D1D6",
        },
        line: {
          DEFAULT: "#D7E7EA",
          strong: "#B8D1D6",
        },
        fg: {
          DEFAULT: "#061B3D",
          muted: "#31506F",
          subtle: "#6D8496",
        },
        brand: {
          50: "#EAFBF4",
          100: "#DDF4EC",
          400: "#22B887",
          500: "#119F73",
          600: "#0C7E5D",
          700: "#075E48",
        },
        up: "#119F73",
        down: "#DC2626",
        accent: "#22B887",
        nosignal: "#B45309",
        parsefail: "#C026D3",
        regime: {
          calm: "#16A34A",
          elevated: "#B45309",
          stressed: "#DC2626",
        },
      },
      boxShadow: {
        card: "0 1px 3px rgba(6,27,61,0.08), 0 1px 2px rgba(6,27,61,0.04)",
        glow: "0 0 0 1px rgba(34,184,135,0.2), 0 4px 12px -2px rgba(34,184,135,0.16)",
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
