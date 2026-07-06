/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        sans: ["Inter Tight", "-apple-system", "BlinkMacSystemFont", "system-ui", "sans-serif"],
      },
    },
  },
  // The bespoke design system lives in src/styles/design-system.css as plain
  // CSS (ported verbatim from the Jinja base template). Tailwind is layered on
  // top for incidental layout utilities only, so we disable preflight to avoid
  // fighting the existing reset.
  corePlugins: { preflight: false },
  plugins: [],
};
