const SUFFIX_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

/** A readable reference such as WEB-20261025-K7QD for tickets typed into the console. */
export function generateTicketId(now: Date = new Date()): string {
  const date = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('')
  const bytes = crypto.getRandomValues(new Uint8Array(4))
  const suffix = Array.from(bytes, (byte) => SUFFIX_ALPHABET[byte % SUFFIX_ALPHABET.length]).join('')
  return `WEB-${date}-${suffix}`
}
