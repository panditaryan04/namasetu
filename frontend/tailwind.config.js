/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Clinical teal (primary) and saffron (accent). Shades checked for AA contrast on white/slate-950.
        teal: {
          50: "#effefb",
          100: "#c8fff4",
          200: "#91fee9",
          300: "#53f5dc",
          400: "#1fe0c8",
          500: "#06c4b0",
          600: "#019e90",
          700: "#067d74",
          800: "#0a635d",
          900: "#0d524d",
          950: "#003231",
        },
        saffron: {
          50: "#fff8ed",
          100: "#ffefd4",
          200: "#ffdba8",
          300: "#ffc070",
          400: "#ff9933",
          500: "#fe7c11",
          600: "#ef6107",
          700: "#c64808",
          800: "#9d390f",
          900: "#7e3110",
        },
        // Light medical-blue theme; values live as CSS variables in index.css.
        med: {
          bg: "var(--bg)",
          primary: "var(--primary)",
          "primary-dark": "var(--primary-dark)",
          accent: "var(--accent)",
          heading: "var(--text-heading)",
          body: "var(--text-body)",
          border: "var(--border)",
        },
      },
      fontFamily: {
        sans: ["Inter Variable", "Inter", "Noto Sans Devanagari", "system-ui", "sans-serif"],
        display: ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "Noto Sans Devanagari", "system-ui", "sans-serif"],
      },
      backgroundImage: {
        // Decorative gradient (dots, rings) and the AA-safe darker steps used under white or as text.
        "brand-gradient": "linear-gradient(135deg, var(--primary), var(--accent))",
        "brand-gradient-strong": "linear-gradient(135deg, var(--primary-dark), var(--accent-dark))",
        "brand-gradient-text": "linear-gradient(135deg, var(--primary-text), var(--accent-text))",
      },
      boxShadow: {
        glass: "0 20px 60px -15px rgba(14, 165, 233, 0.25)",
        "glass-sm": "0 8px 30px -12px rgba(14, 165, 233, 0.22)",
        lift: "0 12px 28px -10px rgba(3, 105, 161, 0.45)",
      },
      keyframes: {
        orb: {
          "0%, 100%": { transform: "translate3d(0, 0, 0) scale(1)" },
          "50%": { transform: "translate3d(6vw, -4vh, 0) scale(1.08)" },
        },
      },
      animation: {
        orb: "orb 28s ease-in-out infinite",
        "orb-slow": "orb 40s ease-in-out infinite reverse",
      },
    },
  },
  plugins: [],
};
