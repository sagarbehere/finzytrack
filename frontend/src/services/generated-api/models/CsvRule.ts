/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CsvColumnMapping } from './CsvColumnMapping';
/**
 * Full CSV import rule definition.
 */
export type CsvRule = {
    /**
     * Human-readable rule name
     */
    name: string;
    /**
     * Column separator character
     */
    separator?: string;
    /**
     * File encoding
     */
    encoding?: string;
    /**
     * Number of lines to skip at the start (before header)
     */
    skip_lines_start?: number;
    /**
     * Number of lines to skip at the end
     */
    skip_lines_end?: number;
    /**
     * Date format string (strftime tokens)
     */
    date_format?: string;
    /**
     * Decimal separator character
     */
    decimal_separator?: string;
    /**
     * Column index mappings
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

