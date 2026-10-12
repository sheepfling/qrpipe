import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(root, 'receiver/src');
const output = resolve(root, 'receiver/dist');

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await build({
  entryPoints: [resolve(source, 'app.js')],
  outfile: resolve(output, 'app.js'),
  bundle: true,
  format: 'esm',
  target: ['safari15', 'chrome100'],
  legalComments: 'none',
});
for (const file of ['index.html', 'styles.css', 'manifest.webmanifest', 'icon.svg', 'sw.js']) {
  await cp(resolve(source, file), resolve(output, file));
}
await cp(resolve(root, 'receiver/THIRD_PARTY_NOTICES.md'), resolve(output, 'THIRD_PARTY_NOTICES.md'));
await cp(resolve(root, 'receiver/LICENSE-jsQR'), resolve(output, 'LICENSE-jsQR'));
const htmlPath = resolve(output, 'index.html');
const html = await readFile(htmlPath, 'utf8');
if (!html.includes('src="./app.js"') || !html.includes('href="./styles.css"')) {
  throw new Error('receiver shell references do not match build outputs');
}
await writeFile(resolve(output, '.nojekyll'), '');
console.log(`Built static, offline-capable receiver: ${output}`);
