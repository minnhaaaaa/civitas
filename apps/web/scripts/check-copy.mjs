import { readdir, readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url));
const forbiddenDashes = /[\u2013\u2014]/u;

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? sourceFiles(path) : [path];
    }),
  );
  return nested.flat().filter((path) => [".ts", ".tsx", ".css"].includes(extname(path)));
}

const failures = [];
for (const path of await sourceFiles(sourceRoot)) {
  const content = await readFile(path, "utf8");
  if (forbiddenDashes.test(content)) failures.push(path);
}

if (failures.length > 0) {
  throw new Error(`Frontend copy contains en or em dashes:\n${failures.join("\n")}`);
}
