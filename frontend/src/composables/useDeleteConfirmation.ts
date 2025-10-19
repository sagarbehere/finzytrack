import { ref } from 'vue'

export interface DeleteConfirmDialogOptions {
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'warning' | 'info' | 'success'
}

export function useDeleteConfirmation() {
  const isOpen = ref(false)
  const dialogOptions = ref<DeleteConfirmDialogOptions>({
    title: '',
    message: '',
    confirmText: 'Confirm',
    cancelText: 'Cancel',
    variant: 'danger'
  })

  let resolvePromise: ((value: boolean) => void) | null = null

  const showConfirm = (options: DeleteConfirmDialogOptions): Promise<boolean> => {
    dialogOptions.value = {
      confirmText: 'Confirm',
      cancelText: 'Cancel',
      variant: 'danger',
      ...options
    }
    isOpen.value = true

    return new Promise<boolean>((resolve) => {
      resolvePromise = resolve
    })
  }

  const handleConfirm = () => {
    isOpen.value = false
    if (resolvePromise) {
      resolvePromise(true)
      resolvePromise = null
    }
  }

  const handleCancel = () => {
    isOpen.value = false
    if (resolvePromise) {
      resolvePromise(false)
      resolvePromise = null
    }
  }

  const handleClose = () => {
    handleCancel()
  }

  return {
    isOpen,
    dialogOptions,
    showConfirm,
    handleConfirm,
    handleCancel,
    handleClose
  }
}
