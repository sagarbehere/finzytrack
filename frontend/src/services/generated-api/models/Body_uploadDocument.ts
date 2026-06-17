/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_uploadDocument = {
    file: Blob;
    /**
     * Document date (YYYY-MM-DD); defaults to today
     */
    date?: (string | null);
    /**
     * Slug source (transaction narration/payee)
     */
    narration?: (string | null);
};

