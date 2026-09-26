import { describe, expect, it } from "vitest";

import { parseFrame } from "./sse";

describe("parseFrame", () => {
  it("reads the event name and JSON data", () => {
    expect(parseFrame('event: reading\ndata: {"a": 1}')).toEqual({
      event: "reading",
      data: { a: 1 },
    });
  });
  it("defaults the event name and ignores comments", () => {
    expect(parseFrame('data: {"x": true}')).toEqual({ event: "message", data: { x: true } });
    expect(parseFrame(": heartbeat")).toBeNull();
  });
  it("rejects malformed data", () => {
    expect(parseFrame("event: x\ndata: {not json")).toBeNull();
  });
});
