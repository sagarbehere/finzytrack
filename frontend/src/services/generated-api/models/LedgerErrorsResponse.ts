/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LedgerValidationError } from './LedgerValidationError';
/**
 * Current ledger parse errors.
 */
export type LedgerErrorsResponse = {
    error_count: number;
    errors: Array<LedgerValidationError>;
};

