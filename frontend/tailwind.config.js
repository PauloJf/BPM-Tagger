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
  // fighting the existing reset — and Tailwind's `container` core plugin,
  // whose breakpoint-stepped max-widths silently overrode the design system's
  // `.container { max-width: 1180px }` (capping every page at 768px on
  // 768-1023px viewports, 1024px up to 1279px, and widening to 1280px above —
  // which also collapsed the Run cockpit to one overflowing column on
  // ~1000px-wide windows).
  corePlugins: { preflight: false, container: false },
  plugins: [],
};
