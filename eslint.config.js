import js from '@eslint/js';
import globals from 'globals';

export default [
  {
    ignores: [
      '.venv/',
      'node_modules/',
      'src/nomos/site/static/dist/',
      'staticfiles/',
    ],
  },
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
    },
    rules: {
      indent: ['error', 2],
    },
  },
  {
    files: ['vite.config.js', 'eslint.config.js'],
    languageOptions: {
      globals: globals.node,
    },
  },
];
