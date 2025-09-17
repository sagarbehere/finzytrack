/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request model for OFX account detection.
 */
export type OFXDetectionRequest = {
    /**
     * Institution name from OFX
     */
    institution: string;
    /**
     * Financial Institution ID
     */
    institution_fid?: (string | null);
    /**
     * Account type
     */
    account_type: string;
    /**
     * Full account ID
     */
    account_id: string;
};

