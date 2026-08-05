# Releasing Finzytrack

## The golden rule

**A version tag points at an already-tested commit. Never move a published version tag.**
Iterate on CI *dispatch builds* or on `-rc` tags; create the final `vX.Y.Z` tag
exactly once, on the commit whose CI build you have tested. This avoids the
"tag → build → find a bug → re-point the tag → repeat" cycle.

## Version: one source of truth

**`/VERSION` (repo root) is the only place to bump the version.** Everything
else is derived from it at build time:

- `desktop/finzytrack.spec` reads it, bundles it, and sets the macOS
  `CFBundleShortVersionString`.
- `desktop/build.py` mirrors it into `frontend/package.json` — **do not edit
  `frontend/package.json`'s `version` by hand.**
- `backend/app/_version.py` reads it → `__version__` → the About screen.

Do **not** touch `version="1.0.0"` in `backend/app/main.py` — that is the
OpenAPI *document* version, unrelated to the app version.

The git tag must equal `v` + the contents of `/VERSION` (e.g. `/VERSION` = `0.2.1`
→ tag `v0.2.1`). Keep them in lockstep.

## Build stamps: which binary is this?

Test builds of the same `/VERSION` are otherwise indistinguishable — two dispatch
builds of different commits both report `0.2.1`, and neither you nor a tester can
tell which one is installed. So `desktop/build.py` writes a `BUILD_INFO` file with
the build's commit, and the About screen shows it as semver build metadata:

```
0.2.1+3894c8a              a dispatch build of commit 3894c8a
0.2.1+3894c8a (modified)   built from a dirty working tree
0.2.2                      a release build — no stamp
```

**Release builds carry no stamp, automatically.** A release is exactly a build
whose HEAD is the `v<VERSION>` tag, which `build.py` detects — so tagging is all
it takes. Override with `--stamp` / `--no-stamp` if you ever need to.

The commit cannot live in `/VERSION` itself: committing that file would change
the SHA it names. `BUILD_INFO` is generated per build and gitignored — never
commit it. `CFBundleShortVersionString` keeps the clean `/VERSION` value, since
Apple wants dot-separated integers there.

## Release flow

1. **Bump `/VERSION`** (e.g. `0.2.1`) and commit it.
2. **Build the CI binary without tagging.** GitHub → Actions →
   **Build Desktop App** → *Run workflow*, on your commit:
   - `make_release = false` (default) → builds all platforms and uploads
     downloadable **artifacts**, with **no release and no tag**. Download and
     test these — this is the exact binary CI produces.
   - `make_release = true` → also publishes a **prerelease** under a throwaway
     `dev-<branch>-<sha>` tag, if you want a shareable URL for a tester.
3. **Iterate.** Found a problem in the CI build? Fix, push, re-run the dispatch.
   No version tag is involved, so there is nothing to move.
4. **Tag once, when the CI artifact passes.** On the tested commit:
   ```
   git tag v0.2.1 <commit> && git push origin v0.2.1
   ```
   A plain `vX.Y.Z` tag makes the workflow create a **draft** GitHub release
   (safety net). Review it, then **Publish** from the GitHub UI.
5. **Ship the docs.** Push `finzytrack-docs`, and add an
   [Upgrade Notes](https://github.com/sagarbehere/finzytrack) entry for anything
   that needs user attention.

## Release candidates (tag-driven alternative)

If you prefer to iterate via tags: any tag containing a hyphen builds as a
**prerelease** (`v0.2.1-rc.1`, `-rc.2`, …). Each rc is a **new** tag — never
moved. Tag the final `v0.2.1` (no hyphen) once an rc passes. Same golden rule,
just expressed through rc tags instead of dispatch builds.

## Notes

- Shipping changed seed recipes/dashboards changes the bundle content-digest, so
  existing users get the seed-content refresh notice on upgrade — pristine demo
  copies are updated, customized ones are preserved
  (`dev-docs/seed-content-refresh.md`).
- The dispatch-build-then-test path is also the cure for "the CI binary breaks
  in ways my local build didn't": you test CI's own output before committing to
  a version number.
- **Builds are not bit-reproducible, so artifact hashes differ between runs —
  this is expected.** The tag triggers a *fresh* build, so its `sha256` sums
  won't match the dispatch build you tested. The cause is packaging
  nondeterminism (timestamps baked into the `.zip`/AppImage squashfs, PyInstaller
  build paths), not code or dependency drift (all deps are pinned). Same commit +
  same pinned toolchain → functionally identical program. "Test-then-tag"
  therefore validates the *commit and toolchain*, not the literal shipped bytes.
  If you ever need the exact tested bytes to ship, publish the dispatch build's
  artifacts (`make_release=true`) instead of rebuilding on the tag.

## Quick checklist

- [ ] `/VERSION` bumped and committed
- [ ] CI dispatch build tested on all target platforms (not just a local build)
- [ ] `vX.Y.Z` tag created on the tested commit and pushed
- [ ] draft release reviewed and published
- [ ] `finzytrack-docs` pushed; Upgrade Notes entry added if user action is needed
