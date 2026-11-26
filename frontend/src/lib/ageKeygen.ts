/**
 * age X25519 key pairs made in the browser with WebCrypto, so the private key never reaches the
 * server. Same format as `age-keygen`: the recipient is bech32("age", public key) and the
 * identity is bech32("age-secret-key-", private scalar) in upper case.
 */

const CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const GENERATOR = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];

function polymod(values: number[]): number {
  let chk = 1;
  for (const value of values) {
    const top = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ value;
    GENERATOR.forEach((g, i) => {
      if ((top >>> i) & 1) chk ^= g;
    });
  }
  return chk;
}

function hrpExpand(hrp: string): number[] {
  const codes = [...hrp].map((c) => c.charCodeAt(0));
  return [...codes.map((c) => c >> 5), 0, ...codes.map((c) => c & 31)];
}

/** 8-bit bytes to 5-bit groups, zero-padded at the end. */
function toWords(bytes: Uint8Array): number[] {
  const words: number[] = [];
  let acc = 0;
  let bits = 0;
  for (const byte of bytes) {
    acc = (acc << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      words.push((acc >> bits) & 31);
    }
    acc &= (1 << bits) - 1;
  }
  if (bits > 0) words.push((acc << (5 - bits)) & 31);
  return words;
}

/** BIP-173 bech32 (not bech32m) without the 90-character limit, as age uses it. */
export function bech32Encode(hrp: string, bytes: Uint8Array): string {
  const data = toWords(bytes);
  const mod = polymod([...hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0]) ^ 1;
  const checksum = [0, 1, 2, 3, 4, 5].map((i) => (mod >>> (5 * (5 - i))) & 31);
  return `${hrp}1${[...data, ...checksum].map((d) => CHARSET[d]).join("")}`;
}

function base64UrlToBytes(text: string): Uint8Array {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base64 + "=".repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export interface AgeKeyPair {
  /** age1..., safe to share and to store on the server. */
  publicKey: string;
  /** The contents of an age-keygen identity file: comments plus AGE-SECRET-KEY-1... */
  identityFile: string;
}

/** WebCrypto exists only in secure contexts (HTTPS or localhost). */
export function canGenerateInBrowser(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && typeof crypto?.subtle !== "undefined";
}

export async function generateAgeKeyPair(now: Date = new Date()): Promise<AgeKeyPair> {
  const pair = (await crypto.subtle.generateKey({ name: "X25519" }, true, ["deriveBits"])) as CryptoKeyPair;
  const jwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
  if (!jwk.d || !jwk.x) throw new Error("The browser did not export the X25519 key.");
  const publicKey = bech32Encode("age", base64UrlToBytes(jwk.x));
  const secret = bech32Encode("age-secret-key-", base64UrlToBytes(jwk.d)).toUpperCase();
  const created = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    publicKey,
    identityFile: `# created: ${created}\n# public key: ${publicKey}\n${secret}\n`,
  };
}
