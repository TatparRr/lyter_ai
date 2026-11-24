import { useEffect, useRef, useState } from 'react'
import './App.css'

const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')

export default function App() {
  const [sessionId] = useState(() => `s-${crypto.randomUUID().slice(0, 8)}`)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [sending, setSending] = useState(false)
  const endRef = useRef(null)

  useEffect(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), [messages])

  async function sendMessage() {
    const message = input.trim()
    if (!message || sending) return
    setInput('')
    setSending(true)
    setMessages(current => [...current, { role: 'user', content: message }])
    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message, ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {}) })
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Request failed')
      setMessages(current => [...current, { role: 'assistant', content: data.reply, metadata: data.metadata }])
    } catch (error) {
      setMessages(current => [...current, { role: 'error', content: `Unable to send message: ${error.message}` }])
    } finally {
      setSending(false)
    }
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendMessage()
    }
  }

  return (
    <main className="app-shell">
      <header>
        <div className="brand-mark">AI</div>
        <div><p className="eyebrow">LYFTER</p><h1>Conversation Studio</h1></div>
        <span className="session-pill">Session live</span>
      </header>

      <section className="chat-card">
        <label className="prompt-label">System prompt <span>optional</span></label>
        <input
          className="system-input"
          data-testid="system-prompt-input"
          value={systemPrompt}
          onChange={event => setSystemPrompt(event.target.value)}
          placeholder="Set the assistant's behavior…"
        />

        <div className="messages" aria-live="polite">
          {messages.length === 0 && (
            <div className="empty-state">
              <div className="orb">✦</div>
              <h2>Where should we begin?</h2>
              <p>Ask a question or start a conversation.</p>
            </div>
          )}
          {messages.map((message, index) => (
            message.role === 'assistant' ? (
              <article className="message-row assistant-row" data-testid="assistant-message" key={index}>
                <div className="avatar">AI</div>
                <div className="assistant-content">
                  <p className="bubble assistant-bubble">{message.content}</p>
                  <div className="metadata">
                    <span data-testid="meta-source" className={message.metadata.resolution_source === 'LLM_CACHE' ? 'cache' : ''}>{message.metadata.resolution_source}</span>
                    <span data-testid="meta-model">{message.metadata.model}</span>
                    <span data-testid="meta-latency">{message.metadata.latency_ms} ms</span>
                    <span>In <b data-testid="meta-input-tokens">{message.metadata.input_tokens}</b></span>
                    <span>Out <b data-testid="meta-output-tokens">{message.metadata.output_tokens}</b></span>
                  </div>
                </div>
              </article>
            ) : (
              <div className={`message-row ${message.role === 'error' ? 'error-row' : 'user-row'}`} key={index}>
                {message.role === 'error' ? <div className="error-bubble">{message.content}</div> : <p className="bubble user-bubble">{message.content}</p>}
              </div>
            )
          ))}
          <div ref={endRef} />
        </div>

        <div className="composer">
          <textarea
            data-testid="chat-input"
            value={input}
            onChange={event => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Message the assistant…"
            rows={2}
          />
          <button data-testid="send-button" onClick={sendMessage} disabled={!input.trim() || sending} aria-label="Send message">
            {sending ? '…' : '↑'}
          </button>
        </div>
      </section>
    </main>
  )
}
