/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: 'hsl(var(--canvas) / <alpha-value>)',
        surface: 'hsl(var(--surface) / <alpha-value>)',
        raised: 'hsl(var(--raised) / <alpha-value>)',
        ink: 'hsl(var(--ink) / <alpha-value>)',
        'ink-muted': 'hsl(var(--ink-muted) / <alpha-value>)',
        'ink-faint': 'hsl(var(--ink-faint) / <alpha-value>)',
        rule: 'hsl(var(--rule) / <alpha-value>)',
        'rule-strong': 'hsl(var(--rule-strong) / <alpha-value>)',
        accent: 'hsl(var(--accent) / <alpha-value>)',
        'accent-ink': 'hsl(var(--accent-ink) / <alpha-value>)',
        'accent-wash': 'hsl(var(--accent-wash) / <alpha-value>)',
        caution: 'hsl(var(--caution) / <alpha-value>)',
        'caution-wash': 'hsl(var(--caution-wash) / <alpha-value>)',
        critical: 'hsl(var(--critical) / <alpha-value>)',
        positive: 'hsl(var(--positive) / <alpha-value>)',
      },
      fontFamily: {
        serif: ['ui-serif', 'Georgia', "'Times New Roman'", 'serif'],
        sans: [
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          "'Segoe UI'",
          'Roboto',
          "'Helvetica Neue'",
          'Arial',
          'sans-serif',
        ],
        mono: [
          'ui-monospace',
          "'SFMono-Regular'",
          "'Cascadia Mono'",
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      maxWidth: {
        measure: '68ch',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'slide-in-right': {
          from: { transform: 'translateX(1.5rem)', opacity: '0' },
          to: { transform: 'translateX(0)', opacity: '1' },
        },
        shimmer: {
          '0%': { opacity: '0.45' },
          '50%': { opacity: '0.8' },
          '100%': { opacity: '0.45' },
        },
      },
      animation: {
        'fade-in': 'fade-in 140ms ease-out',
        'slide-in-right': 'slide-in-right 160ms cubic-bezier(0.22, 1, 0.36, 1)',
        shimmer: 'shimmer 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
