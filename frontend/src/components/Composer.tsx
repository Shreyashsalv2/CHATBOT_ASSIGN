import { useEffect, useRef } from 'react'

export const FAILURE_PRESETS = [
  { value: '', label: 'No simulated failure' },
  { value: 'key_1=429', label: 'key_1 → 429' },
  { value: 'key_1=500', label: 'key_1 → 500' },
  { value: 'key_1=429,key_2=500', label: 'key_1 → 429, key_2 → 500' },
  { value: '*=429', label: 'All keys → 429' },
]

interface Props {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  pending: boolean
  systemPrompt: string
  onSystemPromptChange: (value: string) => void
  showSystemPrompt: boolean
  onToggleSystemPrompt: () => void
  simulateFailure: string
  onSimulateFailureChange: (value: string) => void
}

export function Composer({
  value,
  onChange,
  onSend,
  pending,
  systemPrompt,
  onSystemPromptChange,
  showSystemPrompt,
  onToggleSystemPrompt,
  simulateFailure,
  onSimulateFailureChange,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`
  }, [value])

  useEffect(() => {
    if (!pending) textareaRef.current?.focus()
  }, [pending])

  const canSend = value.trim().length > 0 && !pending

  return (
    <div className="composer-wrap">
      {showSystemPrompt && (
        <div className="system-prompt">
          <label htmlFor="system-prompt">System prompt for this session</label>
          <textarea
            id="system-prompt"
            data-testid="system-prompt-input"
            value={systemPrompt}
            onChange={(e) => onSystemPromptChange(e.target.value)}
            placeholder="e.g. You are a friendly tutor who answers in short sentences."
            rows={2}
          />
        </div>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSend) onSend()
        }}
      >
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          aria-label="Message"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              if (canSend) onSend()
            }
          }}
          placeholder="Message Lyftr Chat…"
          rows={1}
        />
        <div className="composer-bar">
          <div className="composer-tools">
            <button
              type="button"
              className={`tool-button ${showSystemPrompt || systemPrompt ? 'active' : ''}`}
              onClick={onToggleSystemPrompt}
              aria-expanded={showSystemPrompt}
            >
              System prompt{systemPrompt.trim() ? ' •' : ''}
            </button>
            <label className={`simulate ${simulateFailure ? 'active' : ''}`}>
              <span className="visually-hidden">Simulate failure</span>
              <select
                data-testid="simulate-failure-select"
                value={simulateFailure}
                onChange={(e) => onSimulateFailureChange(e.target.value)}
                title="Sends the X-Simulate-Failure header with each request"
              >
                {FAILURE_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button type="submit" className="send-button" data-testid="send-button" disabled={!canSend} aria-label="Send message">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </form>
      <p className="footnote">Each reply shows whether it came from the cache, which key answered, and its own token usage.</p>
    </div>
  )
}
