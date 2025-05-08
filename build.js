// build.js

const esbuild = require('esbuild');
const stylePlugin = require('esbuild-style-plugin');
// const path = require('path'); // Not used

async function build() {
    console.log("--- Starting esbuild build process (Revised Approach with Explicit PostCSS Plugins) ---");
    try {
        await esbuild.build({
            entryPoints: {
                'script': 'app/static/script.js',
                'input': 'app/static/input.css'
            },
            bundle: true,
            outdir: 'app/static/dist',
            entryNames: '[name]',
            format: 'iife',
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
                    postcss: {
                        // Explicitly define PostCSS plugins here
                        plugins: [
                            require('tailwindcss')('./tailwind.config.js'), // Pass config path explicitly
                            require('autoprefixer'),
                        ],
                    },
                })
            ],
            assetNames: 'assets/[name]-[hash]',
        });
        console.log('Build successful!');
    } catch (error) {
        console.error('Build failed:', error);
        process.exit(1);
    }
}

build();