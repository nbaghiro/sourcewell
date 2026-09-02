import { describe, expect, it } from "vitest";

import { showsEmptyState } from "./inbox-page";

/**
 * The inbox list is built from *messages*, so a conversation opened with "Message" on a contact
 * has no row in it until something is sent. When the workspace had no messages at all, the empty
 * state replaced the entire grid — including the thread pane — so clicking Message landed you on
 * "No messages yet" with no way to reach the person, whether or not they were in a campaign.
 */
describe("inbox empty state", () => {
  const base = { loading: false, rowCount: 0, selected: null, conversationMissing: false };

  it("shows the placeholder on an empty inbox with nothing open", () => {
    expect(showsEmptyState(base)).toBe(true);
  });

  it("yields to a thread we were asked to open, even with no rows", () => {
    expect(showsEmptyState({ ...base, selected: "enr_1" })).toBe(false);
  });

  it("still shows the placeholder when that thread doesn't resolve", () => {
    // A stale `?enrollment=` link — otherwise the pane hangs on skeletons forever.
    expect(
      showsEmptyState({ ...base, selected: "enr_gone", conversationMissing: true }),
    ).toBe(true);
  });

  it("never shows the placeholder once there are rows", () => {
    expect(showsEmptyState({ ...base, rowCount: 3 })).toBe(false);
    expect(showsEmptyState({ ...base, rowCount: 3, selected: "enr_1" })).toBe(false);
  });

  it("never flashes the placeholder while the first load is in flight", () => {
    expect(showsEmptyState({ ...base, loading: true })).toBe(false);
  });
});
