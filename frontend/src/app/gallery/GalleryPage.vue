<script setup lang="ts">
// The design gallery (S0170): the visual language (design/specs/visual-language.md) drawn with the theme's tokens,
// for review in both themes. Dev only: main.ts mounts it at /dev/gallery under `pnpm dev`, and a production build
// leaves it out (tests/unit/app/galleryBuild.test.ts). Every figure is invented (INV-23). The pieces are specimens:
// each becomes a primitive in the slice that first needs it (the spec's "Parts" table names the row).
// `?theme=dark` or `?theme=light` forces a theme on this page only (nothing is stored); without it the pre-paint
// script's choice stands (the stored `ft/v1/app/theme`, else the OS).
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  ChartColumn,
  ChevronDown,
  LayoutDashboard,
  List,
  Plus,
  Search,
  Sparkles,
  Sun,
  Target,
  Trash2,
  TrendingUp,
  Upload,
  Wallet,
} from '@lucide/vue'

import DestructiveButton from '../../components/app/DestructiveButton.vue'
import { Button } from '../../components/ui/button'
import { Checkbox } from '../../components/ui/checkbox'
import { Input } from '../../components/ui/input'
import AccountRow from './AccountRow.vue'
import TypeBadge from './TypeBadge.vue'

const forced = new URLSearchParams(window.location.search).get('theme')
if (forced === 'dark' || forced === 'light') document.documentElement.classList.toggle('dark', forced === 'dark')

const NAV = [
  { label: 'Dashboards', icon: LayoutDashboard },
  { label: 'Import', icon: Upload },
  { label: 'Transactions', icon: List },
  { label: 'Accounts', icon: Wallet },
  { label: 'Budgets', icon: Target },
  { label: 'Investments', icon: TrendingUp },
  { label: 'Query', icon: ChartColumn },
  { label: 'AI Assistant', icon: Sparkles },
]
const CURRENT = 'Accounts'
const PERIODS = ['All time', 'YTD', 'This month']
const PERIOD = 'YTD'
const segment = (on: boolean) =>
  on ? 'bg-card text-foreground shadow-segment hover:bg-card' : 'text-muted-foreground hover:bg-transparent'
// HTML attributes for the vendored Button and Input go through `v-bind` objects until the owner decides how the
// type gate accepts them (TODO(BL-0326): design/inbox/20261128-vendored-attrs-strict-templates.md); `strictTemplates`
// refuses them as attributes, though Vue applies them to the root element either way.
const label = (text: string) => ({ 'aria-label': text })
const pressed = (on: boolean) => ({ 'aria-pressed': on })
const search = (name: string, placeholder: string) => ({ type: 'search', 'aria-label': name, placeholder })
const SELECT =
  'h-9 rounded-lg border border-input bg-card px-2.5 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
</script>

<template>
  <div class="flex min-h-screen bg-background text-foreground">
    <nav
      aria-label="Main"
      class="flex w-60 shrink-0 flex-col gap-0.5 border-r border-sidebar-border bg-sidebar px-3.5 py-6"
    >
      <div class="px-3 pb-6 text-brand font-bold tracking-title">Finzytrack</div>
      <a
        v-for="item in NAV"
        :key="item.label"
        :href="`#${item.label.toLowerCase().replace(' ', '-')}`"
        :aria-current="item.label === CURRENT ? 'page' : undefined"
        class="flex items-center gap-3 rounded-lg px-3 py-2.25 font-medium"
        :class="
          item.label === CURRENT
            ? 'bg-sidebar-accent font-semibold text-sidebar-accent-foreground'
            : 'text-sidebar-foreground hover:bg-accent'
        "
      >
        <component :is="item.icon" class="size-5" :stroke-width="1.7" />
        <span>{{ item.label }}</span>
      </a>
    </nav>

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex flex-wrap items-center gap-3 border-b border-border bg-card px-8 py-3">
        <Button variant="ghost" size="icon" v-bind="label('Back')" class="text-muted-foreground"><ArrowLeft /></Button>
        <Button variant="ghost" size="icon" v-bind="label('Forward')" class="text-muted-foreground"
          ><ArrowRight
        /></Button>
        <div class="relative ml-2 w-90 max-w-full">
          <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            v-bind="search('Search everything', 'Search transactions, accounts…')"
            class="rounded-lg border-0 bg-secondary pl-9 shadow-none dark:bg-secondary"
          />
        </div>
        <span class="ml-auto flex gap-1">
          <Button variant="ghost" size="icon" v-bind="label('Notifications')" class="text-muted-foreground"
            ><Bell
          /></Button>
          <Button variant="ghost" size="icon" v-bind="label('Theme')" class="text-muted-foreground"><Sun /></Button>
        </span>
      </header>

      <main class="flex flex-col gap-section px-page-x py-page-y">
        <div class="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 class="text-page-title font-bold tracking-title">Accounts</h1>
            <p class="mt-0.5 text-muted-foreground">Balances for the period, by account</p>
          </div>
          <Button class="h-10 rounded-lg px-4 font-semibold"><Plus class="size-4" />New account</Button>
        </div>

        <div class="flex flex-wrap items-center gap-3">
          <div role="group" aria-label="Period" class="flex rounded-lg bg-muted p-0.75">
            <Button
              v-for="period in PERIODS"
              :key="period"
              variant="ghost"
              size="sm"
              v-bind="pressed(period === PERIOD)"
              :class="segment(period === PERIOD)"
              >{{ period }}</Button
            >
            <Button variant="ghost" size="sm" v-bind="pressed(false)" :class="segment(false)">
              More<ChevronDown class="size-3.5" />
            </Button>
          </div>
          <span class="text-sm text-muted-foreground">1 Jan – 28 Nov 2026</span>
          <div class="relative ml-auto w-60">
            <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input v-bind="search('Filter accounts', 'Filter accounts')" class="rounded-lg bg-card pl-9 dark:bg-card" />
          </div>
          <label for="g-type" class="flex items-center gap-2 text-sm text-muted-foreground">
            Type
            <select id="g-type" :class="SELECT">
              <option>All</option>
              <option>Assets</option>
              <option>Liabilities</option>
              <option>Equity</option>
              <option>Income</option>
              <option>Expenses</option>
            </select>
          </label>
          <label for="show-closed" class="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Checkbox id="show-closed" />
            Show closed
          </label>
        </div>

        <section aria-label="Accounts" class="overflow-x-auto rounded-xl bg-card shadow-card">
          <table class="w-full border-collapse text-table">
            <thead>
              <tr class="text-left text-table-head text-muted-foreground">
                <th class="py-3 pr-3 pl-5.5 font-medium">Account</th>
                <th class="p-3 font-medium">Type</th>
                <th class="p-3 font-medium">Currencies</th>
                <th class="py-3 pr-5.5 pl-3 text-right font-medium">Balance</th>
              </tr>
            </thead>
            <tbody>
              <AccountRow
                name="Assets"
                :depth="0"
                open
                type="assets"
                type-label="Assets"
                currencies="INR, USD, +52"
                :balances="['4,73,52,222.45 INR', '10,78,850.40 USD']"
              />
              <AccountRow
                name="Bank"
                :depth="1"
                open
                currencies="INR, USD"
                :balances="['18,40,310.00 INR', '24,120.15 USD']"
              />
              <AccountRow name="HDFC Savings" :depth="2" currencies="INR" :balances="['18,40,310.00 INR']" />
              <AccountRow name="Chase Checking" :depth="2" currencies="USD" :balances="['24,120.15 USD']" />
              <AccountRow
                name="Investments"
                :depth="1"
                :open="false"
                currencies="INR, USD, +50"
                :balances="['4,55,11,912.45 INR', '10,54,730.25 USD']"
              />
              <AccountRow
                name="Liabilities"
                :depth="0"
                :open="false"
                type="liabilities"
                type-label="Liabilities"
                currencies="USD"
                :balances="['−64.63 USD']"
              />
              <AccountRow
                name="Equity"
                :depth="0"
                :open="false"
                type="equity"
                type-label="Equity"
                currencies="INR, USD"
                :balances="['−13,30,678.70 INR', '2,45,175.77 USD']"
              />
              <AccountRow
                name="Income"
                :depth="0"
                :open="false"
                type="income"
                type-label="Income"
                currencies="INR, USD"
                :balances="['−45,25,927.43 INR', '−55,671.22 USD']"
              />
              <AccountRow
                name="Expenses"
                :depth="0"
                :open="false"
                type="expenses"
                type-label="Expenses"
                currencies="INR, USD"
                :balances="['78,38,235.31 INR', '909.13 USD']"
              />
            </tbody>
          </table>
        </section>

        <section aria-labelledby="gallery-parts" class="flex flex-col gap-section border-t border-border pt-page-y">
          <div class="flex flex-wrap items-baseline justify-between gap-3">
            <h2 id="gallery-parts" class="text-xl font-bold tracking-title">Design gallery</h2>
            <p class="text-sm text-muted-foreground">
              Theme:
              <a href="?theme=light" class="text-primary underline-offset-4 hover:underline">Light</a>
              ·
              <a href="?theme=dark" class="text-primary underline-offset-4 hover:underline">Dark</a>
              ·
              <a href="?" class="text-primary underline-offset-4 hover:underline">System or stored</a>
            </p>
          </div>

          <div class="grid gap-section md:grid-cols-2">
            <section aria-labelledby="g-buttons" class="flex flex-col gap-3 rounded-xl bg-card p-5 shadow-card">
              <h3 id="g-buttons" class="font-semibold">Buttons</h3>
              <div class="flex flex-wrap items-center gap-2">
                <Button variant="default">Save</Button>
                <Button variant="secondary">Cancel</Button>
                <Button variant="outline">Export</Button>
                <Button variant="ghost">Skip</Button>
                <Button variant="link">Details</Button>
                <DestructiveButton><Trash2 />Delete</DestructiveButton>
                <Button v-bind="{ disabled: true }">Disabled</Button>
              </div>
            </section>

            <section aria-labelledby="g-badges" class="flex flex-col gap-3 rounded-xl bg-card p-5 shadow-card">
              <h3 id="g-badges" class="font-semibold">Badges</h3>
              <div class="flex flex-wrap gap-2">
                <TypeBadge type="assets">Assets</TypeBadge>
                <TypeBadge type="liabilities">Liabilities</TypeBadge>
                <TypeBadge type="equity">Equity</TypeBadge>
                <TypeBadge type="income">Income</TypeBadge>
                <TypeBadge type="expenses">Expenses</TypeBadge>
              </div>
            </section>

            <section aria-labelledby="g-links" class="flex flex-col gap-3 rounded-xl bg-card p-5 shadow-card">
              <h3 id="g-links" class="font-semibold">Links</h3>
              <p>
                A link in text:
                <a href="#help" class="text-primary underline-offset-4 hover:underline">how balances are counted</a>.
              </p>
              <p>
                A balance link:
                <a
                  href="#transactions"
                  class="font-medium underline decoration-link-underline decoration-dotted underline-offset-4 hover:text-primary hover:decoration-current hover:decoration-solid"
                  >18,40,310.00 INR</a
                >
              </p>
            </section>

            <section aria-labelledby="g-fields" class="flex flex-col gap-3 rounded-xl bg-card p-5 shadow-card">
              <h3 id="g-fields" class="font-semibold">Fields</h3>
              <label for="g-amount" class="flex flex-col gap-1.5 text-sm font-medium">
                Amount
                <Input
                  v-bind="{ id: 'g-amount', placeholder: '0.00', 'aria-describedby': 'g-amount-help' }"
                  class="font-normal"
                />
              </label>
              <p id="g-amount-help" class="-mt-1.5 text-sm text-muted-foreground">Use the account's currency.</p>
              <label for="g-payee" class="flex flex-col gap-1.5 text-sm font-medium">
                Payee
                <Input
                  v-bind="{ id: 'g-payee', 'aria-invalid': 'true', 'aria-describedby': 'g-payee-error' }"
                  class="font-normal"
                />
              </label>
              <p id="g-payee-error" class="-mt-1.5 text-sm text-destructive">Enter a payee.</p>
            </section>

            <section
              aria-labelledby="g-voice"
              class="flex flex-col gap-2 rounded-xl bg-card p-5 shadow-card md:col-span-2"
            >
              <h3 id="g-voice" class="font-semibold">Empty and error text</h3>
              <p class="text-muted-foreground">No accounts match this filter. Clear the filter to see all accounts.</p>
              <p class="text-destructive">The ledger could not be read. Check the file, then try again.</p>
            </section>
          </div>
        </section>
      </main>
    </div>
  </div>
</template>
