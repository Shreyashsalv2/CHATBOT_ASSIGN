import type { ChatMetadata } from '../api'

interface Props {
  content: string
  metadata: ChatMetadata
}

export function AssistantMessage({ content, metadata }: Props) {
  const cached = metadata.resolution_source === 'LLM_CACHE'
  return (
    <div className="message assistant" data-testid="assistant-message">
      <div className="avatar" aria-hidden="true">
        L
      </div>
      <div className="message-body">
        <div className="message-text">{content}</div>
        <div className="meta" aria-label="Response metadata">
          <span className={`chip source ${cached ? 'cache' : 'live'}`} title="Resolution source">
            <span className="dot" aria-hidden="true" />
            <span data-testid="meta-source">{metadata.resolution_source}</span>
          </span>
          <span className="chip" title="Model">
            <span data-testid="meta-model">{metadata.model}</span>
          </span>
          <span className="chip" title="Latency">
            <span data-testid="meta-latency">{metadata.latency_ms} ms</span>
          </span>
          <span className="chip" title="Input tokens for this request">
            in <span data-testid="meta-input-tokens">{metadata.input_tokens}</span>
          </span>
          <span className="chip" title="Output tokens for this reply">
            out <span data-testid="meta-output-tokens">{metadata.output_tokens}</span>
          </span>
        </div>
        <div className="meta-detail">
          key: <span data-testid="meta-key-id">{metadata.key_id ?? 'none (cache)'}</span>
          <span className="sep">·</span>
          fallback attempts: <span data-testid="meta-fallback-attempts">{metadata.fallback_attempts}</span>
        </div>
      </div>
    </div>
  )
}
