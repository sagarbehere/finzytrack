/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TrialExtractedField } from './TrialExtractedField';
/**
 * Response for POST /email/trial-extract.
 */
export type TrialExtractResult = {
    success: boolean;
    transaction_type?: (string | null);
    fields?: Array<TrialExtractedField>;
    note?: string;
    error?: (string | null);
};

