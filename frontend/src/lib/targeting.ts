/**
 * Unified targeting — the client side of the shared audience/search model.
 *
 * `Targeting` is "the kind of person we're after": edited in the campaign composer's audience card
 * AND in Find People (same <TargetingEditor>), stored on `Campaign.criteria`, posted to
 * `/people/search`. `evaluateFit` is a byte-for-byte mirror of the backend `app/targeting.py`
 * `evaluate()`, so the composer's live "~N match" estimate agrees with what server-side ranking
 * produces. Keep the two in lockstep — backend tests/test_targeting.py pins the canonical cases.
 *
 * Scoring: each *specified, scorable* field shares a 90-pt budget by weight; a reachable email adds
 * the final 10; capped at 100; "in the audience" at >= FIT_THRESHOLD. An exclude match disqualifies.
 * The search-only fields (seniorities/functions/technologies/keywords) narrow a provider search but
 * aren't scored — a stored contact doesn't carry them.
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

// ---- scoring (mirror of app/targeting.py) ----

const WEIGHTS = { titles: 30, skills: 30, companies: 20, industries: 15, locations: 15, company_sizes: 10 };

const REGION_ALIASES: Record<string, string[]> = {
  eu: ["de", "uk", "nl", "pt", "ie", "fr", "es", "it", "remote · eu"],
  us: ["us", "usa", "united states"],
  remote: ["remote"],
};

export interface FitContact {
  title?: string | null;
  skills?: string[];
  location?: string | null;
  email?: string | null;
  company?: string | null;
  industry?: string | null;
  company_size?: string | null;
}
export interface FitResult {
  score: number;
  matched: boolean;
  reasons: string[];
}

function containsAny(value: string | null | undefined, needles: string[]): boolean {
  const v = (value ?? "").toLowerCase();
  return !!v && needles.some((n) => n && v.includes(n.toLowerCase()));
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
  if (containsAny(c.company, t.exclude_companies) || containsAny(c.title, t.exclude_titles)) {
    return { score: 0, matched: false, reasons: ["excluded by targeting"] };
  }

  const want = (t.skills ?? []).map((s) => s.toLowerCase());
  const have = (c.skills ?? []).map((s) => s.toLowerCase());
  const overlap = want.filter((s) => have.includes(s));

  const titleMatch = containsAny(c.title, t.titles);
  const companyMatch = containsAny(c.company, t.companies);
  const industryMatch = containsAny(c.industry, t.industries);
  const sizeMatch = containsAny(c.company_size, t.company_sizes);
  const locMatch = locationMatches(c.location, t.locations);

  const cats: { weight: number; hit: number }[] = [];
  if (t.titles.length) cats.push({ weight: WEIGHTS.titles, hit: titleMatch ? 1 : 0 });
  if (want.length) cats.push({ weight: WEIGHTS.skills, hit: overlap.length / want.length });
  if (t.companies.length) cats.push({ weight: WEIGHTS.companies, hit: companyMatch ? 1 : 0 });
  if (t.industries.length) cats.push({ weight: WEIGHTS.industries, hit: industryMatch ? 1 : 0 });
  if (t.locations.length) cats.push({ weight: WEIGHTS.locations, hit: locMatch ? 1 : 0 });
  if (t.company_sizes.length) cats.push({ weight: WEIGHTS.company_sizes, hit: sizeMatch ? 1 : 0 });

  const totalW = cats.reduce((s, x) => s + x.weight, 0);
  let score = totalW > 0 ? (90 * cats.reduce((s, x) => s + x.weight * x.hit, 0)) / totalW : 0;
  if (c.email) score += 10;
  score = Math.min(100, Math.round(score));

  const reasons: string[] = [];
  if (overlap.length) reasons.push(`matches ${overlap.join(", ")}`);
  if (titleMatch) reasons.push("title fits the role");
  if (companyMatch) reasons.push("target company");
  if (industryMatch) reasons.push("target industry");
  if (t.locations.length && locMatch) reasons.push("in target location");
  if (sizeMatch) reasons.push("company size fits");
  if (reasons.length === 0) reasons.push("limited overlap with the criteria");

  return { score, matched: score >= FIT_THRESHOLD, reasons };
}
