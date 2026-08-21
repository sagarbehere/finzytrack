/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A typed warning or error tied to a specific input event.
 */
export type AssembleIssue = {
    /**
     * Index of the event in the request
     */
    event_index: number;
    /**
     * Machine code, e.g. INSUFFICIENT_LOTS, UNKNOWN_COMMODITY
     */
    code: string;
    /**
     * Human-readable detail
     */
    message: string;
};

