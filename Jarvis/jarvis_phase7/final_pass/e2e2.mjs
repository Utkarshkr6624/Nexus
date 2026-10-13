// Final-pass REAL END-TO-END browser test: installed Chrome via playwright-core, the stack started by start.cmd,
// the real backend + Postgres, and the real Qwen3-8B runtime. Nothing is mocked. Output: one JSON evidence file.
import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { chromium } from 'playwright-core'

const UI = 'http://localhost:5173'
const API = 'http://127.0.0.1:8000/api/v1'
const OUT = process.argv[2]
const out = { started: new Date().toISOString(), label: 'REAL END-TO-END', browser: null, steps: [], turns: [], repetition: null, memory: [] }
const tag = Math.random().toString(16).slice(2, 8)
const PW = 'Nexo-Final-2026!x'
const users = [] // {email, token}

function rec(name, status, detail = {}) {
  out.steps.push({ name, status, ...detail })
  console.log(`${status.padEnd(9)} ${name} ${JSON.stringify(detail).slice(0, 240)}`)
}
async function api(method, path, token, body) {
  const r = await fetch(API + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let j = null
  try { j = await r.json() } catch { /* 204 */ }
  return { status: r.status, body: j }
}
async function login(email) {
  const r = await api('POST', '/auth/login', null, { email, password: PW })
  return r.body?.access_token
}
function memory(label) {
  try {
    const gpu = execSync('nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits').toString().trim()
    const ram = execSync('powershell -NoProfile -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)"').toString().trim()
    const llama = execSync('tasklist /FI "IMAGENAME eq llama-server.exe" /NH').toString().includes('llama-server') ? 1 : 0
    out.memory.push({ label, vram_used_total_mb: gpu, free_ram_mb: Number(ram), llama_server_processes: llama })
  } catch (e) { out.memory.push({ label, error: String(e) }) }
}
const adjacent = (t) => { const w = t.toLowerCase().match(/[a-z']+/g) || []; return w.filter((x, i) => i > 0 && x === w[i - 1] && x.length > 1) }

const browser = await chromium.launch({ channel: 'chrome', headless: true })
out.browser = `Chrome ${browser.version()} (headless, playwright-core 1.56.1)`
const context = await browser.newContext()
const page = await context.newPage()
page.on('response', async (r) => {
  const u = r.url()
  if (/\/jarvis\/(chat|confirm)$/.test(u)) {
    let body = null
    try { body = await r.json() } catch { /* */ }
    out.turns.push({ endpoint: u.split('/').pop(), status: r.status(), outcome: body?.outcome, text: body?.text,
      trace: body?.trace?.map((t) => ({ tool: t.tool, status: t.status, arguments: t.arguments, error: t.result?.error, results: t.result?.data?.results?.map((x) => x.title), coverage: t.result?.data?.coverage })),
      proposal: body?.proposal, proposal_id: body?.proposal_id, guards: body?.guards })
  }
})

async function sendNexo(text, timeout = 400_000) {
  const before = out.turns.length
  await page.getByLabel('Message NEXO').fill(text)
  await page.getByRole('button', { name: 'Send' }).click()
  await page.getByText('NEXO is working…').waitFor({ state: 'detached', timeout })
  await page.waitForTimeout(300)
  return out.turns[before] ?? null
}
async function waitIdle(timeout = 400_000) {
  await page.getByText('NEXO is working…').waitFor({ state: 'attached', timeout: 5_000 }).catch(() => {})
  await page.getByText('NEXO is working…').waitFor({ state: 'detached', timeout })
  await page.waitForTimeout(300)
}

try {
  const email = `nexo.final2.${tag}@example.com`
  await api('POST', '/auth/register', null, { username: `nexofinal2${tag}`, email, password: PW })
  const token = await login(email)
  users.push({ email, token })
  await page.goto(UI + '/login')
  await page.locator('#email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PW)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 60_000 })
  await page.goto(UI + '/nexo')
  await page.getByRole('button', { name: /Turn NEXO on/ }).click()
  await page.getByText('NEXO model is ready').waitFor({ timeout: 900_000 })
  memory('model ready')
  const proj = await api('POST', '/projects', token, { name: `NEXO final2 ${tag}` })
  rec('test project created', proj.status === 201 ? 'PASS' : 'FAIL')

  // ---- Single-turn natural-language probes (API, real model, fresh conversation each)
  const probes = [
    'Search Wikipedia for Christopher Nolan films.',
    'hey can u search good movies for me',
    'Look up the Eiffel Tower on Wikipedia.',
    'search for xqzvbnmplk',
    'What tasks do I have open?',
    'Create a task titled "Buy milk".',
    'Add a task to buy milk tomorrow',
  ]
  out.single_turn = []
  for (const p of probes) {
    const s = Date.now()
    const r = await api('POST', '/jarvis/chat', token, { messages: [{ role: 'user', content: p }], timezone: 'Asia/Kolkata' })
    const b = r.body ?? {}
    const row = { prompt: p, http: r.status, secs: (Date.now() - s) / 1000, outcome: b.outcome, text: b.text, repeats: adjacent(b.text ?? ''), guards: b.guards,
      trace: b.trace?.map((t) => ({ tool: t.tool, status: t.status, arguments: t.arguments, error: t.result?.error, results: t.result?.data?.results?.map((x) => x.title), coverage: t.result?.data?.coverage })), proposal: b.proposal }
    out.single_turn.push(row)
    if (b.proposal_id) await api('POST', '/jarvis/confirm', token, { proposal_id: b.proposal_id, approve: false }) // never execute a probe
    rec(`single-turn: ${p}`, 'OBSERVED', { outcome: b.outcome, tools: row.trace?.map((t) => `${t.tool}:${t.status}`), text: (b.text ?? '').slice(0, 160) })
  }

  // ---- Browser approval lifecycle (the prompt spells out the call, as Phase 7b did; labelled as such)
  const title = `NEXO browser check ${tag}`
  const listed = async () => { const r = await api("GET", "/tasks?limit=100", token); return { status: r.status, titles: r.body?.items?.map((x) => x.title) } }
  const countTasks = async () => (await listed()).titles?.filter((x) => x === title).length ?? -1
  const ask = `Please do exactly this: app_create_task {"title": "${title}"}`
  let t = await sendNexo(ask)
  let card = page.getByLabel('Approval request').last()
  if (t?.proposal_id && (await card.count())) {
    const shown = await card.innerText()
    rec('approval card shows tool, target and exact arguments before anything runs', shown.includes('app_create_task') && shown.includes(title) && (await countTasks()) === 0 ? 'PASS' : 'FAIL', { card: shown.slice(0, 300), has_tool: shown.includes('app_create_task'), has_title: shown.includes(title), listed: await listed() })
    await card.getByRole('button', { name: 'Decline' }).click()
    await waitIdle()
    rec('Decline in the browser: nothing created; UI says so', (await countTasks()) === 0 && (await page.getByText('Declined — nothing was run.').count()) > 0 ? 'PASS' : 'FAIL', { outcome: out.turns.at(-1)?.outcome, text: out.turns.at(-1)?.text })
    const replay = await api('POST', '/jarvis/confirm', token, { proposal_id: t.proposal_id, approve: true })
    rec('approving a declined proposal later runs nothing (404)', replay.status === 404 && (await countTasks()) === 0 ? 'PASS' : 'FAIL', { status: replay.status })
  } else rec('approval card (decline path)', 'MODEL_DID_NOT_PROPOSE', { outcome: t?.outcome, text: t?.text })

  await page.reload()
  await page.getByText('NEXO model is ready').waitFor({ timeout: 60_000 })
  t = await sendNexo(ask)
  card = page.getByLabel('Approval request').last()
  if (t?.proposal_id && (await card.count())) {
    const otherEmail = `nexo.other2.${tag}@example.com`
    await api('POST', '/auth/register', null, { username: `nexoother2${tag}`, email: otherEmail, password: PW })
    const other = await login(otherEmail)
    users.push({ email: otherEmail, token: other })
    const stolen = await api('POST', '/jarvis/confirm', other, { proposal_id: t.proposal_id, approve: true })
    rec('another user cannot approve this proposal (404, nothing created)', stolen.status === 404 && (await countTasks()) === 0 ? 'PASS' : 'FAIL', { status: stolen.status })
    const forged = await api('POST', '/jarvis/confirm', token, { proposal_id: 'f'.repeat(32), approve: true })
    const altered = await api('POST', '/jarvis/confirm', token, { proposal_id: t.proposal_id, approve: true, arguments: { title: 'evil' } })
    const noAuth = await api('POST', '/jarvis/confirm', null, { proposal_id: t.proposal_id, approve: true })
    rec('forged id 404, altered arguments 422, no token 401; nothing created', forged.status === 404 && altered.status === 422 && noAuth.status === 401 && (await countTasks()) === 0 ? 'PASS' : 'FAIL', { forged: forged.status, altered: altered.status, noAuth: noAuth.status })
    await card.getByRole('button', { name: 'Approve' }).dblclick() // double click: one execution
    await waitIdle()
    const confirms = out.turns.filter((x) => x.endpoint === 'confirm')
    const n = await countTasks()
    rec('Approve (double-clicked) in the browser: task exists exactly once', n === 1 && confirms.at(-1)?.trace?.some((x) => x.tool === 'app_create_task' && x.status === 'executed_ok') ? 'PASS' : 'FAIL',
      { listed: await listed(), tasks_with_title: n, confirm_responses_total: confirms.length, outcome: confirms.at(-1)?.outcome, text: confirms.at(-1)?.text, guards: confirms.at(-1)?.guards })
    rec('UI reports the approved outcome', (await page.getByText('Approved — the result is below.').count()) > 0 ? 'PASS' : 'FAIL')
    const again = await api('POST', '/jarvis/confirm', token, { proposal_id: t.proposal_id, approve: true })
    rec('approving the same proposal again does not repeat it (404, still 1)', again.status === 404 && (await countTasks()) === 1 ? 'PASS' : 'FAIL', { status: again.status })
  } else rec('approval card (approve path)', 'MODEL_DID_NOT_PROPOSE', { outcome: t?.outcome, text: t?.text })

  // ---- Search sources rendered in the browser, when the model calls the tool
  await page.reload()
  await page.getByText('NEXO model is ready').waitFor({ timeout: 60_000 })
  t = await sendNexo('hey can u search good movies for me')
  const ws = t?.trace?.find((x) => x.tool === 'web_search')
  rec('browser: search request', ws ? 'CALLED' : 'MODEL_DID_NOT_CALL', { search: ws, sources_rendered: await page.getByText(/Search results \(/).count(), links: await page.locator('a[href^="https://en.wikipedia.org"]').count(), text: t?.text })

  await page.getByRole('button', { name: /Turn NEXO off/ }).click()
  await page.getByText('NEXO model is off').waitFor({ timeout: 120_000 })
  await page.waitForTimeout(3000)
  memory('after unload')
  rec('model unloaded', out.memory.at(-1)?.llama_server_processes === 0 ? 'PASS' : 'FAIL', { memory: out.memory.at(-1) })
} catch (e) {
  rec('UNCAUGHT', 'FAIL', { error: String(e?.stack ?? e).slice(0, 1200) })
  try { await page.screenshot({ path: OUT.replace(/\.json$/, '_failure.png') }) } catch { /* */ }
} finally {
  // ---- Cleanup: tasks, project, accounts (verified)
  const cleanup = []
  for (const u of users) {
    if (!u.token) continue
    const tasks = (await api('GET', '/tasks?limit=100', u.token)).body?.items ?? []
    for (const x of tasks) cleanup.push(['task', (await api('DELETE', `/tasks/${x.id}`, u.token)).status])
    const projects = (await api('GET', '/projects?limit=100', u.token)).body?.items ?? []
    for (const p of projects) cleanup.push(['project', (await api('DELETE', `/projects/${p.id}`, u.token)).status])
    cleanup.push(['user', (await api('DELETE', '/users/me', u.token, { password: PW, confirm: true })).status])
    cleanup.push(['login after delete', (await api('POST', '/auth/login', null, { email: u.email, password: PW })).status])
  }
  out.cleanup = cleanup
  rec('cleanup', 'DONE', { cleanup })
  out.finished = new Date().toISOString()
  writeFileSync(OUT, JSON.stringify(out, null, 2))
  await browser.close()
}
