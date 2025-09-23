/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for closing an account. Target account comes from URL path.
 */
export type AccountCloseRequest = {
    /**
     * Closing date
     */
    close_date: string;
    /**
     * Optional reason for closing
     */
    reason?: (string | null);
};

