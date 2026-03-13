export default function handler(req, res) {

  const uid = req.query.uid
  const ip = req.headers['x-forwarded-for'] || req.socket.remoteAddress
  const userAgent = req.headers['user-agent']

  if (!uid) {
    res.status(400).send("User ID missing")
    return
  }

  const fingerprint = ip + "_" + userAgent

  res.status(200).json({
    status: "verified",
    user: uid,
    device: fingerprint
  })

}
