// Build the canvas with esbuild.
//
//   node scripts/build.mjs        -> dist/main.js, dist/preload.js, dist/renderer/{index.html,app.js,app.css}
//   node scripts/build.mjs --web  -> dist-dev/ (renderer + dev shim + mock harness server) for `npm run dev:web`
//   node scripts/build.mjs --demo <dir>  -> the web demo (demo/demo.ts): static files for foyelle.org/demo
import * as esbuild from "esbuild";
import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rm, writeFile, copyFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = process.argv.includes("--web");
const demoAt = process.argv.indexOf("--demo");
const demoOut = demoAt >= 0 ? process.argv[demoAt + 1] : null;
if (demoAt >= 0 && !demoOut) throw new Error("usage: node scripts/build.mjs --demo <dir>");
const r = (...p) => path.join(root, ...p);

const BROWSER_TARGET = "chrome130";
const VIEW_DIR = r("src/renderer/builtin-views");
// Foyelle's fonts (SIL Open Font License, DejaVu's Bitstream Vera licence; licences beside them),
// served with the canvas.
export const FONTS = ["Nunito.ttf", "Nunito-Italic.ttf", "GochiHand-Regular.ttf", "JetBrainsMono.ttf", "JetBrainsMono-Italic.ttf",
  "DejaVuSansCondensed.ttf", "DejaVuSansCondensed-Bold.ttf"];
const BUILTIN_VIEWS = ["approval", "clarify", "secret", "link", "accounts"];

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

async function buildRenderer(outDir, { extraScript, extraStyle, title, icon } = {}) {
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
  if (extraStyle) html = html.replace('<link rel="stylesheet" href="app.css">', `<link rel="stylesheet" href="app.css">\n<link rel="stylesheet" href="${extraStyle}">`);
  if (title) html = html.replace("<title>Foyelle</title>", `<title>${title}</title>`);
  if (icon) html = html.replace("</title>", `</title>\n<link rel="icon" href="${icon}" type="image/svg+xml">`);
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

/**
 * The web demo: the renderer, demo.js (mock harness, recorded helpers, banner) and every card's
 * page as cards/<sha-256>.html, the recorded ones (demo/cards) and the canvas's built-in views.
 * The page asks for a card by the hash of its HTML, so only these pages can load.
 */
async function buildDemo(out) {
  await rm(out, { recursive: true, force: true });
  // The site's own icon (site/favicon.svg), one folder up on foyelle.org.
  await buildRenderer(out, { extraScript: "demo.js", extraStyle: "demo.css", title: "Foyelle demo", icon: "../favicon.svg" });
  await copyFile(r("demo/demo.css"), path.join(out, "demo.css"));
  await mkdir(path.join(out, "cards"), { recursive: true });
  const bySha = {}, byName = {};
  const add = async (html) => {
    const sha = createHash("sha256").update(html, "utf8").digest("hex");
    await writeFile(path.join(out, "cards", `${sha}.html`), html);
    // Without .html: Cloudflare serves cards/<sha>.html there (and redirects the .html address).
    return (bySha[sha] = `cards/${sha}`);
  };
  for (const file of (await readdir(r("demo/cards"))).filter((f) => f.endsWith(".html")).sort()) {
    byName[file] = await add(await readFile(r("demo/cards", file), "utf8"));
  }
  for (const view of BUILTIN_VIEWS) await add(await buildViewHtml(path.join(VIEW_DIR, `${view}.html`), path.join(VIEW_DIR, `${view}.ts`)));
  await esbuild.build({
    entryPoints: [r("demo/demo.ts")],
    bundle: true,
    format: "iife",
    target: BROWSER_TARGET,
    platform: "browser",
    minify: true,
    legalComments: "none",
    outfile: path.join(out, "demo.js"),
    plugins: [{
      name: "demo-cards",
      setup(build) {
        build.onResolve({ filter: /^demo-cards$/ }, () => ({ path: "demo-cards", namespace: "demo-cards" }));
        build.onLoad({ filter: /.*/, namespace: "demo-cards" }, () => ({ contents: JSON.stringify({ bySha, byName }), loader: "json" }));
      },
    }],
  });
}

const t0 = Date.now();
if (demoOut) await buildDemo(path.resolve(demoOut));
else if (web) await buildWeb();
else await buildElectron();
console.log(`[build] ${demoOut ? `${demoOut} (demo)` : web ? "dist-dev (web)" : "dist (electron)"} in ${Date.now() - t0} ms`);
