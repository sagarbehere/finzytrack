/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AccountDocumentCreateRequest = {
    /**
     * Account the document belongs to
     */
    account: string;
    /**
     * Document date
     */
    date: string;
    /**
     * Ledger-relative path returned by the upload endpoint
     */
    path: string;
    tags?: Array<string>;
    links?: Array<string>;
    metadata?: (Record<string, any> | null);
};

