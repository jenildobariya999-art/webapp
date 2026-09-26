/**
 * webhook-bridge.js
 *
 * script.js never includes the page's ?webhook=... URL in its request to
 * api/process.js, so the server has no way to notify the bot. This script
 * must be loaded BEFORE script.js. It transparently wraps window.fetch:
 * every call behaves exactly as before, EXCEPT the call to api/process.js
 * has the page's webhook URL injected into its JSON body, so process.js
 * (server-side, no CORS restrictions) can POST the result to the bot itself.
 *
 * No other changes to script.js are required.
 */
(function () {
  const originalFetch = window.fetch;
  const params = new URLSearchParams(window.location.search);
  const webhookUrl = params.get('webhook');

  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';

    if (webhookUrl && url.indexOf('api/process.js') !== -1 && init && typeof init.body === 'string') {
      try {
        const bodyObj = JSON.parse(init.body);
        bodyObj.webhook = webhookUrl;
        init = Object.assign({}, init, { body: JSON.stringify(bodyObj) });
      } catch (err) {
        console.error('webhook-bridge: could not inject webhook URL into request', err);
      }
    }

    return originalFetch(input, init);
  };
})();
