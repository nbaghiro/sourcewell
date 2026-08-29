// Pins evaluateFit to the shared canonical case table (shared/targeting-cases.json), the same
// table backend/tests/test_targeting.py runs through app/targeting.py. The two evaluators are
// byte-for-byte mirrors; a scoring change must land on both sides plus the table, or a suite fails.
import { describe, expect, it } from "vitest";

import cases from "../../../shared/targeting-cases.json";
import { evaluateFit, reachability, toTargeting, type FitContact, type Targeting } from "./targeting";

describe("evaluateFit mirrors app/targeting.py::evaluate", () => {
  for (const c of cases) {
    it(c.name, () => {
      const contact = c.contact as FitContact;
      const targeting = toTargeting(c.targeting as Partial<Targeting>);
      expect(evaluateFit(contact, targeting).score).toBe(c.fit);
    });
  }
});

describe("exclude and reachability semantics", () => {
  it("an exclude match hard-disqualifies an otherwise-perfect match", () => {
    const t = toTargeting({ titles: ["VP of Sales"], exclude_titles: ["intern"] });
    const res = evaluateFit({ title: "VP of Sales (intern program)" }, t);
    expect(res.score).toBe(0);
    expect(res.reasons.join(" ")).toContain("exclud");
  });

  it("reachability is a separate axis from fit", () => {
    expect(reachability({ email: "a@b.com", email_status: "valid" })).toBe("verified");
    expect(reachability({ email: "a@b.com" })).toBe("reachable");
    expect(reachability({ linkedin_url: "https://linkedin.com/in/x" })).toBe("reachable");
    expect(reachability({})).toBe("needs_enrichment");
  });
});
