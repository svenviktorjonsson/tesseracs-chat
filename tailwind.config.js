/** @type {import('tailwindcss').Config} */
module.exports = {
    safelist: [
        'js-sticky',
    ],
    content: [
      './app/static/**/*.html',      // Scan all our HTML files
      './app/static/script.js',        // Scan our main script file
      './app/static/js/**/*.js',     // Scan our custom JS modules in the js/ folder
      '!./app/static/js/**/*.min.js', // IMPORTANT: Exclude minified vendor files
    ],
    theme: {
      extend: {},
    },
    plugins: [],
  }