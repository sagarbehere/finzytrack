/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttachedFile } from './AttachedFile';
import type { ChatMessage } from './ChatMessage';
export type AssistantChatRequest = {
    messages: Array<ChatMessage>;
    file?: (AttachedFile | null);
    context?: (Record<string, any> | null);
};

