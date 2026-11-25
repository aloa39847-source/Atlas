import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

async function freePort() {
  const server = createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const { port } = server.address()
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  return port
}

test('built frontend is served with its entry module and assets', async () => {
  const port = await freePort()
  const viteBin = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))
  const child = spawn(process.execPath, [viteBin, 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    stdio: 'ignore',
  })
  const base = `http://127.0.0.1:${port}`
  let response
  try {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (child.exitCode !== null) throw new Error(`Vite preview exited with code ${child.exitCode}`)
      try {
        response = await fetch(base)
        break
      } catch {
        await delay(150)
      }
    }
    assert.ok(response, 'Vite preview did not become ready')
    assert.equal(response.status, 200)
    const html = await response.text()
    assert.match(html, /<div id=["']root["']>/)
    const entry = html.match(/(?:src|href)=["']([^"']+\.js)["']/)
    assert.ok(entry, 'built HTML does not reference a JavaScript entry asset')
    const assetResponse = await fetch(new URL(entry[1], base))
    assert.equal(assetResponse.status, 200)
    assert.match(assetResponse.headers.get('content-type') || '', /javascript/)
    const bundle = await assetResponse.text()
    assert.ok(bundle.length > 1000, 'served JavaScript bundle is unexpectedly small')
  } finally {
    child.kill('SIGTERM')
    await Promise.race([
      new Promise(resolve => child.once('exit', resolve)),
      delay(2000),
    ])
    if (child.exitCode === null) child.kill('SIGKILL')
  }
})
