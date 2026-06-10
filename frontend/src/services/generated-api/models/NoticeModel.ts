/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type NoticeModel = {
    /**
     * Stable identifier for the notice kind.
     */
    code: string;
    /**
     * error | warning | info
     */
    severity: string;
    /**
     * ledger | system | background
     */
    source: string;
    title: string;
    message: string;
    /**
     * Discriminator that re-surfaces a previously-dismissed notice when it changes. Frontend dismissal state is keyed by (code, signature).
     */
    signature: string;
    dismissible?: boolean;
    details?: (Array<string> | null);
    /**
     * Optional URL to a docs page describing this notice in detail.
     */
    learn_more_url?: (string | null);
};

