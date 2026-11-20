export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export type ResolutionSource = 'LIVE_INFERENCE' | 'LLM_CACHE'

export interface ChatMetadata {
  resolution_source: ResolutionSource
  model: string
  latency_ms: number
  input_tokens: number
  output_tokens: number
  key_id: string | null
  fallback_attempts: number
}

export interface ChatResponse {
  session_id: string
  reply: string
  metadata: ChatMetadata
}

export interface ChatRequest {
  session_id: string
  message: string
  system_prompt?: string
}

export class ApiError extends Error {
  status: number | null

  constructor(message: string, status: number | null) {
    super(message)
    this.status = status
  }
}

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((issue: ValidationIssue) => {
        const field = issue.loc?.filter((part) => part !== 'body').join('.')
        const msg = (issue.msg ?? 'invalid value').replace(/^Value error, /, '')
        return field ? `${field}: ${msg}` : msg
      })
      .join('; ')
  }
  return 'Unexpected error'
}

export async function sendChat(body: ChatRequest, simulateFailure?: string): Promise<ChatResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (simulateFailure) headers['X-Simulate-Failure'] = simulateFailure

  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}/chat`, { method: 'POST', headers, body: JSON.stringify(body) })
  } catch {
    throw new ApiError(`Could not reach the server at ${API_BASE_URL}. Please check your connection and try again.`, null)
  }

  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    // non-JSON body; handled below
  }

  if (!res.ok) {
    const detail = describeDetail((data as { detail?: unknown } | null)?.detail)
    if (res.status === 503) {
      throw new ApiError(`All providers are unavailable right now (${detail}). Please try again in a moment.`, 503)
    }
    if (res.status === 422) {
      throw new ApiError(`Your message couldn't be sent: ${detail}.`, 422)
    }
    throw new ApiError(`Something went wrong (HTTP ${res.status}): ${detail}.`, res.status)
  }
  return data as ChatResponse
}
