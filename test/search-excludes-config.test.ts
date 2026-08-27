/**
 * brains-port — retrieval self-poisoning fix: config-plane synthesis-page
 * exclusion (`search.exclude_slug_prefixes`) + recency defaults
 * (`search.recency_default`, `search.recency_decay`).
 *
 * Covers:
 *  1. Pure resolution: resolveEffectiveExcludes (config merge +
 *     --include-synthetic opt-back-in), loadOverridesFromConfig parsing of
 *     the three new keys, resolveRecencyDecayMap's configTriples plane, and
 *     the knobsHash `hx=` fold (port of upstream #2825).
 *  2. PGLite integration: a `digests/` page disappears from hybridSearch
 *     once the config key is set, reappears with includeSynthetic, and the
 *     recency_default key stamps recency_boost on results.
 *
 * Default-state guarantee pinned throughout: with all keys unset, behavior
 * is identical to pre-patch.
 */

import { describe, test, expect, beforeAll, afterAll } from 'bun:test';
import { PGLiteEngine } from '../src/core/pglite-engine.ts';
import { hybridSearch } from '../src/core/search/hybrid.ts';
import { withEnv } from './helpers/with-env.ts';
import {
  resolveEffectiveExcludes,
  DEFAULT_HARD_EXCLUDES,
} from '../src/core/search/source-boost.ts';
import {
  loadOverridesFromConfig,
  loadSearchModeConfig,
  resolveSearchMode,
  knobsHash,
} from '../src/core/search/mode.ts';
import { resolveRecencyDecayMap, DEFAULT_RECENCY_DECAY } from '../src/core/search/recency-decay.ts';
import type { ChunkInput } from '../src/core/types.ts';

// ---------------------------------------------------------------------------
// 1. Pure resolution
// ---------------------------------------------------------------------------

describe('resolveEffectiveExcludes', () => {
  test('no config, no per-call → both undefined (pre-patch behavior)', () => {
    const r = resolveEffectiveExcludes({});
    expect(r.excludePrefixes).toBeUndefined();
    expect(r.includePrefixes).toBeUndefined();
  });

  test('config prefixes merge with per-call excludes (deduped)', () => {
    const r = resolveEffectiveExcludes({
      perCallExclude: ['scratch/', 'digests/'],
      configPrefixes: ['digests/', 'scorecards/'],
    });
    expect(r.excludePrefixes!.sort()).toEqual(['digests/', 'scorecards/', 'scratch/']);
  });

  test('includeSynthetic drops the config plane and re-includes config + env prefixes', () => {
    const r = resolveEffectiveExcludes({
      perCallExclude: ['scratch/'],
      configPrefixes: ['digests/'],
      includeSynthetic: true,
      envValue: 'scorecards/',
    });
    // Per-call excludes survive (caller intent), config plane is dropped.
    expect(r.excludePrefixes).toEqual(['scratch/']);
    // Config + env planes are opted back in.
    expect(r.includePrefixes!.sort()).toEqual(['digests/', 'scorecards/']);
  });

  test('includeSynthetic does NOT re-include DEFAULT_HARD_EXCLUDES', () => {
    const r = resolveEffectiveExcludes({ configPrefixes: ['digests/'], includeSynthetic: true, envValue: '' });
    for (const noise of DEFAULT_HARD_EXCLUDES) {
      expect(r.includePrefixes ?? []).not.toContain(noise);
    }
  });
});

describe('loadOverridesFromConfig — brains-port keys', () => {
  test('search.exclude_slug_prefixes parses comma-separated prefixes', () => {
    const out = loadOverridesFromConfig({ 'search.exclude_slug_prefixes': 'digests/, scorecards/ ,' });
    expect(out.exclude_slug_prefixes).toEqual(['digests/', 'scorecards/']);
  });

  test('empty exclude value falls through (no override)', () => {
    const out = loadOverridesFromConfig({ 'search.exclude_slug_prefixes': ' , ' });
    expect(out.exclude_slug_prefixes).toBeUndefined();
  });

  test('search.recency_default accepts off|on|strong, rejects junk', () => {
    expect(loadOverridesFromConfig({ 'search.recency_default': 'on' }).recency_default).toBe('on');
    expect(loadOverridesFromConfig({ 'search.recency_default': ' STRONG ' }).recency_default).toBe('strong');
    expect(loadOverridesFromConfig({ 'search.recency_default': 'off' }).recency_default).toBe('off');
    expect(loadOverridesFromConfig({ 'search.recency_default': 'sometimes' }).recency_default).toBeUndefined();
  });

  test('search.recency_decay validates the triple format; malformed falls through', () => {
    expect(
      loadOverridesFromConfig({ 'search.recency_decay': 'telegram/:14:1.5,digests/:0:0' }).recency_decay,
    ).toBe('telegram/:14:1.5,digests/:0:0');
    expect(loadOverridesFromConfig({ 'search.recency_decay': 'not-a-triple' }).recency_decay).toBeUndefined();
  });

  test('absent keys → no overrides (default-state guarantee)', () => {
    const out = loadOverridesFromConfig({});
    expect(out.exclude_slug_prefixes).toBeUndefined();
    expect(out.recency_default).toBeUndefined();
    expect(out.recency_decay).toBeUndefined();
  });
});

describe('loadSearchModeConfig — GBRAIN_RECENCY_DEFAULT env plane', () => {
  const nullEngine = { getConfig: async (_k: string) => null };

  test('env wins over the (unset) config table', async () => {
    await withEnv({ GBRAIN_RECENCY_DEFAULT: 'on' }, async () => {
      const input = await loadSearchModeConfig(nullEngine);
      expect(input.overrides?.recency_default).toBe('on');
    });
  });

  test('env unset → key absent (default-state guarantee)', async () => {
    await withEnv({ GBRAIN_RECENCY_DEFAULT: undefined }, async () => {
      const input = await loadSearchModeConfig(nullEngine);
      expect(input.overrides?.recency_default).toBeUndefined();
    });
  });
});

describe('resolveRecencyDecayMap — configTriples plane', () => {
  test('config plane overrides defaults, env still wins over config', () => {
    const map = resolveRecencyDecayMap({
      configTriples: 'telegram/:14:1.5,daily/:30:0.1',
      envValue: 'daily/:7:2.0',
    });
    expect(map['telegram/']).toEqual({ halflifeDays: 14, coefficient: 1.5 });
    // env beats config for daily/:
    expect(map['daily/']).toEqual({ halflifeDays: 7, coefficient: 2.0 });
    // untouched defaults survive:
    expect(map['concepts/']).toEqual(DEFAULT_RECENCY_DECAY['concepts/']);
  });

  test('malformed config plane is dropped, not fatal', () => {
    const map = resolveRecencyDecayMap({ configTriples: 'garbage', envValue: '' });
    expect(map).toEqual(resolveRecencyDecayMap({ envValue: '' }));
  });
});

describe('knobsHash hx= fold (#2825 port)', () => {
  const knobs = resolveSearchMode({});

  test('different hard-exclude lists produce different hashes', () => {
    const a = knobsHash(knobs, { hardExcludes: ['digests/'] });
    const b = knobsHash(knobs, { hardExcludes: [] });
    const c = knobsHash(knobs);
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
  });

  test('exclude list order does not matter (sorted fold)', () => {
    const a = knobsHash(knobs, { hardExcludes: ['a/', 'b/'] });
    const b = knobsHash(knobs, { hardExcludes: ['b/', 'a/'] });
    expect(a).toBe(b);
  });
});

// ---------------------------------------------------------------------------
// 2. PGLite integration through bare hybridSearch
// ---------------------------------------------------------------------------

let engine: PGLiteEngine;

beforeAll(async () => {
  engine = new PGLiteEngine();
  await engine.connect({});
  await engine.initSchema();

  await engine.putPage('digests/2026-08-01', {
    type: 'note',
    title: 'Daily digest — 2026-08-01',
    compiled_truth: 'zorbofin scanner tickers digest summary of the day',
    timeline: '',
  });
  await engine.upsertChunks('digests/2026-08-01', [
    {
      chunk_index: 0,
      chunk_text: 'zorbofin scanner tickers digest summary of the day',
      chunk_source: 'compiled_truth',
      token_count: 9,
    },
  ] satisfies ChunkInput[]);

  await engine.putPage('telegram/scanner/2026-08-01', {
    type: 'note',
    title: 'Telegram Scanner — 2026-08-01',
    compiled_truth: 'zorbofin scanner tickers raw channel message',
    timeline: '',
  });
  await engine.upsertChunks('telegram/scanner/2026-08-01', [
    {
      chunk_index: 0,
      chunk_text: 'zorbofin scanner tickers raw channel message',
      chunk_source: 'compiled_truth',
      token_count: 8,
    },
  ] satisfies ChunkInput[]);
}, 60_000);

afterAll(async () => {
  await engine.disconnect();
});

describe('hybridSearch + search.exclude_slug_prefixes (PGLite)', () => {
  // "zorbofin" is an invented token that appears only in the two fixture
  // pages, so ambient corpus noise can't leak into the assertions. No
  // embedding provider is configured in tests → keyword arm only, which is
  // exactly the SQL path the exclusion clause guards.
  const Q = 'zorbofin scanner tickers';

  test('default (key unset): digest pages ARE returned (pre-patch behavior)', async () => {
    const slugs = (await hybridSearch(engine, Q, { limit: 10, expansion: false })).map((r) => r.slug);
    expect(slugs).toContain('digests/2026-08-01');
    expect(slugs).toContain('telegram/scanner/2026-08-01');
  });

  test('key set: digest pages disappear; primary sources stay', async () => {
    await engine.setConfig('search.exclude_slug_prefixes', 'digests/');
    try {
      const slugs = (await hybridSearch(engine, Q, { limit: 10, expansion: false })).map((r) => r.slug);
      expect(slugs).not.toContain('digests/2026-08-01');
      expect(slugs).toContain('telegram/scanner/2026-08-01');
    } finally {
      await engine.unsetConfig('search.exclude_slug_prefixes');
    }
  });

  test('includeSynthetic opts back in for a single call', async () => {
    await engine.setConfig('search.exclude_slug_prefixes', 'digests/');
    try {
      const slugs = (
        await hybridSearch(engine, Q, { limit: 10, expansion: false, includeSynthetic: true })
      ).map((r) => r.slug);
      expect(slugs).toContain('digests/2026-08-01');
    } finally {
      await engine.unsetConfig('search.exclude_slug_prefixes');
    }
  });

  test('per-call include_slug_prefixes also opts back in', async () => {
    await engine.setConfig('search.exclude_slug_prefixes', 'digests/');
    try {
      const slugs = (
        await hybridSearch(engine, Q, { limit: 10, expansion: false, include_slug_prefixes: ['digests/'] })
      ).map((r) => r.slug);
      expect(slugs).toContain('digests/2026-08-01');
    } finally {
      await engine.unsetConfig('search.exclude_slug_prefixes');
    }
  });
});

describe('hybridSearch + search.recency_default (PGLite)', () => {
  const Q = 'zorbofin scanner tickers';

  test('default (key unset): neutral query gets NO recency boost', async () => {
    const results = await hybridSearch(engine, Q, { limit: 10, expansion: false });
    const scanner = results.find((r) => r.slug === 'telegram/scanner/2026-08-01');
    expect(scanner).toBeDefined();
    expect(scanner!.recency_boost).toBeUndefined();
  });

  test('key set to on: recency stage stamps recency_boost > 1 on dated pages', async () => {
    await engine.setConfig('search.recency_default', 'on');
    try {
      const results = await hybridSearch(engine, Q, { limit: 10, expansion: false });
      const scanner = results.find((r) => r.slug === 'telegram/scanner/2026-08-01');
      expect(scanner).toBeDefined();
      // telegram/ has no entry in the default decay map → fallback config
      // (halflife 90d, coefficient 0.5); the page was just written, so
      // days_old ≈ 0 → factor ≈ 1.5.
      expect(scanner!.recency_boost).toBeGreaterThan(1.0);
    } finally {
      await engine.unsetConfig('search.recency_default');
    }
  });

  test('explicit per-call recency off beats the config default', async () => {
    await engine.setConfig('search.recency_default', 'on');
    try {
      const results = await hybridSearch(engine, Q, { limit: 10, expansion: false, recency: 'off' });
      const scanner = results.find((r) => r.slug === 'telegram/scanner/2026-08-01');
      expect(scanner).toBeDefined();
      expect(scanner!.recency_boost).toBeUndefined();
    } finally {
      await engine.unsetConfig('search.recency_default');
    }
  });
});
