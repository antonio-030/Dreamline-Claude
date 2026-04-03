#!/usr/bin/env node
/**
 * Dreamline Auto-Sync Hook v2 (automatisch installiert)
 * Sendet Claude Code Sessions + Projektkontext an Dreamline.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');

const DREAMLINE_URL = '{dreamline_url}';
const DREAMLINE_API_KEY = '{api_key}';
const PROJECT_NAME = '{project_name}';

const sessionId = process.env.CLAUDE_SESSION_ID || 'unknown';
const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
const trackerPath = path.join(projectDir, '.claude', '.dreamline-synced');

function loadSynced() {{
  try {{ return new Set(fs.existsSync(trackerPath) ? fs.readFileSync(trackerPath,'utf-8').split('\n').filter(Boolean) : []); }} catch {{ return new Set(); }}
}}

function readStdin() {{
  return new Promise(r => {{
    let d=''; process.stdin.setEncoding('utf-8');
    process.stdin.on('data', c => d+=c);
    process.stdin.on('end', () => r(d));
    setTimeout(() => r(d), 2000);
  }});
}}

// Projektkontext sammeln: CLAUDE.md + Dateistruktur
function gatherProjectContext() {{
  const ctx = [];

  // CLAUDE.md lesen (Projektregeln)
  const claudeMdPaths = [
    path.join(projectDir, 'CLAUDE.md'),
    path.join(projectDir, '.claude', 'CLAUDE.md'),
  ];
  for (const p of claudeMdPaths) {{
    try {{
      if (fs.existsSync(p)) {{
        const content = fs.readFileSync(p, 'utf-8');
        ctx.push('[CLAUDE.md]\n' + content.substring(0, 3000));
        break;
      }}
    }} catch {{}}
  }}

  // Dateistruktur (nur Top-Level + wichtige Unterordner)
  try {{
    const ignore = new Set(['.git', 'node_modules', '.next', '__pycache__', '.venv', 'venv', '.claude-flow', '.swarm', '.hive-mind', 'dist', 'build']);
    const tree = [];

    function walk(dir, prefix, depth) {{
      if (depth > 2) return;
      try {{
        const entries = fs.readdirSync(dir, {{ withFileTypes: true }});
        for (const e of entries) {{
          if (ignore.has(e.name) || e.name.startsWith('.')) continue;
          const rel = prefix + e.name;
          if (e.isDirectory()) {{
            tree.push(rel + '/');
            if (depth < 2) walk(path.join(dir, e.name), rel + '/', depth + 1);
          }} else {{
            tree.push(rel);
          }}
          if (tree.length > 100) return;
        }}
      }} catch {{}}
    }}
    walk(projectDir, '', 0);
    if (tree.length) ctx.push('[Dateistruktur]\n' + tree.join('\n'));
  }} catch {{}}

  // package.json oder requirements.txt (Abhängigkeiten)
  for (const depFile of ['package.json', 'requirements.txt', 'pyproject.toml']) {{
    try {{
      const p = path.join(projectDir, depFile);
      if (fs.existsSync(p)) {{
        const content = fs.readFileSync(p, 'utf-8');
        ctx.push('[' + depFile + ']\n' + content.substring(0, 1500));
      }}
    }} catch {{}}
  }}

  return ctx.join('\n\n---\n\n');
}}

async function send(content, context) {{
  const messages = [
    {{ role: 'user', content: 'Claude Code Session (' + PROJECT_NAME + '): ' + sessionId }},
    {{ role: 'assistant', content: content.substring(content.length > 5000 ? content.length-5000 : 0) }}
  ];

  const body = JSON.stringify({{
    messages: messages,
    outcome: 'neutral',
    metadata: {{
      project: PROJECT_NAME,
      session_id: sessionId,
      source: 'dreamline-hook',
      project_dir: projectDir,
      project_context: context ? context.substring(0, 8000) : null,
    }}
  }});
  return new Promise(r => {{
    const url = new URL('/api/v1/sessions', DREAMLINE_URL);
    const req = http.request({{
      hostname: url.hostname, port: url.port, path: url.pathname,
      method: 'POST', timeout: 8000,
      headers: {{ 'Content-Type':'application/json', 'Authorization':'Bearer '+DREAMLINE_API_KEY, 'Content-Length':Buffer.byteLength(body) }}
    }}, res => {{ let d=''; res.on('data',c=>d+=c); res.on('end',()=>r(res.statusCode)); }});
    req.on('error', () => r(0));
    req.on('timeout', () => {{ req.destroy(); r(0); }});
    req.write(body); req.end();
  }});
}}

async function main() {{
  if (loadSynced().has(sessionId)) process.exit(0);

  // Projektkontext sammeln
  const context = gatherProjectContext();

  // Session-Inhalt lesen
  let content = await readStdin();
  if (!content || content.length < 50) {{
    try {{
      const home = process.env.HOME || process.env.USERPROFILE || '';
      const projKey = projectDir.replace(/[:\\/]/g, '-').replace(/^-+/, '');
      const dirs = [
        path.join(home, '.claude', 'projects', projKey),
        path.join(home, '.claude', 'projects'),
      ];
      for (const dir of dirs) {{
        if (!fs.existsSync(dir)) continue;
        const files = fs.readdirSync(dir).filter(f => f.endsWith('.jsonl') && !f.startsWith('agent-'))
          .map(f => ({{ name: f, mt: fs.statSync(path.join(dir,f)).mtimeMs }})).sort((a,b) => b.mt-a.mt);
        if (files.length > 0) {{ content = fs.readFileSync(path.join(dir, files[0].name),'utf-8').split('\n').slice(-50).join('\n'); break; }}
      }}
    }} catch {{}}
  }}
  if (!content || content.length < 50) process.exit(0);

  const status = await send(content, context);
  if (status === 200 || status === 201) {{
    try {{ fs.appendFileSync(trackerPath, sessionId+'\n'); }} catch {{}}
  }}
}}
main().catch(() => process.exit(0));
