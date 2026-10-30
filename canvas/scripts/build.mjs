// Build the canvas with esbuild.
//
//   node scripts/build.mjs        -> dist/main.js, dist/preload.js, dist/renderer/{index.html,app.js,app.css}
//   node scripts/build.mjs --web  -> dist-dev/ (renderer + dev shim + mock harness server) for `npm run dev:web`
import * as esbuild from "esbuild";
import { mkdir, readFile, rm, writeFile, copyFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = process.argv.includes("--web");
const r = (...p) => path.join(root, ...p);

const BROWSER_TARGET = "chrome130";
const VIEW_DIR = r("src/renderer/builtin-views");
// Foyelle's fonts (SIL Open Font License, DejaVu's Bitstream Vera licence; licences beside them),
// served with the canvas.
export const FONTS = ["Nunito.ttf", "Nunito-Italic.ttf", "GochiHand-Regular.ttf", "JetBrainsMono.ttf", "JetBrainsMono-Italic.ttf",
  "DejaVuSansCondensed.ttf", "DejaVuSansCondensed-Bold.ttf"];
const BUILTIN_VIEWS = ["approval", "clarify", "secret", "link", "accounts", "cards"];

/** Bundles a view script and inlines it, plus the shared view CSS, into its HTML template. */
export async function buildViewHtml(htmlPath, scriptPath, cssPath = path.join(VIEW_DIR, "view.css")) {
  const [template, css, bundle] = await Promise.all([
    readFile(htmlPath, "utf8"),
    readFile(cssPath, "utf8"),
    esbuild.build({
      entryPoints: [scriptPath],
      bundle: true,
      format: "iife",
      minify: true,
      write: false,
      target: BROWSER_TARGET,
      platform: "browser",
      legalComments: "none",
    }),
  ]);
  const js = bundle.outputFiles[0].text.replace(/<\/(script)/gi, "<\\/$1");
  if (!template.includes("<!-- @view-script -->") || !template.includes("<!-- @view-style -->")) {
    throw new Error(`${htmlPath}: missing @view-script or @view-style marker`);
  }
  return template
    .replace("<!-- @view-style -->", () => `<style>\n${css}</style>`)
    .replace("<!-- @view-script -->", () => `<script>${js}</script>`);
}

/** esbuild plugin: `import html from "builtin-view:approval"` yields the finished view HTML. */
const builtinViews = {
  name: "builtin-views",
  setup(build) {
    build.onResolve({ filter: /^builtin-view:/ }, (args) => ({ path: args.path.slice("builtin-view:".length), namespace: "builtin-view" }));
    build.onLoad({ filter: /.*/, namespace: "builtin-view" }, async (args) => {
      if (!BUILTIN_VIEWS.includes(args.path)) return { errors: [{ text: `unknown built-in view ${args.path}` }] };
      const html = await buildViewHtml(path.join(VIEW_DIR, `${args.path}.html`), path.join(VIEW_DIR, `${args.path}.ts`));
      return {
        contents: html,
        loader: "text",
        watchFiles: [path.join(VIEW_DIR, `${args.path}.html`), path.join(VIEW_DIR, `${args.path}.ts`), path.join(VIEW_DIR, "view.css")],
      };
    });
  },
};

async function buildRenderer(outDir, { extraScript } = {}) {
  await mkdir(outDir, { recursive: true });
  await esbuild.build({
    entryPoints: [r("src/renderer/app.ts")],
    bundle: true,
    format: "iife",
    target: BROWSER_TARGET,
    platform: "browser",
    minify: true,
    sourcemap: false,
    legalComments: "none",
    outfile: path.join(outDir, "app.js"),
    plugins: [builtinViews],
    define: { "process.env.NODE_ENV": '"production"' },
  });
  await copyFile(r("src/renderer/app.css"), path.join(outDir, "app.css"));
  await mkdir(path.join(outDir, "fonts"), { recursive: true });
  for (const font of FONTS) await copyFile(r("src/renderer/fonts", font), path.join(outDir, "fonts", font));
  let html = await readFile(r("src/renderer/index.html"), "utf8");
  if (extraScript) {
    html = html.replace('<script src="app.js"></script>', `<script src="${extraScript}"></script>\n<script src="app.js"></script>`);
  }
  await writeFile(path.join(outDir, "index.html"), html);
}

async function buildElectron() {
  await rm(r("dist"), { recursive: true, force: true });
  const common = { bundle: true, platform: "node", target: "node22", format: "cjs", external: ["electron"], legalComments: "none", minify: false };
  await esbuild.build({ ...common, entryPoints: [r("src/main/main.ts")], outfile: r("dist/main.js") });
  await esbuild.build({ ...common, entryPoints: [r("src/preload/preload.ts")], outfile: r("dist/preload.js") });
  await buildRenderer(r("dist/renderer"));
}

async function buildWeb() {
  const out = r("dist-dev");
  await rm(out, { recursive: true, force: true });
  await buildRenderer(path.join(out, "web"), { extraScript: "dev-shim.js" });
  await esbuild.build({
    entryPoints: [r("dev/dev-shim.ts")],
    bundle: true,
    format: "iife",
    target: BROWSER_TARGET,
    outfile: path.join(out, "web/dev-shim.js"),
  });
  // Demo surfaces for the mock harness, built with the same view runtime.
  await mkdir(path.join(out, "surfaces"), { recursive: true });
  for (const name of ["files", "status"]) {
    const html = await buildViewHtml(r(`dev/surfaces/${name}.html`), r(`dev/surfaces/${name}.ts`), r("dev/surfaces/surface.css"));
    await writeFile(path.join(out, "surfaces", `${name}.html`), html);
  }
  await esbuild.build({
    entryPoints: [r("dev/dev-server.ts")],
    bundle: true,
    platform: "node",
    target: "node22",
    format: "esm",
    external: ["ws"],
    outfile: path.join(out, "dev-server.mjs"),
  });
}

const t0 = Date.now();
if (web) await buildWeb();
else await buildElectron();
console.log(`[build] ${web ? "dist-dev (web)" : "dist (electron)"} in ${Date.now() - t0} ms`);
