// Tests for the GitHub Pages OAuth relay (site/oauth/google/relay.js).
// Run: node --test dev/site-tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import { CALLBACK_PATH, relayTarget, returnOrigin } from "../../site/oauth/google/relay.js";

function state(payload) {
  return Buffer.from(JSON.stringify(payload)).toString("base64url");
}

const good = (o) => state({ v: 1, o, n: "nonce" });

test("accepts bare http and https origins", () => {
  for (const origin of [
    "http://192.168.1.10:8080",
    "https://dbbm.example.com",
    "http://localhost:5173",
    "https://[::1]:8443",
  ]) {
    assert.equal(returnOrigin(good(origin)), origin);
  }
});

test("rejects anything that is not exactly an origin", () => {
  for (const origin of [
    "javascript:alert(1)",
    "data:text/html,x",
    "ftp://host",
    "https://user:pw@host",
    "https://host/",
    "https://host/path",
    "https://host?x=1",
    "https://host#x",
    "https://HOST",
    "https://host:443",
    "not a url",
  ]) {
    assert.equal(returnOrigin(good(origin)), null, origin);
  }
});

test("rejects malformed state", () => {
  for (const value of [
    null,
    "",
    "not base64!",
    state({ v: 2, o: "https://h", n: "x" }),
    state({ v: 1, o: "https://h" }),
    state({ v: 1, o: 5, n: "x" }),
    state("just a string"),
    Buffer.from("{not json").toString("base64url"),
  ]) {
    assert.equal(returnOrigin(value), null, String(value));
  }
});

test("passes the query through to the callback unchanged", () => {
  const s = good("http://192.168.1.10:8080");
  const search = `?state=${s}&code=4/0Abc-def&scope=https://www.googleapis.com/auth/drive.file`;
  assert.equal(relayTarget(search), `http://192.168.1.10:8080${CALLBACK_PATH}${search}`);
  const denied = `?error=access_denied&state=${s}`;
  assert.equal(relayTarget(denied), `http://192.168.1.10:8080${CALLBACK_PATH}${denied}`);
});

test("stops without state", () => {
  assert.equal(relayTarget("?code=abc"), null);
  assert.equal(relayTarget(""), null);
});
