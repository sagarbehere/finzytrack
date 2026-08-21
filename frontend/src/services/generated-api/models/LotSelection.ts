/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One chosen lot + how many units of it to sell (from GET /holdings).
 */
export type LotSelection = {
    /**
     * The lot's acquisition date
     */
    acquisition_date: string;
    /**
     * The lot's per-unit cost
     */
    cost_number: (number | string);
    /**
     * The lot's cost currency
     */
    cost_currency: string;
    /**
     * Units of this lot to sell (> 0)
     */
    units: (number | string);
};

