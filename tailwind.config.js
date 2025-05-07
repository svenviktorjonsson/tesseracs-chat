/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
      "./app/static/**/*.html", // Scan HTML files in static
      "./app/static/**/*.js",   // Scan JS files in static
    ],
    theme: {
      extend: {},
    },
    plugins: [],
  }