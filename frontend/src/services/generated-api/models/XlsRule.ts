/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CsvColumnMapping } from './CsvColumnMapping';
/**
 * Full XLS import rule definition.
 */
export type XlsRule = {
    /**
     * Human-readable rule name
     */
    name: string;
    /**
     * 0-based sheet index to read from
     */
    sheet_index?: number;
    /**
     * Sheet name to read from (overrides sheet_index if provided)
     */
    sheet_name?: (string | null);
    /**
     * Number of rows to skip at the start (including header row)
     */
    skip_lines_start?: number;
    /**
     * Number of rows to skip at the end
     */
    skip_lines_end?: number;
    /**
     * Date format string (strftime tokens), used when date cell is a string
     */
    date_format?: string;
    /**
     * Decimal separator character for string-valued amount cells
     */
    decimal_separator?: string;
    /**
     * Column index mappings (same as CSV rules)
     */
    columns: CsvColumnMapping;
    /**
     * Default Beancount account for this source
     */
    default_account: string;
    /**
     * Default currency
     */
    default_currency?: string;
    /**
     * Whether to negate parsed amounts
     */
    negate_amounts?: boolean;
};

