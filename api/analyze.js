// /api/analyze — on-demand AI analysis, button-triggered rather than daily.
//
//   GET  /api/analyze?kind=performance&days=180   -> cached analysis (public read)
//   POST /api/analyze?kind=performance&days=180   -> generate (AUTH REQUIRED)
//        &force=1                                  -> ignore the cooldown
//
// Auth on POST is not optional: the pages are public and every generation bills
// the Qwen API, so an unprotected button lets anyone drain the quota. Reads stay
// public so visitors still see the latest analysis.
//
// EXCEPTION: kind=body (weight + blood pressure) is private on EVERY method,
// including GET — its cached analysis would otherwise be publicly readable.
import { query } from './_lib/db.js';
import { requireAuth, sendJson } from './_lib/http.js';
import { buildAnalysisInput, fingerprint } from './_lib/analysis-input.js';
import { performancePrompt, analyticsPrompt } from './_lib/analysis-prompts.js';
import { buildCoachInput } from './_lib/coach-input.js';
import { coachPrompt } from './_lib/coach-prompt.js';
import { buildBodyInput } from './_lib/body-input.js';
import { bodyPrompt } from './_lib/body-prompt.js';

const KINDS = { performance: performancePrompt, analytics: analyticsPrompt, coach: coachPrompt, body: bodyPrompt };
// The AI Coach reasons over monthly running stats too, so it has its own input.
const buildInput = (kind, days) => {
  if (kind === 'body') return buildBodyInput();
  return kind === 'coach' ? buildCoachInput() : buildAnalysisInput(days);
};
// body input carries its own fingerprint; the others share the performance one.
const fingerprintOf = (kind, input) => (kind === 'body' ? input.fingerprint : fingerprint(input));
const hasData = (kind, input) =>
  kind === 'body' ? Boolean(input.has_data) : Boolean(input.running_economy || Object.keys(input.metrics || {}).length);
const COOLDOWN_MINUTES = 10;
const QWEN_ENDPOINT = process.env.QWEN_ENDPOINT
  || 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions';
const QWEN_MODEL = process.env.QWEN_MODEL || 'qwen-plus';

function periodKey(days) {
  return days > 0 ? `${days}d` : 'all';
}

function parseDays(raw) {
  const n = parseInt(raw || '180', 10);
  if (!Number.isFinite(n) || n < 0) return 180;
  return Math.min(n, 3650);
}

async function callQwen(prompt, apiKey) {
  const res = await fetch(QWEN_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: QWEN_MODEL,
      messages: [
        { role: 'system', content: 'You are a JSON-only API. Return only valid JSON, no markdown, no code fences.' },
        { role: 'user', content: prompt },
      ],
      temperature: 0.6,
      max_tokens: 4000,
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Qwen ${res.status}: ${body.slice(0, 300)}`);
  }
  const json = await res.json();
  let content = (json.choices?.[0]?.message?.content || '').trim();
  // Models still fence occasionally despite the instruction.
  if (content.startsWith('```')) content = content.replace(/^```[a-z]*\n?/i, '').replace(/```$/, '').trim();
  try {
    return JSON.parse(content);
  } catch {
    throw new Error(`Model did not return valid JSON: ${content.slice(0, 200)}`);
  }
}

async function readCached(kind, key) {
  const { rows } = await query(
    `SELECT kind, period_key, generated_at, model, inputs_fingerprint, payload
       FROM ai_analyses WHERE kind = $1 AND period_key = $2`,
    [kind, key]
  );
  if (!rows.length) return null;
  const r = rows[0];
  return {
    kind: r.kind,
    period: r.period_key,
    generated_at: r.generated_at instanceof Date ? r.generated_at.toISOString() : r.generated_at,
    model: r.model,
    inputs_fingerprint: r.inputs_fingerprint,
    analysis: r.payload,
  };
}

export default async function handler(req, res) {
  const url = new URL(req.url, 'http://x');
  const kind = (url.searchParams.get('kind') || 'performance').toLowerCase();
  const days = parseDays(url.searchParams.get('days'));
  // The body analysis is not period-scoped: one cached row, always the latest data.
  const key = kind === 'body' ? 'current' : periodKey(days);

  if (!KINDS[kind]) {
    return sendJson(res, 400, { error: 'unknown kind', allowed: Object.keys(KINDS) });
  }
  if (kind === 'body' && !requireAuth(req, res)) return;

  try {
    if (req.method === 'GET') {
      const cached = await readCached(kind, key);
      if (!cached) return sendJson(res, 200, { kind, period: key, analysis: null });
      // Tell the UI whether the underlying data moved since this was written.
      let stale = false;
      try {
        const input = await buildInput(kind, days);
        stale = fingerprintOf(kind, input) !== cached.inputs_fingerprint;
      } catch { /* staleness is advisory only */ }
      return sendJson(res, 200, { ...cached, stale });
    }

    if (req.method !== 'POST') return sendJson(res, 405, { error: 'method not allowed' });
    if (!requireAuth(req, res)) return;

    const apiKey = (process.env.QWEN_API_KEY || '').trim();
    if (!apiKey) {
      return sendJson(res, 503, {
        error: 'qwen_not_configured',
        detail: 'QWEN_API_KEY is not set on this deployment. Add it in the Vercel project settings.',
      });
    }

    const force = url.searchParams.get('force') === '1';
    const existing = await readCached(kind, key);
    if (existing && !force) {
      const ageMin = (Date.now() - new Date(existing.generated_at).getTime()) / 60000;
      if (ageMin < COOLDOWN_MINUTES) {
        return sendJson(res, 200, {
          ...existing, cached: true,
          cooldown_remaining_minutes: Math.ceil(COOLDOWN_MINUTES - ageMin),
        });
      }
    }

    const input = await buildInput(kind, days);
    if (!hasData(kind, input)) {
      return sendJson(res, 200, { kind, period: key, analysis: null, detail: 'No data in this period.' });
    }

    const analysis = await callQwen(KINDS[kind](input), apiKey);
    const fp = fingerprintOf(kind, input);

    await query(
      `INSERT INTO ai_analyses (kind, period_key, generated_at, model, inputs_fingerprint, payload)
       VALUES ($1, $2, now(), $3, $4, $5::jsonb)
       ON CONFLICT (kind, period_key) DO UPDATE SET
         generated_at = now(), model = EXCLUDED.model,
         inputs_fingerprint = EXCLUDED.inputs_fingerprint, payload = EXCLUDED.payload`,
      [kind, key, QWEN_MODEL, fp, JSON.stringify(analysis)]
    );

    return sendJson(res, 200, {
      kind, period: key, generated_at: new Date().toISOString(),
      model: QWEN_MODEL, inputs_fingerprint: fp, analysis, stale: false,
    });
  } catch (err) {
    return sendJson(res, 500, { error: 'server_error', detail: String((err && err.message) || err) });
  }
}
