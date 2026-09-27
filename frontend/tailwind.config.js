/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#090d16',
        surface: {
          DEFAULT: '#111726',
          muted: '#192238',
          border: '#23304c',
        },
        accent: {
          green: '#10b981',
          emerald: '#059669',
          red: '#ef4444',
          cyan: '#06b6d4',
          blue: '#3b82f6',
          purple: '#a855f7',
          yellow: '#eab308',
        }
      }
    },
  },
  plugins: [],
}
