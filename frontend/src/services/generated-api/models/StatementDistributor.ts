/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type StatementDistributor = {
    /**
     * Distributor key: the ARN code, or DIRECT
     */
    key: string;
    /**
     * What to show (ARN code, or 'Direct plans')
     */
    label: string;
    /**
     * Names of the funds this distributor covers
     */
    funds: Array<string>;
};

