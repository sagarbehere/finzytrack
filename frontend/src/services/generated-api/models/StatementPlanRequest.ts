/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RowCorrection } from './RowCorrection';
import type { StatementMapping } from './StatementMapping';
export type StatementPlanRequest = {
    token: string;
    mapping?: StatementMapping;
    corrections?: Array<RowCorrection>;
};

