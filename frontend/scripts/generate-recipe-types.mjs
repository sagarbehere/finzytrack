// Regenerate src/types/recipes.generated.ts from src/types/recipe.schema.json.
// Run via `npm run generate-recipe-types`. Implemented in JS rather than as a
// shell-quoted json2ts invocation so the banner comment survives Windows cmd.exe,
// which doesn't honor single-quote argument grouping.
import { compileFromFile } from 'json-schema-to-typescript';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const schema = resolve(here, '..', 'src', 'types', 'recipe.schema.json');
const out = resolve(here, '..', 'src', 'types', 'recipes.generated.ts');

const banner = '/* Auto-generated from recipe.schema.json — do not edit by hand. Regenerate via npm run generate-recipe-types. */';

const ts = await compileFromFile(schema, { bannerComment: banner });
writeFileSync(out, ts);
console.log(`wrote ${out}`);
