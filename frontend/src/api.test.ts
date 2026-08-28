import { describe, expect, it } from "vitest";

import { apiProblem, sentence } from "./api";

describe("VITE_API_URL", () => {
  it("is a path or an absolute URL, and nothing half-resolved", () => {
    expect(apiProblem("/api")).toBe("");
    expect(apiProblem("https://api.mindkeep.io")).toBe("");
    expect(apiProblem("http://localhost:8001/")).toBe("");
    expect(apiProblem("https://")).toContain('VITE_API_URL is "https://"'); // an empty reference
    expect(apiProblem("")).toContain("must be a path");
    expect(apiProblem("api.mindkeep.io")).toContain("absolute URL");
  });
});

describe("an error body", () => {
  it("is shown as a sentence: FastAPI's detail, an HTML page's title, or the text", () => {
    expect(sentence('{"detail":"no such bundle"}')).toBe("no such bundle");
    expect(
      sentence(
        "<!DOCTYPE html><html><head><title>mindkeep.io | 524: A timeout occurred</title></head></html>",
      ),
    ).toBe("mindkeep.io | 524: A timeout occurred");
    expect(sentence("plain text")).toBe("plain text");
  });
});
