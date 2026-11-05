-- 013_hybrid_search_tsvector.sql
-- A lexical leg for vector_search: a tsvector over title/stack/description, so
-- an exact token match (e.g. "kafka") surfaces even when bge-m3 encodes the word
-- toward a non-technical sense. Fused with the dense cosine ranking via
-- reciprocal-rank fusion in storage/vector_search.py.
--
-- A trigger, not a GENERATED column: the only ways to fold text[] into text
-- (array_to_string, to_jsonb, ::text) are STABLE, and generated columns require
-- an IMMUTABLE expression. The trigger is the idiom that still precomputes and
-- indexes the value.
--
-- 'simple' config, not 'english': the corpus is EN+RU, and 'simple' lowercases
-- and tokenizes without stemming (and has no stopwords), so it does
-- cross-language exact-token match. stack is already folded by normalize.alias().
-- Re-runnable: applying twice is a no-op.

BEGIN;

ALTER TABLE structured_postings
    ADD COLUMN IF NOT EXISTS search_tsv tsvector;

-- Backfill rows that predate the column (the frozen eval snapshot is a
-- dump-restore). Done before the trigger exists so it is a plain UPDATE.
UPDATE structured_postings
   SET search_tsv = to_tsvector('simple',
        coalesce(title, '') || ' ' ||
        array_to_string(stack, ' ') || ' ' ||
        coalesce(description, ''))
 WHERE search_tsv IS NULL;

CREATE INDEX IF NOT EXISTS structured_postings_search_tsv_gin
    ON structured_postings USING gin (search_tsv);

CREATE OR REPLACE FUNCTION structured_postings_search_tsv_update()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv := to_tsvector('simple',
        coalesce(NEW.title, '') || ' ' ||
        array_to_string(NEW.stack, ' ') || ' ' ||
        coalesce(NEW.description, ''));
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS structured_postings_search_tsv_trigger ON structured_postings;
CREATE TRIGGER structured_postings_search_tsv_trigger
    BEFORE INSERT OR UPDATE OF title, stack, description ON structured_postings
    FOR EACH ROW EXECUTE FUNCTION structured_postings_search_tsv_update();

COMMIT;
