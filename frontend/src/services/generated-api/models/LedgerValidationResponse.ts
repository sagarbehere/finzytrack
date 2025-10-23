/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LedgerValidationError } from './LedgerValidationError';
/**
 * Response for ledger validation.
 */
export type LedgerValidationResponse = {
    valid: boolean;
    errors: Array<LedgerValidationError>;
    warnings: Array<LedgerValidationError>;
    summary: string;
};

