/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        // Plus Jakarta Sans — the rounded-geometric sans used across the dashboard
        // (headings + body). Falls back to the system stack until the webfont loads.
        sans: [
          '"Plus Jakarta Sans"',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Helvetica',
          'Arial',
          'sans-serif',
        ],
      },
      colors: {
        // Forest-green palette (Donezo-style): soft mint tints → deep pine.
        //   primary accent #3a7d52 · buttons #2f6b45 · deep cards/headings #15311f
        brand: {
          50: '#eef6f1', // page tint / light chips
          100: '#d6ebdd', // pale mint — soft chips, active nav pill
          200: '#aed7bb',
          300: '#7fbd93',
          400: '#4f9d6a',
          500: '#3a7d52', // primary accent (medium green)
          600: '#2f6b45', // buttons / filled controls
          700: '#285a3a', // dark accents
          800: '#1f4730', // deep green — featured cards
          900: '#15311f', // near-black green — headings
        },
      },
      boxShadow: {
        // Soft, low-contrast card shadow matching the reference dashboard.
        card: '0 1px 2px rgba(16, 40, 28, 0.04), 0 8px 24px -12px rgba(16, 40, 28, 0.12)',
        'card-hover': '0 2px 4px rgba(16, 40, 28, 0.06), 0 16px 32px -14px rgba(16, 40, 28, 0.18)',
      },
    },
  },
  plugins: [],
}
