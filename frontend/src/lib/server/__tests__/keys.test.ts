import { describe, expect, it } from "vitest";
import { keyFromBytes, newApiKey } from "../keys";

// Expected values computed by Python: "fw_" + secrets.token_urlsafe(24) with token_bytes patched to
// return these bytes, then hashlib.sha256(key).hexdigest() and key[:7] + "..." + key[-4:], exactly
// as billing.create_key does.
describe("API key format", () => {
  it("matches billing.create_key for a known example", () => {
    const k = keyFromBytes(Uint8Array.from({ length: 24 }, (_, i) => i));
    expect(k.key).toBe("fw_AAECAwQFBgcICQoLDA0ODxAREhMUFRYX");
    expect(k.keyHash).toBe("539cabd5a7a75cbb2a925321dc25f555cd6041af29b6432f11d1cb2914dc1716");
    expect(k.keyId).toBe("fw_AAEC...FRYX");
  });

  it("uses the urlsafe alphabet without padding", () => {
    const k = keyFromBytes(Uint8Array.from({ length: 24 }, (_, i) => [251, 255, 191][i % 3]));
    expect(k.key).toBe("fw_-_-_-_-_-_-_-_-_-_-_-_-_-_-_-_-_");
    expect(k.keyHash).toBe("726e453ebde2fe86b7866a8cc9cdd01c36c4dd42b05061cff5140682b29f5210");
    expect(k.keyId).toBe("fw_-_-_...-_-_");
  });

  it("mints 35-character keys from 24 random bytes", () => {
    const a = newApiKey();
    const b = newApiKey();
    expect(a.key).toMatch(/^fw_[A-Za-z0-9_-]{32}$/);
    expect(a.keyHash).toMatch(/^[0-9a-f]{64}$/);
    expect(a.key).not.toBe(b.key);
  });
});
