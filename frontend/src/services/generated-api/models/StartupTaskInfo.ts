/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A detected pending task. Detection is read-only — nothing is mutated
 * until the user applies it.
 */
export type StartupTaskInfo = {
    /**
     * Stable task identifier.
     */
    id: string;
    /**
     * Short title shown in the dialog.
     */
    title: string;
    /**
     * What will change and why (plain text/markdown).
     */
    summary: string;
    /**
     * 'action_required' (gates the app) or 'info'.
     */
    severity: string;
    /**
     * True if the app should gate until applied.
     */
    requires_consent: boolean;
    /**
     * Relative docs path for 'Learn more' (e.g. 'upgrade-notes/...').
     */
    docs_path?: (string | null);
    /**
     * Task-specific detail (e.g. counts).
     */
    details?: Record<string, any>;
};

