const CJK_RE = /[\u3400-\u9fff]/g;
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

export function repairMojibakeText(text: string | null | undefined, preserveNewlines = false) {
  if (text == null) return text;

  const cleaned = preserveNewlines
    ? text.replace(/\x00/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/^[ \t]+|[ \t]+$/g, "")
    : text.replace(/\x00/g, "").replace(/\r/g, "").replace(/\n/g, "").replace(/^[ \t]+|[ \t]+$/g, "");
  if (!cleaned) return cleaned;

  let repaired = cleaned;
  try {
    repaired = decodeURIComponent(
      Array.from(cleaned)
        .map((ch) => `%${ch.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join(""),
    );
  } catch {
    return cleaned;
  }

  if (repaired === cleaned) return cleaned;

  const originalCjk = countCjk(cleaned);
  const repairedCjk = countCjk(repaired);
  const originalNoise = countMojibakeChars(cleaned);
  const repairedNoise = countMojibakeChars(repaired);

  if (repairedCjk > originalCjk) return repaired;
  if (repairedNoise < originalNoise) return repaired;
  return cleaned;
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
