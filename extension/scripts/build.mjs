/**
 * Build script: assemble loadable extension bundles for Chrome and Firefox.
 *
 * There is no bundler — the extension ships native ES modules. This script only
 * merges the shared manifest with the platform-specific manifest and copies the
 * source + icons into `dist/<platform>/`.
 *
 * Usage:
 *   node scripts/build.mjs            # builds both chrome and firefox
 *   node scripts/build.mjs chrome     # builds a single target
 */

import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const srcDir = join(root, "src");
const iconsDir = join(root, "icons");
const distDir = join(root, "dist");

const TARGETS = ["chrome", "firefox"];

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function buildTarget(target) {
  const base = await readJson(join(srcDir, "manifest.base.json"));
  const overrides = await readJson(join(srcDir, `manifest.${target}.json`));
  const manifest = { ...base, ...overrides };

  const outDir = join(distDir, target);
  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  // Copy source, excluding the manifest fragments.
  await cp(srcDir, join(outDir, "src"), {
    recursive: true,
    filter: (path) => !/manifest\.(base|chrome|firefox)\.json$/.test(path),
  });
  await cp(iconsDir, join(outDir, "icons"), { recursive: true });

  await writeFile(join(outDir, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  console.log(`Built ${target} -> ${join("dist", target)}`);
}

async function main() {
  const requested = process.argv.slice(2);
  const targets = requested.length > 0 ? requested : TARGETS;
  for (const target of targets) {
    if (!TARGETS.includes(target)) {
      throw new Error(`Unknown target '${target}'. Valid targets: ${TARGETS.join(", ")}`);
    }
    await buildTarget(target);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
