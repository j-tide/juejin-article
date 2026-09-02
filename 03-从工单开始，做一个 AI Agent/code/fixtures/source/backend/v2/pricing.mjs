// Synthetic current main: compare amount, not only rule revision.
export function quote(body, activeRule) {
  if (body.quoted_total_cents !== activeRule.total_cents) {
    return { code: 'PRICE_CHANGED', total_cents: activeRule.total_cents };
  }
  return { code: 'OK', total_cents: activeRule.total_cents };
}
