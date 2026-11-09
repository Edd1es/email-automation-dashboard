// Creates the Python environment, writes .env with random secrets, adds sample data.
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { hashPassword } from '../gateway/src/password.js';
import { venvPython } from './py.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = path.join(root, 'backend');
const run = (command, args) => execFileSync(command, args, { cwd: backend, stdio: 'inherit' });

// Windows installs Python as `python` (or the `py` launcher); macOS and Linux as `python3`.
function systemPython() {
  const candidates = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
  for (const command of candidates) {
    try {
      execFileSync(command, ['--version'], { stdio: 'ignore' });
      return command;
    } catch { /* try the next one */ }
  }
  throw new Error('Python 3.11 or newer is required. Install it from https://www.python.org/downloads/');
}

if (!existsSync(venvPython)) run(systemPython(), ['-m', 'venv', '.venv']);
run(venvPython, ['-m', 'pip', 'install', '-q', '-r', 'requirements-dev.txt']);

const envPath = path.join(root, '.env');
if (existsSync(envPath)) {
  console.log('\nKept your existing .env.');
} else {
  const password = randomBytes(12).toString('base64url');
  const secrets = {
    __FLASK_SECRET_KEY__: randomBytes(32).toString('hex'),
    __INTERNAL_API_TOKEN__: randomBytes(32).toString('hex'),
    __JWT_SECRET__: randomBytes(32).toString('hex'),
    // Single-quoted in .env because the hash contains $ characters.
    __ADMIN_PASSWORD_HASH__: await hashPassword(password),
  };
  let text = readFileSync(path.join(root, '.env.example'), 'utf8');
  for (const [placeholder, value] of Object.entries(secrets)) {
    text = text.replace(placeholder, value);
  }
  writeFileSync(envPath, text, { mode: 0o600 });
  console.log(`\nCreated .env. Sign in with username "admin" and password: ${password}`);
  console.log('This password is shown only once.');
}

run(venvPython, ['-m', 'app.cli', 'seed']);
console.log('\nSetup finished. Start everything with: npm run dev');
