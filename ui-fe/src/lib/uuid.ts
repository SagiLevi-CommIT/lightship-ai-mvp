/**
 * UUIDv4 generator with safe fallbacks.
 *
 * On modern browsers uses ``crypto.randomUUID``; falls back to
 * ``crypto.getRandomValues`` seeded ``Math.random`` on older runtimes
 * (or HTTP-only contexts where the Web Crypto API isn't exposed) so
 * the UI never crashes when queuing assets.
 */
export function uuidv4(): string {
  if (typeof globalThis !== 'undefined' && globalThis.crypto) {
    const c = globalThis.crypto as Crypto & { randomUUID?: () => string };
    if (typeof c.randomUUID === 'function') {
      try {
        return c.randomUUID();
      } catch {
        /* fall through */
      }
    }
    if (typeof c.getRandomValues === 'function') {
      const bytes = new Uint8Array(16);
      c.getRandomValues(bytes);
      return bytesToUuid(bytes);
    }
  }

  const bytes = new Uint8Array(16);
  for (let i = 0; i < 16; i += 1) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  return bytesToUuid(bytes);
}

function bytesToUuid(bytes: Uint8Array): string {
  // Per RFC 4122 §4.4 — set version (0100) and variant (10).
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex: Array<string> = [];
  for (let i = 0; i < 16; i += 1) {
    hex.push(bytes[i].toString(16).padStart(2, '0'));
  }
  return (
    hex.slice(0, 4).join('') +
    '-' +
    hex.slice(4, 6).join('') +
    '-' +
    hex.slice(6, 8).join('') +
    '-' +
    hex.slice(8, 10).join('') +
    '-' +
    hex.slice(10, 16).join('')
  );
}
