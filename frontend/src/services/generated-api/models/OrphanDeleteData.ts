/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OrphanDeleteData = {
    deleted: Array<string>;
    /**
     * Paths that became referenced, escaped the root, or could not be removed
     */
    skipped: Array<string>;
    message: string;
};

