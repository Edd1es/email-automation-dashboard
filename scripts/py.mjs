// Runs the backend's virtualenv Python on Windows, macOS and Linux.
// Usage: node scripts/py.mjs -m app.cli seed
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'backend');
export const venvPython = process.platform === 'win32'
  ? path.join(backend, '.venv', 'Scripts', 'python.exe')
  : path.join(backend, '.venv', 'bin', 'python');

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (!existsSync(venvPython)) {
    console.error('The Python environment is missing. Run: npm run setup');
    process.exit(1);
  }
  const result = spawnSync(venvPython, process.argv.slice(2), { cwd: backend, stdio: 'inherit' });
  process.exit(result.status ?? 1);
}
