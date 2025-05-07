const esbuild = require('esbuild');
const stylePlugin = require('esbuild-style-plugin');
const path = require('path');

async function build() {
    console.log("--- Starting esbuild build process (Revised Approach) ---");
    try {
        await esbuild.build({
            // Define BOTH JS and main CSS as entry points
            entryPoints: {
                'script': 'app/static/script.js', // Output will be dist/script.js
                'input': 'app/static/input.css'   // Output will be dist/input.css
            },
            bundle: true,
            // Use outdir instead of outfile when using multiple entry points
            outdir: 'app/static/dist',
            entryNames: '[name]', // Keep original names (script.js, input.css)
            format: 'iife', // For script.js
            platform: 'browser',
            sourcemap: true,
            minify: false,
            loader: {
              '.woff': 'file',
              '.woff2': 'file',
              '.ttf': 'file',
              '.eot': 'file',
              '.svg': 'file'
            },
            plugins: [
                stylePlugin({
                    // No 'extract' needed when CSS is an entry point
                    // Plugin is still needed for PostCSS processing of input.css
                    // and potentially CSS imported from node_modules via script.js
                    postcss: {
                        // Intentionally empty to force loading postcss.config.js
                    }
                })
            ],
            // Assets relative to the outdir ('dist')
            assetNames: 'assets/[name]-[hash]',

        });
        console.log('Build successful!');
    } catch (error) {
        console.error('Build failed:', error);
        process.exit(1);
    }
}

build();
