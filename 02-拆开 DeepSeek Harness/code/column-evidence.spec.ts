// Original integration probes for the column. MIT.
// Copy beside upstream mock-adapter.ts with run-evidence.sh.
import { mkdtemp, mkdir, readFile, writeFile, rm, copyFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { spawnSync } from 'node:child_process'
import { expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import Loader from '@deepseek-ai/cordis-plugin-loader'
import Include from '@deepseek-ai/cordis-plugin-include'
import Group from '@deepseek-ai/cordis-plugin-group'
import { PluginPackages } from '@deepseek-ai/dsh-app-boot'
import LlmRuntime, { createUserMessage } from '@deepseek-ai/dsh-llm'
import SessionStore, { SessionId } from '@deepseek-ai/dsh-session'
import SessionProjectionRegistry from '@deepseek-ai/dsh-session-projection'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { defineContentToolFixture } from '@deepseek-ai/dsh-tools'
import AgentRegistry, { assembleContextFor } from '@deepseek-ai/dsh-agent'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import AgentPresets from '@deepseek-ai/dsh-agent-presets'
import { MockAdapter, textResponse, toolCallResponse } from './mock-adapter.ts'

async function harness(adapter: MockAdapter) {
  const ctx = new Context()
  await ctx.plugin(LlmRuntime)
  await ctx.plugin(SessionStore)
  await ctx.plugin(SessionProjectionRegistry)
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  await ctx.plugin(AgentRegistry)
  await ctx.plugin(AgentLoop, { agents: [] })
  ctx.llm.registerAdapter(['mock'], adapter)
  return ctx
}

async function evidence(name: string, value: unknown) {
  const dir = process.env['DSH_COLUMN_EVIDENCE_DIR']
  if (!dir) return
  await mkdir(dir, { recursive: true })
  await writeFile(join(dir, name), JSON.stringify(value, null, 2) + '\n')
}

it('traces a scripted read-edit-test task through the real loop and real filesystem', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dsh-column-bug-'))
  const file = join(dir, 'price.mjs')
  const before = 'export const total = (price, count) => price + count;\n'
  const after = 'export const total = (price, count) => price * count;\n'
  await writeFile(file, before)
  const adapter = new MockAdapter([
    toolCallResponse('read-1', 'column_read', {}),
    toolCallResponse('edit-1', 'column_edit', {}),
    toolCallResponse('test-1', 'column_test', {}),
    textResponse('修复已完成，测试通过。'),
  ])
  const ctx = await harness(adapter)
  const verify = () => spawnSync(process.execPath, ['--input-type=module', '-e',
    `import assert from 'node:assert/strict'; import { total } from ${JSON.stringify(pathToFileURL(file).href)}; assert.equal(total(12, 3), 36);`], { encoding: 'utf8' })
  const baseline = verify()
  expect(baseline.status).toBe(1)
  const content = (text: string) => [{ type: 'text' as const, text }]
  ctx.tools.register(defineContentToolFixture({ name: 'column_read', description: '读取隔离测试文件', parameters: {}, execute: async () => content(await readFile(file, 'utf8')) }))
  ctx.tools.register(defineContentToolFixture({ name: 'column_edit', description: '应用预先给定的修复', parameters: {}, execute: async () => { await writeFile(file, after); return content('written') } }))
  let exitCode: number | null = null
  ctx.tools.register(defineContentToolFixture({ name: 'column_test', description: '用独立 Node 进程验证结果', parameters: {}, execute: async () => { exitCode = verify().status; return content(`exit=${exitCode}`) } }))
  try {
    const handle = await ctx.agents.create({ sessionId: SessionId('column-bug-trace'), agentOptions: { provider: 'mock', model: 'mock' } })
    const agent = handle.agent
    agent.followup(createUserMessage({ content: content('帮我修复 total 的乘法错误，并运行测试。'), source: { kind: 'user' } }))
    // This isolated test owns the full run and admits no other messages.
    await agent.whenIdle()
    const events = agent.session.snapshotEvents()
    const counts = Object.fromEntries(['turn/start', 'step/start', 'tool/call', 'tool/result', 'turn/end'].map(type => [type, events.filter(e => e.type === type).length]))
    expect(counts).toEqual({ 'turn/start': 1, 'step/start': 4, 'tool/call': 3, 'tool/result': 3, 'turn/end': 1 })
    expect(adapter.requests).toHaveLength(4)
    expect(await readFile(file, 'utf8')).toBe(after)
    expect(exitCode).toBe(0)
    const roles = adapter.requests.map(request => request.messages.map(message => message.role))
    await evidence('task-trace.json', {
      model: 'scripted MockAdapter; no real model invocation',
      tools: 'column-owned deterministic tools; real DSH registry, loop, log, filesystem and child Node assertion',
      counts, modelRequests: adapter.requests.length, baselineExitCode: baseline.status, fixedExitCode: exitCode,
      before, after, requestRoles: roles, events,
    })
    await handle.dispose()
  } finally {
    await ctx.fiber.dispose()
    await rm(dir, { recursive: true, force: true })
  }
})

it('mounts a custom preset, isolates it, and releases standing contributions with its roster', async () => {
  const assets = process.env['DSH_COLUMN_ASSETS_DIR']
  if (!assets) throw new Error('Set DSH_COLUMN_ASSETS_DIR to the column lab directory')
  const dir = await mkdtemp(join(tmpdir(), 'dsh-column-preset-'))
  const preset = join(dir, 'evidence-review')
  await mkdir(preset)
  const plugin = join(preset, 'review-plugin.mjs')
  await copyFile(join(assets, 'review-plugin.mjs'), plugin)
  await copyFile(join(assets, 'agent.cordis.yml'), join(preset, 'agent.cordis.yml'))
  await copyFile(join(assets, 'preset.yml'), join(preset, 'preset.yml'))
  const adapter = new MockAdapter([toolCallResponse('check-1', 'review_checklist', {}), textResponse('已取得审查清单。')])
  const ctx = await harness(adapter)
  try {
    ctx.baseUrl = pathToFileURL(dir).href + '/'
    await ctx.plugin(Loader)
    await ctx.plugin(PluginPackages)
    ctx.loader.builtins.include = Include
    ctx.loader.builtins.group = Group
    const rosterFiber = await ctx.plugin(AgentPresets, { default: 'evidence-review', roots: [{ path: dir, trust: 'user' }], includeShippedRoot: false, includeUserRoot: false })
    const review = await ctx.agents.create({ sessionId: SessionId('column-review'), agentOptions: { provider: 'mock', model: 'mock' }, setup: async agentCtx => { await ctx.agentPresets.mount(agentCtx, 'evidence-review') } })
    const plain = await ctx.agents.create({ sessionId: SessionId('column-plain'), agentOptions: { provider: 'mock', model: 'mock' } })
    const reviewTools = ctx.tools.schemas(review.agent).map(x => x.name)
    const plainTools = ctx.tools.schemas(plain.agent).map(x => x.name)
    expect(reviewTools).toContain('review_checklist')
    expect(plainTools).not.toContain('review_checklist')
    const assembly = await ctx.systemPrompt.assemble(assembleContextFor(review.agent))
    expect(assembly.sections.some(x => x.name === 'column:review-contract')).toBe(true)
    review.agent.followup(createUserMessage({ content: [{ type: 'text', text: '取得审查清单。' }], source: { kind: 'user' } }))
    await review.agent.whenIdle()
    expect(adapter.requests).toHaveLength(2)
    const result = review.agent.session.snapshotEvents().find(e => e.type === 'tool/result')
    expect(result).toBeDefined()
    expect(JSON.stringify(result)).toContain('位置 → 触发条件 → 可复现步骤 → 实际结果 → 影响 → 修复验证')
    await review.dispose()
    expect(ctx.agents.get(SessionId('column-review'))).toBeUndefined()
    // A preset's standing generation belongs to the roster, not one joined Agent.
    const standingSurvivesAgent = ctx.tools.get('review_checklist', review.agent) !== undefined
    expect(standingSurvivesAgent).toBe(true)
    expect(ctx.tools.schemas(plain.agent).map(x => x.name)).not.toContain('review_checklist')
    await rosterFiber.dispose()
    const toolAfterDispose = ctx.tools.get('review_checklist', review.agent)
    expect(toolAfterDispose).toBeUndefined()
    const after = await ctx.systemPrompt.assemble(assembleContextFor(review.agent))
    expect(after.sections.some(x => x.name === 'column:review-contract')).toBe(false)
    await evidence('preset-trace.json', { model: 'scripted MockAdapter; no real model invocation', reviewTools, plainTools, requestCount: adapter.requests.length, result, standingSurvivesAgent, toolRemovedAfterRosterDispose: true, promptRemovedAfterRosterDispose: true })
    await plain.dispose()
  } finally {
    await ctx.fiber.dispose()
    await rm(dir, { recursive: true, force: true })
  }
})
