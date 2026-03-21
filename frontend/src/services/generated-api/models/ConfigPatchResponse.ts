/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Config } from './Config';
/**
 * Response after a partial config update via the GUI settings editor.
 */
export type ConfigPatchResponse = {
    config: Config;
    restart_required: boolean;
    restart_reason?: (string | null);
};

