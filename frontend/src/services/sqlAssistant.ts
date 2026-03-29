import { AiService, ApiError } from '@/services/generated-api'
import { GenerateQueryRequest } from '@/services/generated-api'

export type QueryLanguage = 'sqlite' | 'beanquery'

const LANGUAGE_MAP: Record<QueryLanguage, GenerateQueryRequest.language> = {
  sqlite: GenerateQueryRequest.language.SQLITE,
  beanquery: GenerateQueryRequest.language.BEANQUERY,
}

/**
 * Send a natural language question to the backend and get back a query
 * in the specified language (SQL or BQL).
 */
export async function generateQuery(
  naturalLanguageQuery: string,
  language: QueryLanguage = 'sqlite',
): Promise<string> {
  try {
    const resp = await AiService.generateQuery({
      question: naturalLanguageQuery,
      language: LANGUAGE_MAP[language],
    })
    return (resp.data as { query: string }).query
  } catch (e: unknown) {
    if (e instanceof ApiError) {
      const msg = e.body?.error?.message || e.message
      throw new Error(msg)
    }
    throw e
  }
}
