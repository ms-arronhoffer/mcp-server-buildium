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

/**
 * Map of build-time environment variables to extension config keys. When set,
 * these bake preconfigured (but user-overridable) defaults into the shipped
 * extension so users don't have to type settings after install. Only public
 * identifiers/URLs — never secrets.
 */
const ENV_TO_CONFIG_KEY = {
  MCP_SERVER_URL: "mcpServerUrl",
  ENTRA_TENANT_ID: "entraTenantId",
  ENTRA_CLIENT_ID: "entraClientId",
  ENTRA_SCOPES: "entraScopes",
  LLM_MODEL: "llmModel",
};

const CONFIG_KEYS = new Set(Object.values(ENV_TO_CONFIG_KEY));

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

/**
 * Resolve the build-time baked defaults from (in ascending precedence) an
 * optional `config.defaults.json` file at the extension root and the process
 * environment. Returns only recognised, non-empty string fields.
 */
async function resolveBakedConfig() {
  /** @type {Record<string, string>} */
  const baked = {};

  // 1) Optional config.defaults.json (git-ignored; distributor-provided).
  try {
    const fromFile = await readJson(join(root, "config.defaults.json"));
    for (const key of CONFIG_KEYS) {
      const value = fromFile[key];
      if (typeof value === "string" && value.trim() !== "") baked[key] = value.trim();
    }
  } catch {
    // No defaults file — that's fine.
  }

  // 2) Environment variables override the file.
  for (const [envName, key] of Object.entries(ENV_TO_CONFIG_KEY)) {
    const value = process.env[envName];
    if (typeof value === "string" && value.trim() !== "") baked[key] = value.trim();
  }

  return baked;
}

/** Render the generated `config.baked.js` module source for the given values. */
function renderBakedModule(baked) {
  return (
    "/** Generated at build time by scripts/build.mjs. Do not edit by hand. */\n" +
    `export const BAKED_CONFIG = ${JSON.stringify(baked, null, 2)};\n`
  );
}

async function buildTarget(target, baked) {
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

  // Overwrite the placeholder baked-config module with the resolved defaults so
  // the shipped extension is preconfigured (values remain user-overridable).
  await writeFile(join(outDir, "src", "config.baked.js"), renderBakedModule(baked));

  await writeFile(join(outDir, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  const bakedKeys = Object.keys(baked);
  const suffix = bakedKeys.length ? ` (baked: ${bakedKeys.join(", ")})` : "";
  console.log(`Built ${target} -> ${join("dist", target)}${suffix}`);
}

async function main() {
  const requested = process.argv.slice(2);
  const targets = requested.length > 0 ? requested : TARGETS;
  const baked = await resolveBakedConfig();
  for (const target of targets) {
    if (!TARGETS.includes(target)) {
      throw new Error(`Unknown target '${target}'. Valid targets: ${TARGETS.join(", ")}`);
    }
    await buildTarget(target, baked);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
