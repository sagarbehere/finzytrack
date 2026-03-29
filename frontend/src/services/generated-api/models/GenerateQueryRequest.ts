/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type GenerateQueryRequest = {
    /**
     * Natural language question
     */
    question: string;
    /**
     * Target query language
     */
    language?: GenerateQueryRequest.language;
};
export namespace GenerateQueryRequest {
    /**
     * Target query language
     */
    export enum language {
        SQLITE = 'sqlite',
        BEANQUERY = 'beanquery',
    }
}

