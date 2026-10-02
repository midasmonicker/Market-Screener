import { readdir, readFile } from 'node:fs/promises';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const dataDirectory = resolve(
  process.argv[2] ?? resolve(scriptDirectory, '../public/data')
);
const conflictMarkers = ['<<<<<<<', '=======', '>>>>>>>'];

async function findJsonFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nestedFiles = await Promise.all(
    entries.map(async (entry) => {
      const entryPath = resolve(directory, entry.name);
      if (entry.isDirectory()) return findJsonFiles(entryPath);
      return entry.isFile() && entry.name.endsWith('.json') ? [entryPath] : [];
    })
  );
  return nestedFiles.flat();
}

let jsonFiles;
try {
  jsonFiles = await findJsonFiles(dataDirectory);
} catch (error) {
  console.error(`Cannot scan generated data directory ${dataDirectory}: ${error.message}`);
  process.exit(1);
}

if (jsonFiles.length === 0) {
  console.error(`No JSON files found under ${dataDirectory}`);
  process.exit(1);
}

const failures = [];
for (const filePath of jsonFiles.sort()) {
  const displayPath = relative(dataDirectory, filePath);
  const contents = await readFile(filePath, 'utf8');

  for (const marker of conflictMarkers) {
    if (contents.includes(marker)) {
      failures.push(`${displayPath}: contains conflict marker ${JSON.stringify(marker)}`);
    }
  }

  try {
    JSON.parse(contents);
  } catch (error) {
    failures.push(`${displayPath}: invalid JSON (${error.message})`);
  }
}

if (failures.length > 0) {
  console.error(`Generated data validation failed:\n${failures.join('\n')}`);
  process.exit(1);
}

console.log(`Validated ${jsonFiles.length} generated JSON file(s) under ${dataDirectory}`);