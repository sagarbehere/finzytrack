/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An account document (a ``Document`` directive).
 */
export type DocumentDetails = {
    date: string;
    account: string;
    /**
     * Ledger-relative path (use with the serve endpoint)
     */
    path: string;
    /**
     * Filename basename for display
     */
    display_name: string;
    tags?: Array<string>;
    links?: Array<string>;
    metadata?: Record<string, any>;
};

