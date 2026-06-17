/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Returned by the upload endpoint. ``path`` is the exact ledger-relative
 * string to stage into a transaction's ``document`` metadata or to pass to
 * the account-document create endpoint.
 */
export type DocumentUploadData = {
    /**
     * Ledger-relative path to the stored file
     */
    path: string;
    /**
     * Full SHA-256 hex of the file content
     */
    full_hash: string;
    /**
     * File size in bytes
     */
    size: number;
    /**
     * Stored filename (basename) for display
     */
    display_name: string;
};

