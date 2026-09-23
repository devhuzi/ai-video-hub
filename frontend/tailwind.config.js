/** @type {import('tailwindcss').Config} */

// Every colour comes from a CSS variable defined in src/index.css (RGB channels,
// so opacity modifiers like bg-accent/20 keep working). Components must use
// these tokens only — no arbitrary hex classes.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      canvas: token("canvas"),
      surface: token("surface"),
      raised: token("raised"),
      hover: token("hover"),
      line: {
        DEFAULT: token("line"),
        strong: token("line-strong"),
      },
      fg: {
        DEFAULT: token("fg"),
        secondary: token("fg-secondary"),
        muted: token("fg-muted"),
      },
      accent: {
        DEFAULT: token("accent"),
        hover: token("accent-hover"),
        fg: token("accent-fg"),
      },
      ring: token("ring"),
      destructive: {
        DEFAULT: token("destructive"),
        fg: token("destructive-fg"),
      },
      // Status colours — only for run / step / item status.
      status: {
        running: token("status-running"),
        completed: token("status-completed"),
        failed: token("status-failed"),
        paused: token("status-paused"),
        neutral: token("status-neutral"),
      },
      overlay: token("overlay"),
    },
    fontFamily: {
      sans: ['"Inter Variable"', "Inter", "system-ui", "sans-serif"],
      mono: ['"JetBrains Mono Variable"', '"JetBrains Mono"', "ui-monospace", "monospace"],
    },
    fontSize: {
      "2xs": ["11px", { lineHeight: "16px" }],
      xs: ["12px", { lineHeight: "16px" }],
      sm: ["13px", { lineHeight: "20px" }],
      base: ["14px", { lineHeight: "20px" }],
      md: ["16px", { lineHeight: "24px" }],
      lg: ["20px", { lineHeight: "28px" }],
      xl: ["24px", { lineHeight: "32px" }],
    },
    borderRadius: {
      none: "0",
      sm: "4px",
      DEFAULT: "6px",
      full: "9999px",
    },
    boxShadow: {
      none: "none",
      menu: "0 8px 24px -4px rgb(0 0 0 / 0.35), 0 2px 6px -2px rgb(0 0 0 / 0.25)",
    },
    extend: {},
  },
  plugins: [],
};
