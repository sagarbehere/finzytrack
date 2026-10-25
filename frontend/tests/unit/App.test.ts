import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from '../../src/App.vue'

describe('the empty shell (M0)', () => {
  it('renders the product name as the page heading', () => {
    const wrapper = mount(App)
    expect(wrapper.get('h1').text()).toBe('Finzytrack')
  })
})
