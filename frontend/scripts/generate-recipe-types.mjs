// Regenerate recipe types AND runtime enum consts from src/types/recipe.schema.json.
// Run via `npm run generate-recipe-types`. Implemented in JS rather than as a
// shell-quoted json2ts invocation so the banner comment survives Windows cmd.exe,
// which doesn't honor single-quote argument grouping.
//
// Two outputs, both derived from the ONE schema so they can't drift from it:
//   - recipes.generated.ts       — the TS interfaces (via json-schema-to-typescript)
//   - recipes.enums.generated.ts — the runtime `as const` arrays that mirror the
//     schema's enums/const discriminators (STEP_KINDS, SUPPORTED_CHART_TYPES,
//     VALID_VALUE_FORMATS, QUERY_ENGINES). These used to be hand-maintained in
//     recipes.ts with a "must match the schema" comment — the exact drift hazard
//     this codegen removes.
import { compileFromFile } from 'json-schema-to-typescript';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = resolve(here, '..', 'src', 'types', 'recipe.schema.json');
const typesOut = resolve(here, '..', 'src', 'types', 'recipes.generated.ts');
const enumsOut = resolve(here, '..', 'src', 'types', 'recipes.enums.generated.ts');

const banner = '/* Auto-generated from recipe.schema.json — do not edit by hand. Regenerate via npm run generate-recipe-types. */';

// ── 1. Types ──────────────────────────────────────────────────────────────────
const ts = await compileFromFile(schemaPath, { bannerComment: banner });
writeFileSync(typesOut, ts);
console.log(`wrote ${typesOut}`);

// ── 2. Runtime enum consts ─────────────────────────────────────────────────────
const schema = JSON.parse(readFileSync(schemaPath, 'utf8'));
const defs = schema.$defs;

/** Resolve a local "#/$defs/Name" ref to its definition object. */
const deref = (ref) => defs[ref.replace('#/$defs/', '')];

/** The `prop` discriminator const from each variant of a discriminated union. */
const unionConsts = (defName, prop) =>
  defs[defName].oneOf.map((v) => deref(v.$ref).properties[prop].const);

const asConst = (name, values) =>
  `export const ${name} = [${values.map((v) => `'${v}'`).join(', ')}] as const`;

const enums = [
  banner,
  '',
  "// Runtime mirrors of the schema's enums/discriminators. Import these instead of",
  '// re-declaring the value lists by hand.',
  '',
  asConst('STEP_KINDS', unionConsts('Step', 'kind')),
  asConst('SUPPORTED_CHART_TYPES', defs.ChartType.enum),
  asConst('VALID_VALUE_FORMATS', defs.ValueFormat.enum),
  asConst('QUERY_ENGINES', defs.QueryStep.properties.engine.enum),
  '',
].join('\n');

writeFileSync(enumsOut, enums);
console.log(`wrote ${enumsOut}`);
