/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        darkBg: "#090d16",
        cardBg: "#111827",
        cardBorder: "#1f2937",
        accentEmerald: "#10b981",
        accentBlue: "#3b82f6",
        accentCyan: "#06b6d4",
        accentRed: "#ef4444",
        accentYellow: "#f59e0b"
      }
    },
  },
  plugins: [],
}
