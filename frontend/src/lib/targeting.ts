/**
 * Unified targeting — the client side of the shared audience/search model.
 *
 * `Targeting` is "the kind of person we're after": edited in the campaign composer's audience card
 * AND in Find People (same <TargetingEditor>), stored on `Campaign.criteria`, posted to
 * `/people/search`. `evaluateFit` is a byte-for-byte mirror of the backend `app/targeting.py`
 * `evaluate()`, so the composer's live "~N match" estimate agrees with what server-side ranking
 * produces. Keep the two in lockstep — backend tests/test_targeting.py pins the canonical cases.
 *
 * Two axes: `evaluateFit` → Fit 0-100 (weighted fraction of *specified* criteria matched; skills
 * fractional; excludes hard-disqualify; "in the audience" at >= FIT_THRESHOLD) and `reachability`
 * → whether we can act on them. Fit is NOT affected by having an email. Titles/companies match on
 * substring ("VP" ⊇ "SVP"); excludes on word boundaries ("intern" ≠ "International"); seniority/
 * function via a synonym taxonomy. technologies/keywords stay search-only but give a neutral floor
 * when they're the only criteria.
 */
export const FIT_THRESHOLD = 40;

export interface Targeting {
  titles: string[];
  seniorities: string[];
  functions: string[];
  skills: string[];
  locations: string[];
  companies: string[];
  industries: string[];
  company_sizes: string[];
  technologies: string[];
  keywords: string;
  exclude_companies: string[];
  exclude_titles: string[];
}

export function emptyTargeting(): Targeting {
  return {
    titles: [],
    seniorities: [],
    functions: [],
    skills: [],
    locations: [],
    companies: [],
    industries: [],
    company_sizes: [],
    technologies: [],
    keywords: "",
    exclude_companies: [],
    exclude_titles: [],
  };
}

/** Coerce a stored `Campaign.criteria` (partial / legacy {titles,skills,locations}) into Targeting. */
export function toTargeting(c: Partial<Targeting> | null | undefined): Targeting {
  return { ...emptyTargeting(), ...(c ?? {}), keywords: c?.keywords ?? "" };
}

export type ChipField = Exclude<keyof Targeting, "keywords">;

/** True if the targeting specifies anything at all (any chip field or free-text keywords). */
export function targetingHasCriteria(t: Targeting): boolean {
  if (t.keywords.trim().length > 0) return true;
  return (Object.keys(t) as (keyof Targeting)[]).some(
    (k) => k !== "keywords" && (t[k] as string[]).length > 0,
  );
}

export const TARGETING_FIELDS: {
  key: ChipField;
  label: string;
  group: "person" | "company" | "exclude";
  scored: boolean;
  placeholder: string;
}[] = [
  { key: "titles", label: "Titles", group: "person", scored: true, placeholder: "VP of Sales…" },
  { key: "seniorities", label: "Seniority", group: "person", scored: false, placeholder: "Director, VP…" },
  { key: "functions", label: "Function", group: "person", scored: false, placeholder: "Engineering, Sales…" },
  { key: "skills", label: "Skills", group: "person", scored: true, placeholder: "Salesforce…" },
  { key: "locations", label: "Locations", group: "person", scored: true, placeholder: "EU, Berlin…" },
  { key: "companies", label: "Companies", group: "company", scored: true, placeholder: "Acme, Globex…" },
  { key: "industries", label: "Industries", group: "company", scored: true, placeholder: "Fintech…" },
  { key: "company_sizes", label: "Company size", group: "company", scored: true, placeholder: "51-200, 501-1,000…" },
  { key: "technologies", label: "Technologies", group: "company", scored: false, placeholder: "React, AWS…" },
  { key: "exclude_companies", label: "Exclude companies", group: "exclude", scored: true, placeholder: "Competitor Inc…" },
  { key: "exclude_titles", label: "Exclude titles", group: "exclude", scored: true, placeholder: "Intern, Student…" },
];

// ---- scoring (mirror of app/targeting.py — keep byte-for-byte) ----

const WEIGHTS = {
  titles: 30,
  skills: 30,
  companies: 20,
  seniorities: 20,
  industries: 15,
  locations: 15,
  functions: 10,
  company_sizes: 10,
};

const SEARCH_ONLY_FLOOR = 50;

const REGION_ALIASES: Record<string, string[]> = {
  eu: ["de", "uk", "nl", "pt", "ie", "fr", "es", "it", "remote · eu"],
  us: ["us", "usa", "united states"],
  remote: ["remote"],
};

const SENIORITY_ALIASES: Record<string, string> = {
  "vice president": "vp", vp: "vp", svp: "vp", evp: "vp",
  "c-level": "exec", "c-suite": "exec", cxo: "exec", chief: "exec",
  ceo: "exec", cto: "exec", cfo: "exec", coo: "exec",
  founder: "exec", owner: "exec", president: "exec", partner: "exec",
  director: "director", dir: "director", head: "director",
  senior: "senior", sr: "senior",
  lead: "lead", staff: "lead", principal: "lead",
  manager: "manager", mgr: "manager",
  mid: "mid", intermediate: "mid",
  junior: "junior", jr: "junior", entry: "junior", intern: "junior", associate: "junior",
};
const FUNCTION_ALIASES: Record<string, string> = {
  engineering: "engineering", eng: "engineering", software: "engineering",
  developer: "engineering", development: "engineering", dev: "engineering",
  sales: "sales",
  marketing: "marketing", growth: "marketing",
  product: "product", design: "design", data: "data",
  operations: "operations", ops: "operations",
  finance: "finance", accounting: "finance",
  people: "people", hr: "people", "human resources": "people", recruiting: "people",
  support: "support", "customer success": "support",
  legal: "legal",
};

export type Reachability = "verified" | "reachable" | "needs_enrichment";

export interface FitContact {
  title?: string | null;
  skills?: string[];
  location?: string | null;
  email?: string | null;
  email_status?: string | null;
  linkedin_url?: string | null;
  company?: string | null;
  industry?: string | null;
  company_size?: string | null;
  seniority?: string | null;
  function?: string | null;
}
export interface FitResult {
  score: number;
  matched: boolean;
  reasons: string[];
}

/** Permissive substring (so "VP" matches "SVP"). Used for positive title/company matches. */
function containsAny(value: string | null | undefined, needles: string[]): boolean {
  const v = (value ?? "").toLowerCase();
  return !!v && needles.some((n) => n && v.includes(n.toLowerCase()));
}

const isAlnum = (ch: string): boolean => /[\p{L}\p{N}]/u.test(ch);

/** Precise whole-word containment (so "intern" ≠ "International"). Used for excludes. */
function wordContains(value: string | null | undefined, needle: string): boolean {
  const v = (value ?? "").toLowerCase();
  const n = needle.toLowerCase().trim();
  if (!v || !n) return false;
  let start = 0;
  for (;;) {
    const i = v.indexOf(n, start);
    if (i < 0) return false;
    const before = i > 0 ? v[i - 1] : "";
    const after = i + n.length < v.length ? v[i + n.length] : "";
    if (!isAlnum(before) && !isAlnum(after)) return true;
    start = i + 1;
  }
}

function anyWord(value: string | null | undefined, needles: string[]): boolean {
  return needles.some((n) => n && wordContains(value, n));
}

function canon(value: string | null | undefined, aliases: Record<string, string>): string {
  const k = (value ?? "").toLowerCase().trim();
  return aliases[k] ?? k;
}

/** Normalized-synonym equality for the single-token fields (seniority / function). */
function bucketMatch(
  value: string | null | undefined,
  crits: string[],
  aliases: Record<string, string>,
): boolean {
  if (crits.length === 0) return false;
  const cv = canon(value, aliases);
  if (!cv) return false;
  const set = new Set(crits.map((c) => canon(c, aliases)));
  return set.has(cv);
}

function locationMatches(loc: string | null | undefined, crits: string[]): boolean {
  if (crits.length === 0) return true;
  const cl = (loc ?? "").toLowerCase();
  return crits.some((c) => {
    const k = c.toLowerCase();
    if (cl.includes(k)) return true;
    return (REGION_ALIASES[k] ?? []).some((tok) => cl.includes(tok));
  });
}

export function evaluateFit(c: FitContact, t: Targeting): FitResult {
  if (anyWord(c.company, t.exclude_companies) || anyWord(c.title, t.exclude_titles)) {
    return { score: 0, matched: false, reasons: ["excluded by targeting"] };
  }

  const want = (t.skills ?? []).map((s) => s.toLowerCase());
  const have = (c.skills ?? []).map((s) => s.toLowerCase());
  const overlap = want.filter((w) => have.some((h) => wordContains(h, w)));

  const titleMatch = containsAny(c.title, t.titles);
  const companyMatch = containsAny(c.company, t.companies);
  const industryMatch = containsAny(c.industry, t.industries);
  const sizeMatch = containsAny(c.company_size, t.company_sizes);
  const locMatch = locationMatches(c.location, t.locations);
  const senMatch = bucketMatch(c.seniority, t.seniorities, SENIORITY_ALIASES);
  const fnMatch = bucketMatch(c.function, t.functions, FUNCTION_ALIASES);

  const cats: { weight: number; hit: number }[] = [];
  if (t.titles.length) cats.push({ weight: WEIGHTS.titles, hit: titleMatch ? 1 : 0 });
  if (want.length) cats.push({ weight: WEIGHTS.skills, hit: overlap.length / want.length });
  if (t.companies.length) cats.push({ weight: WEIGHTS.companies, hit: companyMatch ? 1 : 0 });
  if (t.seniorities.length) cats.push({ weight: WEIGHTS.seniorities, hit: senMatch ? 1 : 0 });
  if (t.industries.length) cats.push({ weight: WEIGHTS.industries, hit: industryMatch ? 1 : 0 });
  if (t.locations.length) cats.push({ weight: WEIGHTS.locations, hit: locMatch ? 1 : 0 });
  if (t.functions.length) cats.push({ weight: WEIGHTS.functions, hit: fnMatch ? 1 : 0 });
  if (t.company_sizes.length) cats.push({ weight: WEIGHTS.company_sizes, hit: sizeMatch ? 1 : 0 });

  const totalW = cats.reduce((s, x) => s + x.weight, 0);
  let fit: number;
  if (totalW > 0) fit = (100 * cats.reduce((s, x) => s + x.weight * x.hit, 0)) / totalW;
  else if (t.technologies.length || t.keywords.trim()) fit = SEARCH_ONLY_FLOOR;
  else fit = 0;
  const score = Math.min(100, Math.round(fit));

  const reasons: string[] = [];
  if (overlap.length) reasons.push(`matches ${overlap.join(", ")}`);
  if (titleMatch) reasons.push("title fits the role");
  if (senMatch) reasons.push("seniority fits");
  if (fnMatch) reasons.push("right function");
  if (companyMatch) reasons.push("target company");
  if (industryMatch) reasons.push("target industry");
  if (t.locations.length && locMatch) reasons.push("in target location");
  if (sizeMatch) reasons.push("company size fits");
  if (reasons.length === 0) reasons.push("limited overlap with the criteria");

  return { score, matched: score >= FIT_THRESHOLD, reasons };
}

/** A separate axis from fit: can we act on this candidate? Mirror of `reachability()`. */
export function reachability(c: FitContact): Reachability {
  if (c.email_status === "valid") return "verified";
  if (c.email || c.linkedin_url) return "reachable";
  return "needs_enrichment";
}
