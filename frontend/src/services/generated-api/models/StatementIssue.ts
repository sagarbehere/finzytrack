/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StatementIssue = {
    /**
     * Machine-readable issue code
     */
    code: string;
    /**
     * Plain-language explanation
     */
    message: string;
    /**
     * Fund the issue is about, if any
     */
    security_key?: (string | null);
    /**
     * Row the issue is about, if any
     */
    row_id?: (string | null);
};

