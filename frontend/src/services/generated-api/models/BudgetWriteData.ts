/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BudgetItem } from './BudgetItem';
/**
 * Response payload for POST/PUT/DELETE.
 */
export type BudgetWriteData = {
    /**
     * The written directive (null for delete)
     */
    budget?: (BudgetItem | null);
    message: string;
};

