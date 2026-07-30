/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to replace the ledger's operating_currency option.
 */
export type OperatingCurrenciesUpdateRequest = {
    /**
     * Full replacement list of operating currency codes (empty clears the whitelist)
     */
    currencies: Array<string>;
};

