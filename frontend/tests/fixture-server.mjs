// Browser-only fixtures. Controlled chunks test UI states, never model quality.
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { resolve, extname } from 'node:path'

const root = resolve('dist')
const pending = new Map()
const answer = '根据原文，报销期限为30天。[D01/C01]'
function result(status = 'answered') {
  return { run_id: 'browser-fixture', elapsed_seconds: 1, provider: 'fixture', trace: [],
    result: { status, completed: status === 'answered', answer: status === 'cancelled' ? '任务已取消，未完成。' : answer,
      citations: status === 'answered' ? [{ doc_id: 'D01', chunk_id: 'C01', version_date: '2026-01-10', quote: '报销期限为 30 天，自费用发生之日起计算。' }] : [], gaps: [], conflicts: [] } }
}
function emit(response, event) { response.write(`event: ${event.event}\ndata: ${JSON.stringify(event)}\n\n`) }
function json(response, value) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)) }

createServer(async (request, response) => {
  const path = new URL(request.url, 'http://127.0.0.1').pathname
  if (path === '/api/config') return json(response, { deepseek_available: true, hybrid_available: false, corpora: [{ id: 'conflict', label: '浏览器测试资料' }, { id: 'single', label: '单一制度' }] })
  if (path === '/api/chat') {
    let body = ''; for await (const chunk of request) body += chunk
    const data = JSON.parse(body)
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'close' })
    emit(response, { event: 'run_start', request_id: data.request_id })
    emit(response, { event: 'answer_start', validated: false })
    emit(response, { event: 'answer_delta', delta: data.question.includes('修正') ? '错误草稿99天' : '根据原文，报销期限为', validated: false })
    if (data.question.includes('断开')) { response.end(); return }
    pending.set(data.request_id, { response, mode: data.question })
    request.on('close', () => { /* cancellation uses a separate endpoint */ })
    return
  }
  if (path.startsWith('/api/cancel/')) {
    const run = pending.get(path.split('/').at(-1))
    if (run) { emit(run.response, { event: 'result', record: result('cancelled') }); run.response.end(); pending.delete(path.split('/').at(-1)) }
    return json(response, { cancelled: !!run })
  }
  if (path === '/test/release') {
    for (const [id, run] of pending) {
      if (run.mode.includes('修正')) {
        emit(run.response, { event: 'answer_reset', message: '校验未通过，正在修正回答。' })
        emit(run.response, { event: 'answer_start', validated: false })
      }
      emit(run.response, { event: 'result', record: result() }); run.response.end(); pending.delete(id)
    }
    return json(response, { released: true })
  }
  const target = resolve(root, '.' + (path === '/' ? '/index.html' : path))
  if (!target.startsWith(root + '/') && !target.startsWith(root + '\\')) { response.writeHead(404); response.end(); return }
  try {
    const bytes = await readFile(target)
    response.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' })[extname(target)] || 'application/octet-stream' })
    response.end(bytes)
  } catch { response.writeHead(404); response.end() }
}).listen(8766, '127.0.0.1')
