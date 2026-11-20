import { useEffect, useRef, useState } from 'react'
import { ApiError, sendChat, type ChatMetadata } from './api'
import { AssistantMessage } from './components/AssistantMessage'
import { Composer } from './components/Composer'

type Message =
  | { id: number; role: 'user'; content: string }
  | { id: number; role: 'assistant'; content: string; metadata: ChatMetadata }
  | { id: number; role: 'error'; content: string }

const SUGGESTIONS = ['What is the capital of France?', 'Explain response caching in one paragraph.', 'Give me three tips for writing clean Python.']

function newSessionId() {
  return `s-${crypto.randomUUID()}`
}

let nextId = 1

export default function App() {
  // A new session starts on each page load.
  const [sessionId, setSessionId] = useState(newSessionId)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [showSystemPrompt, setShowSystemPrompt] = useState(false)
  const [simulateFailure, setSimulateFailure] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, pending])

  async function send(text: string) {
    const message = text.trim()
    if (!message || pending) return
    setInput('')
    setMessages((prev) => [...prev, { id: nextId++, role: 'user', content: message }])
    setPending(true)
    try {
      const res = await sendChat(
        { session_id: sessionId, message, ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {}) },
        simulateFailure || undefined,
      )
      setMessages((prev) => [...prev, { id: nextId++, role: 'assistant', content: res.reply, metadata: res.metadata }])
    } catch (err) {
      const content = err instanceof ApiError ? err.message : 'Something unexpected went wrong. Please try again.'
      setMessages((prev) => [...prev, { id: nextId++, role: 'error', content }])
    } finally {
      setPending(false)
    }
  }

  function newChat() {
    setSessionId(newSessionId())
    setMessages([])
    setInput('')
  }

  const empty = messages.length === 0

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">
            L
          </span>
          <span className="brand-name">Lyftr Chat</span>
        </div>
        <div className="topbar-right">
          <span className="session" title="Session id">
            {sessionId.slice(0, 10)}…
          </span>
          <button type="button" className="ghost-button" onClick={newChat} disabled={pending}>
            New chat
          </button>
        </div>
      </header>

      <main className="thread" aria-live="polite">
        {empty ? (
          <section className="welcome">
            <h1>How can I help you today?</h1>
            <p>Send a message to start a new conversation. Repeat a first message in a new chat to see a cache hit.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="suggestion" onClick={() => send(s)} disabled={pending}>
                  {s}
                </button>
              ))}
            </div>
          </section>
        ) : (
          <div className="messages">
            {messages.map((m) => {
              if (m.role === 'user') {
                return (
                  <div key={m.id} className="message user" data-testid="user-message">
                    <div className="message-text">{m.content}</div>
                  </div>
                )
              }
              if (m.role === 'assistant') {
                return <AssistantMessage key={m.id} content={m.content} metadata={m.metadata} />
              }
              return (
                <div key={m.id} className="message error" role="alert" data-testid="error-message">
                  <div className="message-text">{m.content}</div>
                </div>
              )
            })}
            {pending && (
              <div className="message assistant pending" aria-label="Assistant is typing">
                <div className="avatar" aria-hidden="true">
                  L
                </div>
                <div className="typing">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </main>

      <Composer
        value={input}
        onChange={setInput}
        onSend={() => send(input)}
        pending={pending}
        systemPrompt={systemPrompt}
        onSystemPromptChange={setSystemPrompt}
        showSystemPrompt={showSystemPrompt}
        onToggleSystemPrompt={() => setShowSystemPrompt((v) => !v)}
        simulateFailure={simulateFailure}
        onSimulateFailureChange={setSimulateFailure}
      />
    </div>
  )
}
