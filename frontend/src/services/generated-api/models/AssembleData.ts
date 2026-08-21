/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssembleIssue } from './AssembleIssue';
import type { DraftTransaction } from './DraftTransaction';
/**
 * Response data for POST /api/investments/assemble.
 *
 * Partial success is allowed: some events become drafts while others produce
 * typed errors (skipped) or warnings (draft still built).
 */
export type AssembleData = {
    drafts?: Array<DraftTransaction>;
    warnings?: Array<AssembleIssue>;
    errors?: Array<AssembleIssue>;
};

