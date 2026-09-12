import { createClient } from '@supabase/supabase-js';

// api/process.js - Vercel Serverless Function (Multi-Bot Isolated Verification)
export default async function handler(req, res) {
  // 1. CORS Headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({
      status: 'failed',
      message: 'Method Not Allowed. POST required.'
    });
  }

  try {
    const data = req.body || {};
    const {
      user_id,
      device_id,
      botusername,
      bot_hash,
      webhook,
      user_agent,
      platform,
      language,
      timezone,
      hardware_concurrency,
      device_memory,
      screen_resolution
    } = data;

    // Normalizing bot handle (removes @ and spaces if any)
    let botUser = (botusername || req.query.botusername || '').trim();
    if (botUser.startsWith('@')) {
      botUser = botUser.substring(1);
    }

    const hash = (bot_hash || req.query.hash || '').trim();
    const webhookUrl = (webhook || req.query.webhook || '').trim();

    // Client IP Detection
    const clientIp = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || 
                     req.headers['x-real-ip'] || 
                     req.socket?.remoteAddress || 
                     '0.0.0.0';

    // 2. Mandatory Validations
    if (!user_id || !device_id) {
      return res.status(200).json({
        status: 'failed',
        message: 'Verification criteria not met: Missing User ID or Device ID.'
      });
    }

    if (device_id.length < 8) {
      return res.status(200).json({
        status: 'failed',
        message: 'Verification criteria not met: Invalid fingerprint.'
      });
    }

    // 3. Connect to Supabase
    const supabaseUrl = process.env.SUPABASE_URL || 'https://neeppziqbuwxhmhpifda.supabase.co';
    const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY;

    if (!supabaseKey) {
      return res.status(500).json({
        status: 'failed',
        message: 'Database configuration missing: Set SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY in Vercel.'
      });
    }

    const supabase = createClient(supabaseUrl, supabaseKey);

    // =========================================================================
    // CHECK 1: SAME USER RETURNING ON THE SAME BOT + SAME DEVICE
    // (Already Verified ONLY if this user has already verified ON THIS EXACT BOT)
    // =========================================================================
    let selfQuery = supabase
      .from('bot_device_verifications')
      .select('id')
      .eq('device_id', device_id)
      .eq('user_id', String(user_id));

    if (botUser) {
      selfQuery = selfQuery.or(`bot_username.eq.${botUser},bot_username.eq.@${botUser}`);
    }

    const { data: selfCheck } = await selfQuery.limit(1);

    if (selfCheck && selfCheck.length > 0) {
      await sendWebhook(webhookUrl, {
        status: 'pass',
        message: 'Already Verified',
        fingerprint: device_id,
        botusername: botUser,
        user_id: user_id
      });

      return res.status(200).json({
        status: 'continue',
        message: 'Device already verified on this bot.'
      });
    }

    // =========================================================================
    // CHECK 2: ANTI-CLONE FRAUD (Same device used by ANOTHER account ON THIS BOT)
    // =========================================================================
    let cloneQuery = supabase
      .from('bot_device_verifications')
      .select('user_id')
      .eq('device_id', device_id);

    if (botUser) {
      cloneQuery = cloneQuery.or(`bot_username.eq.${botUser},bot_username.eq.@${botUser}`);
    }

    const { data: cloneCheck } = await cloneQuery.limit(1);

    if (cloneCheck && cloneCheck.length > 0 && String(cloneCheck[0].user_id) !== String(user_id)) {
      await sendWebhook(webhookUrl, {
        status: 'fail',
        message: 'Device already used',
        fingerprint: device_id,
        botusername: botUser,
        user_id: user_id
      });

      return res.status(200).json({
        status: 'attempt',
        message: 'Device already used on another account for this bot.'
      });
    }

    // =========================================================================
    // CHECK 3: FRESH VERIFICATION FOR THIS BOT -> INSERT RECORD
    // =========================================================================
    const { error: insertError } = await supabase
      .from('bot_device_verifications')
      .insert([
        {
          device_id,
          user_id: String(user_id),
          bot_username: botUser,
          bot_hash: hash,
          ip_address: clientIp,
          user_agent: user_agent || '',
          platform: platform || '',
          language: language || '',
          timezone: timezone || '',
          hardware_concurrency: String(hardware_concurrency || ''),
          device_memory: String(device_memory || ''),
          screen_resolution: screen_resolution || ''
        }
      ]);

    if (insertError) {
      console.error('Supabase Insert Error:', insertError);
      return res.status(500).json({
        status: 'failed',
        message: 'Database insert failed: ' + insertError.message
      });
    }

    // Notify Bot Webhook -> Success!
    await sendWebhook(webhookUrl, {
      status: 'pass',
      message: 'Verified Successfully',
      fingerprint: device_id,
      botusername: botUser,
      user_id: user_id
    });

    return res.status(200).json({
      status: 'success',
      message: 'Device verified successfully.'
    });

  } catch (err) {
    console.error('Server error:', err);
    return res.status(500).json({
      status: 'failed',
      message: 'Server internal error: ' + err.message
    });
  }
}

async function sendWebhook(url, payload) {
  if (!url || !url.startsWith('http')) return false;
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Jenil-Dobariya-Verification-Gateway/2.0'
      },
      body: JSON.stringify(payload)
    });
    return resp.ok;
  } catch (e) {
    console.error('Webhook dispatch failed:', e);
    return false;
  }
}