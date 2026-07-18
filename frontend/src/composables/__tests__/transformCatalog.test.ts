import { describe, it, expect } from 'vitest'
import { transformCatalog } from '@/composables/useRecipeTransforms'
import catalog from '@/types/transforms.catalog.json'

// transforms.catalog.json is the single source of truth for the transform names
// (it generates the schema-doc table and feeds the backend validator). The runtime
// registry `transformCatalog` in useRecipeTransforms.ts implements the functions.
// This test binds the two: the registry must implement EXACTLY the cataloged set —
// no unlisted transform, no cataloged-but-unimplemented name. Add a transform in
// both places and this stays green; add it in only one and it fails.
describe('transform catalog ↔ runtime registry', () => {
  it('implements exactly the transforms declared in transforms.catalog.json', () => {
    const cataloged = catalog.transforms.map((t: { name: string }) => t.name).sort()
    const registered = Object.keys(transformCatalog).sort()
    expect(registered).toEqual(cataloged)
  })
})
