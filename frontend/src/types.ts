export type Evidence = {
  title?: string;
  url?: string;
  source?: string;
  detail?: string;
};
export type Evaluation = {
  result_id: string;
  source: string;
  phase: "open" | "closed";
  produced_at: string;
  covered_at: string | null;
  model: string | null;
  rubric: string | null;
  score_status: string;
  scores: Record<string, number | null>;
  reasoning: Record<string, string>;
  evidence: Record<string, unknown>;
};
export type Action = {
  id: string;
  title: string;
  owner: string;
  due_at: string | null;
  state: string;
  note: string;
  completed_at: string | null;
};
export type Draft = {
  id: string;
  kind: "customer" | "engineering" | "internal" | "feedback";
  variant: "standard" | "detailed" | "concise" | "high_level";
  scenario: "update" | "follow_up";
  use_when: string;
  review_notes: string;
  title: string;
  body: string;
  state: string;
  updated_at: string;
  evidence: Evidence[];
};
export type WritingStyle = {
  learning_enabled: boolean;
  audiences: Record<
    string,
    {
      preferences: string;
      sample_count: number;
      confidence: string;
      observations: string[];
      historical_source_count: number;
      historical_observations: string[];
    }
  >;
  examples: {
    id: string;
    audience: string;
    origin: string;
    source_label: string;
    preview: string;
  }[];
};
export type Analysis = {
  summary: string;
  concerns: { title?: string; reason?: string; metric?: string }[];
  next_actions: unknown[];
  evidence: Evidence[];
  coverage: { name?: string; state?: string; detail?: string; tool?: string }[];
  gaps: string[];
};
export type Outcome = {
  action_id: string;
  title: string;
  completed_at: string;
  before: Evaluation | null;
  after: Evaluation | null;
  deltas: Record<string, number>;
};
export type Case = {
  id: string;
  number: string;
  account: string;
  subject: string;
  status: string;
  priority: string;
  owner_email: string;
  is_closed: boolean;
  closed_at: string | null;
  updated_at: string;
  source: string;
  revision: string;
  active: boolean;
  observed_at: string;
  analysis: Analysis | null;
  analysis_stale: boolean;
  evaluation: Evaluation | null;
  draft_count: number;
  actions: Action[];
  history?: Evaluation[];
  drafts?: Draft[];
  usage?: Record<string, unknown>[];
  events?: { id: string; kind: string; detail: string; created_at: string }[];
  outcomes?: Outcome[];
};
export type Source = {
  key: string;
  state: string;
  message: string;
  checked_at: string;
  login_url: string | null;
};
export type Run = {
  id: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  state: string;
  stage: string;
  detail: string | null;
  closed_review: number;
};
export type Worker = {
  key: string;
  name: string;
  role: string;
  state: string;
  case_id?: string;
  error?: string;
  started_at?: string;
  tab_id: string;
};
export type Overview = {
  owner: string;
  timezone: string;
  schedule: string[];
  days: string;
  inventory_complete: boolean;
  inventory_source: string;
  collected_at: string | null;
  notes: string[];
  sources: Source[];
  runs: Run[];
  workers: Worker[];
  good_threshold: number;
};
export type Lesson = {
  id: string;
  title: string;
  lesson: string;
  supporting_cases: string[];
  limitations: string;
  state: string;
  created_at: string;
  reviewed_at: string | null;
};
