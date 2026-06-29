import type { Recipe } from '../types.ts';

/**
 * brains-port: Claude Max CLI bridge.
 *
 * Points GBrain's openai-compatible client at a local shim
 * (`sidecars/max-bridge/server.py`) that wraps `claude-agent-sdk`, so deep
 * synthesis runs on the flat-fee Claude **Max** plan (CLI-inherited auth)
 * instead of a metered Anthropic API key.
 *
 * Scope: **chat/reasoning only, text-only**. The bridge calls the SDK with
 * `allowed_tools=[]`, so `supports_tools: false` — the gateway must never send
 * tool definitions here. Tool-calling / subagent loops stay on Ollama or the
 * native `anthropic` recipe. No embedding touchpoint: embeds go to Ollama
 * (bge-m3); the Max plan is never billed for embeddings.
 */
export const maxBridge: Recipe = {
  id: 'max-bridge',
  name: 'Claude Max (local CLI bridge)',
  tier: 'openai-compat',
  implementation: 'openai-compatible',
  base_url_default: 'http://127.0.0.1:8789/v1',
  auth_env: {
    required: [], // local shim, unauthenticated; resolveAuth supplies a dummy token
    optional: ['MAXBRIDGE_BASE_URL'],
    setup_url: 'https://github.com/awvmeijer/gbrain/blob/brains-port/sidecars/max-bridge/server.py',
  },
  // The OpenAI-compatible SDK requires *some* api key; the bridge ignores it.
  resolveAuth() {
    return { headerName: 'Authorization', token: 'Bearer local-max-bridge' };
  },
  touchpoints: {
    chat: {
      models: ['claude-sonnet', 'claude-opus', 'claude-haiku'],
      supports_tools: false, // bridge is text-only (SDK allowed_tools=[])
      supports_subagent_loop: false,
      supports_prompt_cache: true,
      max_context_tokens: 200000,
      cost_per_1m_input_usd: 0, // flat-fee Max plan; no metered cost
      cost_per_1m_output_usd: 0,
      price_last_verified: '2026-06-29',
    },
  },
  // Local-server readiness probe (doctor + wizard). Short timeout; the gateway
  // wraps probes in its own 200ms allSettled guard.
  async probe(baseURL?: string) {
    const url = (baseURL || maxBridge.base_url_default || '').replace(/\/$/, '') + '/models';
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 1500);
      const res = await fetch(url, { signal: ctrl.signal });
      clearTimeout(t);
      if (res.ok) return { ready: true };
      return { ready: false, hint: `max-bridge responded ${res.status} at ${url}` };
    } catch {
      return {
        ready: false,
        hint: `max-bridge not reachable at ${url}. Start it: ~/brain/.venv/bin/python sidecars/max-bridge/server.py`,
      };
    }
  },
  setup_hint:
    'Start the bridge (`~/brain/.venv/bin/python sidecars/max-bridge/server.py`), then route reasoning to it: `gbrain config set chat_model max-bridge:claude-sonnet`.',
};
