/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One open lot of a held commodity, read from the mirror `lots` table.
 */
export type HoldingLot = {
    account: string;
    units: string;
    cost_number: string;
    cost_currency: string;
    acquisition_date: string;
    book_value?: (string | null);
    label?: (string | null);
};

