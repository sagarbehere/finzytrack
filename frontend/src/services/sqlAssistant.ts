import { AiService } from '@/services/generated-api'
import { GenerateQueryRequest } from '@/services/generated-api'

export type QueryLanguage = 'sqlite' | 'beanquery'

const LANGUAGE_MAP: Record<QueryLanguage, GenerateQueryRequest.language> = {
  sqlite: GenerateQueryRequest.language.SQLITE,
  beanquery: GenerateQueryRequest.language.BEANQUERY,
}

/**
 * Send a natural language question to the backend and get back a query
 * in the specified language (SQL or BQL).
 *
 * On failure the underlying `ApiError` propagates unchanged — callers route
 * it through `errorHandler.display()` per the frontend error-handling rule.
 */
export async function generateQuery(
  naturalLanguageQuery: string,
  language: QueryLanguage = 'sqlite',
): Promise<string> {
  const resp = await AiService.generateQuery({
    question: naturalLanguageQuery,
    language: LANGUAGE_MAP[language],
  })
  return resp.data?.query ?? ''
}
