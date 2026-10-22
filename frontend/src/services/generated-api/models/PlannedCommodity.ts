/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type PlannedCommodity = {
    security_key: string;
    code: string;
    status: PlannedCommodity.status;
    /**
     * Metadata keys this import writes (existing keys are never overwritten)
     */
    set_metadata: Record<string, string>;
};
export namespace PlannedCommodity {
    export enum status {
        NEW = 'new',
        EXISTING = 'existing',
    }
}

