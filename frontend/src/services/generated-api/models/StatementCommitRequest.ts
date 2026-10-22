/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RowCorrection } from './RowCorrection';
import type { StatementMapping } from './StatementMapping';
export type StatementCommitRequest = {
    token: string;
    mapping?: StatementMapping;
    corrections?: Array<RowCorrection>;
    /**
     * preview_hash of the preview the user saw
     */
    expected_preview_hash: string;
};

