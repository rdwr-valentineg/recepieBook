/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Frank Ruhl Libre"', '"Heebo"', 'serif'],
        body: ['"Heebo"', '"Assistant"', 'sans-serif'],
      },
      colors: {
        cream: '#FAF5EC',
        ink: '#2A2118',
        terracotta: {
          DEFAULT: '#C65D3D',
          dark: '#A04A2E',
        },
      },
    },
  },
  plugins: [],
};
