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
  // ---- NEXUS: protection and auth
  const anon = await api('GET', '/tasks')
  rec('API rejects unauthenticated /tasks', anon.status === 401 ? 'PASS' : 'FAIL', { status: anon.status })
  await page.goto(UI + '/nexo')
  await page.waitForURL(/\/login/, { timeout: 30_000 }).catch(() => {})
  rec('unauthenticated /nexo redirects to /login', page.url().includes('/login') ? 'PASS' : 'FAIL', { url: page.url() })

  const email = `nexo.final.${tag}@example.com`
  await page.goto(UI + '/register')
  await page.locator('#username').fill(`nexofinal${tag}`)
  await page.locator('#email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PW)
  await page.getByLabel('Confirm password').fill(PW)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.waitForURL(/\/dashboard/, { timeout: 60_000 })
  rec('register through the UI signs in and lands on /dashboard', 'PASS', { url: page.url() })
  const token = await login(email)
  users.push({ email, token })

  await page.goto(UI + '/')
  await page.waitForURL(/\/command-center/, { timeout: 30_000 })
  rec('/ opens the NEXUS main app (Command Center) when signed in', 'PASS', { url: page.url() })

  // Sign out and back in through the login form.
  await page.getByRole('button', { name: /Account menu for/ }).click()
  await page.getByText('Sign out', { exact: true }).click()
  await page.waitForURL(/\/login/, { timeout: 30_000 })
  rec('Sign out from the account menu returns to /login', 'PASS', { url: page.url() })
  await page.locator('#email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PW)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 60_000 })
  rec('sign in through the login form', 'PASS', { url: page.url() })

  // ---- /assistant is the DeBERTa classifier, separate from NEXO
  await page.goto(UI + '/assistant')
  await page.getByRole('heading', { name: 'AI Assistant' }).first().waitFor({ timeout: 30_000 })
  await page.locator('#voice-assistant-input').fill('show me my open tasks')
  await page.getByRole('button', { name: 'Classify' }).click()
  await page.getByText('Latest decision').waitFor({ timeout: 120_000 })
  const decision = (await page.locator('text=Latest decision').locator('xpath=ancestor::div[contains(@class,"rounded")][1]').innerText()).slice(0, 400)
  rec('/assistant (DeBERTa) classifies a typed request', 'PASS', { decision })
  const route = await api('POST', '/ml/route', token, { text: 'show me my open tasks' })
  rec('DeBERTa /ml/route answers with NEXO off', route.status === 200 ? 'PASS' : 'FAIL', { status: route.status, intent: route.body?.intent ?? route.body?.prediction?.intent, body: JSON.stringify(route.body).slice(0, 200) })

  // ---- /nexo
  await page.goto(UI + '/nexo')
  await page.getByRole('heading', { name: 'NEXO' }).first().waitFor({ timeout: 30_000 })
  const off = await api('POST', '/jarvis/chat', token, { messages: [{ role: 'user', content: 'hi' }] })
  rec('chat with the model off is refused clearly (no fake answer)', off.status === 503 ? 'PASS' : 'FAIL', { status: off.status, message: off.body?.error?.message })
  memory('model off')
  const voiceOff = await page.getByRole('button', { name: /Talk to NEXO/ }).isDisabled()
  rec('Talk to NEXO is disabled while the model is off', voiceOff ? 'PASS' : 'FAIL')

  await page.getByRole('button', { name: /Turn NEXO on/ }).click()
  const t0 = Date.now()
  await page.getByText('NEXO model is ready').waitFor({ timeout: 900_000 })
  rec('Turn NEXO on loads the real model', 'PASS', { seconds: Math.round((Date.now() - t0) / 1000) })
  memory('model ready')

  // Conversation
  let t = await sendNexo('What is the capital of France? Answer in one sentence.')
  rec('real conversational reply', t?.status === 200 && t.text ? 'PASS' : 'FAIL', { outcome: t?.outcome, text: t?.text, repeats: adjacent(t?.text ?? '') })

  // Search
  t = await sendNexo('Search Wikipedia for Christopher Nolan films.')
  const searched = t?.trace?.find((x) => x.tool === 'web_search')
  const shown = await page.getByText(/Search results \(/).count()
  rec('search request reaches the real web_search tool', searched ? (searched.status === 'executed_ok' || searched.status === 'partial' ? 'PASS' : 'FAIL') : 'MODEL_DID_NOT_CALL', { outcome: t?.outcome, search: searched, sources_rendered: shown, text: t?.text, repeats: adjacent(t?.text ?? '') })

  t = await sendNexo('Can you search for good movies for me?')
  const s2 = t?.trace?.find((x) => x.tool === 'web_search')
  rec('movie-recommendation search', s2 ? 'CALLED' : 'MODEL_DID_NOT_CALL', { outcome: t?.outcome, search: s2, text: t?.text, repeats: adjacent(t?.text ?? '') })

  // ---- Writes through approval
  const proj = await api('POST', '/projects', token, { name: `NEXO final ${tag}` })
  rec('test project created (app_create_task needs exactly one project)', proj.status === 201 ? 'PASS' : 'FAIL', { status: proj.status })
  const title = `NEXO browser check ${tag}`
  const countTasks = async () => (await api('GET', `/tasks?search=${encodeURIComponent(title)}`, token)).body?.items?.filter((x) => x.title === title).length ?? -1

  t = await sendNexo(`Create a task titled "${title}".`)
  let card = page.getByLabel('Approval request').last()
  if (t?.proposal_id && (await card.count())) {
    rec('write request shows an approval card with the exact call', 'PASS', { proposal: t.proposal, text: t.text })
    await card.getByRole('button', { name: 'Decline' }).click()
    await waitIdle()
    const n = await countTasks()
    rec('Decline: nothing is created', n === 0 ? 'PASS' : 'FAIL', { tasks_with_title: n, reply: out.turns.at(-1)?.outcome })
    const replay = await api('POST', '/jarvis/confirm', token, { proposal_id: t.proposal_id, approve: true })
    rec('approving a declined proposal afterwards runs nothing (404)', replay.status === 404 && (await countTasks()) === 0 ? 'PASS' : 'FAIL', { status: replay.status })
  } else {
    rec('write request shows an approval card', 'MODEL_DID_NOT_PROPOSE', { outcome: t?.outcome, text: t?.text })
  }

  t = await sendNexo(`Create a task titled "${title}".`)
  card = page.getByLabel('Approval request').last()
  if (t?.proposal_id && (await card.count())) {
    // Another user cannot use this proposal id.
    const otherEmail = `nexo.other.${tag}@example.com`
    await api('POST', '/auth/register', null, { username: `nexoother${tag}`, email: otherEmail, password: PW })
    const other = await login(otherEmail)
    users.push({ email: otherEmail, token: other })
    const stolen = await api('POST', '/jarvis/confirm', other, { proposal_id: t.proposal_id, approve: true })
    rec("another user's approval of this id runs nothing (404)", stolen.status === 404 && (await countTasks()) === 0 ? 'PASS' : 'FAIL', { status: stolen.status })
    const forged = await api('POST', '/jarvis/confirm', token, { proposal_id: 'f'.repeat(32), approve: true })
    const altered = await api('POST', '/jarvis/confirm', token, { proposal_id: t.proposal_id, approve: true, arguments: { title: 'evil' } })
    rec('forged id 404, altered arguments 422', forged.status === 404 && altered.status === 422 ? 'PASS' : 'FAIL', { forged: forged.status, altered: altered.status })

    await card.getByRole('button', { name: 'Approve' }).click()
    await waitIdle()
    const after = out.turns.at(-1)
    const n = await countTasks()
    const ok = after?.trace?.some((x) => x.tool === 'app_create_task' && x.status === 'executed_ok')
    rec('Approve: the task exists in the database exactly once', n === 1 && ok ? 'PASS' : 'FAIL', { tasks_with_title: n, outcome: after?.outcome, text: after?.text, guards: after?.guards })
    const uiSays = await page.getByText('Approved — the result is below.').count()
    rec('UI reports the approved outcome', uiSays > 0 ? 'PASS' : 'FAIL')
    const again = await api('POST', '/jarvis/confirm', token, { proposal_id: t.proposal_id, approve: true })
    rec('approving the same proposal twice does not repeat it (404, still 1 task)', again.status === 404 && (await countTasks()) === 1 ? 'PASS' : 'FAIL', { status: again.status })
  } else {
    rec('second write request shows an approval card', 'MODEL_DID_NOT_PROPOSE', { outcome: t?.outcome, text: t?.text })
  }

  // ---- Tool boundaries (real model)
  t = await sendNexo('Send a message to my team saying the build is green.')
  rec('messaging request (no messaging service)', 'OBSERVED', { outcome: t?.outcome, text: t?.text, trace: t?.trace, claims_sent: /\b(i(?:'ve| have)? sent|message (?:was|has been) sent)\b/i.test(t?.text ?? '') })
  t = await sendNexo('Run this Python code: print(2+2)')
  rec('python execution request (no sandbox)', 'OBSERVED', { outcome: t?.outcome, text: t?.text, trace: t?.trace })

  // ---- Repetition: current config vs Qwen's recommended non-thinking sampling, straight to llama-server :8766
  const SYS = 'You are the assistant inside NEXUS. State which tool you would use and with what arguments; report outcomes only from actual tool results. Your name is NEXO.'
  const prompts = {
    fact: [{ role: 'user', content: 'At what temperature does water boil at sea level?' }],
    plan: [{ role: 'user', content: 'Give me a short 3-step plan to prepare for my exams next week.' }],
    chat: [{ role: 'user', content: 'Hi! Who are you and what can you help me with?' }],
    explain: [{ role: 'user', content: 'Explain in a few sentences how a hash map works.' }],
    search_answer: [
      { role: 'user', content: 'search good movies for me' },
      { role: 'assistant', content: '', tool_calls: [{ type: 'function', function: { name: 'web_search', arguments: { query: 'good movies' } } }] },
      { role: 'tool', name: 'web_search', content: JSON.stringify({ ok: true, data: { query: 'good movies', provider: 'wikipedia', coverage: 'Wikipedia articles only, not the general web', results: [{ title: 'Cult film', url: 'https://en.wikipedia.org/wiki/Cult_film' }, { title: 'The Good, the Bad and the Ugly', url: 'https://en.wikipedia.org/wiki/The_Good,_the_Bad_and_the_Ugly' }], truncated: false } }) },
    ],
  }
  const configs = {
    current_greedy_dry08: { temperature: 0, top_k: 1, dry_multiplier: 0.8 },
    qwen_recommended_nonthinking_seed1: { temperature: 0.7, top_p: 0.8, top_k: 20, min_p: 0, dry_multiplier: 0.8, seed: 1 },
    qwen_recommended_nonthinking_seed2: { temperature: 0.7, top_p: 0.8, top_k: 20, min_p: 0, dry_multiplier: 0.8, seed: 2 },
  }
  const rep = { endpoint: 'llama-server :8766 /apply-template + /completion (same path as serve_jarvis.py)', note: 'research comparison only; runtime config not changed', runs: {} }
  for (const [cname, cfg] of Object.entries(configs)) {
    for (const [pname, msgs] of Object.entries(prompts)) {
      const tpl = await (await fetch('http://127.0.0.1:8766/apply-template', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages: [{ role: 'system', content: SYS }, ...msgs], chat_template_kwargs: { enable_thinking: false } }) })).json()
      const s = Date.now()
      const c = await (await fetch('http://127.0.0.1:8766/completion', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: tpl.prompt, n_predict: 256, cache_prompt: true, stop: ['<|im_end|>'], ...cfg }) })).json()
      rep.runs[`${cname}|${pname}`] = { text: c.content, secs: (Date.now() - s) / 1000, adjacent_repeats: adjacent(c.content), stop: c.stop_type ?? c.stopped_eos }
    }
  }
  const sum = {}
  for (const k of Object.keys(configs)) sum[k] = Object.entries(rep.runs).filter(([n]) => n.startsWith(k + '|')).reduce((a, [, v]) => a + v.adjacent_repeats.length, 0)
  rep.adjacent_repeat_totals = sum
  out.repetition = rep
  rec('repetition comparison recorded', 'DONE', sum)

  // ---- Unload
  await page.goto(UI + '/nexo')
  await page.getByRole('button', { name: /Turn NEXO off/ }).click()
  await page.getByText('NEXO model is off').waitFor({ timeout: 120_000 })
  await page.waitForTimeout(3000)
  memory('after unload')
  const llamaGone = out.memory.at(-1)?.llama_server_processes === 0
  const routeAfter = await api('POST', '/ml/route', token, { text: 'show me my open tasks' })
  rec('Turn NEXO off frees the model; DeBERTa still answers', llamaGone && routeAfter.status === 200 ? 'PASS' : 'FAIL', { memory: out.memory.at(-1), route: routeAfter.status })
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
