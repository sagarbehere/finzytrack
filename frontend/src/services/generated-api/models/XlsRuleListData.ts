/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { InvalidRuleSummary } from './InvalidRuleSummary';
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
     * Rule files that failed to load
     */
    invalid_rules?: Array<InvalidRuleSummary>;
    /**
     * Path to the rules directory
     */
    rules_dir?: (string | null);
};

