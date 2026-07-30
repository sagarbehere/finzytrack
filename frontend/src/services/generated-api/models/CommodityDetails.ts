/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CommodityUsageData } from './CommodityUsageData';
/**
 * Detailed commodity information including transaction usage data.
 */
export type CommodityDetails = {
    /**
     * Commodity/currency code (e.g., 'USD', 'AAPL')
     */
    code: string;
    /**
     * Full name from commodity directive
     */
    name?: (string | null);
    /**
     * Beancount 'asset-class' metadata (e.g., 'cash', 'stock', 'etf')
     */
    asset_class?: (string | null);
    /**
     * Whether this commodity plays a currency (unit-of-account) role. Derived from operating_currency (primary) then asset-class (fallback).
     */
    is_currency?: boolean;
    /**
     * Earliest date this commodity appeared
     */
    first_seen?: (string | null);
    /**
     * Latest date this commodity appeared
     */
    last_seen?: (string | null);
    /**
     * Transaction usage statistics
     */
    usage: CommodityUsageData;
    /**
     * Additional commodity metadata
     */
    metadata?: Record<string, any>;
};

