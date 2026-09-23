-- Durable state for the application worker. SQLite, WAL mode, local filesystem only.
-- Every irreversible or externally visible step has a row written BEFORE it happens.

CREATE TABLE settings (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL
);

-- Canonical job = (portal, tenant, requisition). Provisional jobs are keyed by a normalized URL
-- until the real identity is known, then merged transactionally.
CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,
  portal        TEXT NOT NULL,
  tenant        TEXT NOT NULL DEFAULT '',
  requisition   TEXT NOT NULL DEFAULT '',
  provisional   INTEGER NOT NULL DEFAULT 0,
  resolved_url  TEXT NOT NULL,
  company       TEXT NOT NULL DEFAULT '',
  title         TEXT NOT NULL DEFAULT '',
  location      TEXT NOT NULL DEFAULT '',
  realm_id      TEXT,
  posting_state TEXT NOT NULL DEFAULT 'open',          -- open | closed
  merged_into   TEXT REFERENCES jobs(id),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE job_aliases (
  alias       TEXT PRIMARY KEY,                        -- 'url:<normalized>' | 'board:<board id>'
  job_id      TEXT NOT NULL REFERENCES jobs(id),
  source      TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX job_aliases_job ON job_aliases(job_id);

-- An authentication realm: who issues the identity, not just the hostname.
CREATE TABLE realms (
  id             TEXT PRIMARY KEY,                     -- e.g. 'workday:skyworks'
  portal         TEXT NOT NULL,
  tenant         TEXT NOT NULL,
  allowed_hosts  TEXT NOT NULL,                        -- JSON list; credentials are only ever typed on these
  auth_method    TEXT NOT NULL DEFAULT 'password',     -- password | email_code | magic_link | sso | guest
  verified       INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);

CREATE TABLE accounts (
  id                     TEXT PRIMARY KEY,
  user_id                TEXT NOT NULL,
  realm_id               TEXT NOT NULL REFERENCES realms(id),
  username               TEXT NOT NULL DEFAULT '',
  credential_ref         TEXT NOT NULL DEFAULT '',
  status                 TEXT NOT NULL,
  status_reason          TEXT NOT NULL DEFAULT '',
  auth_method            TEXT NOT NULL DEFAULT 'password',
  failed_logins          INTEGER NOT NULL DEFAULT 0,
  registration_intent_at TEXT,
  verification_ref       TEXT NOT NULL DEFAULT '',        -- vault ref of a pending verification link/code
  override_json          TEXT NOT NULL DEFAULT '{}',      -- user-approved per-account exceptions
  awaiting_since         TEXT,
  last_login_at          TEXT,
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL,
  UNIQUE (user_id, realm_id)
);

CREATE TABLE sessions (
  account_id    TEXT PRIMARY KEY REFERENCES accounts(id),
  state_ref     TEXT NOT NULL,                         -- encrypted file reference, never the cookies themselves
  saved_at      TEXT NOT NULL,
  validated_at  TEXT,
  expires_at    TEXT
);

CREATE TABLE profile_facts (
  id             TEXT PRIMARY KEY,
  predicate      TEXT NOT NULL,
  value_json     TEXT NOT NULL,
  scope_json     TEXT NOT NULL DEFAULT '{"kind":"user"}',
  source_json    TEXT NOT NULL,
  confirmed_at   TEXT NOT NULL,
  valid_from     TEXT,
  valid_until    TEXT,
  sensitivity    TEXT NOT NULL DEFAULT 'normal',       -- normal | sensitive
  supersedes     TEXT,
  superseded_by  TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX profile_facts_pred ON profile_facts(predicate);

-- Answers you gave, and answers derived from facts. Derived answers point at their evidence and are
-- invalidated when that evidence changes; they are never used as evidence themselves.
CREATE TABLE answer_memory (
  id              TEXT PRIMARY KEY,
  semantic_key    TEXT NOT NULL,
  wording         TEXT NOT NULL,
  options_json    TEXT NOT NULL DEFAULT '[]',
  value_json      TEXT NOT NULL,
  scope_json      TEXT NOT NULL DEFAULT '{"kind":"user"}',
  basis           TEXT NOT NULL,                       -- explicit | derived | generated
  evidence_json   TEXT NOT NULL DEFAULT '[]',
  derivation      TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  invalidated_at  TEXT
);
CREATE INDEX answer_memory_key ON answer_memory(semantic_key);

CREATE TABLE form_versions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id       TEXT NOT NULL REFERENCES jobs(id),
  fingerprint  TEXT NOT NULL,
  schema_json  TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  UNIQUE (job_id, fingerprint)
);

CREATE TABLE applications (
  id                 TEXT PRIMARY KEY,
  user_id            TEXT NOT NULL,
  job_id             TEXT NOT NULL REFERENCES jobs(id),
  state              TEXT NOT NULL,
  state_reason       TEXT NOT NULL DEFAULT '',
  needs_json         TEXT NOT NULL DEFAULT '[]',
  retry_at           REAL,
  retries            INTEGER NOT NULL DEFAULT 0,
  cancel_requested   INTEGER NOT NULL DEFAULT 0,
  allow_resubmit     INTEGER NOT NULL DEFAULT 0,
  check_requested    INTEGER NOT NULL DEFAULT 0,            -- run a read-only outcome check (reconcile)
  material_policy    TEXT NOT NULL DEFAULT 'saved_default',
  board_ref          TEXT NOT NULL DEFAULT '',
  submitted_snapshot TEXT,
  employer_status    TEXT NOT NULL DEFAULT '',            -- confirmation | interview | rejected | offer (from email)
  employer_status_at TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (user_id, job_id)
);
CREATE INDEX applications_state ON applications(state);

CREATE TABLE attempts (
  id                TEXT PRIMARY KEY,
  application_id    TEXT NOT NULL REFERENCES applications(id),
  number            INTEGER NOT NULL,
  kind              TEXT NOT NULL DEFAULT 'apply',     -- apply | reconcile
  lease_owner       TEXT,
  lease_expires     REAL,
  actions_used      INTEGER NOT NULL DEFAULT 0,
  action_budget     INTEGER NOT NULL DEFAULT 80,
  submit_intent_at  TEXT,
  submit_clicked_at TEXT,
  outcome           TEXT,
  snapshot_json     TEXT,
  started_at        TEXT NOT NULL,
  ended_at          TEXT
);
-- At most one live attempt per application.
CREATE UNIQUE INDEX attempts_one_live ON attempts(application_id) WHERE ended_at IS NULL;

CREATE TABLE requests (
  request_id     TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,
  response_json  TEXT NOT NULL,
  created_at     TEXT NOT NULL
);

CREATE TABLE events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  at           TEXT NOT NULL,
  entity_kind  TEXT NOT NULL,                          -- application | account | job | mail | system
  entity_id    TEXT NOT NULL,
  kind         TEXT NOT NULL,
  data_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_entity ON events(entity_kind, entity_id);

CREATE TABLE confirmations (
  id              TEXT PRIMARY KEY,
  job_id          TEXT NOT NULL REFERENCES jobs(id),
  application_id  TEXT REFERENCES applications(id),
  account_id      TEXT,
  kind            TEXT NOT NULL,                       -- page | history | email | user
  evidence_json   TEXT NOT NULL,
  provenance      TEXT NOT NULL,
  observed_at     TEXT NOT NULL
);

CREATE TABLE mail_events (
  message_key     TEXT PRIMARY KEY,                    -- stable mailbox message identity
  received_at     TEXT,
  sender          TEXT NOT NULL DEFAULT '',
  subject         TEXT NOT NULL DEFAULT '',
  purpose         TEXT NOT NULL DEFAULT '',            -- activation | login_code | reset | recommendation | confirmation | rejection | interview | other
  decision        TEXT NOT NULL DEFAULT '',            -- consumed | unassigned | ignored | expired | rejected
  account_id      TEXT,
  application_id  TEXT,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  processed_at    TEXT NOT NULL
);

CREATE TABLE artifacts (
  id          TEXT PRIMARY KEY,
  sha256      TEXT NOT NULL,
  kind        TEXT NOT NULL,                           -- resume | cover_letter | other
  name        TEXT NOT NULL,
  path        TEXT NOT NULL,
  version     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

CREATE TABLE locks (
  name     TEXT PRIMARY KEY,
  owner    TEXT NOT NULL,
  expires  REAL NOT NULL
);

-- Required questions no rule or saved answer covers yet. Exported for a preparation pass.
CREATE TABLE pending_questions (
  id              TEXT PRIMARY KEY,
  semantic_key    TEXT NOT NULL,
  question_json   TEXT NOT NULL,
  job_id          TEXT REFERENCES jobs(id),
  application_id  TEXT REFERENCES applications(id),
  first_seen      TEXT NOT NULL,
  last_seen       TEXT NOT NULL,
  resolved_at     TEXT,
  UNIQUE (semantic_key, job_id)
);
