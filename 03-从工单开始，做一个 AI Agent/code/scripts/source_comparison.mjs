// Runs only the public synthetic fixtures; the source-reading tool never imports code.
import { checkout as oldCheckout } from '../fixtures/source/frontend/v1/checkout.mjs';
import { checkout as newCheckout } from '../fixtures/source/frontend/v2/checkout.mjs';
import { route as oldRoute } from '../fixtures/source/backend/v1/router.mjs';
import { route as newRoute } from '../fixtures/source/backend/v2/router.mjs';
const cart = { quote_version: 'r1', quoted_total_cents: 3200 };
const rule = { version: 'r2', total_cents: 3200 };
const actual = {};
for (const [name, checkout, route] of [['deployed_v1', oldCheckout, oldRoute], ['main_v2', newCheckout, newRoute]]) {
  const requests = [];
  const ui = await checkout(cart, async (path, body) => {
    const response = route(path, body, rule);
    requests.push({ path, body, response });
    return response;
  });
  actual[name] = { requests, ui };
}
console.log(JSON.stringify({ synthetic: true, model_api_called: false, cart, rule, actual }, null, 2));
