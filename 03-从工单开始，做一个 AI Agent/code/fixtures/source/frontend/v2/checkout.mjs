// Synthetic checkout page logic; no real merchant data or network calls.
import { requestQuote } from './api.mjs';

export async function checkout(cart, send) {
  const result = await requestQuote(cart, send);
  if (result.code === 'PRICE_CHANGED') {
    return { action: 'show_notice', text: '价格已更新，请重新确认' };
  }
  if (result.code !== 'OK') return { action: 'show_error', text: '暂时无法结算' };
  return { action: 'confirm_quote', total_cents: result.total_cents };
}
