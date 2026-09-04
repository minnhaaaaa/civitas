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
const files = await sourceFiles(sourceRoot);
for (const path of files) {
  const content = await readFile(path, "utf8");
  if (forbiddenDashes.test(content)) failures.push(path);
}

if (failures.length > 0) {
  throw new Error(`Frontend copy contains en or em dashes:\n${failures.join("\n")}`);
}

const landing = await readFile(new URL("../src/LandingPage.tsx", import.meta.url), "utf8");
const styles = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

if (landing.includes('className="hero-indent"')) {
  throw new Error("Hero headline lines must share one left edge");
}

const installHeading = styles.match(
  /\.install-heading h2\s*\{[^}]*clamp\([^,]+,[^,]+,\s*(\d+)px\)/su,
);
if (!installHeading || Number(installHeading[1]) > 74) {
  throw new Error("Install heading must use the compact display scale");
}

const responsiveInstallSizes = [
  ...styles.matchAll(/\.install-heading h2\s*\{[^}]*clamp\([^,]+,[^,]+,\s*(\d+)px\)/gsu),
].map((match) => Number(match[1]));
if (responsiveInstallSizes.length < 2 || Math.max(...responsiveInstallSizes.slice(1)) > 52) {
  throw new Error("Install heading must stay within three lines on mobile");
}

const juryStamp = styles.match(/\.jury-stamp\s*\{([^}]*)\}/su)?.[1] ?? "";
if (!juryStamp.includes("right:") || juryStamp.includes("left:")) {
  throw new Error("Jury badge must be anchored to the right on desktop");
}
const responsiveJuryStamps = [...styles.matchAll(/\.jury-stamp\s*\{([^}]*)\}/gsu)].slice(1);
if (!responsiveJuryStamps.some((match) => match[1].includes("left:"))) {
  throw new Error("Jury badge must be anchored to the left on mobile");
}

const responsiveClosingSizes = [
  ...styles.matchAll(/\.closing-section h2\s*\{[^}]*clamp\([^,]+,[^,]+,\s*(\d+)px\)/gsu),
].map((match) => Number(match[1]));
if (
  responsiveClosingSizes.length < 2 ||
  responsiveClosingSizes[0] > 80 ||
  responsiveClosingSizes.at(-1) > 46
) {
  throw new Error("Closing heading must stay within three lines on mobile");
}
