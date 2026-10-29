export async function consumeEvents(response, receive) {
  if (!response.ok) {
    let message = '请求失败，请稍后重试。'
    try { message = (await response.json()).error || message } catch { /* HTTP without JSON */ }
    throw new Error(message)
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream') || !response.body) {
    throw new Error('服务未返回事件流，请检查后端是否正常运行。')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n')
        if (data) receive(JSON.parse(data))
      }
      if (buffer.length > 8 * 1024 * 1024) throw new Error('响应片段过大。')
    }
  } finally {
    reader.releaseLock()
  }
}
