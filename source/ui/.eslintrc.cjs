/* ESLint config for the publisher dashboard (Vite + React + TypeScript).
 * Classic (.eslintrc) config — matches the `eslint . --ext ts,tsx` lint script
 * and the pinned eslint 8 / @typescript-eslint 8 toolchain. `.cjs` because
 * package.json is `"type": "module"`, so ESLint must load this as CommonJS.
 */
module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
  ],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  settings: {
    react: { version: 'detect' },
  },
  rules: {
    ...require('eslint-plugin-react-hooks').configs.recommended.rules,
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
  },
  ignorePatterns: [
    'dist',
    'build',
    'coverage',
    'node_modules',
    '*.config.js',
    '*.config.ts',
    'vite.config.*',
    '.eslintrc.cjs',
  ],
};
