import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import Markdown from 'react-markdown'
import { consumeEvents } from './stream.js'
import './styles.css'

function Icon({ name, size = 20, ...props }) {
  const paths = {
    book: <><path d="M12 5c-3-2-7-2-10 0v15c3-2 7-2 10 0 3-2 7-2 10 0V5c-3-2-7-2-10 0Z" /><path d="M12 5v15" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    send: <><path d="m5 12 7-7 7 7M12 5v15" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    arrow: <path d="m9 5 7 7-7 7" />,
    check: <path d="m5 12 4 4L19 6" />,
    quote: <><path d="M10 6H4v7h6v5H4M21 6h-6v7h6v5h-6" /></>,
    file: <><path d="M14 3H5v18h14V8l-5-5Z" /><path d="M14 3v5h5M8 12h8M8 16h5" /></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" /></>,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z" /></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.file}</svg>
}

const statuses = {
  answered: ['已有依据', 'success'], needs_clarification: ['待你确认', 'warning'],
  insufficient_evidence: ['资料不足', 'warning'], tool_error: ['工具暂不可用', 'error'],
  model_error: ['模型暂不可用', 'error'], step_limit: ['达到执行上限', 'warning'],
  cancelled: ['已停止', 'muted'], error: ['请求失败', 'error'],
}
const tools = { search_docs: '检索相关资料', read_chunk: '读取原文', finish: '核验回答与引用' }
const examples = [
  ['报销制度', '报销期限多久？', 'book'],
  ['材料清单', '报销需要准备哪些材料？', 'file'],
  ['产品功能', '影文桌面版支持编辑字幕吗？', 'spark'],
]

function App() {
  const [config, setConfig] = useState(null)
  const [connectionError, setConnectionError] = useState('')
  const [provider, setProvider] = useState('deepseek')
  const [retrieval, setRetrieval] = useState('hybrid')
  const [corpus, setCorpus] = useState('conflict')
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [source, setSource] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const active = useRef(null)
  const composer = useRef(null)
  const end = useRef(null)

  useEffect(() => {
    fetch('/api/config').then(r => { if (!r.ok) throw new Error(); return r.json() }).then(data => {
      setConfig(data)
      if (!data.deepseek_available) setProvider('mock')
      if (!data.hybrid_available) setRetrieval('bm25')
    }).catch(() => setConnectionError('未连接到后端。请先运行 python run.py serve --build。'))
    return () => {
      const current = active.current
      if (current) {
        fetch(`/api/cancel/${current.id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', keepalive: true }).catch(() => {})
        current.controller.abort()
      }
    }
  }, [])

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages])

  function update(id, change) {
    setMessages(previous => previous.map(message => message.id === id ? { ...message, ...change(message) } : message))
  }

  async function ask(event) {
    event?.preventDefault()
    const text = question.trim()
    if (!text || busy || !config) return
    const id = crypto.randomUUID().replaceAll('-', '')
    const assistantId = `${id}-answer`
    const controller = new AbortController()
    active.current = { id, controller }
    const history = messages.filter(m => m.role === 'user' || (m.role === 'assistant' && m.record && !['cancelled', 'model_error', 'tool_error', 'step_limit'].includes(m.status)))
      .slice(-20).map(m => ({ role: m.role, content: m.answer || m.content }))
    setMessages(previous => [...previous, { id, role: 'user', content: text },
      { id: assistantId, role: 'assistant', answer: '', status: 'running', steps: [], phase: '正在连接…', provider }])
    setQuestion(''); setBusy(true); setStopping(false); setSource(null)
    let complete = false
    try {
      const response = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ question: text, history, provider, retrieval, corpus, request_id: id }),
      })
      await consumeEvents(response, event => {
        if (active.current?.id !== id) return
        if (event.event === 'progress') {
          update(assistantId, message => {
            const steps = [...message.steps]
            let phase = message.phase
            if (event.phase === 'model_start') phase = '正在理解问题与核对证据…'
            if (event.phase === 'tool_start') {
              phase = tools[event.tool] || '正在处理…'
              steps.push({ tool: event.tool, label: phase, done: false })
            }
            if (event.phase === 'tool_done') {
              const index = steps.findLastIndex(step => step.tool === event.tool && !step.done)
              if (index >= 0) steps[index] = { ...steps[index], done: true, ok: event.ok, summary: event.summary, elapsed: event.elapsed_seconds }
            }
            return { steps, phase }
          })
        } else if (event.event === 'answer_start' || event.event === 'answer_reset') {
          update(assistantId, () => ({ answer: '', phase: event.message || '正在生成回答，引用待核验…' }))
        } else if (event.event === 'answer_delta') {
          update(assistantId, message => ({ answer: message.answer + event.delta, phase: '正在生成回答，引用待核验…' }))
        } else if (event.event === 'result') {
          complete = true
          update(assistantId, () => ({ answer: event.record.result.answer, status: event.record.result.status, record: event.record }))
        } else if (event.event === 'error') {
          throw new Error(event.message)
        }
      })
      if (!complete) throw new Error('连接提前结束，回答尚未完成校验，请重试。')
    } catch (error) {
      if (!complete) update(assistantId, () => ({ answer: error.name === 'AbortError' ? '已停止本次回答。' : (error instanceof TypeError ? '连接已中断，回答未完成校验，请重试。' : error.message),
        status: error.name === 'AbortError' ? 'cancelled' : 'error' }))
    } finally {
      if (active.current?.id === id) { active.current = null; setBusy(false); setStopping(false); composer.current?.focus() }
    }
  }

  async function stop() {
    const current = active.current
    if (!current || stopping) return
    setStopping(true)
    try {
      const result = await fetch(`/api/cancel/${current.id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', signal: AbortSignal.timeout(3000) })
      if (!result.ok || !(await result.json()).cancelled) current.controller.abort()
    } catch { current.controller.abort() }
  }

  function reset() {
    if (busy) return
    setMessages([]); setSource(null); setQuestion(''); composer.current?.focus()
  }

  function download(record) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = `knowledge-${record.run_id}.json`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const lastRecord = messages.findLast(message => message.record)?.record
  return <div className="app-shell">
    <aside className={`sidebar ${settingsOpen ? 'open' : ''}`}>
      <button className="mobile-sidebar-close icon-button" aria-label="关闭设置" onClick={() => setSettingsOpen(false)}>×</button>
      <a className="brand" href="/" aria-label="知序首页"><span className="brand-icon"><Icon name="book" size={24} /></span><span>知序<span className="brand-subtitle">企业知识问答</span></span></a>
      <button className="new-chat" onClick={reset} disabled={busy}><Icon name="plus" size={18} />新建对话<span>↗</span></button>
      <div className="sidebar-section"><span className="section-label">本次检索</span>
        <label htmlFor="corpus">知识库</label>
        <select id="corpus" value={corpus} onChange={e => { setCorpus(e.target.value); reset() }} disabled={busy}>
          {(config?.corpora || [{ id: 'conflict', label: '企业资料 · 含冲突制度' }, { id: 'single', label: '报销制度 · 单一版本' }]).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <label htmlFor="retrieval">检索方式</label>
        <select id="retrieval" value={retrieval} onChange={e => setRetrieval(e.target.value)} disabled={busy}>
          <option value="hybrid">混合检索 · 语义与关键词</option><option value="bm25">关键词检索 · BM25</option>
        </select>
        <label htmlFor="provider">回答模型</label>
        <select id="provider" value={provider} onChange={e => setProvider(e.target.value)} disabled={busy}>
          <option value="deepseek" disabled={config && !config.deepseek_available}>DeepSeek</option><option value="mock">离线演示</option>
        </select>
        {provider === 'mock' && <p className="setting-note">演示仅验证流程，不代表真实模型决策。</p>}
        {retrieval === 'hybrid' && config && !config.hybrid_available && <p className="setting-note warning-text">未配置向量 Key，将明确降级为关键词检索。</p>}
      </div>
      <div className="sidebar-note"><span className="note-icon"><Icon name="check" size={16} /></span><div><strong>让答案有据可查</strong><p>读取原文后作答，遇到制度冲突时先查证，再向你确认。</p></div></div>
      <div className="sidebar-footer"><span className={`connection-dot ${config ? 'online' : ''}`} />{config ? '本地服务已连接' : '正在连接本地服务'}<span className="local-tag">LOCAL</span></div>
    </aside>

    <main className="main-panel">
      <header className="topbar"><div><button className="mobile-settings icon-button" aria-label="打开设置" onClick={() => setSettingsOpen(!settingsOpen)}><Icon name="settings" /></button><span className="breadcrumb">工作空间<span>/</span><strong>知识问答</strong></span></div><span className="source-pill"><span className="connection-dot online" />基于企业资料回答</span></header>
      <div className="conversation" role="log" aria-label="问答记录">
        {connectionError && <div className="connection-error" role="alert">{connectionError}</div>}
        {messages.length === 0 ? <section className="welcome">
          <span className="welcome-emblem"><Icon name="spark" size={31} /></span><p className="eyebrow">YOUR KNOWLEDGE, WITH EVIDENCE</p>
          <h1>让每个答案，<br className="mobile-break" />都有出处。</h1>
          <p className="welcome-description">从制度到产品说明，找到你需要的答案。<br />查看原文依据，也看见尚待确认的地方。</p>
          <div className="question-cards">{examples.map(([category, text, icon]) => <button key={text} onClick={() => { setQuestion(text); composer.current?.focus() }}>
            <span className="example-category"><Icon name={icon} size={17} />{category}</span><span className="example-question">{text}<Icon name="arrow" size={15} /></span>
          </button>)}</div>
          <p className="welcome-tip"><Icon name="quote" size={15} />回答附原文引用 · 资料不足时明确说明 · 冲突时先查证</p>
        </section> : <div className="messages">{messages.map(message => message.role === 'user' ? <div className="user-row" key={message.id}><div className="user-message">{message.content}</div><span className="avatar user-avatar">你</span></div> :
          <article className="assistant-row" key={message.id} aria-label="助手回答">
            <span className="avatar assistant-avatar"><Icon name="spark" size={17} /></span>
            <div className="assistant-content"><div className="answer-heading"><strong>知序</strong><span className="model-label">{message.provider === 'mock' ? '离线演示' : 'DeepSeek'}</span>{message.status === 'running' ? <span className="status-badge streaming"><span className="spinner" />生成中 · 待核验</span> : <span className={`status-badge ${statuses[message.status]?.[1] || ''}`}>{statuses[message.status]?.[0] || message.status}</span>}</div>
              {!message.answer && message.status === 'running' && <div className="thinking"><span className="thinking-dots"><i /><i /><i /></span>{message.phase}</div>}
              {message.answer && <div className={`answer-text ${message.status === 'running' ? 'draft' : ''}`}><Markdown>{message.answer}</Markdown>{message.status === 'running' && <span className="stream-caret" />}</div>}
              {message.steps.length > 0 && <details className="execution"><summary><span><Icon name="check" size={14} />{message.status === 'running' ? message.phase : `已记录 ${message.steps.length} 个执行步骤`}</span><span>查看过程</span></summary><ol>{message.steps.map((step, i) => <li key={i}><span className={step.done ? (step.ok ? 'step-check' : 'step-warning') : 'spinner'}>{step.done ? (step.ok ? '✓' : '!') : ''}</span><div><strong>{step.label}</strong>{step.summary && <p>{step.summary}</p>}</div>{step.elapsed != null && <span className="step-time">{step.elapsed.toFixed(1)}s</span>}</li>)}</ol></details>}
              {message.record?.result.citations.length > 0 && <div className="citation-strip"><span>参考资料</span>{[...new Map(message.record.result.citations.map(c => [`${c.doc_id}/${c.chunk_id}`, c])).values()].map(c => <button key={`${c.doc_id}/${c.chunk_id}`} onClick={() => setSource(c)}><Icon name="file" size={14} />{c.doc_id}<span>{c.version_date}</span><Icon name="arrow" size={12} /></button>)}</div>}
              {message.record?.result.gaps.length > 0 && <div className="gaps"><strong>还需确认</strong><ul>{message.record.result.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></div>}
              {message.record && <div className="answer-footer"><span>耗时 {message.record.elapsed_seconds.toFixed(1)}s{message.record.trace.some(t => t.observation?.retrieval?.degraded) ? ' · 已降级为关键词检索' : ''}</span><button onClick={() => download(message.record)}><Icon name="download" size={13} />下载运行记录</button></div>}
            </div>
          </article>)}</div>}
        <div ref={end} />
      </div>

      <div className="composer-area"><form className="composer" onSubmit={ask}>
        <label className="sr-only" htmlFor="question">输入你的问题</label><textarea id="question" ref={composer} rows={2} maxLength={4000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="询问制度、产品，或继续追问…" disabled={busy} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); ask() } }} />
        <div className="composer-bottom"><span><Icon name="book" size={14} />{corpus === 'conflict' ? '企业资料' : '单一版本报销制度'}<span className="composer-divider">·</span>{retrieval === 'hybrid' ? '混合检索' : '关键词检索'}</span>{busy ? <button type="button" className="send-button stop-button" onClick={stop} disabled={stopping} aria-label="停止回答"><Icon name="stop" size={17} />{stopping ? '停止中' : '停止'}</button> : <button type="submit" className="send-button" disabled={!question.trim() || !config} aria-label="发送问题"><Icon name="send" size={19} /></button>}</div>
      </form><p className="composer-help">Enter 发送，Shift + Enter 换行<span>答案以引用原文为依据，请留意待确认事项。</span></p></div>
    </main>

    <aside className={`sources-panel ${source ? 'source-open' : ''}`} aria-label="原文依据">
      <div className="sources-heading"><strong>原文依据</strong><span>{source ? <button className="icon-button" aria-label="关闭原文" onClick={() => setSource(null)}>×</button> : <Icon name="quote" size={18} />}</span></div>
      {source ? <div className="source-detail"><span className="document-id"><Icon name="file" size={16} />{source.doc_id} / {source.chunk_id}</span><h2>文档原文片段</h2><p className="source-version">版本日期 {source.version_date}</p><blockquote>{source.quote}</blockquote><div className="source-assurance"><Icon name="check" size={15} />引文已与本轮读取的原文核对</div></div> : <div className="source-empty"><div className="source-art"><Icon name="file" size={42} /><span><Icon name="check" size={14} /></span></div><h2>每一条依据，都可回看</h2><p>回答完成后，点击参考资料<br />查看对应的文档版本与原文。</p>{lastRecord && <span className="source-count">本次回答包含 {lastRecord.result.citations.length} 条引用</span>}</div>}
      <div className="sources-bottom"><span>关于资料</span><p>当前知识库为合成资料，用于演示制度与产品问答。</p></div>
    </aside>
    {settingsOpen && <button className="sidebar-overlay" aria-label="收起设置遮罩" onClick={() => setSettingsOpen(false)} />}
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
