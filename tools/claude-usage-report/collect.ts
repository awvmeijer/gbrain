#!/usr/bin/env bun
/**
 * Standalone Claude Code token-usage collector.
 *
 * Walks ~/.claude/projects/**\/*.jsonl (session transcripts, including the
 * subagents/ subfolders) and aggregates real per-turn token usage by
 * project, model, and session. No gbrain imports — reads only local
 * Claude Code transcript files.
 *
 * Usage: bun run tools/claude-usage-report/collect.ts > /tmp/claude-usage.json
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';

const PROJECTS_DIR = join(homedir(), '.claude', 'projects');

interface UsageLine {
  cwd: string;
  sessionId: string;
  timestamp: string;
  model: string;
  isSubagent: boolean;
  messageId: string;
  requestId: string;
  uuid: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
}

function walk(dir: string, out: string[] = []): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = join(dir, entry);
    let st;
    try {
      st = statSync(full);
    } catch {
      continue;
    }
    if (st.isDirectory()) walk(full, out);
    else if (entry.endsWith('.jsonl')) out.push(full);
  }
  return out;
}

function parseFile(path: string, lines: UsageLine[]): void {
  const isSubagent = path.includes(`${join('', 'subagents', '')}`) || path.includes('/subagents/');
  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch {
    return;
  }
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    let evt: any;
    try {
      evt = JSON.parse(line);
    } catch {
      continue;
    }
    if (evt.type !== 'assistant') continue;
    const usage = evt.message?.usage;
    if (!usage) continue;
    lines.push({
      cwd: evt.cwd ?? '(unknown)',
      sessionId: evt.sessionId ?? '(unknown)',
      timestamp: evt.timestamp ?? '',
      model: evt.message?.model ?? '(unknown)',
      isSubagent,
      messageId: evt.message?.id ?? '',
      requestId: evt.requestId ?? '',
      uuid: evt.uuid ?? '',
      input_tokens: usage.input_tokens ?? 0,
      output_tokens: usage.output_tokens ?? 0,
      cache_read_input_tokens: usage.cache_read_input_tokens ?? 0,
      cache_creation_input_tokens: usage.cache_creation_input_tokens ?? 0,
    });
  }
}

function dedupe(lines: UsageLine[]): UsageLine[] {
  const seen = new Set<string>();
  const out: UsageLine[] = [];
  for (const l of lines) {
    const key = l.messageId ? `id:${l.messageId}` : `rq:${l.requestId}:${l.uuid}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(l);
  }
  return out;
}

function totalOf(l: UsageLine): number {
  return l.input_tokens + l.output_tokens + l.cache_read_input_tokens + l.cache_creation_input_tokens;
}

function main() {
  const files = walk(PROJECTS_DIR);
  const lines: UsageLine[] = [];
  for (const f of files) parseFile(f, lines);
  const deduped = dedupe(lines);

  // --- by project ---
  const byProject = new Map<
    string,
    {
      input_tokens: number;
      output_tokens: number;
      cache_read_input_tokens: number;
      cache_creation_input_tokens: number;
      turns: number;
      sessions: Set<string>;
      subagent_turns: number;
      firstSeen: string;
      lastSeen: string;
    }
  >();
  for (const l of deduped) {
    let p = byProject.get(l.cwd);
    if (!p) {
      p = {
        input_tokens: 0,
        output_tokens: 0,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
        turns: 0,
        sessions: new Set(),
        subagent_turns: 0,
        firstSeen: l.timestamp,
        lastSeen: l.timestamp,
      };
      byProject.set(l.cwd, p);
    }
    p.input_tokens += l.input_tokens;
    p.output_tokens += l.output_tokens;
    p.cache_read_input_tokens += l.cache_read_input_tokens;
    p.cache_creation_input_tokens += l.cache_creation_input_tokens;
    p.turns += 1;
    p.sessions.add(l.sessionId);
    if (l.isSubagent) p.subagent_turns += 1;
    if (l.timestamp && l.timestamp < p.firstSeen) p.firstSeen = l.timestamp;
    if (l.timestamp && l.timestamp > p.lastSeen) p.lastSeen = l.timestamp;
  }

  const projects = [...byProject.entries()]
    .map(([cwd, p]) => {
      const total = p.input_tokens + p.output_tokens + p.cache_read_input_tokens + p.cache_creation_input_tokens;
      const cacheDenom = p.cache_read_input_tokens + p.cache_creation_input_tokens;
      return {
        project: cwd,
        total_tokens: total,
        input_tokens: p.input_tokens,
        output_tokens: p.output_tokens,
        cache_read_input_tokens: p.cache_read_input_tokens,
        cache_creation_input_tokens: p.cache_creation_input_tokens,
        turns: p.turns,
        subagent_turns: p.subagent_turns,
        sessions: p.sessions.size,
        cache_efficiency: cacheDenom > 0 ? p.cache_read_input_tokens / cacheDenom : null,
        first_seen: p.firstSeen,
        last_seen: p.lastSeen,
      };
    })
    .sort((a, b) => b.total_tokens - a.total_tokens);

  // --- by model ---
  const byModel = new Map<string, { input_tokens: number; output_tokens: number; cache_read_input_tokens: number; cache_creation_input_tokens: number; turns: number }>();
  for (const l of deduped) {
    let m = byModel.get(l.model);
    if (!m) {
      m = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, turns: 0 };
      byModel.set(l.model, m);
    }
    m.input_tokens += l.input_tokens;
    m.output_tokens += l.output_tokens;
    m.cache_read_input_tokens += l.cache_read_input_tokens;
    m.cache_creation_input_tokens += l.cache_creation_input_tokens;
    m.turns += 1;
  }
  const models = [...byModel.entries()]
    .map(([model, m]) => ({
      model,
      total_tokens: m.input_tokens + m.output_tokens + m.cache_read_input_tokens + m.cache_creation_input_tokens,
      ...m,
    }))
    .sort((a, b) => b.total_tokens - a.total_tokens);

  // --- by session (top heaviest) ---
  const bySession = new Map<string, { project: string; total: number; turns: number; firstSeen: string; lastSeen: string; models: Set<string> }>();
  for (const l of deduped) {
    let s = bySession.get(l.sessionId);
    if (!s) {
      s = { project: l.cwd, total: 0, turns: 0, firstSeen: l.timestamp, lastSeen: l.timestamp, models: new Set() };
      bySession.set(l.sessionId, s);
    }
    s.total += totalOf(l);
    s.turns += 1;
    s.models.add(l.model);
    if (l.timestamp && l.timestamp < s.firstSeen) s.firstSeen = l.timestamp;
    if (l.timestamp && l.timestamp > s.lastSeen) s.lastSeen = l.timestamp;
  }
  const topSessions = [...bySession.entries()]
    .map(([sessionId, s]) => ({
      sessionId,
      project: s.project,
      total_tokens: s.total,
      turns: s.turns,
      models: [...s.models],
      first_seen: s.firstSeen,
      last_seen: s.lastSeen,
    }))
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .slice(0, 15);

  // --- daily trend ---
  const byDay = new Map<string, number>();
  for (const l of deduped) {
    if (!l.timestamp) continue;
    const day = l.timestamp.slice(0, 10);
    byDay.set(day, (byDay.get(day) ?? 0) + totalOf(l));
  }
  const trend = [...byDay.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([day, total_tokens]) => ({ day, total_tokens }));

  const grandTotal = projects.reduce((acc, p) => acc + p.total_tokens, 0);

  const result = {
    generated_at: new Date().toISOString(),
    files_scanned: files.length,
    raw_lines: lines.length,
    deduped_lines: deduped.length,
    grand_total_tokens: grandTotal,
    projects,
    models,
    top_sessions: topSessions,
    daily_trend: trend,
  };

  console.log(JSON.stringify(result, null, 2));
}

main();
