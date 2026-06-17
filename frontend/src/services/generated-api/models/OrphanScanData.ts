/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OrphanCandidateData } from './OrphanCandidateData';
export type OrphanScanData = {
    orphans: Array<OrphanCandidateData>;
    /**
     * Files younger than this were excluded
     */
    grace_seconds: number;
};

