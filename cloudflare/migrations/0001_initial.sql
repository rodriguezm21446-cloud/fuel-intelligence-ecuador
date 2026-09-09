CREATE TABLE IF NOT EXISTS source_state (
  source_id TEXT PRIMARY KEY,
  etag TEXT,
  last_modified TEXT,
  body_hash TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  error_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS event_candidates (
  event_key TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  status TEXT NOT NULL,
  dispatch_attempts INTEGER NOT NULL DEFAULT 0,
  dispatched_at TEXT,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS event_candidates_recent_idx
ON event_candidates(detected_at DESC);

CREATE INDEX IF NOT EXISTS event_candidates_status_idx
ON event_candidates(status, detected_at);
