// Original example for the column. MIT.
// Prompt guidance is not a permission boundary; tool visibility is scoped by the preset.
export const name = 'column-review-checklist'
export const inject = ['tools', 'systemPrompt']

export function apply(ctx) {
  ctx.effect(() => ctx.systemPrompt.section({
    name: 'column:review-contract',
    order: 20,
    text: '审查代码时先收集证据，输出位置、复现条件和影响。不要把推测写成已验证的结果。',
  }))
  ctx.effect(() => ctx.tools.register({
    name: 'review_checklist',
    description: '返回审查交付前需要逐项核对的证据清单。',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    execute: async () => '位置 → 触发条件 → 可复现步骤 → 实际结果 → 影响 → 修复验证',
  }))
}
