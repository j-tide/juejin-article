import { quote } from './pricing.mjs';

export function route(path, body, activeRule) {
  if (path !== '/checkout/quote') return { code: 'NOT_FOUND' };
  return quote(body, activeRule);
}
