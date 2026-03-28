/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A single file or directory entry.
 */
export type FileEntry = {
    name: string;
    type: FileEntry.type;
    size?: (number | null);
};
export namespace FileEntry {
    export enum type {
        FILE = 'file',
        DIRECTORY = 'directory',
    }
}

