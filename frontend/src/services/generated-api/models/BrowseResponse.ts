/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FileEntry } from './FileEntry';
/**
 * Directory listing response.
 */
export type BrowseResponse = {
    current_path: string;
    parent_path?: (string | null);
    home_path: string;
    entries: Array<FileEntry>;
};

