import test from 'node:test'
import assert from 'node:assert/strict'
import { consumeEvents } from '../src/stream.js'

function response(chunks) {
  return new Response(new ReadableStream({ start(controller) {
    for (const chunk of chunks) controller.enqueue(chunk)
    controller.close()
  } }), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('parse Unicode and multiple SSE events across arbitrary byte boundaries', async () => {
  const bytes = new TextEncoder().encode(': ping\r\n\r\nevent: answer_delta\r\ndata: {"event":"answer_delta","delta":"中文🌱"}\r\n\r\ndata: {"event":"result"}\n\n')
  const chunks = [...bytes].map(byte => new Uint8Array([byte]))
  const events = []
  await consumeEvents(response(chunks), event => events.push(event))
  assert.deepEqual(events, [{ event: 'answer_delta', delta: '中文🌱' }, { event: 'result' }])
})

test('server errors are displayed without trying to parse a stream', async () => {
  await assert.rejects(consumeEvents(new Response('{"error":"请求参数无效"}', { status: 400 }), () => {}), /请求参数无效/)
})

test('non-stream responses are rejected', async () => {
  await assert.rejects(consumeEvents(new Response('{}'), () => {}), /事件流/)
})

test('events are delivered before the response is complete', async () => {
  let controller
  const events = []
  const body = new ReadableStream({ start(value) { controller = value } })
  const pending = consumeEvents(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }), event => events.push(event))
  controller.enqueue(new TextEncoder().encode('data: {"event":"answer_delta","delta":"先出现"}\n\n'))
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(events[0].delta, '先出现')
  controller.enqueue(new TextEncoder().encode('data: {"event":"result"}\n\n'))
  controller.close()
  await pending
  assert.equal(events[1].event, 'result')
})
