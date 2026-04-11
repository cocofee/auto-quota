const CJK_RE = /[\u3400-\u9fff]/g;
const PRIVATE_USE_RE = /[\ue000-\uf8ff]/g;
const REPLACEMENT_RE = /\ufffd/g;
const MOJIBAKE_CHARS = new Set(
  "脌脕脗脙脛脜脝脟脠脡脢脣脤脥脦脧脨脩脪脫脭脮脰脴脵脷脹脺脻脼" +
  "脿谩芒茫盲氓忙莽猫茅锚毛矛铆卯茂冒帽貌贸么玫枚酶霉煤没眉媒镁每",
);

function countCjk(text: string): number {
  return (text.match(CJK_RE) || []).length;
}

function countMojibakeChars(text: string): number {
  let count = 0;
  for (const ch of text) {
    if (MOJIBAKE_CHARS.has(ch)) count += 1;
  }
  return count;
}

function countPrivateUseChars(text: string): number {
  return (text.match(PRIVATE_USE_RE) || []).length;
}

function countReplacementChars(text: string): number {
  return (text.match(REPLACEMENT_RE) || []).length;
}

function repairLatin1Utf8(text: string): string | null {
  try {
    return decodeURIComponent(
      Array.from(text)
        .map((ch) => `%${ch.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join(""),
    );
  } catch {
    return null;
  }
}

function repairGb18030Utf8(text: string): string | null {
  try {
    const bytes = new Uint8Array(Array.from(text, (ch) => ch.charCodeAt(0)));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

type TextScore = readonly [number, number, number, number];

function scoreText(text: string): TextScore {
  return [
    countCjk(text),
    -countMojibakeChars(text),
    -countPrivateUseChars(text),
    -countReplacementChars(text),
  ];
}

export function compareTextScores(left: TextScore, right: TextScore): number {
  for (let index = 0; index < left.length; index += 1) {
    const delta = left[index] - right[index];
    if (delta !== 0) {
      return delta;
    }
  }
  return 0;
}

export function repairMojibakeText(text: string | null | undefined, preserveNewlines = false) {
  if (text == null) return text;

  const cleaned = preserveNewlines
    ? text.replace(/\x00/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/^[ \t]+|[ \t]+$/g, "")
    : text.replace(/\x00/g, "").replace(/\r/g, "").replace(/\n/g, "").replace(/^[ \t]+|[ \t]+$/g, "");
  if (!cleaned) return cleaned;

  const candidates = [cleaned];
  for (const candidate of [repairLatin1Utf8(cleaned), repairGb18030Utf8(cleaned)]) {
    if (candidate && !candidates.includes(candidate)) {
      candidates.push(candidate);
    }
  }

  const cleanedScore = scoreText(cleaned);
  let best = cleaned;
  let bestScore = cleanedScore;

  for (const candidate of candidates) {
    const candidateScore = scoreText(candidate);
    if (compareTextScores(candidateScore, bestScore) > 0) {
      best = candidate;
      bestScore = candidateScore;
    }
  }

  return compareTextScores(bestScore, cleanedScore) > 0 ? best : cleaned;
}

export function repairMojibakeData<T>(value: T, preserveNewlines = false): T {
  if (typeof value === "string") {
    return repairMojibakeText(value, preserveNewlines) as T;
  }
  if (Array.isArray(value)) {
    return value.map((item) => repairMojibakeData(item, preserveNewlines)) as T;
  }
  if (value && typeof value === "object") {
    if (
      value instanceof Blob ||
      value instanceof ArrayBuffer ||
      value instanceof Date ||
      value instanceof FormData
    ) {
      return value;
    }
    const entries = Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key,
      repairMojibakeData(item, preserveNewlines),
    ]);
    return Object.fromEntries(entries) as T;
  }
  return value;
}
