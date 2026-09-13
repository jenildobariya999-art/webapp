// api/bot-info.js (Vercel Serverless Function)
// Telegram Bot ka Real Avatar / Profile Photo Fetch karne ke liye
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const { botusername, bot_token } = req.query;

  // 1. Agar bot_token provide kiya gaya hai (via env ya query)
  const token = bot_token || process.env.TELEGRAM_BOT_TOKEN;

  if (token) {
    try {
      // Step A: Bot Info
      const meRes = await fetch(`https://api.telegram.org/bot${token}/getMe`);
      const meData = await meRes.json();
      
      if (meData.ok && meData.result) {
        const botId = meData.result.id;
        const botUser = meData.result.username;
        const botName = meData.result.first_name;

        // Step B: User Profile Photos for Bot
        const photosRes = await fetch(`https://api.telegram.org/bot${token}/getUserProfilePhotos?user_id=${botId}&limit=1`);
        const photosData = await photosRes.json();

        if (photosData.ok && photosData.result && photosData.result.photos && photosData.result.photos.length > 0) {
          const photoArray = photosData.result.photos[0];
          // Sabse best resolution photo pick karein
          const fileId = photoArray[photoArray.length - 1].file_id;

          // Step C: Get File Path
          const fileRes = await fetch(`https://api.telegram.org/bot${token}/getFile?file_id=${fileId}`);
          const fileData = await fileRes.json();

          if (fileData.ok && fileData.result && fileData.result.file_path) {
            const photoUrl = `https://api.telegram.org/file/bot${token}/${fileData.result.file_path}`;
            return res.status(200).json({
              success: true,
              username: botUser,
              name: botName,
              photo_url: photoUrl
            });
          }
        }

        // Agar profile photo set nahi hai, return default info
        return res.status(200).json({
          success: true,
          username: botUser,
          name: botName,
          photo_url: null
        });
      }
    } catch (err) {
      console.error('Telegram Bot API fetch error:', err);
    }
  }

  // 2. Agar token nahi hai ya sirf botusername diya hai:
  // Telegram public page (t.me/botusername) se direct real og:image / profile avatar extract karein!
  if (botusername) {
    const cleanUser = botusername.replace(/^@/, '').trim();
    try {
      const tgPageRes = await fetch(`https://t.me/${cleanUser}`, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
      });
      const html = await tgPageRes.text();

      // Extract og:image
      const ogImageMatch = html.match(/<meta property="og:image" content="([^"]+)"/i);
      const titleMatch = html.match(/<meta property="og:title" content="([^"]+)"/i);

      if (ogImageMatch && ogImageMatch[1] && !ogImageMatch[1].includes('telegram-logo')) {
        return res.status(200).json({
          success: true,
          username: cleanUser,
          name: titleMatch ? titleMatch[1] : cleanUser,
          photo_url: ogImageMatch[1]
        });
      }
    } catch (e) {
      console.error('t.me scrape error:', e);
    }

    // Direct Telegram CDN image URL fallback
    return res.status(200).json({
      success: true,
      username: cleanUser,
      name: cleanUser,
      photo_url: `https://t.me/i/userpic/320/${cleanUser}.jpg`
    });
  }

  return res.status(400).json({
    success: false,
    message: 'Please provide botusername or set TELEGRAM_BOT_TOKEN'
  });
}
