/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One entry in the GET /profiles response.
 */
export type ProfileInfo = {
    name: string;
    profile_id: string;
    beancount_account: string;
    default_currency: string;
    lookback_days?: (number | null);
    file: string;
};

