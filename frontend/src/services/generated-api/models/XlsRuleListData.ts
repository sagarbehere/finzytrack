/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { XlsRuleSummary } from './XlsRuleSummary';
/**
 * Response wrapper for XLS rules listing.
 */
export type XlsRuleListData = {
    /**
     * Available XLS rules
     */
    rules?: Array<XlsRuleSummary>;
    /**
     * Path to the rules directory
     */
    rules_dir?: (string | null);
};

