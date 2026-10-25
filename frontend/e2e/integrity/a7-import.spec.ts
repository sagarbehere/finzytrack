// Integrity flow A7 (review:L8 §1.8 and §1.3 acceptance tests 1-8; review:27 Appendix B: M6): two sources staged
// together keep their own accounts; autocategorize fills only open slots; duplicates are reviewed; the register
// confirm focuses Cancel; commit reports skipped duplicates; undo. Spec: ADR-0027 (anchor postings, open slots,
// re-match at commit, skip and report), INV-14 (nothing silently dropped; re-import idempotent), L4-01, L4-02, L4-07.
// Assumed, not decided: the route `/w/{ws}/import`, the panels' names, the fixture files (from the import corpus,
// BL-0003/the demo generator), and the commit summary wording.
import { expect, test } from '@playwright/test'

import { fixme } from '../fixme'
import { WS } from './workspace'

const OFX = 'corpora/import/checking-march.ofx'
const CSV = 'corpora/import/card-march.csv'

test.fixme(
  'A7: per-draft accounts, open slots only, duplicate review, commit reports skipped duplicates, undo',
  fixme({ id: 'BL-0068', due: 'M6', reason: 'capability not built' }),
  async ({ page }) => {
    await page.goto(`/w/${WS}/import`)
    await page.getByLabel('Statement file').setInputFiles(OFX)
    await page.getByRole('combobox', { name: 'Account' }).fill('Assets:Checking')
    await page.getByRole('option', { name: 'Assets:Checking' }).click()
    await page.getByRole('button', { name: 'Read file' }).click()
    await page.getByLabel('Statement file').setInputFiles(CSV)
    await page.getByRole('combobox', { name: 'Account' }).fill('Liabilities:Card')
    await page.getByRole('option', { name: 'Liabilities:Card' }).click()
    await page.getByRole('button', { name: 'Read file' }).click()

    const drafts = page.getByRole('grid', { name: 'Drafts' })
    await expect(drafts.getByRole('row', { name: /Assets:Checking/ })).not.toHaveCount(0)
    await expect(drafts.getByRole('row', { name: /Liabilities:Card/ })).not.toHaveCount(0) // each keeps its anchor

    const filled = await drafts.getByRole('row').allTextContents()
    await page.getByRole('button', { name: 'Suggest categories' }).click()
    const after = await drafts.getByRole('row').allTextContents()
    for (const [i, text] of filled.entries()) {
      if (!text.includes('Pick an account')) expect(after[i]).toBe(text) // only open slots change
    }

    await drafts.getByRole('button', { name: 'Possible duplicate' }).first().click()
    const dup = page.getByRole('dialog', { name: /Duplicate \(1 of \d+\)/ })
    await dup.getByRole('button', { name: 'Skip' }).click()

    await page.getByRole('button', { name: 'Add to ledger' }).click()
    const confirm = page.getByRole('alertdialog')
    await expect(confirm.getByRole('button', { name: 'Cancel' })).toBeFocused()
    await confirm.getByRole('button', { name: 'Add to ledger' }).click()
    await expect(page.getByRole('status')).toContainText(/skipped \d+ duplicate/i)

    // Re-reading the same file and committing again adds nothing (INV-14: re-import is idempotent).
    await page.getByLabel('Statement file').setInputFiles(OFX)
    await page.getByRole('button', { name: 'Read file' }).click()
    await page.getByRole('button', { name: 'Add to ledger' }).click()
    await page.getByRole('alertdialog').getByRole('button', { name: 'Add to ledger' }).click()
    await expect(page.getByRole('status')).toContainText(/added 0/i)

    await page.getByRole('button', { name: 'Undo import' }).first().click()
    await expect(page.getByRole('status')).toContainText(/undone/i)
  },
)
