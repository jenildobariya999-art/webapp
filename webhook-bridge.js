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
          // Fire-and-forget: mode 'no-cors' avoids CORS blocking this
          // notification (we don't need to read the webhook's reply).
          originalFetch(webhookUrl, {
            method: 'POST',
            mode: 'no-cors',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
          }).catch(function (err) {
            console.warn('webhook-bridge: failed to notify bot webhook', err);
          });
        }).catch(function (err) {
          console.warn('webhook-bridge: could not read response to forward', err);
        });
      } catch (err) {
        console.warn('webhook-bridge: unexpected error', err);
      }
    }

    return response;
  };
})();
