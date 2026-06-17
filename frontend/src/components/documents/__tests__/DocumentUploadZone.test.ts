import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import DocumentUploadZone from '@/components/documents/DocumentUploadZone.vue'

function fileDrop(files: File[]): DragEvent {
  return {
    preventDefault() {},
    dataTransfer: { files, types: ['Files'] },
  } as unknown as DragEvent
}

describe('DocumentUploadZone', () => {
  it('hints the camera/photo + PDF accept types by default (mobile capture)', () => {
    const wrapper = mount(DocumentUploadZone)
    const input = wrapper.find('input[type="file"]')
    expect(input.attributes('accept')).toBe('image/*,application/pdf')
  })

  it('emits files-selected on drop', async () => {
    const wrapper = mount(DocumentUploadZone)
    const file = new File(['x'], 'r.pdf', { type: 'application/pdf' })
    await wrapper.trigger('drop', fileDrop([file]))
    const emitted = wrapper.emitted('files-selected')
    expect(emitted).toBeTruthy()
    expect((emitted![0][0] as File[])[0].name).toBe('r.pdf')
  })

  it('does not emit when no files are present', async () => {
    const wrapper = mount(DocumentUploadZone)
    await wrapper.trigger('drop', { preventDefault() {}, dataTransfer: { files: [], types: [] } } as any)
    expect(wrapper.emitted('files-selected')).toBeUndefined()
  })
})
