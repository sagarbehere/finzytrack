// Integrity flow A7 (review:L8 §1.8 and §1.3 acceptance tests 1-8; review:27 Appendix B: M6): two sources staged
// together keep their own accounts; autocategorize fills only open slots; the register confirm focuses Cancel; a
// re-read of the same file is reviewed as duplicates, commit skips and reports them and adds nothing; undo. Spec:
// ADR-0027 (anchor postings, open slots, re-match at commit, skip and report), INV-14 (nothing silently dropped;
// re-import idempotent), L4-01, L4-02, L4-07. The two statements are written here, so the flow needs no corpus
// file. Assumed, not decided: the route `/w/{ws}/import`, the panels' names, the demo's accounts `Assets:Checking`
// and `Liabilities:Card`, and the summary wording.
import { expect, test, type Page } from '@playwright/test'

import { fixme } from '../fixme'
import { WS } from './workspace'

const OFX = {
  name: 'checking-march.ofx',
  mimeType: 'application/x-ofx',
  buffer: Buffer.from(
    [
      'OFXHEADER:100',
      'DATA:OFXSGML',
      'VERSION:102',
      '',
      '<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD<BANKTRANLIST>',
      '<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260303<TRNAMT>-42.10<FITID>A7-1<NAME>Corner Grocer</STMTTRN>',
      '<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260305<TRNAMT>1500.00<FITID>A7-2<NAME>Payroll</STMTTRN>',
      '</BANKTRANLIST><LEDGERBAL><BALAMT>1457.90<DTASOF>20260331</LEDGERBAL></STMTRS></STMTTRNRS>',
      '</BANKMSGSRSV1></OFX>',
    ].join('\n'),
  ),
}
const CSV = {
  name: 'card-march.csv',
  mimeType: 'text/csv',
  buffer: Buffer.from('Date,Description,Amount\n2026-03-04,Cinema,-18.00\n2026-03-06,Bookshop,-25.50\n'),
}

async function read(page: Page, file: typeof OFX, account: string): Promise<void> {
  await page.getByLabel('Statement file').setInputFiles(file)
  await page.getByRole('combobox', { name: 'Account' }).fill(account)
  await page.getByRole('option', { name: account }).click()
  await page.getByRole('button', { name: 'Read file' }).click()
}

test.fixme(
  'A7: per-draft accounts, open slots only, re-read reviewed as duplicates and skipped, undo',
  fixme({ id: 'BL-0068', due: 'M6', reason: 'capability not built' }),
  async ({ page }) => {
    await page.goto(`/w/${WS}/import`)
    await read(page, OFX, 'Assets:Checking')
    await read(page, CSV, 'Liabilities:Card')

    const drafts = page.getByRole('grid', { name: 'Drafts' })
    await expect(drafts.getByRole('row', { name: /Assets:Checking/ })).toHaveCount(2)
    await expect(drafts.getByRole('row', { name: /Liabilities:Card/ })).toHaveCount(2) // each keeps its anchor

    const filled = await drafts.getByRole('row').allTextContents()
    await page.getByRole('button', { name: 'Suggest categories' }).click()
    const after = await drafts.getByRole('row').allTextContents()
    for (const [i, text] of filled.entries()) {
      if (!text.includes('Pick an account')) expect(after[i]).toBe(text) // only open slots change
    }

    await page.getByRole('button', { name: 'Add to ledger' }).click()
    const confirm = page.getByRole('alertdialog')
    await expect(confirm.getByRole('button', { name: 'Cancel' })).toBeFocused()
    await confirm.getByRole('button', { name: 'Add to ledger' }).click()
    await expect(page.getByRole('status')).toContainText(/added 4/i)

    // The same statement again: every line is a duplicate (same FITID on the same account), reviewed, then skipped.
    await read(page, OFX, 'Assets:Checking')
    await drafts.getByRole('button', { name: 'Possible duplicate' }).first().click()
    await expect(page.getByRole('dialog', { name: /Duplicate \(1 of 2\)/ })).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: 'Skip' }).click()
    await page.getByRole('button', { name: 'Add to ledger' }).click()
    await page.getByRole('alertdialog').getByRole('button', { name: 'Add to ledger' }).click()
    await expect(page.getByRole('status')).toContainText(/added 0/i)
    await expect(page.getByRole('status')).toContainText(/skipped 2 duplicates/i)

    await page.getByRole('button', { name: 'Undo import' }).first().click()
    await expect(page.getByRole('status')).toContainText(/undone/i)
  },
)
