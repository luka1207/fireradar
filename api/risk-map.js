/**
 * GET /api/risk-map
 *
 * Kthen snapshot-in aktual të rrezikut për të gjitha zonat e Shqipërisë.
 * E lexon nga Upstash Redis (e populluar nga scripts/update_risk.py
 * që xhiron çdo orë në GitHub Actions).
 *
 * Frontend-i e thërret këtë çdo 60 sekonda (polling) për efekt "live".
 */

export default async function handler(req, res) {
  const UPSTASH_URL = process.env.UPSTASH_REDIS_REST_URL;
  const UPSTASH_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;

  if (!UPSTASH_URL || !UPSTASH_TOKEN) {
    return res.status(500).json({
      error: "Upstash nuk është konfiguruar (mungojnë env variables në Vercel).",
    });
  }

  try {
    const response = await fetch(`${UPSTASH_URL}/get/risk:all`, {
      headers: { Authorization: `Bearer ${UPSTASH_TOKEN}` },
    });

    if (!response.ok) {
      throw new Error(`Upstash u përgjigj me status ${response.status}`);
    }

    const data = await response.json();

    if (!data.result) {
      return res.status(200).json({
        zones: [],
        message: "Asnjë të dhënë akoma — prit ciklin e parë të GitHub Actions.",
      });
    }

    const parsed = JSON.parse(data.result);

    // Cache i shkurtër në edge — ndihmon performancën pa e bërë "jo-live"
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");

    return res.status(200).json(parsed);
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
