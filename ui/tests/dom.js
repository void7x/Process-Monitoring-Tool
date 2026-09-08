"use strict";

/**
 * Test harness for the Process Monitor dashboard.
 *
 * The shipped `ui/app.js` is intentionally a single IIFE with no exports so
 * the browser can load it directly.  For the test suite we boot a jsdom
 * window, replace `window.fetch` with a controllable mock, then run
 * `app.js` against that environment.  Tests then assert on DOM changes,
 * fetch calls, and event interactions.
 */

const fs = require("fs");
const path = require("path");
const { JSDOM, ResourceLoader } = require("jsdom");

const HTML_PATH = path.join(__dirname, "..", "index.html");
const APP_JS_PATH = path.join(__dirname, "..", "app.js");

class LocalResourceLoader extends ResourceLoader {
  fetch(url, options) {
    // Only serve files that are present in the UI directory; everything
    // else (e.g. http://localhost/styles.css during a smoke load) returns
    // an empty 200 so jsdom does not block on network failures.
    if (url.startsWith("file://")) {
      const local = url.replace("file://", "");
      try {
        return Promise.resolve(fs.readFileSync(local));
      } catch (_) {
        return Promise.resolve(Buffer.from(""));
      }
    }
    if (url.startsWith("http://localhost/")) {
      const localName = url.replace("http://localhost/", "");
      const localPath = path.join(__dirname, "..", localName);
      try {
        return Promise.resolve(fs.readFileSync(localPath));
      } catch (_) {
        return Promise.resolve(Buffer.from(""));
      }
    }
    return super.fetch(url, options);
  }
}

function createDom() {
  const html = fs.readFileSync(HTML_PATH, "utf8");
  const dom = new JSDOM(html, {
    url: "http://localhost/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
    resources: new LocalResourceLoader(),
  });
  return dom;
}

function loadApp(dom) {
  const js = fs.readFileSync(APP_JS_PATH, "utf8");
  // Run the dashboard IIFE in the jsdom window.
  dom.window.eval(js);
  return dom.window;
}

/**
 * Build a controllable mock for `window.fetch`.  Each entry in
 * `routes` maps a URL substring to either a fixed JSON value or a
 * function that produces a response based on the request.
 */
function makeFetchMock(routes) {
  return function mockFetch(input, init) {
    const url = typeof input === "string" ? input : input.url;
    for (const [pattern, handler] of Object.entries(routes)) {
      if (url.includes(pattern)) {
        if (typeof handler === "function") {
          const result = handler({ url, init });
          // A function handler may either return the response body
          // directly, or an object with ``body``/``status`` fields.
          if (result && (result.body !== undefined || result.status !== undefined)) {
            return Promise.resolve({
              ok: result.ok !== false,
              status: result.status || 200,
              json: () => Promise.resolve(result.body),
            });
          }
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(result),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(handler),
        });
      }
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ error: { message: "no mock for " + url } }),
    });
  };
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate, { timeout = 2000, interval = 25 } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (predicate()) return true;
    await wait(interval);
  }
  return predicate();
}

module.exports = { createDom, loadApp, makeFetchMock, wait, waitFor };
