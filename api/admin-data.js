/**
 * api/admin-data.js
 * Admin endpoint for data.html - lists and deletes verification records.
 * Protected by a shared secret (set ADMIN_API_KEY in your Vercel env vars).
 */
import { createClient } from '@supabase/supabase-js';

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://neeppziqbuwxhmhpifda.supabase.co';
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY || '';
const ADMIN_API_KEY = process.env.ADMIN_API_KEY || '';

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Admin-Key');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const key = req.headers['x-admin-key'] || (req.query && req.query.key) || (req.body && req.body.key);
  if (!ADMIN_API_KEY || key !== ADMIN_API_KEY) {
    return res.status(401).json({ ok: false, error: 'Unauthorized' });
  }

  try {
    if (req.method === 'GET') {
      const bot = req.query.bot;
      const limit = Math.min(Number(req.query.limit) || 500, 2000);

      let query = supabase
        .from('bot_device_verifications')
        .select('id, created_at, user_id, bot_username, status, device_id, platform, user_agent, timezone, ip_address, screen_resolution')
        .order('created_at', { ascending: false })
        .limit(limit);

      if (bot) query = query.eq('bot_username', bot);

      const { data, error } = await query;
      if (error) throw error;

      return res.status(200).json({ ok: true, records: data });
    }

    if (req.method === 'DELETE') {
      const { ids, bot, device_id } = req.body || {};

      if (Array.isArray(ids) && ids.length > 0) {
        const { error } = await supabase.from('bot_device_verifications').delete().in('id', ids);
        if (error) throw error;
        return res.status(200).json({ ok: true, deleted: ids.length });
      }

      if (bot && device_id) {
        const { error } = await supabase
          .from('bot_device_verifications')
          .delete()
          .eq('bot_username', bot)
          .eq('device_id', device_id);
        if (error) throw error;
        return res.status(200).json({ ok: true });
      }

      if (bot) {
        const { error } = await supabase.from('bot_device_verifications').delete().eq('bot_username', bot);
        if (error) throw error;
        return res.status(200).json({ ok: true });
      }

      return res.status(400).json({ ok: false, error: 'Nothing to delete - pass ids, bot, or bot+device_id' });
    }

    return res.status(405).json({ ok: false, error: 'Method Not Allowed' });
  } catch (err) {
    console.error('admin-data error:', err);
    return res.status(500).json({ ok: false, error: err.message || 'Internal error' });
  }
}
