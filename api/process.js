/**
 * Jenil Dobariya • Multi-Bot Telemetry & Verification Processor
 * Route: /api/process
 *
 * Contract (matches script.js exactly):
 *   REQUEST body sent by script.js:
 *     { user_id, bot_hash, device_id, user_agent, platform, timezone,
 *       hardware_concurrency, device_memory, screen_resolution }
 *     - bot_hash is read by script.js from the URL query param `?bot=...`
 *     - device_id is the FingerprintJS visitorId
 *
 *   RESPONSE expected by script.js:
 *     { status: 'success' | 'attempt' | 'failed', message?: string }
 *     - 'success' -> new device, verification passes -> view-success
 *     - 'attempt' -> this user already verified this exact device before -> view-already
 *     - 'failed'  -> device previously verified under a DIFFERENT user (clone) -> view-failed
 */
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://neeppziqbuwxhmhpifda.supabase.co';
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY || '';
const BOT_TOKENS = {
  // Add your telegram bot credentials here or pass dynamic credentials
  default: process.env.TELEGRAM_BOT_TOKEN || ''
};

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

export default async function handler(req, res) {
  // Enable CORS for Telegram WebApp clients
  res.setHeader('Access-Control-Allow-Credentials', true);
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,OPTIONS,PATCH,DELETE,POST,PUT');
  res.setHeader('Access-Control-Allow-Headers', 'X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ status: 'failed', message: 'Method Not Allowed' });
  }

  try {
    const payload = req.body || {};
    const {
      user_id,
      // script.js sends the bot identifier as "bot_hash" (from the ?bot= query
      // param). Some older callers may still send "bot_username" - accept either.
      bot_hash,
      bot_username,
      device_id,
      platform,
      user_agent,
      timezone,
      screen_resolution,
      hardware_concurrency,
      device_memory,
      canvas_hash
    } = payload;

    const botKey = (bot_hash || bot_username || '').toString().replace(/^@/, '').trim();

    if (!user_id || !botKey || !device_id) {
      return res.status(400).json({ status: 'failed', message: 'Missing mandatory identification params' });
    }

    const clientIp = req.headers['x-forwarded-for'] || req.socket.remoteAddress || '127.0.0.1';

    // 1. Check for existing verifications of this device under this bot scope
    const { data: existingRecords, error: checkError } = await supabase
      .from('bot_device_verifications')
      .select('id, user_id, device_id, status')
      .eq('bot_username', botKey)
      .eq('device_id', device_id);

    if (checkError) {
      console.error('Supabase lookup error:', checkError);
    }

    // Decide the outcome BEFORE inserting, based on prior records:
    //   - no prior record for this device                -> 'success'  (new, clean device)
    //   - prior record(s), all belonging to this user     -> 'attempt'  (already verified)
    //   - prior record belonging to a DIFFERENT user       -> 'failed'   (clone attempt, blocked)
    let outcome = 'success';
    let failMessage = null;

    if (existingRecords && existingRecords.length > 0) {
      const clone = existingRecords.find(r => String(r.user_id) !== String(user_id));
      if (clone) {
        outcome = 'failed';
        failMessage = `This device was already verified under a different Telegram account.`;
      } else {
        outcome = 'attempt';
      }
    }

    // 2. Telemetry Insertion (log every attempt, regardless of outcome)
    // status column stores the exact same value we send back to the client
    // ('success' | 'attempt' | 'failed') - avoids any mismatch with a
    // pre-existing CHECK constraint on this column.
    const insertPayload = {
      user_id: String(user_id),
      bot_username: botKey,
      status: outcome,
      device_id,
      ip_address: String(clientIp).split(',')[0].trim(),
      platform: platform || 'Unknown Platform',
      user_agent: user_agent || req.headers['user-agent'] || '',
      timezone: timezone || 'UTC',
      screen_resolution: screen_resolution || '0x0',
      hardware_concurrency: Number(hardware_concurrency) || 4,
      device_memory: Number(device_memory) || 8,
      canvas_hash: canvas_hash || null,
      bot_hash: botKey,
      details: {
        timestamp: new Date().toISOString(),
        action: outcome === 'success' ? 'APPROVED' : outcome === 'attempt' ? 'DUPLICATE' : 'DENIED_FRAUD',
        reason: failMessage,
        headers: {
          cf_ray: req.headers['cf-ray'] || null,
          user_lang: req.headers['accept-language'] || null
        }
      }
    };

    const { data: inserted, error: insertError } = await supabase
      .from('bot_device_verifications')
      .insert([insertPayload])
      .select()
      .single();

    if (insertError) {
      console.error('Supabase insert error:', JSON.stringify(insertError));
      // Surface the real reason instead of a generic message so it's
      // diagnosable from the rendered page, not just server logs.
      return res.status(200).json({
        status: 'failed',
        message: `DB insert failed: ${insertError.message || insertError.code || 'unknown error'}`
      });
    }

    // 3. Optional Telegram Notification Callback (only on a fresh pass)
    const botToken = BOT_TOKENS[botKey] || BOT_TOKENS.default;
    if (botToken && outcome === 'success') {
      try {
        await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: user_id,
            text: `✅ *Verification Passed*\nDevice clearance authenticated for @${botKey}.`,
            parse_mode: 'Markdown'
          })
        });
      } catch (tgErr) {
        console.warn('Telegram notify error:', tgErr.message);
      }
    }

    // Response shape matches exactly what script.js checks:
    // data.status === 'success' | 'attempt' | 'failed', and data.message on failure.
    const responseBody = { status: outcome, record_id: inserted.id };
    if (outcome === 'failed') {
      responseBody.message = failMessage || 'Verification criteria not met.';
    }

    return res.status(200).json(responseBody);

  } catch (err) {
    console.error('Processing engine exception:', err);
    // Still respond 200 with the status shape script.js understands (a 500
    // status is fine for logs, but keep the JSON body readable either way),
    // and include the real error text so it's visible on-screen for debugging.
    return res.status(200).json({
      status: 'failed',
      message: (err && err.message) ? `Error: ${err.message}` : 'Internal Verification Error'
    });
  }
}
