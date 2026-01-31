/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CsvRuleSummary } from './CsvRuleSummary';
/**
 * Response wrapper for CSV rules listing.
 */
export type CsvRuleListData = {
    /**
     * Available CSV rules
     */
    rules?: Array<CsvRuleSummary>;
    /**
     * Path to the rules directory
     */
    rules_dir?: (string | null);
};

