/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountDocumentCreateRequest } from '../models/AccountDocumentCreateRequest';
import type { AccountDocumentDeleteRequest } from '../models/AccountDocumentDeleteRequest';
import type { ApiResponse_AccountDocumentCreateData_ } from '../models/ApiResponse_AccountDocumentCreateData_';
import type { ApiResponse_AccountDocumentDeleteData_ } from '../models/ApiResponse_AccountDocumentDeleteData_';
import type { ApiResponse_DocumentListData_ } from '../models/ApiResponse_DocumentListData_';
import type { ApiResponse_DocumentUploadData_ } from '../models/ApiResponse_DocumentUploadData_';
import type { ApiResponse_OrphanDeleteData_ } from '../models/ApiResponse_OrphanDeleteData_';
import type { ApiResponse_OrphanScanData_ } from '../models/ApiResponse_OrphanScanData_';
import type { Body_uploadDocument } from '../models/Body_uploadDocument';
import type { OrphanDeleteRequest } from '../models/OrphanDeleteRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DocumentsService {
    /**
     * Upload Document
     * Store an uploaded file and return its ledger-relative path + full hash.
     *
     * The path is staged client-side into a draft transaction's ``document``
     * metadata (written on save) or passed to ``POST /account``. Any file type is
     * accepted up to the size cap.
     * @param formData
     * @returns ApiResponse_DocumentUploadData_ Successful Response
     * @throws ApiError
     */
    public static uploadDocument(
        formData: Body_uploadDocument,
    ): CancelablePromise<ApiResponse_DocumentUploadData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/documents/upload',
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Serve Document
     * Stream a stored document. Path-safe: traversal/absolute paths and symlink
     * escapes are rejected (invariant I10).
     * @param path Ledger-relative path of the stored document
     * @returns any Successful Response
     * @throws ApiError
     */
    public static serveDocument(
        path: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/documents/file',
            query: {
                'path': path,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Account Documents
     * List the ``Document`` directives attached to an account.
     * @param account Account to list documents for
     * @returns ApiResponse_DocumentListData_ Successful Response
     * @throws ApiError
     */
    public static listAccountDocuments(
        account: string,
    ): CancelablePromise<ApiResponse_DocumentListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/documents/account',
            query: {
                'account': account,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Create Account Document
     * Attach a document to an account via a ``Document`` directive.
     * @param requestBody
     * @returns ApiResponse_AccountDocumentCreateData_ Successful Response
     * @throws ApiError
     */
    public static createAccountDocument(
        requestBody: AccountDocumentCreateRequest,
    ): CancelablePromise<ApiResponse_AccountDocumentCreateData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/documents/account',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Account Document
     * Detach an account document (remove its ``Document`` directive). The file
     * on disk is left in place — use the orphan sweep to remove unreferenced
     * files.
     * @param requestBody
     * @returns ApiResponse_AccountDocumentDeleteData_ Successful Response
     * @throws ApiError
     */
    public static deleteAccountDocument(
        requestBody: AccountDocumentDeleteRequest,
    ): CancelablePromise<ApiResponse_AccountDocumentDeleteData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/documents/account',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Scan Orphan Documents
     * List files in the documents root referenced by nothing in the ledger and
     * older than the grace window.
     * @returns ApiResponse_OrphanScanData_ Successful Response
     * @throws ApiError
     */
    public static scanOrphanDocuments(): CancelablePromise<ApiResponse_OrphanScanData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/documents/orphans/scan',
        });
    }
    /**
     * Delete Orphan Documents
     * Delete selected orphan files. Each is re-validated as still-orphaned
     * against the current ledger immediately before unlinking; any that became
     * referenced (or escaped the root) are reported in ``skipped``. Deleted files
     * remain recoverable from git history.
     * @param requestBody
     * @returns ApiResponse_OrphanDeleteData_ Successful Response
     * @throws ApiError
     */
    public static deleteOrphanDocuments(
        requestBody: OrphanDeleteRequest,
    ): CancelablePromise<ApiResponse_OrphanDeleteData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/documents/orphans/delete',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
