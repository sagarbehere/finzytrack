/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LedgerValidationError } from './LedgerValidationError';
/**
 * Current ledger parse errors from the beancount cache.
 */
export type LedgerErrorsResponse = {
    error_count: number;
    errors: Array<LedgerValidationError>;
};

