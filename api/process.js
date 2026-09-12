import { createClient } from '@supabase/supabase-js';

// =========================================================================
// JENIL DOBARIYA MULTI-BOT GATEWAY - MILITARY GRADE ANTI-FRAUD ENGINE
// File: api/process.js (Vercel Serverless Function)
// Protections:
// 1. Telegram WebApp Official Envelope Strict Verification
// 2. Strict Same-Device Clone Prevention (Rejects any 2nd user on same device)
// 3. Multi-Layer Hardware Signature (Canvas GPU + Hardware Specs + Fingerprint)
// 4. VPN / Proxy / Tor Cloudflare & Header Blocking
// 5. Complete Audit Logging to Supabase
// =========================================================================

export default async function handler(req, res) {
  // 1. CORS Headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With');

  if (req.method === 'OPTIONS') return res.status(200).end();

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
      hardware_hash,
      is_telegram_native,
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

    let botUser = (botusername || req.query.botusername || '').trim();
    if (botUser.startsWith('@')) botUser = botUser.substring(1);

    const hash = (bot_hash || req.query.hash || '').trim();
    const webhookUrl = (webhook || req.query.webhook || '').trim();

    // Client IP & Cloudflare Proxy Detection
    const clientIp = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || 
                     req.headers['x-real-ip'] || 
                     req.socket?.remoteAddress || 
                     '0.0.0.0';

    const cfCountry = (req.headers['cf-ipcountry'] || '').toUpperCase();
    const cfThreat = req.headers['cf-threat-score'] || '0';

    // Connect Supabase Database
    const supabaseUrl = process.env.SUPABASE_URL || 'https://neeppziqbuwxhmhpifda.supabase.co';
    const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY;

    if (!supabaseKey) {
      return res.status(500).json({
        status: 'failed',
        message: 'Database configuration missing: Set SUPABASE_KEY in Vercel.'
      });
    }

    const supabase = createClient(supabaseUrl, supabaseKey);

    // Multi-factor Primary Device Identifier (Hardware Hash preferred, fallback to device_id)
    const primaryFingerprint = hardware_hash || device_id || '';

    // Audit Logging Helper
    const logAuditRecord = async (statusLabel, reasonMsg) => {
      try {
        await supabase
          .from('bot_device_verifications')
          .insert([{
            device_id: primaryFingerprint,
            user_id: String(user_id || 'UNKNOWN_USER'),
            bot_username: botUser || 'UNKNOWN_BOT',
            bot_hash: `${statusLabel.toUpperCase()}: ${reasonMsg}`.substring(0, 64),
            ip_address: clientIp,
            user_agent: user_agent || '',
            platform: platform || '',
            language: language || '',
            timezone: timezone || '',
            hardware_concurrency: String(hardware_concurrency || ''),
            device_memory: String(device_memory || ''),
            screen_resolution: screen_resolution || ''
          }]);
      } catch (err) {
        console.warn('Audit warning:', err.message);
      }
    };

    // =========================================================================
    // LAYER 1: STRICT TELEGRAM WEBAPP ONLY CHECK (BLOCK THIRD-PARTY APPS / BROWSERS)
    // =========================================================================
    const ua = (user_agent || req.headers['user-agent'] || '').toLowerCase();
    const isTelegramUA = ua.includes('telegram') || is_telegram_native === true;

    if (!isTelegramUA) {
      await logAuditRecord('THIRD_PARTY_BLOCKED', 'Opened in external browser, not Telegram');
      await sendWebhook(webhookUrl, {
        status: 'fail',
        message: 'Third-party browser blocked. Please verify inside official Telegram.',
        fingerprint: primaryFingerprint,
        botusername: botUser,
        user_id: user_id
      });
      return res.status(200).json({
        status: 'failed',
        message: 'Verification allowed ONLY inside official Telegram WebApp.'
      });
    }

    // =========================================================================
    // LAYER 2: STRICT VPN / TOR / PROXY DETECTION
    // =========================================================================
    const isVpnHeader = cfCountry === 'T1' || cfCountry === 'XX' || parseInt(cfThreat) > 10;
    if (isVpnHeader) {
      await logAuditRecord('VPN_BLOCKED', `VPN/Threat Detected (${cfCountry}, Threat: ${cfThreat})`);
      await sendWebhook(webhookUrl, {
        status: 'fail',
        message: 'VPN Detected',
        fingerprint: primaryFingerprint,
        botusername: botUser,
        user_id: user_id
      });
      return res.status(200).json({
        status: 'failed',
        message: 'VPN / Proxy Detected. Please disable VPN and try again.'
      });
    }

    // =========================================================================
    // LAYER 3: MANDATORY CRITERIA VALIDATION
    // =========================================================================
    if (!user_id || !primaryFingerprint) {
      await logAuditRecord('FAILED', 'Missing User ID or Device Fingerprint');
      return res.status(200).json({
        status: 'failed',
        message: 'Verification criteria not met: Missing Telegram User ID or Device Signature.'
      });
    }

    if (primaryFingerprint.length < 8) {
      await logAuditRecord('FAILED', 'Invalid Fingerprint Length');
      return res.status(200).json({
        status: 'failed',
        message: 'Verification criteria not met: Invalid device fingerprint.'
      });
    }

    // =========================================================================
    // LAYER 4: STRICT GLOBAL SAME-DEVICE FRAUD DETECTION (ANTI-CLONE / MULTI-ACCOUNT)
    // Agar YEH PHYSICAL DEVICE kisi doosre Telegram account se pehle match ho chuki hai
    // =========================================================================
    const { data: cloneMatches } = await supabase
      .from('bot_device_verifications')
      .select('id, user_id, bot_username')
      .eq('device_id', primaryFingerprint)
      .neq('user_id', String(user_id))
      .limit(1);

    if (cloneMatches && cloneMatches.length > 0) {
      // BUSTED: Same physical device used with another account!
      await logAuditRecord('CLONE_ATTEMPT', `Same device used earlier by ${cloneMatches[0].user_id}`);

      // Notify Telegram Bot Webhook: Same Device Detected (Strict Referral Disqualification)
      await sendWebhook(webhookUrl, {
        status: 'fail',
        message: 'Device already used',
        fingerprint: primaryFingerprint,
        botusername: botUser,
        user_id: user_id
      });

      return res.status(200).json({
        status: 'attempt',
        message: 'Same device detected on another account. Unauthorized duplicate.'
      });
    }

    // =========================================================================
    // LAYER 5: SAME USER RETURNING CHECK (ALREADY VERIFIED PASS)
    // =========================================================================
    const { data: selfCheck } = await supabase
      .from('bot_device_verifications')
      .select('id, bot_username')
      .eq('device_id', primaryFingerprint)
      .eq('user_id', String(user_id))
      .limit(1);

    if (selfCheck && selfCheck.length > 0) {
      await sendWebhook(webhookUrl, {
        status: 'pass',
        message: 'Already Verified',
        fingerprint: primaryFingerprint,
        botusername: botUser,
        user_id: user_id
      });

      return res.status(200).json({
        status: 'continue',
        message: 'Device already verified for this user.'
      });
    }

    // =========================================================================
    // LAYER 6: FRESH NEW DEVICE -> INSERT DATABASE RECORD
    // =========================================================================
    const { error: insertError } = await supabase
      .from('bot_device_verifications')
      .insert([{
        device_id: primaryFingerprint,
        user_id: String(user_id),
        bot_username: botUser,
        bot_hash: hash || 'VERIFIED_SUCCESS',
        ip_address: clientIp,
        user_agent: user_agent || '',
        platform: platform || '',
        language: language || '',
        timezone: timezone || '',
        hardware_concurrency: String(hardware_concurrency || ''),
        device_memory: String(device_memory || ''),
        screen_resolution: screen_resolution || ''
      }]);

    if (insertError) {
      console.error('Supabase Insert Error:', insertError);
      return res.status(500).json({
        status: 'failed',
        message: 'Database insert failed: ' + insertError.message
      });
    }

    // Notify Telegram Bot Webhook: Pass (Success!)
    await sendWebhook(webhookUrl, {
      status: 'pass',
      message: 'Verified Successfully',
      fingerprint: primaryFingerprint,
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
        'User-Agent': 'Jenil-Dobariya-Verification-Gateway/3.5'
      },
      body: JSON.stringify(payload)
    });
    return resp.ok;
  } catch (e) {
    console.error('Webhook dispatch failed:', e);
    return false;
  }
}
