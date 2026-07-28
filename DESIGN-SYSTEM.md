# BPM Tagger — Design System

Reference for the web UI. The **source of truth is `src/styles/design-system.css`**
(tokens + utility classes) — this doc summarizes it and the conventions layered on
top. Don't invent new tokens; extend the CSS variables if something is missing.

## Principles
- **Dark-first**, violet accent. A light theme exists via `<html data-theme="light">`.
- **Utility classes for structure, inline styles for one-offs.** Reusable patterns
  (`btn`, `badge`, `card`, `segmented`, `filter-pills`, `tracks-table`, …) live in
  `design-system.css`; page-specific layout is inline `style={{…}}` referencing the
  CSS variables. No Tailwind styling (preflight is off; it's layout-utility only).
- **Monospace for data** (BPM, counts, timestamps, technical labels); Inter Tight
  for everything else.

## Typography
- Sans: **Inter Tight** (self-hosted, weights 400–700), body 14px.
- Mono: **JetBrains Mono** via `var(--mono)` (400–600) — numbers use
  `font-variant-numeric: tabular-nums`.
- Page title: `h1` **28 / 600 / -0.02em** (see PageHeader). Detail/hero pages keep
  their own: TrackDetail 22, PlaylistDetail 24, About 26.
- Eyebrow / section label: mono **10px / 600 / uppercase / 0.12em**, `--muted`.

## Color tokens (oklch; see `:root` in design-system.css)
| Group | Variables |
|---|---|
| Surfaces | `--bg --surface --surface-2 --border --border-strong --chip-bg --row-hover --wave` |
| Text | `--text` (near-white) · `--muted` |
| Accent (hue 290) | `--accent --accent-2 --accent-soft --accent-soft-strong --accent-border --accent-glow` |
| OK / green | `--ok-fg --ok-bg --ok-bd` |
| Warn / amber | `--warn-fg --warn-bg --warn-bg-hover --warn-bd` |
| Error / red | `--err-fg --err-bg --err-bd` |
| Info / blue | `--info-fg --info-bg --info-bd` |

Accent is derived from `--accent-h: 290` — rotate the hue to re-theme.

## Shape & motion
- Radius: buttons/inputs **8px**, cards **14px**, pills/toggles **999px**, tiles ~8/12px.
- Card: `background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:22px`.
- Focus ring: `outline:2px solid var(--accent); outline-offset:2px` via `:focus-visible`.
- Keyframes: `pulse-beat` (BPM dot), `pulse-soft` (scan dot), `ripple` (tap), `fade-in`
  (page enter), `shake-x` (login error), `scan-bar`, `spin`. All suppressed under
  `prefers-reduced-motion`.

## Core components (class → use)
- `btn` + `btn-primary | btn-ghost | btn-soft | btn-danger | btn-bare`; sizes
  `btn-sm | btn-md | btn-lg`.
- `badge` + `badge--ok | --review | --error | --locked | --neutral | --accent | --pending`
  (status pills; add `badge-dot` for the leading dot).
- `chip` + `chip--have | --missing | --queued | --neutral | --active | --done | --failed | --warn`
  (grabber/queue counts).
- `segmented` + `segmented-btn(.active)` — the standard switcher (Library tabs, Run
  mode picker). Prefer this over ad-hoc button rows.
- `filter-pills` + `filter-pill(.active)` + `pill-count` — filter strips.
- `tracks-table` / `tracks-header` / `tracks-row(.flagged)` — list tables.
- Inputs: bare `input/select/textarea` are already themed. `toggle-wrap`,
  `stepper`, `slider-wrap`, `search-wrap`+`search-input` for controls.
- `card`, `stat-card`, `browse-card`, `pl-card`, `review-card` — surfaces.
- `pagination`, `section-label`+`section-hint`, `flash.success|error`, `conf-bar-*`.

## Shared React components (`src/components/`)
- **`PageHeader`** — every top-level page's header: `title` (h1 28), optional
  `subtitle` (13 muted), optional `tabs` slot, right-aligned `actions` slot. Search /
  filter / pagination go in a separate toolbar row *below* it, not inside. Detail/hero
  pages and Login/Nav do **not** use it.
- **`BpmMark`** — the 5-bar equalizer logo glyph; single source for nav + login (`size` prop).
- `LibraryTabs` — `segmented` Tracks/Artists/Albums switcher.
- `Artwork` (`Cover`, `ArtistImage`, `ArtToggle`), `trackBits`, `DetectorBar`,
  `BpmDisplay`, `Toggle`, `PlayerBar`, `Nav`.

## Layout conventions
- App shell: fixed sidebar (`--sidebar-w` 220px, 62px collapsed) above 1100px;
  mobile top-bar + hamburger below. Content in `.container` (max 1180px). Persistent
  `.player-bar` starts past the sidebar.
- Page header spacing: `margin-bottom:22`; toolbar row `margin-bottom:16`.
- Run mode: single-column phone layout by default; two-column `.run-desktop`
  cockpit above 900px.
- Breakpoints: **700px** (mobile top-bar / dense tables), **900px** (Run desktop),
  **1100px** (sidebar).

## Voice
Terse, technical, lower-case eyebrow labels. Numbers and file/tech names in mono.
Tooltips explain *why* (e.g. "starred tracks are preferred when building run queues").
