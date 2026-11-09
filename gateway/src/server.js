import { createApp, loadConfig } from './app.js';

const config = loadConfig();
const app = createApp(config);

const server = app.listen(config.port, () => {
  console.log(`Gateway listening on http://localhost:${config.port}`);
});

function shutdown() {
  app.locals.live.close();
  server.close(() => process.exit(0));
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
