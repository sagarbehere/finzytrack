// Use relative URLs so Vite's dev proxy (/api → backend) and production
// bundling both work without any hardcoded host/port.
const API_BASE = ''

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AttachedFile {
  name: string
  content_base64: string
}

export interface SseThinkingEvent {
  type: 'thinking'
  content: string
}

export interface SseTokenEvent {
  type: 'token'
  content: string
}

export interface SseToolStartEvent {
  type: 'tool_start'
  tool: string
  message: string
  args?: Record<string, unknown>  // Tool call arguments
}

export interface SseToolResultEvent {
  type: 'tool_result'
  tool: string
  success: boolean
  message: string
  data?: Record<string, unknown>   // Full tool result data for details panel
  recipe?: Record<string, unknown>  // Attached by preview_recipe tool
}

export interface SseErrorEvent {
  type: 'error'
  message: string
}

export interface SseDoneEvent {
  type: 'done'
}

export interface SseValidationWarningEvent {
  type: 'validation_warning'
  rule: string
  severity: 'warn' | 'info'
  message: string
  details?: Record<string, unknown>
}

export type SseEvent =
  | SseThinkingEvent
  | SseTokenEvent
  | SseToolStartEvent
  | SseToolResultEvent
  | SseErrorEvent
  | SseDoneEvent
  | SseValidationWarningEvent

export async function* streamAssistantChat(
  messages: ChatMessage[],
  file: AttachedFile | null,
  context: Record<string, string> = {},
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const response = await fetch(`${API_BASE}/api/assistant/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, file, context }),
    signal,
  })

  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText)
    throw new Error(`Request failed (${response.status}): ${text}`)
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''

    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      const jsonStr = line.slice('data:'.length).trim()
      if (!jsonStr) continue
      try {
        yield JSON.parse(jsonStr) as SseEvent
      } catch {
        // malformed event — skip
      }
    }
  }
}

export async function readFileAsBase64(file: File): Promise<AttachedFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      // dataUrl = "data:<mime>;base64,<data>"
      const base64 = dataUrl.split(',')[1]
      resolve({ name: file.name, content_base64: base64 })
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
