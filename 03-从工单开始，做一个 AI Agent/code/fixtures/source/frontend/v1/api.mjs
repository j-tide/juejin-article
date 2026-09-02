export async function requestQuote(cart, send) {
  return send('/checkout/quote', {
    quote_version: cart.quote_version,
    quoted_total_cents: cart.quoted_total_cents,
  });
}
