/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Config } from './Config';
import type { FileUpdateMetadata } from './FileUpdateMetadata';
/**
 * Response after config file update.
 */
export type ConfigUpdateResponse = {
    file: FileUpdateMetadata;
    config: Config;
    restart_required: boolean;
    restart_reason?: (string | null);
};

