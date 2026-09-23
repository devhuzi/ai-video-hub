import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";

export default [
  { ignores: ["build/**", "node_modules/**"] },
  { settings: { react: { version: "detect" } } },
  js.configs.recommended,
  react.configs.flat.recommended,
  react.configs.flat["jsx-runtime"],
  jsxA11y.flatConfigs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.browser, process: "readonly" },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "react-hooks": reactHooks },
    settings: { react: { version: "detect" } },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react/prop-types": "off",
      // Generated clips have no caption tracks to offer.
      "jsx-a11y/media-has-caption": "off",
    },
  },
  {
    files: ["vite.config.js"],
    languageOptions: { sourceType: "module", globals: { ...globals.node } },
  },
  {
    files: ["tailwind.config.js", "postcss.config.js"],
    languageOptions: { sourceType: "commonjs", globals: { ...globals.node } },
  },
];
