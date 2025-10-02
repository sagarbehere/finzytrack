/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Response body for commit endpoint.
 */
export type CommitResponse = {
    /**
     * Whether commit was successful
     */
    success: boolean;
    /**
     * Number of transactions committed
     */
    count: number;
    /**
     * Success or error message
     */
    message: string;
};

