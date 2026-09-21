/**
 * Jenil Dobariya • Multi-Bot Telemetry & Verification Processor
 * Route: /api/process (Rewritten from process.php via vercel.json)
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
    return res.status(405).json({ ok: false, error: 'Method Not Allowed' });
  }

  try {
    const payload = req.body || {};
    const {
      user_id,
      bot_username,
      device_id,
      platform,
      user_agent,
      timezone,
      screen_resolution,
      hardware_concurrency,
      device_memory,
      canvas_hash,
      bot_hash
    } = payload;

    if (!user_id || !bot_username || !device_id) {
      return res.status(400).json({ ok: false, error: 'Missing mandatory identification params' });
    }

    const cleanBot = bot_username.replace(/^@/, '').trim();
    const clientIp = req.headers['x-forwarded-for'] || req.socket.remoteAddress || '127.0.0.1';

    // 1. Check for Duplicate Device ID under this isolated bot scope
    const { data: existingRecords, error: checkError } = await supabase
      .from('bot_device_verifications')
      .select('id, user_id, device_id, status')
      .eq('bot_username', cleanBot)
      .eq('device_id', device_id);

    if (checkError) {
      console.error('Supabase lookup error:', checkError);
    }

    let verdict = 'PASSED';
    let blockReason = null;

    if (existingRecords && existingRecords.length > 0) {
      // Find if an identical device was registered under a DIFFERENT user ID
      const priorUser = existingRecords.find(r => String(r.user_id) !== String(user_id));
      if (priorUser) {
        verdict = 'BLOCKED_CLONE_ATTEMPT';
        blockReason = `Same device fingerprint previously verified by Telegram ID: ${priorUser.user_id}`;
      }
    }

    // 2. Telemetry Insertion
    const insertPayload = {
      user_id: String(user_id),
      bot_username: cleanBot,
      status: verdict,
      device_id,
      ip_address: String(clientIp).split(',')[0].trim(),
      platform: platform || 'Unknown Platform',
      user_agent: user_agent || req.headers['user-agent'] || '',
      timezone: timezone || 'UTC',
      screen_resolution: screen_resolution || '0x0',
      hardware_concurrency: Number(hardware_concurrency) || 4,
      device_memory: Number(device_memory) || 8,
      canvas_hash: canvas_hash || null,
      bot_hash: bot_hash || null,
      action: verdict === 'PASSED' ? 'APPROVED' : 'DENIED_FRAUD',
      reason: blockReason,
      details: {
        timestamp: new Date().toISOString(),
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
      throw insertError;
    }

    // 3. Optional Telegram Notification Callback
    const botToken = BOT_TOKENS[cleanBot] || BOT_TOKENS.default;
    if (botToken && verdict === 'PASSED') {
      try {
        await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: user_id,
            text: `✅ *Verification Passed*\nDevice clearance authenticated for @${cleanBot}.`,
            parse_mode: 'Markdown'
          })
        });
      } catch (tgErr) {
        console.warn('Telegram notify error:', tgErr.message);
      }
    }

    return res.status(200).json({
      ok: true,
      verified: verdict === 'PASSED',
      status: verdict,
      record_id: inserted.id,
      message: verdict === 'PASSED' ? 'Verification successfully completed.' : 'Verification rejected: Fraudulent multi-account clone attempt detected.'
    });

  } catch (err) {
    console.error('Processing engine exception:', err);
    return res.status(500).json({ ok: false, error: err.message || 'Internal Verification Error' });
  }
}
