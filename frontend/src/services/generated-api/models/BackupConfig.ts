/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Backup system configuration.
 */
export type BackupConfig = {
    /**
     * Enable backup system
     */
    enabled?: boolean;
    /**
     * Number of backups to retain
     */
    retention_count?: number;
    /**
     * Automatically cleanup old backups
     */
    cleanup_on_exceed?: boolean;
};

