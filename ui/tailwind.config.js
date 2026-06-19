/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Palette from the supplied swatch: deep teal → charcoal, with pale mint.
        //   #2a3133 charcoal · #34504f dark teal · #3d7e80 teal · #d2e2dd mint
        brand: {
          50: '#eef5f4',
          100: '#d2e2dd', // pale mint (swatch #4) — page tint / light chips
          200: '#aecfca',
          300: '#82b2ae',
          400: '#56938f',
          500: '#3d7e80', // medium teal (swatch #3) — primary accent
          600: '#356a6b',
          700: '#34504f', // dark teal (swatch #2) — deep accents
          800: '#2c4040',
          900: '#2a3133', // charcoal (swatch #1) — sidebar / headings
        },
      },
    },
  },
  plugins: [],
}
