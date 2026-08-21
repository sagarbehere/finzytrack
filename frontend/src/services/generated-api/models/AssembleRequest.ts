/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AddHoldingEvent } from './AddHoldingEvent';
import type { BuyEvent } from './BuyEvent';
import type { CashDividendEvent } from './CashDividendEvent';
import type { DripEvent } from './DripEvent';
/**
 * Request body for POST /api/investments/assemble.
 */
export type AssembleRequest = {
    /**
     * One or more typed investment events
     */
    events: Array<(BuyEvent | DripEvent | CashDividendEvent | AddHoldingEvent)>;
};

