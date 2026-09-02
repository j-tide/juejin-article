// Synthetic old deployment: every rule revision asks for reconfirmation.
export function quote(body, activeRule) {
  if (body.quote_version !== activeRule.version) {
    return { code: 'PRICE_CHANGED', total_cents: activeRule.total_cents };
  }
  return { code: 'OK', total_cents: activeRule.total_cents };
}
