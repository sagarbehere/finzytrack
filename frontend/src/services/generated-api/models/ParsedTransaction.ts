/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Posting } from './Posting';
export type ParsedTransaction = {
    date?: string;
    flag?: string;
    payee?: string;
    narration?: string;
    postings?: Array<Posting>;
    tags?: Array<string>;
    links?: Array<string>;
};

