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
      },
      fontFamily: {
        sans: ["Inter", "Noto Sans Devanagari", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
