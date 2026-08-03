import type { Config } from 'tailwindcss';

/**
 * Tailwind 配置。
 * 主题颜色通过 CSS 变量（src/styles/index.css）驱动，支持明/暗两套主题，
 * 暗色主题由 documentElement 上的 `dark` class 控制（见 hooks/useTheme.ts）。
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        canvas: 'rgb(var(--lf-canvas) / <alpha-value>)',
        surface: 'rgb(var(--lf-surface) / <alpha-value>)',
        raised: 'rgb(var(--lf-raised) / <alpha-value>)',
        border: 'rgb(var(--lf-border) / <alpha-value>)',
        ink: {
          DEFAULT: 'rgb(var(--lf-ink) / <alpha-value>)',
          muted: 'rgb(var(--lf-ink-muted) / <alpha-value>)',
          faint: 'rgb(var(--lf-ink-faint) / <alpha-value>)',
        },
        brand: {
          DEFAULT: 'rgb(var(--lf-brand) / <alpha-value>)',
          soft: 'rgb(var(--lf-brand-soft) / <alpha-value>)',
        },
        ok: 'rgb(var(--lf-ok) / <alpha-value>)',
        warn: 'rgb(var(--lf-warn) / <alpha-value>)',
        danger: 'rgb(var(--lf-danger) / <alpha-value>)',
        info: 'rgb(var(--lf-info) / <alpha-value>)',
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgb(0 0 0 / 0.04), 0 1px 3px rgb(0 0 0 / 0.06)',
        pop: '0 4px 16px rgb(0 0 0 / 0.12)',
      },
      borderRadius: {
        card: '0.625rem',
      },
    },
  },
  plugins: [],
} satisfies Config;
