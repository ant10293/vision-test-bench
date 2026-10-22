const ACRONYMS = new Set(["ai", "cv", "api", "json", "sam", "iou", "gpt", "fps", "vad", "url"]);

const WORDS: Record<string, string> = {
  openai: "OpenAI",
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Turn camelCase, kebab-case, and snake_case identifiers into a short label.
 *  A lone word is capitalized. Sentences, JSON, and ids are returned unchanged.
 */
export function humanize(value: string): string {
  if (!value || /[\s"'`{}[\]()@/\\,;:!?]/.test(value)) return value;
  if (UUID.test(value)) return value;
  if (/^[A-Z][A-Z0-9]+$/.test(value)) return formatWord(value, true);
  if (!/[_-]/.test(value) && !/[a-z\d][A-Z]/.test(value) && !/[A-Z]{2,}[a-z]/.test(value)) return capitalizeWord(value);

  const spaced = value
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z\d])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/(\d)-(?=\d)/g, "$1\u0000")
    .replace(/-/g, " ")
    .replace(/\u0000/g, "-");
  const words = spaced.split(/\s+/).filter(Boolean);
  if (!words.length) return value;
  return words
    .map((word, index) => formatWord(word, index === 0))
    .join(" ")
    .replace(/head mounted/gi, (match) => (match.startsWith("H") ? "Head-mounted" : "head-mounted"));
}

/** "Object 1" beside a name becomes "Object 1: bolt". The id stands alone when there is no name. */
export function labeledId(localId: string, name?: string): string {
  const id = humanize(localId);
  const label = name ? humanize(name).trim() : "";
  if (!label) return id;
  return `${id}: ${label}`;
}

/** Format identifier tokens inside a label, and leave ordinary words in place. */
export function humanizeText(value: string): string {
  return value
    .split(/(\s+)/)
    .map((part) => (/^\s+$/.test(part) || !shouldRewriteToken(part) ? part : humanize(part)))
    .join("");
}

function shouldRewriteToken(part: string): boolean {
  if (/_/.test(part) || /[a-z\d][A-Z]/.test(part) || /^[A-Z][A-Z0-9]+$/.test(part)) return true;
  return part.includes("-") && (/\d/.test(part) || part.split("-").length >= 3);
}

function capitalizeWord(value: string): string {
  if (!/^[a-z]/.test(value)) return value;
  const lower = value.toLowerCase();
  if (ACRONYMS.has(lower)) return lower.toUpperCase();
  if (WORDS[lower]) return WORDS[lower];
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function formatWord(word: string, first: boolean): string {
  if (/^\d/.test(word)) return word;
  const lower = word.toLowerCase();
  if (ACRONYMS.has(lower)) return lower.toUpperCase();
  if (WORDS[lower]) return WORDS[lower];
  if (!first) return lower;
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
