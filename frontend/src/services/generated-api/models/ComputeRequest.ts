/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to run a registered compute function.
 */
export type ComputeRequest = {
    /**
     * Name of the compute function to run
     */
    function: string;
    /**
     * Scalar arguments for the function
     */
    args?: Record<string, any>;
};

