/**
 * useDocuments — frontend access to the documents backend.
 *
 * Wraps the generated `DocumentsService` (upload, account-doc CRUD, orphan
 * sweep) and centralises error routing through `errorHandler`. Document
 * *viewing* uses a plain URL (built from the same base the generated client
 * uses) so a stored file opens in a new browser tab / inline PDF viewer.
 */
import { ref } from 'vue'
import { DocumentsService } from '@/services/generated-api'
import type {
  DocumentUploadData,
  DocumentDetails,
  AccountDocumentCreateRequest,
  OrphanScanData,
  OrphanDeleteData,
} from '@/services/generated-api'
import { OpenAPI } from '@/services/generated-api/core/OpenAPI'
import { errorHandler } from '@/utils/ErrorHandler'

export function useDocuments() {
  const isUploading = ref(false)
  const error = ref<string | null>(null)

  /** URL that streams a stored document (for opening in a new tab / inline view). */
  function serveUrl(path: string): string {
    return `${OpenAPI.BASE}/api/documents/file?path=${encodeURIComponent(path)}`
  }

  /** Open a stored document in a new browser tab. */
  function openDocument(path: string): void {
    window.open(serveUrl(path), '_blank', 'noopener')
  }

  /**
   * Upload a file; returns its ledger-relative path + hash. ``date`` (the
   * transaction/document date) drives the filename prefix and date shard;
   * ``narration`` seeds the human-readable slug.
   */
  async function uploadDocument(
    file: File,
    opts: { date?: string; narration?: string } = {},
  ): Promise<DocumentUploadData> {
    isUploading.value = true
    error.value = null
    try {
      const resp = await DocumentsService.uploadDocument({
        file,
        date: opts.date ?? null,
        narration: opts.narration ?? null,
      })
      if (!resp.success || !resp.data) {
        throw new Error('Upload failed: no data returned')
      }
      return resp.data
    } catch (err: any) {
      error.value = err?.body?.error?.message || err?.message || 'Upload failed'
      errorHandler.display(err)
      throw err
    } finally {
      isUploading.value = false
    }
  }

  async function listAccountDocuments(account: string): Promise<DocumentDetails[]> {
    try {
      const resp = await DocumentsService.listAccountDocuments(account)
      return resp.data?.documents ?? []
    } catch (err) {
      errorHandler.display(err)
      throw err
    }
  }

  async function attachAccountDocument(request: AccountDocumentCreateRequest): Promise<void> {
    try {
      await DocumentsService.createAccountDocument(request)
    } catch (err) {
      errorHandler.display(err)
      throw err
    }
  }

  async function detachAccountDocument(account: string, path: string): Promise<void> {
    try {
      await DocumentsService.deleteAccountDocument({ account, path })
    } catch (err) {
      errorHandler.display(err)
      throw err
    }
  }

  async function scanOrphans(): Promise<OrphanScanData> {
    try {
      const resp = await DocumentsService.scanOrphanDocuments()
      if (!resp.success || !resp.data) throw new Error('Scan failed')
      return resp.data
    } catch (err) {
      errorHandler.display(err)
      throw err
    }
  }

  async function deleteOrphans(paths: string[]): Promise<OrphanDeleteData> {
    try {
      const resp = await DocumentsService.deleteOrphanDocuments({ paths })
      if (!resp.success || !resp.data) throw new Error('Delete failed')
      return resp.data
    } catch (err) {
      errorHandler.display(err)
      throw err
    }
  }

  return {
    isUploading,
    error,
    serveUrl,
    openDocument,
    uploadDocument,
    listAccountDocuments,
    attachAccountDocument,
    detachAccountDocument,
    scanOrphans,
    deleteOrphans,
  }
}
