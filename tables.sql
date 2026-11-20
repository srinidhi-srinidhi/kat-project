-- Etown Database Schema Snapshot
-- Generated on 2026-10-27 15:11:54.421800

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- ============================================================
-- Schema: public
-- ============================================================

CREATE OR REPLACE FUNCTION public.vector_in(cstring, oid, integer)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_in$function$
;
CREATE OR REPLACE FUNCTION public.vector_out(vector)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_out$function$
;
CREATE OR REPLACE FUNCTION public.vector_typmod_in(cstring[])
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_typmod_in$function$
;
CREATE OR REPLACE FUNCTION public.vector_recv(internal, oid, integer)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_recv$function$
;
CREATE OR REPLACE FUNCTION public.vector_send(vector)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_send$function$
;
CREATE OR REPLACE FUNCTION public.l2_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$l2_distance$function$
;
CREATE OR REPLACE FUNCTION public.inner_product(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$inner_product$function$
;
CREATE OR REPLACE FUNCTION public.cosine_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$cosine_distance$function$
;
CREATE OR REPLACE FUNCTION public.l1_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$l1_distance$function$
;
CREATE OR REPLACE FUNCTION public.vector_dims(vector)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_dims$function$
;
CREATE OR REPLACE FUNCTION public.vector_norm(vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_norm$function$
;
CREATE OR REPLACE FUNCTION public.l2_normalize(vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$l2_normalize$function$
;
CREATE OR REPLACE FUNCTION public.binary_quantize(vector)
 RETURNS bit
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$binary_quantize$function$
;
CREATE OR REPLACE FUNCTION public.subvector(vector, integer, integer)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$subvector$function$
;
CREATE OR REPLACE FUNCTION public.vector_add(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_add$function$
;
CREATE OR REPLACE FUNCTION public.vector_sub(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_sub$function$
;
CREATE OR REPLACE FUNCTION public.vector_mul(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_mul$function$
;
CREATE OR REPLACE FUNCTION public.vector_concat(vector, vector)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_concat$function$
;
CREATE OR REPLACE FUNCTION public.vector_lt(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_lt$function$
;
CREATE OR REPLACE FUNCTION public.vector_le(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_le$function$
;
CREATE OR REPLACE FUNCTION public.vector_eq(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_eq$function$
;
CREATE OR REPLACE FUNCTION public.vector_ne(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_ne$function$
;
CREATE OR REPLACE FUNCTION public.vector_ge(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_ge$function$
;
CREATE OR REPLACE FUNCTION public.vector_gt(vector, vector)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_gt$function$
;
CREATE OR REPLACE FUNCTION public.vector_cmp(vector, vector)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_cmp$function$
;
CREATE OR REPLACE FUNCTION public.vector_l2_squared_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_l2_squared_distance$function$
;
CREATE OR REPLACE FUNCTION public.vector_negative_inner_product(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_negative_inner_product$function$
;
CREATE OR REPLACE FUNCTION public.vector_spherical_distance(vector, vector)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_spherical_distance$function$
;
CREATE OR REPLACE FUNCTION public.vector_accum(double precision[], vector)
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_accum$function$
;
CREATE OR REPLACE FUNCTION public.vector_avg(double precision[])
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_avg$function$
;
CREATE OR REPLACE FUNCTION public.vector_combine(double precision[], double precision[])
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_combine$function$
;
CREATE OR REPLACE FUNCTION public.vector(vector, integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector$function$
;
CREATE OR REPLACE FUNCTION public.array_to_vector(integer[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;
CREATE OR REPLACE FUNCTION public.array_to_vector(real[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;
CREATE OR REPLACE FUNCTION public.array_to_vector(double precision[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;
CREATE OR REPLACE FUNCTION public.array_to_vector(numeric[], integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_vector$function$
;
CREATE OR REPLACE FUNCTION public.vector_to_float4(vector, integer, boolean)
 RETURNS real[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_to_float4$function$
;
CREATE OR REPLACE FUNCTION public.ivfflathandler(internal)
 RETURNS index_am_handler
 LANGUAGE c
AS '$libdir/vector', $function$ivfflathandler$function$
;
CREATE OR REPLACE FUNCTION public.hnswhandler(internal)
 RETURNS index_am_handler
 LANGUAGE c
AS '$libdir/vector', $function$hnswhandler$function$
;
CREATE OR REPLACE FUNCTION public.ivfflat_halfvec_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$ivfflat_halfvec_support$function$
;
CREATE OR REPLACE FUNCTION public.ivfflat_bit_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$ivfflat_bit_support$function$
;
CREATE OR REPLACE FUNCTION public.hnsw_halfvec_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$hnsw_halfvec_support$function$
;
CREATE OR REPLACE FUNCTION public.hnsw_bit_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$hnsw_bit_support$function$
;
CREATE OR REPLACE FUNCTION public.hnsw_sparsevec_support(internal)
 RETURNS internal
 LANGUAGE c
AS '$libdir/vector', $function$hnsw_sparsevec_support$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_in(cstring, oid, integer)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_in$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_out(halfvec)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_out$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_typmod_in(cstring[])
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_typmod_in$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_recv(internal, oid, integer)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_recv$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_send(halfvec)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_send$function$
;
CREATE OR REPLACE FUNCTION public.l2_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_distance$function$
;
CREATE OR REPLACE FUNCTION public.inner_product(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_inner_product$function$
;
CREATE OR REPLACE FUNCTION public.cosine_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_cosine_distance$function$
;
CREATE OR REPLACE FUNCTION public.l1_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l1_distance$function$
;
CREATE OR REPLACE FUNCTION public.vector_dims(halfvec)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_vector_dims$function$
;
CREATE OR REPLACE FUNCTION public.l2_norm(halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_norm$function$
;
CREATE OR REPLACE FUNCTION public.l2_normalize(halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_normalize$function$
;
CREATE OR REPLACE FUNCTION public.binary_quantize(halfvec)
 RETURNS bit
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_binary_quantize$function$
;
CREATE OR REPLACE FUNCTION public.subvector(halfvec, integer, integer)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_subvector$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_add(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_add$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_sub(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_sub$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_mul(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_mul$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_concat(halfvec, halfvec)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_concat$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_lt(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_lt$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_le(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_le$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_eq(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_eq$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_ne(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_ne$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_ge(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_ge$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_gt(halfvec, halfvec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_gt$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_cmp(halfvec, halfvec)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_cmp$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_l2_squared_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_l2_squared_distance$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_negative_inner_product(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_negative_inner_product$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_spherical_distance(halfvec, halfvec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_spherical_distance$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_accum(double precision[], halfvec)
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_accum$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_avg(double precision[])
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_avg$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_combine(double precision[], double precision[])
 RETURNS double precision[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_combine$function$
;
CREATE OR REPLACE FUNCTION public.halfvec(halfvec, integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_to_vector(halfvec, integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_to_vector$function$
;
CREATE OR REPLACE FUNCTION public.vector_to_halfvec(vector, integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_to_halfvec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_halfvec(integer[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_halfvec(real[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_halfvec(double precision[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_halfvec(numeric[], integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_halfvec$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_to_float4(halfvec, integer, boolean)
 RETURNS real[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_to_float4$function$
;
CREATE OR REPLACE FUNCTION public.hamming_distance(bit, bit)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$hamming_distance$function$
;
CREATE OR REPLACE FUNCTION public.jaccard_distance(bit, bit)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$jaccard_distance$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_in(cstring, oid, integer)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_in$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_out(sparsevec)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_out$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_typmod_in(cstring[])
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_typmod_in$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_recv(internal, oid, integer)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_recv$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_send(sparsevec)
 RETURNS bytea
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_send$function$
;
CREATE OR REPLACE FUNCTION public.l2_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_distance$function$
;
CREATE OR REPLACE FUNCTION public.inner_product(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_inner_product$function$
;
CREATE OR REPLACE FUNCTION public.cosine_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_cosine_distance$function$
;
CREATE OR REPLACE FUNCTION public.l1_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l1_distance$function$
;
CREATE OR REPLACE FUNCTION public.l2_norm(sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_norm$function$
;
CREATE OR REPLACE FUNCTION public.l2_normalize(sparsevec)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_normalize$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_lt(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_lt$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_le(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_le$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_eq(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_eq$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_ne(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_ne$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_ge(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_ge$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_gt(sparsevec, sparsevec)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_gt$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_cmp(sparsevec, sparsevec)
 RETURNS integer
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_cmp$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_l2_squared_distance(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_l2_squared_distance$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_negative_inner_product(sparsevec, sparsevec)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_negative_inner_product$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec(sparsevec, integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.vector_to_sparsevec(vector, integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$vector_to_sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_to_vector(sparsevec, integer, boolean)
 RETURNS vector
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_to_vector$function$
;
CREATE OR REPLACE FUNCTION public.halfvec_to_sparsevec(halfvec, integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$halfvec_to_sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.sparsevec_to_halfvec(sparsevec, integer, boolean)
 RETURNS halfvec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$sparsevec_to_halfvec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_sparsevec(integer[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_sparsevec(real[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_sparsevec(double precision[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.array_to_sparsevec(numeric[], integer, boolean)
 RETURNS sparsevec
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/vector', $function$array_to_sparsevec$function$
;
CREATE OR REPLACE FUNCTION public.add_town_view(town_id text, user_id text)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
    BEGIN
        INSERT INTO public.etown_town_view_tracking (townprofileid, userprofileid, view_date)
        VALUES (town_id, TRIM(LOWER(user_id)), CURRENT_DATE);

        RETURN TRUE;
    END;
    $function$
;
CREATE OR REPLACE FUNCTION public.sync_mem0_columns()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
            BEGIN
                -- Sync all columns from the JSON payload
                NEW.facts := NEW.payload ->> 'data';
                NEW.userprofileid := NEW.payload ->> 'user_id';
                NEW.conversation_id := NEW.payload ->> 'conversation_id';
                
                -- Default name to 'Unknown' if missing
                NEW.name := COALESCE(NEW.payload ->> 'user_name', 'Unknown');

                -- Handle timestamp
                IF NEW.payload ->> 'created_at' IS NOT NULL THEN
                     NEW.created_at := (NEW.payload ->> 'created_at')::timestamp;
                ELSE
                     NEW.created_at := NOW();
                END IF;

                RETURN NEW;
            END;
            $function$
;
CREATE OR REPLACE FUNCTION public.get_town_view_count(town_id text)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
    BEGIN
        RETURN (SELECT COUNT(*) FROM etown_town_view_tracking WHERE townprofileid = town_id);
    END;
    $function$
;
CREATE OR REPLACE FUNCTION public.get_provider_view_count(provider_id text)
 RETURNS integer
 LANGUAGE sql
 STABLE
AS $function$
    SELECT COUNT(DISTINCT v.userprofileid)::integer
    FROM public.etown_provider_views v
    LEFT JOIN public.etown_userprofile up ON up.userprofileid = v.userprofileid
    WHERE v.providerprofileid = provider_id
      AND v.userprofileid IS NOT NULL
      AND v.userprofileid <> provider_id
      AND NOT public.etown_is_deleted(v.userprofileid)
      AND NOT public.etown_is_deleted(up.contactid)
$function$
;
CREATE OR REPLACE FUNCTION public.normalize_category(category text)
 RETURNS character varying
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
DECLARE
    normalized TEXT;
BEGIN
    -- Handle null or empty input
    IF category IS NULL OR TRIM(category) = '' THEN
        RETURN 'other';
    END IF;
    
    -- Clean and normalize the category
    normalized := LOWER(TRIM(category));
    
    -- Remove special characters (keep only alphanumeric and spaces)
    normalized := REGEXP_REPLACE(normalized, '[^\w\s]', '', 'g');
    
    -- Replace multiple spaces/whitespace with single underscore
    normalized := REGEXP_REPLACE(normalized, '\s+', '_', 'g');
    
    -- Limit length to 50 characters
    normalized := LEFT(normalized, 50);
    
    -- Return 'other' if result is empty
    IF normalized = '' THEN
        RETURN 'other';
    END IF;
    
    RETURN normalized;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_category_display_name(normalized_category character varying)
 RETURNS character varying
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    display_name VARCHAR(100);
BEGIN
    -- Look up display name from patterns table
    SELECT p.display_name INTO display_name
    FROM category_patterns p
    WHERE p.category_name = normalized_category
    AND p.is_active = TRUE
    LIMIT 1;
    
    -- If found, return it
    IF display_name IS NOT NULL THEN
        RETURN display_name;
    END IF;
    
    -- Handle null or 'other' category
    IF normalized_category IS NULL OR normalized_category = 'other' THEN
        RETURN 'Other Services';
    END IF;
    
    -- Convert underscores to spaces and title case as fallback
    RETURN INITCAP(REPLACE(normalized_category, '_', ' '));
END;
$function$
;
CREATE OR REPLACE FUNCTION public.classify_service_regex(business_name text, service_description text, current_category text DEFAULT NULL::text)
 RETURNS character varying
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE
    pattern_record RECORD;
    combined_text TEXT;
    found_category VARCHAR(50) := NULL;
BEGIN
    -- Prepare text for matching (business name + description + current category)
    combined_text := COALESCE(business_name, '') || ' ' || 
                    COALESCE(service_description, '') || ' ' || 
                    COALESCE(current_category, '');
    combined_text := LOWER(TRIM(combined_text));
    
    -- Return 'other' if no text to analyze
    IF combined_text = '' OR combined_text = '  ' THEN
        RETURN 'other';
    END IF;
    
    -- Loop through patterns in priority order
    FOR pattern_record IN 
        SELECT category_name, regex_pattern, pattern_type
        FROM category_patterns 
        WHERE is_active = TRUE 
        ORDER BY priority ASC
    LOOP
        -- Check if pattern matches based on pattern_type
        IF pattern_record.pattern_type = 'both' THEN
            -- Check against combined text
            IF combined_text ~* pattern_record.regex_pattern THEN
                found_category := pattern_record.category_name;
                EXIT;
            END IF;
        ELSIF pattern_record.pattern_type = 'name' THEN
            -- Check only against business name
            IF COALESCE(LOWER(business_name), '') ~* pattern_record.regex_pattern THEN
                found_category := pattern_record.category_name;
                EXIT;
            END IF;
        ELSIF pattern_record.pattern_type = 'description' THEN
            -- Check only against description
            IF COALESCE(LOWER(service_description), '') ~* pattern_record.regex_pattern THEN
                found_category := pattern_record.category_name;
                EXIT;
            END IF;
        END IF;
    END LOOP;
    
    -- Return found category or 'other' as default
    RETURN COALESCE(found_category, 'other');
END;
$function$
;
CREATE OR REPLACE FUNCTION public.bulk_categorize_services(p_table_name text DEFAULT 'businesses'::text)
 RETURNS TABLE(record_id integer, old_category character varying, new_category character varying)
 LANGUAGE plpgsql
AS $function$
DECLARE
    sql_query TEXT;
BEGIN
    -- Build dynamic SQL to update records
    sql_query := format('
        UPDATE %I 
        SET ai_category = classify_service_regex(business_name, service_description, current_category)
        WHERE ai_category IS NULL OR ai_category = ''other'' OR ai_category = ''''
        RETURNING id, COALESCE(ai_category, ''other'') as old_category, 
                 classify_service_regex(business_name, service_description, current_category) as new_category',
        p_table_name);
    
    RETURN QUERY EXECUTE sql_query;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.test_categorization(p_business_name text, p_service_description text, p_current_category text DEFAULT NULL::text)
 RETURNS TABLE(suggested_category character varying, display_name character varying, matched_pattern text)
 LANGUAGE plpgsql
AS $function$
DECLARE
    category VARCHAR(50);
    pattern_used TEXT;
BEGIN
    -- Get the category
    category := classify_service_regex(p_business_name, p_service_description, p_current_category);
    
    -- Find which pattern was used
    SELECT regex_pattern INTO pattern_used
    FROM category_patterns 
    WHERE category_name = category AND is_active = TRUE
    ORDER BY priority ASC
    LIMIT 1;
    
    RETURN QUERY 
    SELECT 
        category,
        get_category_display_name(category),
        COALESCE(pattern_used, 'Default fallback');
END;
$function$
;
CREATE OR REPLACE FUNCTION public.add_category_pattern(p_category_name character varying, p_display_name character varying, p_pattern_type character varying, p_regex_pattern text, p_priority integer DEFAULT 500)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
BEGIN
    INSERT INTO category_patterns (
        category_name, display_name, pattern_type, regex_pattern, priority
    ) VALUES (
        p_category_name, p_display_name, p_pattern_type, p_regex_pattern, p_priority
    );
    RETURN TRUE;
EXCEPTION
    WHEN OTHERS THEN
        RETURN FALSE;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_categorization_stats(p_table_name text DEFAULT 'businesses'::text)
 RETURNS TABLE(category character varying, display_name character varying, count bigint, percentage numeric)
 LANGUAGE plpgsql
AS $function$
DECLARE
    sql_query TEXT;
    total_count BIGINT;
BEGIN
    -- Get total count
    sql_query := format('SELECT COUNT(*) FROM %I WHERE ai_category IS NOT NULL', p_table_name);
    EXECUTE sql_query INTO total_count;
    
    -- Return categorization breakdown
    sql_query := format('
        SELECT 
            COALESCE(ai_category, ''uncategorized'') as category,
            get_category_display_name(ai_category) as display_name,
            COUNT(*) as count,
            ROUND((COUNT(*) * 100.0 / %s), 2) as percentage
        FROM %I 
        WHERE ai_category IS NOT NULL
        GROUP BY ai_category
        ORDER BY COUNT(*) DESC',
        total_count, p_table_name);
    
    RETURN QUERY EXECUTE sql_query;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.set_limit(real)
 RETURNS real
 LANGUAGE c
 STRICT
AS '$libdir/pg_trgm', $function$set_limit$function$
;
CREATE OR REPLACE FUNCTION public.show_limit()
 RETURNS real
 LANGUAGE c
 STABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$show_limit$function$
;
CREATE OR REPLACE FUNCTION public.show_trgm(text)
 RETURNS text[]
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$show_trgm$function$
;
CREATE OR REPLACE FUNCTION public.similarity(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$similarity$function$
;
CREATE OR REPLACE FUNCTION public.similarity_op(text, text)
 RETURNS boolean
 LANGUAGE c
 STABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$similarity_op$function$
;
CREATE OR REPLACE FUNCTION public.word_similarity(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$word_similarity$function$
;
CREATE OR REPLACE FUNCTION public.word_similarity_op(text, text)
 RETURNS boolean
 LANGUAGE c
 STABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$word_similarity_op$function$
;
CREATE OR REPLACE FUNCTION public.word_similarity_commutator_op(text, text)
 RETURNS boolean
 LANGUAGE c
 STABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$word_similarity_commutator_op$function$
;
CREATE OR REPLACE FUNCTION public.similarity_dist(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$similarity_dist$function$
;
CREATE OR REPLACE FUNCTION public.word_similarity_dist_op(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$word_similarity_dist_op$function$
;
CREATE OR REPLACE FUNCTION public.word_similarity_dist_commutator_op(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$word_similarity_dist_commutator_op$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_in(cstring)
 RETURNS gtrgm
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_in$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_out(gtrgm)
 RETURNS cstring
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_out$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_consistent(internal, text, smallint, oid, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_consistent$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_distance(internal, text, smallint, oid, internal)
 RETURNS double precision
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_distance$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_compress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_compress$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_decompress(internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_decompress$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_penalty(internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_penalty$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_picksplit(internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_picksplit$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_union(internal, internal)
 RETURNS gtrgm
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_union$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_same(gtrgm, gtrgm, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gtrgm_same$function$
;
CREATE OR REPLACE FUNCTION public.gin_extract_value_trgm(text, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gin_extract_value_trgm$function$
;
CREATE OR REPLACE FUNCTION public.gin_extract_query_trgm(text, internal, smallint, internal, internal, internal, internal)
 RETURNS internal
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gin_extract_query_trgm$function$
;
CREATE OR REPLACE FUNCTION public.gin_trgm_consistent(internal, smallint, text, integer, internal, internal, internal, internal)
 RETURNS boolean
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gin_trgm_consistent$function$
;
CREATE OR REPLACE FUNCTION public.gin_trgm_triconsistent(internal, smallint, text, integer, internal, internal, internal)
 RETURNS "char"
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$gin_trgm_triconsistent$function$
;
CREATE OR REPLACE FUNCTION public.strict_word_similarity(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$strict_word_similarity$function$
;
CREATE OR REPLACE FUNCTION public.strict_word_similarity_op(text, text)
 RETURNS boolean
 LANGUAGE c
 STABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$strict_word_similarity_op$function$
;
CREATE OR REPLACE FUNCTION public.strict_word_similarity_commutator_op(text, text)
 RETURNS boolean
 LANGUAGE c
 STABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$strict_word_similarity_commutator_op$function$
;
CREATE OR REPLACE FUNCTION public.strict_word_similarity_dist_op(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$strict_word_similarity_dist_op$function$
;
CREATE OR REPLACE FUNCTION public.strict_word_similarity_dist_commutator_op(text, text)
 RETURNS real
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE STRICT
AS '$libdir/pg_trgm', $function$strict_word_similarity_dist_commutator_op$function$
;
CREATE OR REPLACE FUNCTION public.gtrgm_options(internal)
 RETURNS void
 LANGUAGE c
 IMMUTABLE PARALLEL SAFE
AS '$libdir/pg_trgm', $function$gtrgm_options$function$
;
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
            BEGIN
               NEW.updated_at = CURRENT_TIMESTAMP;
               RETURN NEW;
            END;
            $function$
;
CREATE OR REPLACE FUNCTION public.etown_metadata_term()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    DELETE FROM etown_metadata_term;  -- Delete all data and then insert new data.
    INSERT INTO etown_metadata_term (term)
    SELECT DISTINCT term
    FROM ( 
        SELECT LOWER(TRIM(REPLACE(servicecategory, '_', ' '))) as term  
        FROM etown_providerservices_copy  
        WHERE servicecategory IS NOT NULL AND LENGTH(TRIM(servicecategory)) > 2 

        UNION 

        SELECT LOWER(TRIM(businesscategory)) as term 
        FROM etown_providerservices_copy  
        WHERE businesscategory IS NOT NULL AND LENGTH(TRIM(businesscategory)) > 2 

        UNION 

        SELECT LOWER(TRIM(unnest(string_to_array(services, ',')))) as term  
        FROM etown_providerservices_copy  
        WHERE services IS NOT NULL 

        UNION 

        SELECT LOWER(TRIM(unnest(string_to_array( 
            REGEXP_REPLACE(LOWER(TRIM(REPLACE(servicecategory, '_', ' '))), '[^a-z0-9 ]', ' ', 'g'), ' ' 
        )))) as term 
        FROM etown_providerservices_copy 
        WHERE servicecategory IS NOT NULL AND LENGTH(TRIM(servicecategory)) > 2 

        UNION 

        SELECT LOWER(TRIM(unnest(string_to_array( 
            REGEXP_REPLACE(LOWER(TRIM(businesscategory)), '[^a-z0-9 ]', ' ', 'g'), ' ' 
        )))) as term 
        FROM etown_providerservices_copy 
        WHERE businesscategory IS NOT NULL AND LENGTH(TRIM(businesscategory)) > 2 

        UNION 

        SELECT word as term FROM ( 
            SELECT LOWER(unnest(string_to_array( 
                REGEXP_REPLACE(LOWER(TRIM(businessname)), '[^a-z0-9 ]', ' ', 'g'), ' ' 
            ))) as word 
            FROM etown_providerservices_copy  
            WHERE businessname IS NOT NULL 
        ) words 
        WHERE LENGTH(word) > 3 
        GROUP BY word HAVING COUNT(*) >= 2 
    ) terms
    WHERE LENGTH(term) > 2

    ON CONFLICT (term) DO NOTHING;  -- avoids duplicates

	 RETURN NULL; 

END;
$function$
;
CREATE OR REPLACE FUNCTION public.gen_uuid7()
 RETURNS uuid
 LANGUAGE plpgsql
AS $function$
DECLARE
    unix_ts_ms bigint;
    ts_hex text;
    rand_hex text;
    uuid_hex text;
    variant_nibble text;
BEGIN
    unix_ts_ms := floor(extract(epoch from clock_timestamp()) * 1000)::bigint;
    ts_hex := lpad(to_hex(unix_ts_ms), 12, '0');
    rand_hex := substr(md5(random()::text || clock_timestamp()::text || txid_current()::text), 1, 20);
    uuid_hex := ts_hex || rand_hex;

    -- Set version nibble to 7 (UUIDv7)
    uuid_hex := substr(uuid_hex, 1, 12) || '7' || substr(uuid_hex, 14);

    -- Set variant nibble to RFC 4122 variant (8, 9, a, b)
    variant_nibble := substr('89ab', floor(random() * 4)::int + 1, 1);
    uuid_hex := substr(uuid_hex, 1, 16) || variant_nibble || substr(uuid_hex, 18);

    RETURN (
        substr(uuid_hex, 1, 8) || '-' ||
        substr(uuid_hex, 9, 4) || '-' ||
        substr(uuid_hex, 13, 4) || '-' ||
        substr(uuid_hex, 17, 4) || '-' ||
        substr(uuid_hex, 21, 12)
    )::uuid;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.etown_metadata_city()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- TRUNCATE all existing data
   TRUNCATE TABLE etown_metadata_city;

    -- Insert fresh data
    INSERT INTO etown_metadata_city (city)
    SELECT DISTINCT LOWER(TRIM(city)) as city
    FROM etown_providerservices_copy  
    WHERE city IS NOT NULL 
      AND LENGTH(TRIM(city)) > 2
    ORDER BY city
    ON CONFLICT (city) DO NOTHING;

    RETURN NULL;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.add_offer_view(offer_id text, user_id text, viewer_name text)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
BEGIN
    INSERT INTO etown_offer_views (offersid, userprofileid, name)
    VALUES (offer_id, TRIM(LOWER(user_id)), viewer_name)
    ON CONFLICT (offersid, userprofileid) DO NOTHING;
    RETURN FOUND;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.get_offer_view_count(offer_id text)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN (
        SELECT COUNT(DISTINCT userprofileid)
        FROM etown_offer_views
        WHERE offersid = offer_id
    );
END;
$function$
;
CREATE OR REPLACE FUNCTION public.delete_providerservices_copy()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    DELETE FROM public.etown_providerservices_copy
    WHERE providerservicesid = OLD.providerservicesid;
    RETURN OLD;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.pg_stat_statements_info(OUT dealloc bigint, OUT stats_reset timestamp with time zone)
 RETURNS record
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pg_stat_statements', $function$pg_stat_statements_info$function$
;
CREATE OR REPLACE FUNCTION public.pg_stat_statements(showtext boolean, OUT userid oid, OUT dbid oid, OUT toplevel boolean, OUT queryid bigint, OUT query text, OUT plans bigint, OUT total_plan_time double precision, OUT min_plan_time double precision, OUT max_plan_time double precision, OUT mean_plan_time double precision, OUT stddev_plan_time double precision, OUT calls bigint, OUT total_exec_time double precision, OUT min_exec_time double precision, OUT max_exec_time double precision, OUT mean_exec_time double precision, OUT stddev_exec_time double precision, OUT rows bigint, OUT shared_blks_hit bigint, OUT shared_blks_read bigint, OUT shared_blks_dirtied bigint, OUT shared_blks_written bigint, OUT local_blks_hit bigint, OUT local_blks_read bigint, OUT local_blks_dirtied bigint, OUT local_blks_written bigint, OUT temp_blks_read bigint, OUT temp_blks_written bigint, OUT blk_read_time double precision, OUT blk_write_time double precision, OUT temp_blk_read_time double precision, OUT temp_blk_write_time double precision, OUT wal_records bigint, OUT wal_fpi bigint, OUT wal_bytes numeric, OUT jit_functions bigint, OUT jit_generation_time double precision, OUT jit_inlining_count bigint, OUT jit_inlining_time double precision, OUT jit_optimization_count bigint, OUT jit_optimization_time double precision, OUT jit_emission_count bigint, OUT jit_emission_time double precision)
 RETURNS SETOF record
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pg_stat_statements', $function$pg_stat_statements_1_10$function$
;
CREATE OR REPLACE FUNCTION public.pg_stat_statements_reset(userid oid DEFAULT 0, dbid oid DEFAULT 0, queryid bigint DEFAULT 0)
 RETURNS void
 LANGUAGE c
 PARALLEL SAFE STRICT
AS '$libdir/pg_stat_statements', $function$pg_stat_statements_reset_1_7$function$
;
CREATE OR REPLACE FUNCTION public.delete_account_cascade(p_contact_id text, p_mobile text)
 RETURNS TABLE(r_found boolean, r_email text, r_firstname text, r_tables_deleted integer)
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_email       text;
    v_firstname   text;
    v_mobilephone text;
    v_mobile      text;
    v_upids       text[];
    v_mids        text[];
    v_pids        text[];
    v_role_ids    text[];
    v_bookings    text[];
BEGIN
    SELECT c.email, c.firstname, c.mobilephone
      INTO v_email, v_firstname, v_mobilephone
      FROM etown_contact c
     WHERE c.contactid = p_contact_id;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::text, NULL::text, 0;
        RETURN;
    END IF;

    v_mobile := COALESCE(p_mobile, v_mobilephone);

    SELECT COALESCE(array_agg(userprofileid), '{}') INTO v_upids
      FROM etown_userprofile WHERE contactid = p_contact_id;
    SELECT COALESCE(array_agg(memberid), '{}') INTO v_mids
      FROM etown_member WHERE contactid = p_contact_id;
    SELECT COALESCE(array_agg(providerid), '{}') INTO v_pids
      FROM etown_provider WHERE contactid = p_contact_id;
    SELECT COALESCE(array_agg(DISTINCT x), '{}') INTO v_role_ids
      FROM unnest(v_mids || v_pids) AS x;

    -- Every booking the contact touches from either side. Collected up front because
    -- the member leg is only reachable through the header and the provider leg only
    -- through the line items; deleting either first would strand the other.
    SELECT COALESCE(array_agg(DISTINCT b), '{}') INTO v_bookings
      FROM (
            SELECT allrequestid AS b FROM etown_allrequests
             WHERE requestorid = p_contact_id OR requestoruserprofileid = ANY(v_upids)
            UNION
            SELECT bookingid AS b FROM etown_requestservicelists
             WHERE serviceprovidercontactid = p_contact_id
                OR serviceprovideruserprofileid = ANY(v_upids)
                OR serviceproviderid = ANY(v_pids)
           ) s
     WHERE b IS NOT NULL;

    -- Group A: relation / edge tables
    DELETE FROM etown_memberrelation
     WHERE from_contactid = p_contact_id OR to_contactid = p_contact_id
        OR from_userprofile = ANY(v_upids) OR to_userprofile = ANY(v_upids)
        OR from_memberid = ANY(v_mids);
    DELETE FROM etown_providerrelation
     WHERE fromcontactid = p_contact_id OR tocontactid = p_contact_id
        OR fromuserprofileid = ANY(v_upids) OR touserprofileid = ANY(v_upids)
        OR fromproviderid = ANY(v_pids);

    -- Group B: call / session tables
    DELETE FROM etown_call_events WHERE user_id = ANY(v_upids);
    DELETE FROM etown_call_participants WHERE user_id = ANY(v_upids);
    DELETE FROM etown_call_tokens WHERE user_id = ANY(v_upids);
    DELETE FROM etown_call_sessions
     WHERE initiator_user_id = ANY(v_upids) OR target_user_id = ANY(v_upids);

    -- Group C: feed / social content
    DELETE FROM etown_feedcomments
     WHERE commentedbycontactid = p_contact_id OR commentedbyuserprofileid = ANY(v_upids);
    DELETE FROM etown_feedlikes
     WHERE likedbycontactid = p_contact_id OR likesbyuserprofileid = ANY(v_upids);
    DELETE FROM etown_feed WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_facebook_posts WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_instagram_posts WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_social_insights_snapshots WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);

    -- Group D: collections
    DELETE FROM etown_collectionitems
     WHERE usercontactid = p_contact_id OR useruserprofileid = ANY(v_upids) OR providerid = ANY(v_pids);
    DELETE FROM etown_collections
     WHERE usercontactid = p_contact_id OR useruserprofileid = ANY(v_upids);

    -- Group E: notifications / invitations
    DELETE FROM etown_notifications
     WHERE sendercontactid = p_contact_id OR receivercontactid = p_contact_id
        OR senderuserprofileid = ANY(v_upids) OR receiveruserprofileid = ANY(v_upids);
    DELETE FROM etown_invitation
     WHERE invitercontact = p_contact_id OR invitecontact = p_contact_id
        OR inviteruserprofile = ANY(v_upids) OR inviteuserprofile = ANY(v_upids)
        OR (v_mobile IS NOT NULL AND invitecontactmobilenumber = v_mobile)
        OR (v_email IS NOT NULL AND invitecontactemail = v_email);
    DELETE FROM etown_activeusers
     WHERE contactid = p_contact_id OR (v_mobile IS NOT NULL AND mobilenumber = v_mobile);

    -- Group F: messages / conversations
    DELETE FROM etown_messages
     WHERE sender_id = ANY(v_role_ids) OR receiver_id = ANY(v_role_ids);
    DELETE FROM etown_conversations
     WHERE participant_1_id = ANY(v_role_ids) OR participant_2_id = ANY(v_role_ids);
    DELETE FROM etown_chatmessages
     WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids)
        OR memberid = ANY(v_mids) OR providerid = ANY(v_pids);
    DELETE FROM etown_chathistory
     WHERE contactfrom = p_contact_id OR contactto = p_contact_id
        OR userprofileid = ANY(v_upids) OR memberid = ANY(v_mids) OR providerid = ANY(v_pids);

    -- Group G: provider / service tables
    DELETE FROM etown_provider_views
     WHERE userprofileid = ANY(v_upids) OR providerprofileid = ANY(v_pids);
    DELETE FROM etown_requestservicelists
     WHERE bookingid = ANY(v_bookings)
        OR serviceprovidercontactid = p_contact_id
        OR serviceprovideruserprofileid = ANY(v_upids) OR serviceproviderid = ANY(v_pids);
    DELETE FROM etown_providerservices_copy
     WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids) OR providerid = ANY(v_pids);
    DELETE FROM etown_providerservices
     WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids) OR providerid = ANY(v_pids);
    DELETE FROM etown_providerservicesadf
     WHERE contact_id = p_contact_id OR user_profile_id = ANY(v_upids);
    DELETE FROM etown_etownprovider WHERE providerid = ANY(v_pids);
    DELETE FROM etown_provider_details WHERE providerid = ANY(v_pids);
    DELETE FROM etown_ratings
     WHERE providercontactid = p_contact_id OR provideruserprofileid = ANY(v_upids);

    -- Group H: misc user / consumer tables
    DELETE FROM etown_offers
     WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids) OR vieweduserprofileid = ANY(v_upids);
    DELETE FROM etown_offer_views WHERE userprofileid = ANY(v_upids);
    DELETE FROM etown_offerings WHERE userprofileid = ANY(v_upids);
    DELETE FROM etown_favouritetown
     WHERE usercontactid = p_contact_id OR useruserprofileid = ANY(v_upids);
    DELETE FROM etown_town_view_tracking WHERE userprofileid = ANY(v_upids);
    DELETE FROM etown_townprofile WHERE vieweduserprofileid = ANY(v_upids);
    DELETE FROM etown_groups
     WHERE user_contactid = p_contact_id OR user_userprofileid = ANY(v_upids);
    DELETE FROM etown_groupitems
     WHERE user_contactid = p_contact_id OR userprofile_id = ANY(v_upids) OR user_userprofileid = ANY(v_upids);
    DELETE FROM etown_subscriptions
     WHERE current_userprofileid = ANY(v_upids) OR opponent_userprofileid = ANY(v_upids);
    DELETE FROM etown_facebookconnections WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_instagramconnections WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_allrequests
     WHERE allrequestid = ANY(v_bookings)
        OR requestorid = p_contact_id OR requestoruserprofileid = ANY(v_upids);
    DELETE FROM etown_mem0_memories WHERE userprofileid = ANY(v_upids);
    DELETE FROM etown_application_logs
     WHERE contact_id = p_contact_id OR userprofile_id = ANY(v_upids);
    DELETE FROM etown_connectycubedetails
     WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids)
        OR memberprofile = ANY(v_mids) OR providerprofile = ANY(v_pids);
    DELETE FROM etown_contactdevices WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_userlocationdetails WHERE contactid = p_contact_id;
    DELETE FROM etown_mobileverification WHERE v_mobile IS NOT NULL AND mobilenumber = v_mobile;
    DELETE FROM etown_franchise
     WHERE (v_mobile IS NOT NULL AND etown_franchise.phone = v_mobile)
        OR (v_email IS NOT NULL AND etown_franchise.email = v_email);
    DELETE FROM etowntitlegenerationlogs
     WHERE contact_id = p_contact_id OR user_profile_id = ANY(v_upids);

    -- Group I: core entity tables (last)
    DELETE FROM etown_member WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_provider WHERE contactid = p_contact_id OR userprofileid = ANY(v_upids);
    DELETE FROM etown_userprofile WHERE contactid = p_contact_id;
    DELETE FROM etown_contact WHERE contactid = p_contact_id;

    RETURN QUERY SELECT true, v_email, v_firstname, 53;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.pct_change(curr numeric, prev numeric)
 RETURNS numeric
 LANGUAGE sql
 IMMUTABLE
AS $function$
  SELECT CASE
    WHEN prev IS NULL OR prev = 0 THEN NULL
    ELSE ROUND(((curr - prev) / prev) * 100, 1)
  END;
$function$
;
CREATE OR REPLACE FUNCTION public.etown_is_deleted(p_identity_id text)
 RETURNS boolean
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT p_identity_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.etown_retired_identity r
                    WHERE r.identity_id = p_identity_id AND r.deleted = 'true')
$function$
;
CREATE OR REPLACE FUNCTION public.etown_normalize_category(p_existing text, p_servicecat text, p_businesscat text, p_name text, p_desc text, p_services text)
 RETURNS character varying
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
DECLARE
    hit text;
    e   text;
BEGIN
    -- TIER 1 - identity. What the owner called the business, plus the taxonomy fields.
    -- These are chosen by a human about this specific business. businesscategory is
    -- included for completeness but carries almost nothing: 99.6% of prod rows are just
    -- 'service' or 'product'. It is precisely the field that formed the phantom
    -- "management service" phrase when everything was space-joined, which the ' | '
    -- separators below now prevent.
    hit := public.etown_classify_category_keywords(
        coalesce(p_name, '') || ' | ' || coalesce(p_servicecat, '')
        || ' | ' || coalesce(p_businesscat, '')
    );
    IF hit IS NOT NULL THEN RETURN hit; END IF;

    -- TIER 2 - the offering. Names a real service, though it is often one arbitrary item
    -- from the range and is sometimes mis-assigned across businesses.
    hit := public.etown_classify_category_keywords(coalesce(p_services, ''));
    IF hit IS NOT NULL THEN RETURN hit; END IF;

    -- TIER 3 - a pre-existing category value, canonicalised. A per-business label, so
    -- more trustworthy than marketing prose but less than the fields above.
    e := lower(trim(coalesce(p_existing, '')));
    IF e <> '' AND e !~ '^(based_on|the_provided|unclear_services$|other$|retail$|sales$|sales_services$|sales_training$|service$|general_store$|agency$|agency_services$)' THEN
        RETURN CASE e
            WHEN 'jewellery_store'      THEN 'jewelry_store'
            WHEN 'jewellery'            THEN 'jewelry_store'
            WHEN 'jewelry'              THEN 'jewelry_store'
            WHEN 'jewellery_services'   THEN 'jewelry_store'
            WHEN 'mobile_store'         THEN 'mobile_phones'
            WHEN 'mobile_services'      THEN 'mobile_phones'
            WHEN 'phone_repair'         THEN 'mobile_phones'
            WHEN 'automotive'           THEN 'automotive_services'
            WHEN 'auto_services'        THEN 'automotive_services'
            WHEN 'automotive_sales'     THEN 'automotive_services'
            WHEN 'auto_repair'          THEN 'automotive_services'
            WHEN 'car_repair'           THEN 'automotive_services'
            WHEN 'vehicle_repair'       THEN 'automotive_services'
            WHEN 'two_wheeler_services' THEN 'automotive_services'
            WHEN 'auto_dealership'      THEN 'automotive_services'
            WHEN 'auto_dealer'          THEN 'automotive_services'
            WHEN 'auto_sales'           THEN 'automotive_services'
            WHEN 'car_dealer'           THEN 'automotive_services'
            WHEN 'car_dealership'       THEN 'automotive_services'
            WHEN 'tractor_dealer'       THEN 'automotive_services'
            WHEN 'car_accessories'      THEN 'automotive_parts'
            WHEN 'automotive_accessories' THEN 'automotive_parts'
            WHEN 'auto_accessories'     THEN 'automotive_parts'
            WHEN 'auto_parts'           THEN 'automotive_parts'
            WHEN 'tyre_shop'            THEN 'automotive_parts'
            WHEN 'health_services'      THEN 'healthcare_services'
            WHEN 'healthcare'           THEN 'healthcare_services'
            WHEN 'health_clinic'        THEN 'healthcare_services'
            WHEN 'dental_services'      THEN 'healthcare_services'
            WHEN 'dental_clinic'        THEN 'healthcare_services'
            WHEN 'dental_hospital'      THEN 'healthcare_services'
            WHEN 'hospital'             THEN 'healthcare_services'
            WHEN 'eye_hospital'         THEN 'healthcare_services'
            WHEN 'health_products'      THEN 'healthcare_services'
            WHEN 'healthcare_supplies'  THEN 'healthcare_services'
            WHEN 'healthcare_products'  THEN 'healthcare_services'
            WHEN 'medical_supplies'     THEN 'pharmacy'
            WHEN 'homeopathic_store'    THEN 'homeopathy_services'
            WHEN 'homoeopathy_store'    THEN 'homeopathy_services'
            WHEN 'homeopathy'           THEN 'homeopathy_services'
            WHEN 'homeopathy_clinic'    THEN 'homeopathy_services'
            WHEN 'holistic_health'      THEN 'healthcare_services'
            WHEN 'wellness'             THEN 'healthcare_services'
            WHEN 'wellness_services'    THEN 'healthcare_services'
            WHEN 'education'            THEN 'education_services'
            WHEN 'educational_institution' THEN 'education_services'
            WHEN 'educational_services' THEN 'education_services'
            WHEN 'education_institution' THEN 'education_services'
            WHEN 'school'               THEN 'education_services'
            WHEN 'college'              THEN 'education_services'
            WHEN 'tutoring_services'    THEN 'education_services'
            WHEN 'music_school'         THEN 'education_services'
            WHEN 'music_classes'        THEN 'education_services'
            WHEN 'language_training'    THEN 'education_services'
            WHEN 'sports_academy'       THEN 'education_services'
            WHEN 'clothing_services'    THEN 'clothing_store'
            WHEN 'textiles'             THEN 'clothing_store'
            WHEN 'textile_store'        THEN 'clothing_store'
            WHEN 'textile_services'     THEN 'clothing_store'
            WHEN 'textile_design'       THEN 'clothing_store'
            WHEN 'fashion'              THEN 'clothing_store'
            WHEN 'fashion_services'     THEN 'clothing_store'
            WHEN 'fashion_accessories'  THEN 'clothing_store'
            WHEN 'handloom_products'    THEN 'clothing_store'
            WHEN 'curtain_fabric'       THEN 'clothing_store'
            WHEN 'tailoring_services'   THEN 'clothing_store'
            WHEN 'embroidery_services'  THEN 'clothing_store'
            WHEN 'bags_accessories'     THEN 'clothing_store'
            WHEN 'accessories'          THEN 'clothing_store'
            WHEN 'accessories_store'    THEN 'clothing_store'
            WHEN 'electronics'          THEN 'electronics_store'
            WHEN 'electronics_sales'    THEN 'electronics_store'
            WHEN 'technology_sales'     THEN 'electronics_store'
            WHEN 'technology_services'  THEN 'it_services'
            WHEN 'technology'           THEN 'electronics_store'
            WHEN 'computer_services'    THEN 'it_services'
            WHEN 'software_services'    THEN 'it_services'
            WHEN 'software_agency'      THEN 'it_services'
            WHEN 'ecommerce'            THEN 'it_services'
            WHEN 'data_analytics'       THEN 'it_services'
            WHEN 'analytics'            THEN 'it_services'
            WHEN 'business_consulting'  THEN 'consulting_services'
            WHEN 'hr_consulting'        THEN 'consulting_services'
            WHEN 'auto_consulting'      THEN 'consulting_services'
            WHEN 'business_services'    THEN 'consulting_services'
            WHEN 'hr_services'          THEN 'consulting_services'
            WHEN 'recruitment_agency'   THEN 'consulting_services'
            WHEN 'marketing'            THEN 'marketing_services'
            WHEN 'marketing_agency'     THEN 'marketing_services'
            WHEN 'digital_marketing'    THEN 'marketing_services'
            WHEN 'branding'             THEN 'marketing_services'
            WHEN 'signage_services'     THEN 'marketing_services'
            WHEN 'graphics_services'    THEN 'marketing_services'
            WHEN 'accountant'           THEN 'accounting_services'
            WHEN 'audit_services'       THEN 'accounting_services'
            WHEN 'insurance'            THEN 'financial_services'
            WHEN 'insurance_services'   THEN 'financial_services'
            WHEN 'furniture_services'   THEN 'furniture_store'
            WHEN 'furniture_repair'     THEN 'furniture_store'
            WHEN 'building_materials'   THEN 'hardware_store'
            WHEN 'steel_products'       THEN 'hardware_store'
            WHEN 'metal_sales'          THEN 'hardware_store'
            WHEN 'metal_store'          THEN 'hardware_store'
            WHEN 'granite_supplier'     THEN 'hardware_store'
            WHEN 'timber_sales'         THEN 'hardware_store'
            WHEN 'pipes_tubes'          THEN 'hardware_store'
            WHEN 'pipe_traders'         THEN 'hardware_store'
            WHEN 'plumbing_supplies'    THEN 'hardware_store'
            WHEN 'industrial_motors'    THEN 'hardware_store'
            WHEN 'kitchen_supplies'     THEN 'hardware_store'
            WHEN 'electrical_supplies'  THEN 'electrical_services'
            WHEN 'beauty_parlour'       THEN 'beauty_services'
            WHEN 'beauty_salons'        THEN 'beauty_services'
            WHEN 'salon_services'       THEN 'beauty_services'
            WHEN 'spa_services'         THEN 'beauty_services'
            WHEN 'massage_services'     THEN 'beauty_services'
            WHEN 'supermarket'          THEN 'grocery_store'
            WHEN 'general_store'        THEN 'grocery_store'
            WHEN 'meat_shop'            THEN 'grocery_store'
            WHEN 'meat_seafood_store'   THEN 'grocery_store'
            WHEN 'chicken_shop'         THEN 'grocery_store'
            WHEN 'fresh_produce'        THEN 'grocery_store'
            WHEN 'bakery_sweets'        THEN 'bakery'
            WHEN 'sweets_and_snacks'    THEN 'bakery'
            WHEN 'sweet_shop'           THEN 'bakery'
            WHEN 'confectionery'        THEN 'bakery'
            WHEN 'dessert_shop'         THEN 'bakery'
            WHEN 'south_indian_food'    THEN 'restaurant'
            WHEN 'photography_studio'   THEN 'photography_services'
            WHEN 'photo_video_services' THEN 'photography_services'
            WHEN 'creative_studio'      THEN 'photography_services'
            WHEN 'studio_services'      THEN 'photography_services'
            WHEN 'appliance_repair'     THEN 'repair_services'
            WHEN 'transportation_services' THEN 'taxi_service'
            WHEN 'logistics_services'   THEN 'courier_services'
            WHEN 'hospitality'          THEN 'hotel'
            WHEN 'hospitality_services' THEN 'hotel'
            WHEN 'lodging'              THEN 'hotel'
            WHEN 'hostel_services'      THEN 'hostel'
            WHEN 'gym'                  THEN 'fitness_services'
            WHEN 'fitness_center'       THEN 'fitness_services'
            WHEN 'sports'               THEN 'fitness_services'
            WHEN 'temple'               THEN 'religious_services'
            WHEN 'matrimony_services'   THEN 'religious_services'
            WHEN 'travel_agency'        THEN 'travel_services'
            WHEN 'rental_agency'        THEN 'real_estate'
            WHEN 'event_venue'          THEN 'event_management'
            WHEN 'flower_decorators'    THEN 'event_management'
            WHEN 'decorative_items'     THEN 'interior_design'
            WHEN 'art_and_craft'        THEN 'interior_design'
            WHEN 'aquarium_services'    THEN 'interior_design'
            WHEN 'landscaping_services' THEN 'construction_services'
            WHEN 'painting_services'    THEN 'construction_services'
            WHEN 'stationery'           THEN 'stationery_store'
            WHEN 'bookstore'            THEN 'stationery_store'
            WHEN 'book_store'           THEN 'stationery_store'
            WHEN 'used_books'           THEN 'stationery_store'
            WHEN 'office_equipment'     THEN 'stationery_store'
            WHEN 'water_purification'   THEN 'other'
            WHEN 'water_service'        THEN 'other'
            WHEN 'water_supply'         THEN 'other'
            WHEN 'mushroom_sales'       THEN 'agricultural_services'
            WHEN 'luxury_goods'         THEN 'clothing_store'
            ELSE e
        END;
    END IF;

    -- TIER 4 - generated marketing copy. Last resort: an incidental word here is how
    -- petrol stations became bakeries, so nothing above may be overridden by it.
    hit := public.etown_classify_category_keywords(coalesce(p_desc, ''));
    IF hit IS NOT NULL THEN RETURN hit; END IF;

    RETURN 'other';
END;
$function$
;
CREATE OR REPLACE FUNCTION public.trg_set_normalized_category()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.normalized_category := public.etown_normalize_category(
        NEW.normalized_category,
        NEW.servicecategory,
        NEW.businesscategory,
        NEW.businessname,
        NEW.servicedescription,
        NEW.services
    );
    RETURN NEW;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.etown_is_deleted_ci(p_identity_id text)
 RETURNS boolean
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT p_identity_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.etown_retired_identity r
                    WHERE lower(r.identity_id) = lower(p_identity_id) AND r.deleted = 'true')
$function$
;
CREATE OR REPLACE FUNCTION public.etown_is_retired(p_identity_id text)
 RETURNS boolean
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT p_identity_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.etown_retired_identity r
                    WHERE r.identity_id = p_identity_id
                      AND (r.deleted = 'true'
                        OR (r.deactivated = 'true' AND r.reactivation IS DISTINCT FROM 'true')))
$function$
;
CREATE OR REPLACE FUNCTION public.etown_deleted_flag(p_identity_id text)
 RETURNS text
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT CASE WHEN public.etown_is_deleted(p_identity_id) THEN 'true' ELSE 'false' END
$function$
;
CREATE OR REPLACE FUNCTION public.etown_deleted_flag_ci(p_identity_id text)
 RETURNS text
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT CASE WHEN public.etown_is_deleted_ci(p_identity_id) THEN 'true' ELSE 'false' END
$function$
;
CREATE OR REPLACE FUNCTION public.etown_deactivated_flag(p_identity_id text)
 RETURNS text
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT CASE WHEN p_identity_id IS NOT NULL
         AND EXISTS (SELECT 1 FROM public.etown_retired_identity r
                      WHERE r.identity_id = p_identity_id
                        AND r.deactivated = 'true'
                        AND r.reactivation IS DISTINCT FROM 'true')
        THEN 'true' ELSE 'false' END
$function$
;
CREATE OR REPLACE FUNCTION public.etown_classify_category_keywords(p_text text)
 RETURNS character varying
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$

DECLARE
    t text;
BEGIN
    t := lower(coalesce(p_text, ''));
    IF btrim(t, ' |') = '' THEN RETURN NULL; END IF;

    IF t ~ 'driving school|driving class|motor training|driving institute' THEN RETURN 'driving_school'; END IF;
    -- Locksmiths, ahead of automotive and IT. Every one of the 12 locksmith rows on dev was
    -- misfiled: 11 as automotive_services (their names carry "Car Key"/"Bike Key") and
    -- CHENNAI KEY MAKERS as it_services. "repair my door lock" resolves to locksmith and
    -- had nowhere to land.
    IF t ~ 'locksmith|key ?(making|maker|makers|shop|works|cutting)|duplicate key|lock (repair|works|service)' THEN RETURN 'locksmith_services'; END IF;

    -- Scrap / waste / recycling. Recognised but intentionally unclassifiable: without
    -- this rule these dealers fell through to 'management service' -> consulting_services
    -- or 'transport' -> taxi_service. Revisit if a recycling_services category is added.
    IF t ~ '\yscrap\M|recycl|waste management|waste collection|\yjunk\M|kabadi|இரும்பு வியாபார' THEN RETURN 'other'; END IF;

    -- Mobile phones (dedicated). Guard "automobile"/"auto mobile".
    IF t ~ '\ymobile|\ycell\M|cellphone|cell phone|smart ?phone|\yredmi\M|\yvivo\M|\yoppo\M|\ytelecom|mobile recharge|mobile accessor'
       AND t !~ 'auto ?mobile'
       -- Defer to electronics_repair when the text carries an explicit repair signal:
       -- "phonefix mobiles and laptop service | Electronics Repair" is a repair shop,
       -- and \\ymobile matching "mobiles" in its NAME was filing it as a phone shop.
       AND t !~ '(electronic|\ylaptop|\ytv\M|television|headphone|earphone)[^|]{0,40}(\yrepair|servicing|technician)'
       THEN RETURN 'mobile_phones'; END IF;

    -- Fuel retail. Serves vehicles, so it lands in automotive_services; there is no
    -- fuel_station category. Must precede the food rules: forecourt descriptions
    -- advertise "snacks" and "coffee", which is how IndianOil became a bakery.
    IF t ~ 'petrol bunk|petrol pump|petrol station|petroleum|gas station|filling station|fuel station|fuel pump|indian ?oil|bharat petroleum|hindustan petroleum|\yhpcl\M|\yiocl\M|\ybpcl\M|\yhp petrol' THEN RETURN 'automotive_services'; END IF;

    -- Automotive parts before automotive services
    IF t ~ 'spare ?parts|auto ?parts|automobile spare|car accessor|two ?wheeler.*part|\ytyre|\ytires?\M(?= (shop|works|sales|dealer|repair|recycling|centre|center))|lubricant|auto ?spares' THEN RETURN 'automotive_parts'; END IF;
    IF t ~ 'automobile|automotive|\yauto\M|\ycar\M|\ycars\M|vehicle|two ?wheeler|\ybike\M|scooter|\ygarage|\ymotors?\M|car wash|car service|\ytractors?\M' THEN RETURN 'automotive_services'; END IF;

    -- IT & electronics BEFORE clothing (garbage servicecategory='fashion' otherwise
    -- pulls computer shops into clothing_store).
    -- Optical BEFORE IT: 'City Optics Computer Eye Testing & Contact Lens Clinic' is an
    -- optician, and the bare \ycomputer alternative below claimed it for it_services.
    -- Optical also stays ahead of watches and jewellery, which the block below relies on.
    IF t ~ 'optical|spectacle|eye ?wear|opticals|\ylens\M' THEN RETURN 'optical_services'; END IF;

    IF t ~ 'software|\yit services|information technology|infotech|info ?tech|\ycomputer|web ?(design|develop)|app develop|digital solution|\yseo\M|website|programming|\yerp\M|data analyt|\yanalytics\M|networking solution|e-?commerce' THEN RETURN 'it_services'; END IF;
    -- White goods that are REPAIRED, ahead of electronics_store which used to swallow them.
    -- 'fridge repair' and 'washing machine repair' both returned electronics_store, so a
    -- broken-appliance query retrieved the whole electronics cohort while the repairers sat
    -- in the repair_services grab-bag next to a tree cutter.
    -- The two halves must meet inside ONE ' | '-delimited segment. businesscategory is the
    -- literal word 'service' on most rows, so a plain AND of the two patterns matched every
    -- electronics retailer in the corpus - Croma included.
    IF t ~ '(refrigerator|\yfridge|washing machine|microwave|geyser|water heater|gas stove|\ychimney\M|dishwasher|air ?condition|\yac\M|aircon|home appliance|appliance)[^|]{0,40}(\yrepair|servic(e|es|ing)|technician)'
       OR t ~ '(\yrepair|servic(e|es|ing)|technician)[^|]{0,40}(refrigerator|\yfridge|washing machine|microwave|geyser|water heater|gas stove|\ychimney\M|dishwasher|air ?condition|\yac\M|aircon|home appliance|appliance)'
       THEN RETURN 'appliance_repair'; END IF;

    -- Consumer electronics that are REPAIRED. Kept below it_services so a computer shop
    -- stays IT, and below the appliance rule so "AC repair" is not read as electronics.
    IF t ~ '(electronic|\ylaptop|\ytv\M|television|headphone|earphone|\yspeakers?\M|\yaudio\M)[^|]{0,40}(\yrepair|servicing|technician)'
       OR t ~ '(\yrepair|servicing|technician)[^|]{0,40}(electronic|\ylaptop|\ytv\M|television|headphone|earphone|\yspeakers?\M|\yaudio\M)'
       THEN RETURN 'electronics_repair'; END IF;
    IF t ~ 'electronic|\ytv\M|television|home appliance|air ?condition|\yac service|refrigerator|washing machine|\yfridge|\ylaptop|\ycctv|\ydth\M|air ?fiber|\yled\M|gadget|mixie|grinder|inverter|home theatre|home theater' THEN RETURN 'electronics_store'; END IF;

    -- Both orderings here are load-bearing. Optical first: combined "Watches & Opticals"
    -- shops belong in optical_services, the more specialised trade (putting watches first
    -- cost optical 2.8 points). Watches then jewellery: a watch shop's copy routinely
    -- mentions gold straps, which would otherwise make it a jeweller.
    IF t ~ '\ywatch|\ywatches|wall clock|time ?piece' THEN RETURN 'watches_services'; END IF;
    IF t ~ 'jewel|jewell|\ygold\M|\ysilver\M|diamond|ornament|thanga|nagai|bullion|imitation jewel' THEN RETURN 'jewelry_store'; END IF;
    IF t ~ 'footwear|\yshoe|chappal|sandal|slipper' THEN RETURN 'footwear_store'; END IF;
    IF t ~ 'cloth|garment|textile|\ysaree|\ysari\M|readymade|ready made|apparel|fashion|boutique|\ydress|churidar|men.?s wear|women.?s wear|\ysilks?\M|fancy store|handloom|tailoring|tailor ?(shop|works|shope)|\ytailors\M|embroider|kids wear|innerwear' THEN RETURN 'clothing_store'; END IF;

    IF t ~ 'security service|security agency|\yguard\M|surveillance|watchman|bouncer' THEN RETURN 'security_services'; END IF;
    IF t ~ 'marketing|advertis|\ybranding\M|promotion|social media|signage|sign ?board|hoarding|graphic design|\ygraphics\M' THEN RETURN 'marketing_services'; END IF;
    IF t ~ 'printing|\yprint\M|xerox|photocopy|flex print|offset|screen print' THEN RETURN 'printing_services'; END IF;
    -- A bare 'studio' is not a photographer: it made a stationery shop, three gyms and a
    -- boxing studio into photography_services (5 of the 5 such rows on dev). Real studios
    -- are already caught by 'photo'/'photograph'; the qualified forms are belt-and-braces.
    IF t ~ 'photograph|\yphoto\M|videograph|candid|(photo|digital|candid|wedding|portrait|video|movie) ?studio|studio (photograph|photo)' THEN RETURN 'photography_services'; END IF;
    -- 'saloon' is the usual Indian spelling and was absent entirely, so "<name> Hair Saloon"
    -- matched nothing here and only survived via words in its description. A servicecategory
    -- of exactly "beauty" also went unmatched, because every alternative demanded a noun
    -- after it; it is matched as a whole ' | '-delimited segment so prose such as "the
    -- beauty of your home" (furniture copy) still cannot claim this rule.
    IF t ~ 'beauty (parlour|parlor|salon|saloon|shop|centre|center|care|studio|clinic|products?|services?|and personal)|(^|\| )beauty( \||$)|\ybeautician|\ysalons?\M|\ysaloons?\M|parlour|parlor|\yspa\M|massage|makeup|make up|hair ?(cut|dress|salon|saloon)|barber|cosmetic|bridal' THEN RETURN 'beauty_services'; END IF;

    IF t ~ 'astrolog|numerolog|va+stu|horoscope|jothida' THEN RETURN 'astrology_services'; END IF;
    IF t ~ 'ayurved|siddha|\yunani\M|naturopath' THEN RETURN 'ayurveda_services'; END IF;
    IF t ~ '\yhomeo(path|pathy|pathic)|homoeo' THEN RETURN 'homeopathy_services'; END IF;
    -- Healthcare MUST stay ahead of pharmacy. Promoting pharmacy first was tried and
    -- reverted: the chemist pattern's '\ymedicals?\M' also matches the adjective in
    -- "medical services"/"medical care", so 1,799 hospitals and clinics were reclassified
    -- as pharmacies (healthcare accuracy 84.1% -> 63.4%). Precedence is what separates the
    -- two senses of "medical"; tiering already fixed the pharmacy losses on its own.
    IF t ~ '\yhospitals?\M|\yclinic|\ydental\M|dentist|\ydoctor|physio|diagnostic|laboratory|nursing home|medical center|medical centre|health ?care|eye ?hospital|orthopedic|pediatric|gynec|surgeon|scan center' THEN RETURN 'healthcare_services'; END IF;
    IF t ~ 'pharmac|\ymedicals?\M|drug store|chemist|medical shop|\ymedicine' THEN RETURN 'pharmacy'; END IF;
    IF t ~ '\ygym\M|fitness|\yyoga\M|zumba|aerobics|workout|body ?building|crossfit|health club' THEN RETURN 'fitness_services'; END IF;

    -- Education before the food rules: a school's copy mentions its canteen/snacks.
    IF t ~ '\yschool\M|\ycollege|academy|coaching|tuition|tutor|\ytraining\M|\yinstitute|educat|classes|\ylearning\M|kindergarten|playschool|abacus|montessori|university' THEN RETURN 'education_services'; END IF;

    -- Florists. After astrology (a flower shop on dev also sells numerology, and its
    -- business name would otherwise claim that row) and before interior_design,
    -- event_management and bakery, which is where the 24 florist rows were landing.
    IF t ~ 'florist|floral|bouquet|\ygarlands?\M|flower ?(shop|stall|market|garland|arrangement|decor|delivery|export|gift)|\yflowers\M' THEN RETURN 'florist'; END IF;
    -- Pet retail. Recognised but intentionally unclassifiable: Etown has no pet
    -- category, and 'Pet Hypermarket' would otherwise be filed as a grocery store.
    -- \ypets?\M ends on a word boundary, so it cannot match "Petals".
    IF t ~ 'pet ?(shop|store|supplies|grooming|clinic|food|hypermarket|mart)|\ypets?\M' THEN RETURN NULL; END IF;

    IF t ~ 'catering|caterer' THEN RETURN 'catering_services'; END IF;
    IF t ~ 'bakery|\ycake|\ysweet|confection|namkeen|dessert|chocolate|\ybake\M' THEN RETURN 'bakery'; END IF;
    IF t ~ '\ycafe\M|coffee|\ytea\M|juice|ice ?cream' THEN RETURN 'cafe'; END IF;
    -- A market with a food court is a market, not a restaurant. Both halves are
    -- required, so an ordinary restaurant cannot be moved by this.
    IF t ~ 'grocery|super ?market|hypermarket|provision|kirana|departmental|general store|\ymart\M'
       AND t ~ 'food ?(court|stall|point)'
    THEN RETURN 'grocery_store'; END IF;
    -- Food supply, packaging and manufacturing: recognised but intentionally
    -- unclassifiable. Both halves are required - a supply word AND no eatery word - so
    -- a restaurant mentioning wholesale cannot be moved by this.
    IF t ~ 'container|packag|wholesale suppl|food manufactur|raw material'
       AND t !~ 'restaurant|\ymess\M|dhaba|tiffin|biryani|\ymeals\M|dining|eatery|\yhotel\M|\ycafe\M|bakery|catering'
    THEN RETURN NULL; END IF;
    IF t ~ 'restaurant|biryani|\ymeals\M|dhaba|tiffin|fast ?food|food ?(court|stall|point)|\ymess\M|dining|eatery|\yfoods?\M|\ypizza\M|\yburgers?\M|shawarma|\ysamosas?\M|\ysnacks?\M|chaat|parotta|\ydosa\M|\yidli\M|\yhotel\M.*(food|veg|biryani|meals)' THEN RETURN 'restaurant'; END IF;
    -- Packaged drinking water, ahead of grocery. Without it "20 litre water can" resolved to
    -- nothing at all and the lexical arm answered with eight supermarkets.
    IF t ~ 'water can|packaged drinking water|mineral water|drinking water|water suppl|\ybisleri\M|20 ?lit(re|er)|water plant' THEN RETURN 'water_supply'; END IF;
    IF t ~ 'grocery|super ?market|hypermarket|provision|kirana|departmental|general store|\ymart\M|daily needs|\yfmcg\M|flour mill|\ymeat\M|chicken|mutton|\yfish\M|seafood|vegetable|\yfruits?\M' THEN RETURN 'grocery_store'; END IF;

    IF t ~ 'hardware|\ypaints?\M|plywood|building material|\ycement\M|\ysteel\M|sanitary|\ypipes?\M|\ytiles?\M|granite|marble|\ytimber\M|\ybricks?\M|aluminium|construction material' THEN RETURN 'hardware_store'; END IF;
    IF t ~ 'furniture|\ysofa\M|mattress|\yfoam\M|home furnish|modular kitchen|wardrobe|chair (repair|weaving|knitting|caning)|\ycarpent' THEN RETURN 'furniture_store'; END IF;
    IF t ~ 'electrical|electrician|\ywiring\M|stabilizer|\ysolar\M|\yups\M' THEN RETURN 'electrical_services'; END IF;
    IF t ~ 'plumb|borewell|bore well' THEN RETURN 'plumbing_services'; END IF;
    IF t ~ 'construction|\ybuilder|contractor|civil work|\ymason|house painting|painting service|renovation|landscap|fabrication|welding|septic' THEN RETURN 'construction_services'; END IF;
    IF t ~ 'interior (design|decor)|home decor|\ydecor\M|curtains?|\yblinds\M|false ceiling|handicraft|aquarium|flower decor' THEN RETURN 'interior_design'; END IF;
    IF t ~ 'engineering|\ycad\M|surveying|machining|\ylathe\M|tool room' THEN RETURN 'engineering_services'; END IF;
    IF t ~ 'manufactur|\yfactory\M|industries|industrial|\ymills?\M|production unit|processing unit' THEN RETURN 'manufacturing'; END IF;

    IF t ~ 'accounting|accountant|book ?keeping|\yaudit|\ygst\M|chartered account|taxation|income tax' THEN RETURN 'accounting_services'; END IF;
    IF t ~ '\yfinance\M|financial|\yloan\M|\yinsurance\M|mutual fund|investment|chit fund|money transfer|forex' THEN RETURN 'financial_services'; END IF;
    IF t ~ '\ylegal\M|\ylawyer\M|advocate|attorney|notary|law firm' THEN RETURN 'legal_services'; END IF;
    IF t ~ 'real estate|\yproperty\M|properties|land promot|\yplot\M|\yflats?\M|apartment|\yvilla|builder promoter' THEN RETURN 'real_estate'; END IF;

    IF t ~ 'courier|logistics|\ycargo\M|parcel|packers? and movers?|freight|shipping' THEN RETURN 'courier_services'; END IF;
    -- '\ytransport\M' stays anchored. Relaxing it to a bare '\ytransport' prefix was tried
    -- and reverted: it swallowed every servicecategory containing "transportation",
    -- e.g. a cycle stand filed under "transportation and urban convenience" became a taxi
    -- service. The explicit phrases below cover genuine transport operators instead.
    IF t ~ '\ytaxi\M|\ycabs?\M|\ytravels\M|car rental|tours? and travels?|\ytransport\M|transportation service|passenger transport|goods transport|\ylorry\M|\ytempo\M|passenger|van rental' THEN RETURN 'taxi_service'; END IF;
    IF t ~ 'travel agency|tour package|\yvisa\M|passport|ticketing|holiday package' THEN RETURN 'travel_services'; END IF;

    IF t ~ '\yevents?\M|wedding plan|\ymandap\M|marriage hall|function hall|banquet|balloon decor' THEN RETURN 'event_management'; END IF;
    IF t ~ 'hostel|paying guest|\ypg\M' THEN RETURN 'hostel'; END IF;
    IF t ~ '\yhotel\M|\ylodge\M|lodging|\yresort\M|guest ?house|rooms? for rent' THEN RETURN 'hotel'; END IF;
    IF t ~ 'temple|\ychurch\M|mosque|\ypooja\M|\ypuja\M|religious|\ypriest\M|matrimon' THEN RETURN 'religious_services'; END IF;

    -- Maid and domestic help, ahead of consulting (a maid agency was filed as
    -- consulting_services). The maid word must be QUALIFIED: a bare token also matches
    -- "Butter Maid Bakery".
    IF t ~ 'cleaning|housekeeping|pest control|sanitiz|maid ?(agency|agencies|service|services|placement|supply)|domestic help|house ?help|\ymaids?\M[^|]{0,15}(agency|service|placement|supply)' THEN RETURN 'cleaning_services'; END IF;
    IF t ~ 'agricultur|\yfarm\M|nursery|\yseeds?\M|fertilizer|pesticide|\ydairy\M|poultry|cattle|tree (cutting|cutter|felling|trimming|removal)|\ygardening\M' THEN RETURN 'agricultural_services'; END IF;
    -- Sporting goods had no category, so 'cricket bat' resolved to electronics_store and
    -- the exact-category leg then retrieved the whole electronics cohort. Kept below
    -- fitness_services so a gym still classifies as a gym, not as a sports shop.
    IF t ~ 'sports? (shop|store|goods|equipment|wear|accessor)|sporting goods|\ycricket\M|badminton|racquet|\yracket|\ycarrom\M|\ykabaddi\M|sports equipment' THEN RETURN 'sporting_goods'; END IF;
    IF t ~ '\ytoys?\M|toy shop' THEN RETURN 'toy_store'; END IF;
    IF t ~ 'stationery|\ybooks?\M|book ?(shop|store|stall)|\ypens?\M|notebook' THEN RETURN 'stationery_store'; END IF;

    IF t ~ 'consult|\yhr\M|human resource|recruit|manpower|placement|advisory|business service|management service' THEN RETURN 'consulting_services'; END IF;
    IF t ~ '\yrepair|servicing|maintenance' THEN RETURN 'repair_services'; END IF;

    RETURN NULL;  -- no opinion; the caller should try a less-trusted field
END;
$function$
;
CREATE OR REPLACE FUNCTION public.etown_deactivated_flag_ci(p_identity_id text)
 RETURNS text
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
    SELECT CASE WHEN p_identity_id IS NOT NULL
         AND EXISTS (SELECT 1 FROM public.etown_retired_identity r
                      WHERE lower(r.identity_id) = lower(p_identity_id)
                        AND r.deactivated = 'true'
                        AND r.reactivation IS DISTINCT FROM 'true')
        THEN 'true' ELSE 'false' END
$function$
;
CREATE OR REPLACE FUNCTION public.etown_is_retired_ci(p_identity_id text)
 RETURNS boolean
 LANGUAGE sql
 STABLE PARALLEL SAFE
AS $function$
            SELECT p_identity_id IS NOT NULL
               AND EXISTS (
                       SELECT 1 FROM public.etown_retired_identity r
                        WHERE lower(r.identity_id) = lower(p_identity_id)
                          AND (r.deleted = 'true'
                            OR (r.deactivated = 'true'
                                AND r.reactivation IS DISTINCT FROM 'true'))
                   )
        $function$
;
CREATE OR REPLACE FUNCTION public.etown_town_canon(p_name text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE PARALLEL SAFE STRICT
AS $function$
    SELECT regexp_replace(
             replace(replace(
               translate(
                 replace(replace(replace(replace(replace(replace(replace(replace(replace(
                   regexp_replace(lower(p_name), '[^a-z0-9]+', '', 'g'),
                   'zh', 'l'), 'ksh', 'k'),
                   'th', 't'), 'dh', 'd'), 'bh', 'b'), 'gh', 'g'),
                   'ph', 'f'), 'kh', 'k'), 'ch', 'c'),
                 'vy', 'wi'),
               'ee', 'i'), 'oo', 'u'),
             '(.)\1+', '\1', 'g')
$function$
;
CREATE OR REPLACE FUNCTION public.etown_town_distance_km(p_lat1 text, p_lon1 text, p_lat2 text, p_lon2 text)
 RETURNS double precision
 LANGUAGE plpgsql
 IMMUTABLE PARALLEL SAFE
AS $function$
DECLARE
    a1 double precision; o1 double precision;
    a2 double precision; o2 double precision;
    h  double precision;
BEGIN
    BEGIN
        a1 := p_lat1::double precision;
        o1 := p_lon1::double precision;
        a2 := p_lat2::double precision;
        o2 := p_lon2::double precision;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END;

    IF a1 IS NULL OR o1 IS NULL OR a2 IS NULL OR o2 IS NULL THEN
        RETURN NULL;
    END IF;

    h := sin(radians(a2 - a1) / 2) ^ 2
       + cos(radians(a1)) * cos(radians(a2)) * sin(radians(o2 - o1) / 2) ^ 2;
    RETURN 2 * 6371.0 * asin(least(1, sqrt(h)));
END;
$function$
;
CREATE OR REPLACE FUNCTION public.process_invitation(p_invitation_row_id text, p_invite_role text, p_inviter_contact text, p_invite_contact text, p_invite_user_profile text, p_inviter_user_profile_name text, p_inviter_contact_name text, p_invitation_code text)
 RETURNS TABLE(contact json, invitation json)
 LANGUAGE plpgsql
AS $function$
BEGIN

    ------------------------------------------------------------------
    -- INSERT FLOW
    ------------------------------------------------------------------
    IF p_invitation_row_id IS NULL THEN

        RETURN QUERY

        WITH ContactInfo AS
        (
            SELECT *
            FROM etown_contact
            WHERE contactid = p_invite_contact
        ),

        InsertedInvitation AS
        (
            INSERT INTO etown_invitation
            (
                invitationid,
                invite_role,
                invitationtype,
                invitercontact,
                invitecontact,
                inviteuserprofile,
                inviteruserprofilename,
                invitercontactname,
                suffixid,
                invitecontactemail,
                receivername,
                invitationcode,
                createdon
            )

            SELECT
                gen_random_uuid()::text,
                '1',
                'Internal',
                p_inviter_contact,
                p_invite_contact,
                '',
                '',
                firstname,
                '',
                '',
                '',
                'Requested',
                now()
            FROM ContactInfo

            RETURNING *
        ),

        InvitationInfo AS
        (
            SELECT *
            FROM InsertedInvitation
        ),

        MatchedContacts AS
        (
            SELECT c.*
            FROM etown_contact c
            CROSS JOIN ContactInfo ci
            WHERE
                (c.mobilephone = ci.mobilephone
                    AND ci.mobilephone IS NOT NULL)

                OR

                (c.email = ci.email
                    AND ci.email IS NOT NULL)
        )

        SELECT
            'Success',

            (
                SELECT row_to_json(c)
                FROM MatchedContacts c
                LIMIT 1
            ),

            (
                SELECT row_to_json(i)
                FROM InvitationInfo i
                LIMIT 1
            );

    ------------------------------------------------------------------
    -- UPDATE FLOW
    ------------------------------------------------------------------
    ELSE

        RETURN QUERY

        WITH ExistingInvitation AS
        (
            SELECT 1
            FROM etown_invitation
            WHERE invitationid = p_invitation_row_id
            AND invitationcode='accepted'
        ),

        UpdatedInvitation AS
        (
            UPDATE etown_invitation
            SET
                invite_role=p_invite_role,
                invitercontact=p_inviter_contact,
                invitecontact=p_invite_contact,
                inviteuserprofile=p_invite_user_profile,
                inviteruserprofilename=p_inviter_user_profile_name,
                invitercontactname=p_inviter_contact_name,
                invitationcode=p_invitation_code
            WHERE invitationid=p_invitation_row_id
            AND NOT EXISTS
            (
                SELECT 1
                FROM ExistingInvitation
            )

            RETURNING *
        ),

        MemberRelation AS
        (
            SELECT *
            FROM etown_memberrelation
            WHERE
            (
                from_contactid=p_inviter_contact
                AND from_userprofile=p_invite_user_profile
                AND to_contactid=p_invite_contact
                AND to_userprofile=p_invite_user_profile
            )

            OR

            (
                from_contactid=p_invite_contact
                AND from_userprofile=p_invite_user_profile
                AND to_contactid=p_inviter_contact
                AND to_userprofile=p_invite_user_profile
            )
        ),

        ProviderRelation AS
        (
            SELECT *
            FROM etown_providerrelation
            WHERE
            (
                fromcontactid=p_inviter_contact
                AND fromuserprofileid=p_invite_user_profile
                AND tocontactid=p_invite_contact
                AND touserprofileid=p_invite_user_profile
            )

            OR

            (
                fromcontactid=p_invite_contact
                AND fromuserprofileid=p_invite_user_profile
                AND tocontactid=p_inviter_contact
                AND touserprofileid=p_invite_user_profile
            )
        ),

        RelationExists AS
        (
            SELECT EXISTS
            (
                SELECT 1 FROM MemberRelation
                UNION
                SELECT 1 FROM ProviderRelation
            ) AS relation_exists
        ),

        DeletedInvitation AS
        (
            DELETE FROM etown_invitation
            WHERE invitationid=p_invitation_row_id
            AND (SELECT relation_exists FROM RelationExists)
            RETURNING *
        ),

        NotificationExists AS
        (
            SELECT 1
            FROM etown_notifications
            WHERE
            NOT (SELECT relation_exists FROM RelationExists)
            AND
            (
                (
                    sendercontactid=p_inviter_contact
                    AND senderuserprofileid=p_invite_user_profile
                    AND receivercontactid=p_invite_contact
                    AND receiveruserprofileid=p_invite_user_profile
                )

                OR

                (
                    sendercontactid=p_invite_contact
                    AND senderuserprofileid=p_invite_user_profile
                    AND receivercontactid=p_inviter_contact
                    AND receiveruserprofileid=p_invite_user_profile
                )
            )
        ),

        ContactData AS
        (
            SELECT *
            FROM etown_contact
            WHERE contactid=p_invite_contact
            AND NOT EXISTS(SELECT 1 FROM NotificationExists)
            AND NOT (SELECT relation_exists FROM RelationExists)
        ),

        InvitationData AS
        (
            SELECT *
            FROM UpdatedInvitation
        )

        SELECT
            (
                SELECT row_to_json(c)
                FROM ContactData c
                LIMIT 1
            ),

            (
                SELECT row_to_json(i)
                FROM InvitationData i
                LIMIT 1
            );

    END IF;

END;
$function$
;
CREATE OR REPLACE FUNCTION public.trg_provider_image_stack_service()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.providerid IS NOT NULL THEN
        PERFORM public.refresh_provider_image_stack(OLD.providerid);
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.providerid IS DISTINCT FROM OLD.providerid) THEN
        IF NEW.providerid IS NOT NULL THEN
            PERFORM public.refresh_provider_image_stack(NEW.providerid);
        END IF;
    END IF;
    RETURN NULL;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.add_provider_view(provider_id text, user_id text, p_source text DEFAULT NULL::text)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_viewer_id text := NULLIF(TRIM(LOWER(user_id)), 'string');
    v_new       boolean;
BEGIN
    IF v_viewer_id IS NULL THEN
        INSERT INTO etown_provider_views (providerprofileid, userprofileid, source)
        VALUES (provider_id, NULL, p_source)
        ON CONFLICT (providerprofileid) WHERE userprofileid IS NULL DO NOTHING;

        RETURN FOUND;
    END IF;

    INSERT INTO etown_provider_views (providerprofileid, userprofileid, source)
    VALUES (provider_id, v_viewer_id, p_source)
    ON CONFLICT (providerprofileid, userprofileid) DO NOTHING;

    v_new := FOUND;

    IF NOT v_new AND p_source = 'profile_details' THEN
        UPDATE etown_provider_views
           SET source = 'profile_details'
         WHERE providerprofileid = provider_id
           AND userprofileid    = v_viewer_id
           AND source IS DISTINCT FROM 'profile_details';
    END IF;

    RETURN v_new;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.refresh_provider_image_stack(p_providerid text)
 RETURNS void
 LANGUAGE sql
AS $function$
    UPDATE public.etown_provider p
    SET image_stack = s.stack
    FROM (
        SELECT providerid, public.build_provider_image_stack(providerid, serviceimageurl, background_image) AS stack
        FROM public.etown_provider
        WHERE providerid = p_providerid
    ) s
    WHERE p.providerid = s.providerid
      AND p.image_stack IS DISTINCT FROM s.stack;
$function$
;
CREATE OR REPLACE FUNCTION public.trg_capture_etown_category()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_name text;
BEGIN
    v_name := COALESCE(NULLIF(BTRIM(NEW.normalized_category), ''), NULLIF(BTRIM(NEW.servicecategory), ''), 'other');

    IF TG_OP = 'UPDATE'
       AND v_name = COALESCE(NULLIF(BTRIM(OLD.normalized_category), ''), NULLIF(BTRIM(OLD.servicecategory), ''), 'other') THEN
        RETURN NULL;
    END IF;

    INSERT INTO public.etown_category (categoryname, displayname)
    VALUES (v_name, CASE WHEN v_name = 'other' THEN 'Other Services' ELSE INITCAP(REPLACE(v_name, '_', ' ')) END)
    ON CONFLICT (categoryname) DO NOTHING;

    RETURN NULL;
EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'trg_capture_etown_category: could not record category % (%: %)', v_name, SQLSTATE, SQLERRM;
        RETURN NULL;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.enqueue_provider_seo_sync(p_providerid text)
 RETURNS void
 LANGUAGE sql
AS $function$
    INSERT INTO public.etown_provider_seo_sync_queue (providerid)
    SELECT p_providerid
     WHERE p_providerid IS NOT NULL
    ON CONFLICT (providerid) DO UPDATE
       SET enqueued_at = clock_timestamp(),
           next_attempt_at = now(),
           attempts = 0,
           last_error = NULL;
$function$
;
CREATE OR REPLACE FUNCTION public.enqueue_provider_seo_sync_if_listed(p_providerid text)
 RETURNS void
 LANGUAGE sql
AS $function$
    SELECT public.enqueue_provider_seo_sync(p.providerid)
      FROM public.etown_provider p
     WHERE p.providerid = p_providerid
       AND p.providertype = 'etown_verified'
       AND COALESCE(p.deleted, 'false') <> 'true';
$function$
;
CREATE OR REPLACE FUNCTION public.trg_provider_seo_sync_provider()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        IF OLD.providertype = 'etown_verified' AND COALESCE(OLD.deleted, 'false') <> 'true' THEN
            PERFORM public.enqueue_provider_seo_sync(OLD.providerid);
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        IF NEW.providertype = 'etown_verified' AND COALESCE(NEW.deleted, 'false') <> 'true' THEN
            PERFORM public.enqueue_provider_seo_sync(NEW.providerid);
        END IF;
    END IF;
    RETURN NULL;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.trg_provider_seo_sync_service()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM public.enqueue_provider_seo_sync_if_listed(OLD.providerid);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM public.enqueue_provider_seo_sync_if_listed(NEW.providerid);
    END IF;
    RETURN NULL;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.trg_provider_seo_sync_rating()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_allrequestid text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_allrequestid := OLD.allrequestid;
    ELSE
        v_allrequestid := NEW.allrequestid;
    END IF;
    PERFORM public.enqueue_provider_seo_sync_if_listed(p.providerid)
       FROM public.etown_requestservicelists r
       JOIN public.etown_provider p ON p.userprofileid = r.serviceprovideruserprofileid
      WHERE r.bookingid = v_allrequestid
      GROUP BY p.providerid;
    RETURN NULL;
END;
$function$
;
CREATE OR REPLACE FUNCTION public.build_provider_image_stack(p_providerid text, p_profile text, p_background text)
 RETURNS text[]
 LANGUAGE sql
 STABLE
AS $function$
    WITH ends AS (
        SELECT
            CASE WHEN lower(COALESCE(TRIM(p_profile), '')) IN ('', 'null', 'none', 'deleted') THEN NULL ELSE TRIM(p_profile) END AS profile,
            CASE WHEN lower(COALESCE(TRIM(p_background), '')) IN ('', 'null', 'none', 'deleted') THEN NULL ELSE TRIM(p_background) END AS background
    ),
    service_images AS (
        SELECT TRIM(v.url) AS url,
               ROW_NUMBER() OVER (ORDER BY ps.createdon NULLS LAST, ps.providerservicesid, v.slot) AS pos
        FROM public.etown_providerservices ps
        CROSS JOIN LATERAL (VALUES (1, ps.serviceimageurl1), (2, ps.serviceimageurl2), (3, ps.serviceimageurl3)) AS v(slot, url)
        WHERE ps.providerid = p_providerid
          AND lower(COALESCE(ps.deleted, 'false')) <> 'true'
          AND lower(COALESCE(TRIM(v.url), '')) NOT IN ('', 'null', 'none', 'deleted')
    ),
    middle AS (
        SELECT si.url, MIN(si.pos) AS pos
        FROM service_images si, ends e
        WHERE si.url IS DISTINCT FROM e.profile
          AND si.url IS DISTINCT FROM e.background
        GROUP BY si.url
    ),
    stack AS (
        SELECT 0 AS grp, 0::bigint AS pos, profile AS url FROM ends WHERE profile IS NOT NULL
        UNION ALL
        SELECT 1, pos, url FROM middle
        UNION ALL
        SELECT 2, 0, background FROM ends WHERE background IS NOT NULL AND background IS DISTINCT FROM profile
    )
    SELECT COALESCE(array_agg(url ORDER BY grp, pos), '{}'::text[]) FROM stack;
$function$
;
CREATE OR REPLACE FUNCTION public.trg_provider_image_stack_provider()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.image_stack := public.build_provider_image_stack(NEW.providerid, NEW.serviceimageurl, NEW.background_image);
    RETURN NEW;
END;
$function$
;

CREATE TABLE IF NOT EXISTS public.etown_activeusers (
contactid text NULL,
mobilenumber text NULL,
createdon timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL
);
CREATE TABLE IF NOT EXISTS public.etown_ai_onboarding (
aionboardingid text NOT NULL,
prompt_text text NULL,
business_image_url text NULL,
response jsonb NULL,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Kolkata'::text),
turnid text NOT NULL,
role text NOT NULL,
contactid text NULL,
userprofileid text NULL,
response_text text NULL,
response_time_ms integer NULL,
conversationid text NULL,
    PRIMARY KEY (aionboardingid),
    CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_ai_onboarding_bubble (
messageid text NOT NULL,
conversationid text NOT NULL,
prompt_text text NULL,
business_image_url text NULL,
pending_business_name text NULL,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Kolkata'::text),
contactid text NULL,
userprofileid text NULL,
turnid text NOT NULL,
role text NOT NULL,
response_text text NULL,
payload jsonb NULL,
response_time_ms integer NULL,
    PRIMARY KEY (messageid),
    CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_allrequests (
allrequestid text NOT NULL,
requestid text NULL,
requestorname text NULL,
requestorid text NULL,
requestoruserprofileid text NULL,
requestoruserprofileidname text NULL,
requestoraddress text NULL,
requestorlatitude text NULL,
requestorlongitude text NULL,
requestorrequeststatus text NULL,
requeststatus text NULL,
bookingacceptedtime timestamp without time zone NULL,
bookingcompletedtime timestamp without time zone NULL,
cancelreason text NULL,
createdon timestamp without time zone NULL,
ratingvalue bigint NULL,
isRating text NULL,
quantity integer NULL,
requestorfromdate timestamp without time zone NULL,
requestortodate timestamp without time zone NULL,
requestorcontactnumber text NULL,
requestoridentification text NULL,
requestorrequestfileurl text NULL,
requestorrole text NULL,
requestorrolename text NULL,
requestorservicedatetime text NULL,
otp text NULL,
instructions jsonb NULL,
servicetype text NULL,
bookingcancelledtime timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
acceptotp text NULL,
    PRIMARY KEY (allrequestid)
);
CREATE TABLE IF NOT EXISTS public.etown_app_uninstalls (
uninstall_id text NOT NULL DEFAULT (gen_random_uuid())::text,
contactdevicesid text NULL,
contactid text NULL,
userprofileid text NULL,
contactidname text NULL,
device_id text NULL,
app_instance_id text NULL,
device_token_prefix text NULL,
platform text NOT NULL DEFAULT 'unknown'::text,
detected_at timestamp with time zone NOT NULL DEFAULT now(),
detection_source text NOT NULL DEFAULT 'live_push'::text,
fcm_status_code integer NULL,
fcm_error text NULL,
last_seen_at timestamp with time zone NULL,
reinstalled_at timestamp with time zone NULL,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
device_identity text NULL,
    PRIMARY KEY (uninstall_id)
);
CREATE TABLE IF NOT EXISTS public.etown_application_logs (
log_level character varying(20) NOT NULL,
message text NOT NULL,
timestamp_ist timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
module_name character varying(255) NULL,
function_name character varying(255) NULL,
line_number integer NULL,
request_id character varying(255) NULL,
endpoint character varying(500) NULL,
method character varying(10) NULL,
ip_address inet NULL,
user_agent text NULL,
status_code integer NULL,
response_time_ms integer NULL,
stack_trace text NULL,
extra_data jsonb NULL DEFAULT '{}'::jsonb,
created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
userprofile_id character varying(255) NULL,
contact_id character varying(255) NULL,
id uuid NOT NULL DEFAULT gen_random_uuid(),
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_auth_challenge (
id uuid NOT NULL DEFAULT gen_uuid7(),
channel text NOT NULL DEFAULT 'email'::text,
method text NOT NULL,
purpose text NOT NULL DEFAULT 'login'::text,
identifier text NOT NULL,
contactid text NULL,
userprofileid text NULL,
role integer NULL,
secret_hash text NOT NULL,
expires_at timestamp with time zone NOT NULL,
consumed_at timestamp with time zone NULL,
attempts integer NOT NULL DEFAULT 0,
max_attempts integer NOT NULL DEFAULT 5,
resend_count integer NOT NULL DEFAULT 0,
last_sent_at timestamp with time zone NOT NULL DEFAULT now(),
language_code text NULL,
device_id text NULL,
request_ip text NULL,
user_agent text NULL,
createdon timestamp with time zone NOT NULL DEFAULT now(),
    CHECK ((channel = ANY (ARRAY['email'::text, 'sms'::text]))),
    CHECK ((method = ANY (ARRAY['otp'::text, 'magiclink'::text, 'exchange'::text]))),
    PRIMARY KEY (id),
    CHECK ((purpose = ANY (ARRAY['login'::text, 'verify'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_auth_refresh_token (
id uuid NOT NULL DEFAULT gen_uuid7(),
contactid text NOT NULL,
token_hash text NOT NULL,
issued_at timestamp with time zone NOT NULL DEFAULT now(),
expires_at timestamp with time zone NOT NULL,
revoked_at timestamp with time zone NULL,
rotated_to uuid NULL,
device_id text NULL,
user_agent text NULL,
    PRIMARY KEY (id),
    UNIQUE (token_hash)
);
CREATE TABLE IF NOT EXISTS public.etown_banner (
bannerid text NOT NULL,
type text NULL,
image text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bannerid)
);
CREATE TABLE IF NOT EXISTS public.etown_call_events (
id bigint NOT NULL DEFAULT nextval('etown_call_events_id_seq'::regclass),
event_id character varying(255) NOT NULL,
session_id character varying(255) NOT NULL,
user_id character varying(255) NULL,
event_type character varying(100) NOT NULL,
event_data jsonb NULL DEFAULT '{}'::jsonb,
timestamp_utc timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (event_id),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_call_participants (
id bigint NOT NULL DEFAULT nextval('etown_call_participants_id_seq'::regclass),
participant_id character varying(255) NOT NULL,
session_id character varying(255) NOT NULL,
user_id character varying(255) NOT NULL,
hms_peer_id character varying(255) NULL,
role character varying(50) NOT NULL DEFAULT 'guest'::character varying,
join_status character varying(50) NOT NULL DEFAULT 'invited'::character varying,
joined_at timestamp with time zone NULL,
left_at timestamp with time zone NULL,
connection_quality jsonb NULL DEFAULT '{}'::jsonb,
audio_enabled boolean NULL DEFAULT true,
video_enabled boolean NULL DEFAULT false,
created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (participant_id),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_call_sessions (
id bigint NOT NULL DEFAULT nextval('etown_call_sessions_id_seq'::regclass),
session_id character varying(255) NOT NULL,
hms_room_id character varying(255) NOT NULL,
hms_room_name character varying(255) NOT NULL,
initiator_user_id character varying(255) NOT NULL,
target_user_id character varying(255) NOT NULL,
call_status character varying(50) NOT NULL DEFAULT 'created'::character varying,
call_type character varying(20) NOT NULL DEFAULT 'voice'::character varying,
call_quality jsonb NULL DEFAULT '{}'::jsonb,
call_metadata jsonb NULL DEFAULT '{}'::jsonb,
started_at timestamp with time zone NULL,
ended_at timestamp with time zone NULL,
duration_seconds integer NULL,
hms_region character varying(50) NULL,
created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (id),
    UNIQUE (session_id)
);
CREATE TABLE IF NOT EXISTS public.etown_call_tokens (
id bigint NOT NULL DEFAULT nextval('etown_call_tokens_id_seq'::regclass),
token_id character varying(255) NOT NULL,
session_id character varying(255) NOT NULL,
user_id character varying(255) NOT NULL,
hms_auth_token text NOT NULL,
token_type character varying(50) NOT NULL DEFAULT 'auth'::character varying,
expires_at timestamp with time zone NOT NULL,
is_used boolean NULL DEFAULT false,
used_at timestamp with time zone NULL,
created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (id),
    UNIQUE (token_id)
);
CREATE TABLE IF NOT EXISTS public.etown_category (
categoryid uuid NOT NULL DEFAULT gen_random_uuid(),
categoryname text NOT NULL,
displayname text NULL,
categoryimage text NULL,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'utc'::text),
modifiedon timestamp without time zone NULL,
displayname_ta text NULL,
    UNIQUE (categoryname),
    PRIMARY KEY (categoryid)
);
CREATE TABLE IF NOT EXISTS public.etown_category_pin (
categorypinid uuid NOT NULL DEFAULT gen_random_uuid(),
contactid text NOT NULL,
userprofileid text NOT NULL,
cityname text NOT NULL,
areaname text NOT NULL DEFAULT ''::text,
categoryid uuid NOT NULL,
ispinned boolean NOT NULL DEFAULT true,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'utc'::text),
modifiedon timestamp without time zone NULL,
    PRIMARY KEY (categorypinid)
);
CREATE TABLE IF NOT EXISTS public.etown_chathistory (
chathistoryid text NOT NULL,
name text NULL,
title text NULL,
description text NULL,
contactfrom text NULL,
contactto text NULL,
conversationid text NULL,
dateandtime text NULL,
filelocation text NULL,
memberid text NULL,
providerid text NULL,
userprofileid text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chathistoryid)
);
CREATE TABLE IF NOT EXISTS public.etown_chatmessages (
chatmessagesid text NOT NULL,
name text NULL,
prompt text NULL,
connectycubedialogid text NULL,
contactid text NULL,
conversationid text NULL,
conversationrole text NULL,
conversationtype text NULL,
dateandtime text NULL,
latitude double precision NULL,
longitude double precision NULL,
memberid text NULL,
providerid text NULL,
responsetype text NULL,
userprofileid text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (chatmessagesid)
);
CREATE TABLE IF NOT EXISTS public.etown_classification_decisions (
id bigint NOT NULL DEFAULT nextval('etown_classification_decisions_id_seq'::regclass),
conversation_id text NULL,
user_role smallint NULL,
raw_prompt text NULL,
expanded_prompt text NULL,
predicted_intent text NULL,
top1_similarity numeric NULL,
vote_margin numeric NULL,
action text NULL,
shadow boolean NOT NULL DEFAULT true,
created_at timestamp with time zone NOT NULL DEFAULT now(),
decision_reason text NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_collectionitems (
collectionitemsid text NOT NULL,
collectionid text NULL,
providerid text NULL,
isfavourite text NULL,
usercontactid text NULL,
useruserprofileid text NULL,
createdon timestamp without time zone NULL,
usercontactidname text NULL,
useruserprofileidname text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (collectionitemsid)
);
CREATE TABLE IF NOT EXISTS public.etown_collections (
collectionid text NOT NULL,
collectionunicid text NULL,
collectionname text NULL,
collectiontype text NULL,
usercontactid text NULL,
useruserprofileid text NULL,
createdon timestamp without time zone NULL,
usercontactidname text NULL,
useruserprofileidname text NULL,
isMytown text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (collectionid)
);
CREATE TABLE IF NOT EXISTS public.etown_community_lastvisit (
userprofileid text NOT NULL,
last_visited_at timestamp with time zone NOT NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
need_visited_at timestamp with time zone NULL,
update_visited_at timestamp with time zone NULL,
event_visited_at timestamp with time zone NULL,
    PRIMARY KEY (userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_communitycomment (
commentid text NOT NULL,
post_type text NOT NULL,
postid text NOT NULL,
userprofileid text NOT NULL,
contactid text NULL,
comment text NOT NULL,
deleted text NOT NULL DEFAULT 'false'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CHECK ((deleted = ANY (ARRAY['true'::text, 'false'::text]))),
    PRIMARY KEY (commentid),
    CHECK ((post_type = ANY (ARRAY['need'::text, 'update'::text, 'event'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_communitylike (
likeid text NOT NULL,
post_type text NOT NULL,
postid text NOT NULL,
userprofileid text NOT NULL,
contactid text NULL,
likemode integer NOT NULL DEFAULT 1,
likemodename text NOT NULL DEFAULT 'Like'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CHECK ((likemodename =
CASE likemode
    WHEN 1 THEN 'Like'::text
    ELSE 'Dislike'::text
END)),
    CHECK ((likemode = ANY (ARRAY[1, 2]))),
    PRIMARY KEY (likeid),
    CHECK ((post_type = ANY (ARRAY['need'::text, 'update'::text, 'event'::text]))),
    UNIQUE (postid, userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_communityview (
post_type text NOT NULL,
postid text NOT NULL,
userprofileid text NOT NULL,
view_type text NOT NULL DEFAULT 'public'::text,
view_date date NOT NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (postid, userprofileid, view_date),
    CHECK ((post_type = ANY (ARRAY['need'::text, 'update'::text, 'event'::text]))),
    CHECK ((view_type = ANY (ARRAY['onclick'::text, 'public'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_connectycubedetails (
connectycubedetailsid text NOT NULL,
cubeuserid text NULL,
fullname text NULL,
userprofileid text NULL,
address text NULL,
avatarimage text NULL,
contactid text NULL,
email text NULL,
externalid text NULL,
externaluserid text NULL,
guest text NULL,
lastrequestat text NULL,
login text NULL,
memberprofile text NULL,
name text NULL,
password text NULL,
mobilenumber text NULL,
providerprofile text NULL,
role text NULL,
timezone text NULL,
website text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (connectycubedetailsid)
);
CREATE TABLE IF NOT EXISTS public.etown_contact (
contactid text NOT NULL,
firstname text NULL,
address text NULL,
addresscomp text NULL,
city text NULL,
statecode text NULL,
latitude double precision NULL,
longitude double precision NULL,
mobilephone text NULL,
email text NULL,
userlogin text NULL,
createdon timestamp without time zone NULL,
rolecode integer NULL,
rolecodename text NULL,
userregistrationtype text NULL,
activestate text NULL,
accesstoken text NULL,
invitationcode text NULL,
profileimage text NULL,
entityimageid text NULL,
customerid text NULL,
publicsearchstatus text NULL,
additionaladdressinfo text NULL,
pushnotificationcheck text NULL,
content_vector vector NULL,
lasttriggeredtime timestamp without time zone NULL,
verified boolean NULL,
aisearch_consent text NULL DEFAULT false,
chattie_consent text NULL DEFAULT false,
connectycubeid text NULL,
birthdate date NULL,
emailverified boolean NULL,
content_vector_hash text NULL,
from_socialmedia text NULL DEFAULT false,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
alternatemobile text NULL,
    PRIMARY KEY (contactid)
);
CREATE TABLE IF NOT EXISTS public.etown_contactdevices (
contactdevicesid text NOT NULL,
deviceguid text NULL,
ccuserid text NULL,
contactid text NULL,
contactidname text NULL,
userprofileid text NULL,
userprofileidname text NULL,
createdon timestamp without time zone NULL,
device_token text NULL,
device_token_type character varying(20) NULL DEFAULT 'fcm'::character varying,
token_expires_at timestamp with time zone NULL,
last_used_at timestamp with time zone NULL,
voip_device_token text NULL,
device_id text NULL,
updatedon timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
last_probed_at timestamp with time zone NULL,
credsupdate boolean NOT NULL DEFAULT false,
    PRIMARY KEY (contactdevicesid)
);
CREATE TABLE IF NOT EXISTS public.etown_conversations (
id uuid NOT NULL DEFAULT gen_uuid7(),
participant_1_id text NOT NULL,
created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
participant_2_id text NOT NULL,
last_message_id uuid NULL,
last_activity timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
participant_1_type text NOT NULL,
participant_2_type text NOT NULL,
participant_1_deleted_at timestamp without time zone NULL,
participant_2_deleted_at timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
    CHECK ((participant_1_type = ANY (ARRAY['member'::text, 'provider'::text]))),
    CHECK ((participant_2_type = ANY (ARRAY['member'::text, 'provider'::text]))),
    CHECK ((lower(participant_1_id) <> lower(participant_2_id))),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_email_templates (
emailtemplate uuid NOT NULL,
title text NULL,
subject text NULL,
body text NULL,
description text NULL,
language text NULL,
createdon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
modifiedon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
createdby text NULL,
modifiedby text NULL,
solution text NULL,
presentationxml text NULL,
safehtmlofemailtemplate text NULL,
subjectxml text NULL,
versionnumber bigint NULL,
opencount integer NULL,
openrate numeric NULL,
replycount integer NULL,
replyrate numeric NULL,
sentemailcount integer NULL,
record_overwritetime timestamp without time zone NULL,
lineofbusiness text[] NULL,
mimetype text NULL,
includetemplateforcopilotrecommendation boolean NULL,
importsequencecode integer NULL,
generationtypecode integer NULL,
componentstate text NULL,
defaultimage text NULL,
introduceversion text NULL,
    PRIMARY KEY (emailtemplate)
);
CREATE TABLE IF NOT EXISTS public.etown_etownprovider (
etownproviderid text NOT NULL,
providerid text NULL,
providerbusinessname text NULL,
townprofileid text NULL,
townname text NULL,
createdon timestamp without time zone NULL,
areaname text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
is_primary boolean NOT NULL DEFAULT false,
    PRIMARY KEY (etownproviderid)
);
CREATE TABLE IF NOT EXISTS public.etown_event (
eventid text NOT NULL,
townname text NOT NULL,
townprofileid text NOT NULL,
userprofileid text NOT NULL,
contactid text NOT NULL,
title text NOT NULL,
description text NOT NULL,
category text NULL,
latitude double precision NOT NULL,
longitude double precision NOT NULL,
location_name text NULL,
needed_at timestamp with time zone NULL,
attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
status text NOT NULL DEFAULT 'posted'::text,
deleted text NULL DEFAULT 'false'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
start_date date NULL,
start_time time without time zone NULL,
end_date date NULL,
end_time time without time zone NULL,
venue text NULL,
organizer_name text NULL,
organizer_number text NULL,
entry_info text NULL,
review text NULL,
view_count integer NOT NULL DEFAULT 0,
event_status text NULL,
    CHECK ((jsonb_typeof(attachments) = 'array'::text)),
    CHECK (((end_date IS NULL) OR (start_date IS NULL) OR (end_date >= start_date))),
    CHECK (((event_status IS NULL) OR (event_status = ANY (ARRAY['upcoming'::text, 'live'::text, 'completed'::text])))),
    CHECK (((latitude >= ('-90.0'::numeric)::double precision) AND (latitude <= (90.0)::double precision))),
    CHECK (((longitude >= ('-180.0'::numeric)::double precision) AND (longitude <= (180.0)::double precision))),
    PRIMARY KEY (eventid),
    CHECK ((status = ANY (ARRAY['draft'::text, 'posted'::text, 'closed'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_facebook_posts (
facebookpostid uuid NOT NULL DEFAULT gen_random_uuid(),
feed_id text NOT NULL,
contactid text NOT NULL,
userprofileid text NULL,
facebook_post_id character varying(128) NOT NULL,
fb_page_id character varying(64) NOT NULL,
media_type character varying(16) NOT NULL DEFAULT 'STATUS'::character varying,
published_at timestamp with time zone NOT NULL DEFAULT now(),
caption_link_clicks integer NOT NULL DEFAULT 0,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (feed_id),
    PRIMARY KEY (facebookpostid)
);
CREATE TABLE IF NOT EXISTS public.etown_facebookconnections (
facebookconnectionsid uuid NOT NULL DEFAULT gen_random_uuid(),
contactid text NOT NULL,
userprofileid text NULL,
fb_user_id character varying(64) NULL,
fb_page_id character varying(64) NOT NULL,
fb_page_name character varying(128) NULL,
page_access_token text NOT NULL,
user_access_token text NULL,
token_expires_at timestamp with time zone NULL,
is_active boolean NOT NULL DEFAULT true,
connected_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (contactid, userprofileid, fb_page_id),
    PRIMARY KEY (facebookconnectionsid)
);
CREATE TABLE IF NOT EXISTS public.etown_favouritetown (
favouritetownid text NOT NULL,
townname text NULL,
isfavourite text NULL,
usercontactid text NULL,
useruserprofileid text NULL,
createdon timestamp without time zone NULL,
usercontactidname text NULL,
useruserprofileidname text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (favouritetownid)
);
CREATE TABLE IF NOT EXISTS public.etown_feed (
feedid text NOT NULL,
audience integer NULL,
audiencename text NULL,
contactid text NULL,
contactidname text NULL,
userprofileid text NULL,
userprofileidname text NULL,
feedfilelocationurl text NULL,
feedtext text NULL,
role integer NULL,
rolename text NULL,
serviceimageurl text NULL,
servicename text NULL,
tagpeoples text NULL,
thumbnailfileurl text NULL,
createdon timestamp without time zone NULL,
feedfilelocation2 text NULL,
feedfilelocation3 text NULL,
feedfilelocation4 text NULL,
feedfilelocation5 text NULL,
feedvideofilelocation text NULL,
modifiedon timestamp without time zone NULL,
orientation text NULL,
mycircle boolean NULL,
sharetooffer boolean NULL,
groups jsonb NULL,
phonebook jsonb NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
circle_only boolean NOT NULL DEFAULT false,
    PRIMARY KEY (feedid)
);
CREATE TABLE IF NOT EXISTS public.etown_feedcomments (
feedcommentsid text NOT NULL,
feedid text NULL,
commentedbycontactid text NULL,
commentedbycontactidname text NULL,
commentedbyuserprofileid text NULL,
commentedbyuserprofileidname text NULL,
textcomments text NULL,
createdon timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (feedcommentsid)
);
CREATE TABLE IF NOT EXISTS public.etown_feedlikes (
feedlikesid text NOT NULL,
feedid text NULL,
likemode integer NULL,
likemodename text NULL,
likesbyuserprofileid text NULL,
likesbyuserprofileidname text NULL,
createdond timestamp without time zone NULL,
likedbycontactid text NULL,
likedbycontactidname text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (feedlikesid)
);
CREATE TABLE IF NOT EXISTS public.etown_franchise (
franchise_application_id text NOT NULL,
fullname text NULL,
email text NULL,
phone text NULL,
city text NULL,
list_of_towns text NULL,
local_market_understanding text NULL,
provider_onboarding_approach text NULL,
existing_provider_network text NULL,
team_details text NULL,
weekly_time_commitment text NULL,
growth_plan text NULL,
investment_readiness text NULL,
reason_for_etown_franchise_owner text NULL,
expected_start_timeline text NULL,
acknowledge boolean NULL,
signature text NULL,
date date NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (franchise_application_id)
);
CREATE TABLE IF NOT EXISTS public.etown_groupitems (
groupitemsid text NOT NULL,
groupid text NULL,
userprofile_id text NULL,
username text NULL,
user_contactid text NULL,
user_contactidname text NULL,
user_userprofileid text NULL,
user_userprofileidname text NULL,
createdon timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (groupitemsid)
);
CREATE TABLE IF NOT EXISTS public.etown_groups (
groupid text NOT NULL,
groupunicid text NULL,
groupname text NULL,
groupimageurl text NULL,
user_contactid text NULL,
user_contactidname text NULL,
user_userprofileid text NULL,
user_userprofileidname text NULL,
createdon timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (groupid)
);
CREATE TABLE IF NOT EXISTS public.etown_instagram_posts (
instagrampostid uuid NOT NULL DEFAULT gen_random_uuid(),
feed_id text NOT NULL,
contactid text NOT NULL,
userprofileid text NULL,
instagram_media_id character varying(64) NOT NULL,
instagram_business_id character varying(64) NOT NULL,
media_type character varying(16) NOT NULL DEFAULT 'PHOTO'::character varying,
published_at timestamp with time zone NOT NULL DEFAULT now(),
caption_link_clicks integer NOT NULL DEFAULT 0,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (feed_id),
    PRIMARY KEY (instagrampostid)
);
CREATE TABLE IF NOT EXISTS public.etown_instagramconnections (
instagramconnectionsid uuid NOT NULL DEFAULT gen_random_uuid(),
contactid text NOT NULL,
fb_user_id character varying(64) NULL,
fb_page_id character varying(64) NULL,
instagram_business_id character varying(64) NOT NULL,
instagram_username character varying(64) NULL,
instagram_name character varying(128) NULL,
access_token text NOT NULL,
token_expires_at timestamp with time zone NULL,
is_active boolean NOT NULL DEFAULT true,
connected_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
userprofileid text NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (contactid, userprofileid, instagram_business_id),
    PRIMARY KEY (instagramconnectionsid)
);
CREATE TABLE IF NOT EXISTS public.etown_intent_examples (
id bigint NOT NULL DEFAULT nextval('etown_intent_examples_id_seq'::regclass),
intent_label text NOT NULL,
agent_scope text NOT NULL DEFAULT 'both'::text,
example_text text NOT NULL,
language text NOT NULL DEFAULT 'en'::text,
content_vector vector NULL,
source text NOT NULL DEFAULT 'mined'::text,
is_active boolean NOT NULL DEFAULT true,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_invitation (
invitationid text NOT NULL,
invitationcode text NULL,
invitercontact text NULL,
invitercontactname text NULL,
inviteruserprofile text NULL,
inviteruserprofilename text NULL,
invitecontact text NULL,
invitecontactname text NULL,
inviteuserprofile text NULL,
inviteuserprofilename text NULL,
invitecontactmobilenumber text NULL,
invitecontactemail text NULL,
type integer NULL,
typename text NULL,
invitationtype text NULL,
invite_role integer NULL,
invite_rolename text NULL,
isnotification integer NULL,
isnotificationame text NULL,
suffixid text NULL,
isopponentadded text NULL,
createdon timestamp without time zone NULL,
inviterbusinessname text NULL,
inviteebusinessname text NULL,
new_inviterole text NULL,
new_inviterolename text NULL,
receivername text NULL,
status text NULL DEFAULT 'unread'::text,
statuscode text NULL DEFAULT '0'::text,
modifieddate timestamp without time zone NULL,
    PRIMARY KEY (invitationid)
);
CREATE TABLE IF NOT EXISTS public.etown_mem0_memories (
id uuid NOT NULL,
vector vector NULL,
payload jsonb NULL,
facts text NULL,
userprofileid text NULL,
conversation_id text NULL,
name text NULL,
created_at timestamp without time zone NULL DEFAULT now(),
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_member (
memberid text NOT NULL,
userprofileid text NOT NULL,
userprofileidname text NULL,
contactid text NOT NULL,
contactidname text NULL,
dateofcreation timestamp without time zone NULL,
email text NULL,
mobilenumber text NULL,
role integer NULL,
rolename text NULL,
content_vector vector NULL,
name text NULL,
marketing_person_name text NULL,
verified boolean NULL,
content_vector_hash text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
plan_code text NOT NULL DEFAULT 'free'::text,
subscription_status text NOT NULL DEFAULT 'active'::text,
plan_started_on date NULL,
plan_expires_on date NULL,
    PRIMARY KEY (memberid),
    CHECK ((subscription_status = ANY (ARRAY['active'::text, 'cancelled'::text, 'expired'::text, 'suspended'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_member_tracking (
hit_date date NOT NULL DEFAULT CURRENT_DATE,
feeds bigint NOT NULL DEFAULT 0,
mytown_suggestions bigint NOT NULL DEFAULT 0,
ai_search_v2 bigint NOT NULL DEFAULT 0,
chaatie bigint NOT NULL DEFAULT 0,
prompt_suggestion bigint NOT NULL DEFAULT 0,
nearme_list bigint NOT NULL DEFAULT 0,
member_dashboard bigint NOT NULL DEFAULT 0,
pending_invitation bigint NOT NULL DEFAULT 0,
user_exists bigint NOT NULL DEFAULT 0,
etown_lookup bigint NOT NULL DEFAULT 0,
collections bigint NOT NULL DEFAULT 0,
groups bigint NOT NULL DEFAULT 0,
notification_count bigint NOT NULL DEFAULT 0,
invite_notification bigint NOT NULL DEFAULT 0,
mycircle bigint NOT NULL DEFAULT 0,
inbox bigint NOT NULL DEFAULT 0,
request_list bigint NOT NULL DEFAULT 0,
createdon timestamp without time zone NOT NULL DEFAULT now(),
modifieddate timestamp without time zone NOT NULL DEFAULT now(),
active_member bigint NOT NULL DEFAULT 0,
offers_viewed bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (hit_date)
);
CREATE TABLE IF NOT EXISTS public.etown_memberrelation (
memberrelationid text NOT NULL,
favorite integer NULL,
relationshiptype integer NULL,
to_contactid text NULL,
to_firstname text NULL,
to_userprofile text NULL,
to_mobilenumber text NULL,
to_email text NULL,
to_latitude double precision NULL,
to_longitute double precision NULL,
to_profileimage text NULL,
to_userlogin text NULL,
to_mobilenumberaccesstoken text NULL,
to_connectycubeid text NULL,
from_contactid text NULL,
from_firstname text NULL,
from_userprofile text NULL,
from_mobilenumber text NULL,
from_email text NULL,
from_latitude double precision NULL,
from_longitute double precision NULL,
from_profileimage text NULL,
from_userlogin text NULL,
from_mobilenumberaccesstoken text NULL,
from_connectycubeid text NULL,
invite_relationshiptype text NULL,
invite_relationshiptypename text NULL,
createdon timestamp without time zone NULL,
from_memberid text NULL,
from_memberidname text NULL,
favoritename text NULL,
relationshiptypename text NULL,
modifieddate timestamp without time zone NULL,
    PRIMARY KEY (memberrelationid)
);
CREATE TABLE IF NOT EXISTS public.etown_memory_decisions (
id bigint NOT NULL DEFAULT nextval('etown_memory_decisions_id_seq'::regclass),
turn_id uuid NOT NULL,
conversation_id text NULL,
user_id text NULL,
user_role smallint NULL,
raw_prompt text NULL,
memory_enabled boolean NULL,
retrieval_outcome text NULL,
retrieved_count integer NULL,
injected_count integer NULL,
best_similarity numeric NULL,
similarity_floor numeric NULL,
candidates jsonb NULL,
retrieval_ms integer NULL,
affinity_count integer NULL,
affinity_matched text NULL,
affinity_score numeric NULL,
extraction_outcome text NULL,
facts_extracted jsonb NULL,
events jsonb NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_memory_settings (
user_id text NOT NULL,
enabled boolean NOT NULL DEFAULT true,
created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (user_id)
);
CREATE TABLE IF NOT EXISTS public.etown_messages (
id uuid NOT NULL DEFAULT gen_uuid7(),
conversation_id uuid NOT NULL,
sender_id text NOT NULL,
receiver_id text NOT NULL,
is_read boolean NOT NULL DEFAULT false,
created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
message_type text NOT NULL DEFAULT 'text'::text,
sender_type text NOT NULL,
file_url text NULL,
receiver_type text NOT NULL,
message_text text NULL,
read_at timestamp without time zone NULL,
is_deleted boolean NOT NULL DEFAULT false,
edited_at timestamp without time zone NULL,
delivered_at timestamp without time zone NULL,
reply_to_message_id uuid NULL,
sender_deleted_at timestamp without time zone NULL,
receiver_deleted_at timestamp without time zone NULL,
sender_name text NULL,
receiver_name text NULL,
    CHECK ((message_type = ANY (ARRAY['text'::text, 'image'::text, 'video'::text, 'document'::text, 'audio'::text, 'file'::text]))),
    PRIMARY KEY (id),
    CHECK ((receiver_type = ANY (ARRAY['member'::text, 'provider'::text]))),
    CHECK ((lower(sender_id) <> lower(receiver_id))),
    CHECK ((sender_type = ANY (ARRAY['member'::text, 'provider'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_metadata_city (
id integer NOT NULL DEFAULT nextval('etown_metadata_city_id_seq'::regclass),
city text NULL,
created_at timestamp without time zone NULL DEFAULT now(),
    UNIQUE (city),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_metadata_term (
id integer NOT NULL DEFAULT nextval('etown_metadata_term_id_seq'::regclass),
term text NULL,
created_at timestamp without time zone NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (term)
);
CREATE TABLE IF NOT EXISTS public.etown_mobileverification (
mobileverificationid text NOT NULL,
mobilenumber text NULL,
name text NULL,
otp text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (mobileverificationid)
);
CREATE TABLE IF NOT EXISTS public.etown_need (
needid text NOT NULL,
townname text NOT NULL,
townprofileid text NOT NULL,
userprofileid text NOT NULL,
contactid text NOT NULL,
title text NOT NULL,
description text NOT NULL,
category text NULL,
latitude double precision NOT NULL,
longitude double precision NOT NULL,
location_name text NULL,
needed_at timestamp with time zone NULL,
attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
status text NOT NULL DEFAULT 'posted'::text,
deleted text NULL DEFAULT 'false'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
budget text NULL,
need_status text NOT NULL DEFAULT 'open'::text,
view_count integer NOT NULL DEFAULT 0,
    CHECK ((jsonb_typeof(attachments) = 'array'::text)),
    CHECK (((latitude >= ('-90.0'::numeric)::double precision) AND (latitude <= (90.0)::double precision))),
    CHECK (((longitude >= ('-180.0'::numeric)::double precision) AND (longitude <= (180.0)::double precision))),
    CHECK ((need_status = ANY (ARRAY['open'::text, 'closed'::text]))),
    PRIMARY KEY (needid),
    CHECK ((status = ANY (ARRAY['draft'::text, 'posted'::text, 'closed'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_needresponse (
needresponseid text NOT NULL,
needid text NOT NULL,
userprofileid text NOT NULL,
contactid text NULL,
message text NOT NULL,
conversation_id text NULL,
title_message_id text NULL,
message_id text NULL,
deleted text NULL DEFAULT 'false'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (needresponseid)
);
CREATE TABLE IF NOT EXISTS public.etown_notification_pool (
id bigint NOT NULL DEFAULT nextval('etown_notification_pool_id_seq'::regclass),
type_key text NOT NULL,
title text NOT NULL,
body text NOT NULL,
is_active boolean NOT NULL DEFAULT true,
created_on timestamp with time zone NOT NULL DEFAULT now(),
modifieddate timestamp without time zone NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_notification_schedule_runs (
job_name text NOT NULL,
run_date date NOT NULL,
started_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (job_name, run_date)
);
CREATE TABLE IF NOT EXISTS public.etown_notifications (
notificationsid text NOT NULL,
sendercontactid text NULL,
sendercontactidname text NULL,
senderuserprofileid text NULL,
senderuserprofileidname text NULL,
receivercontactid text NULL,
receivercontactidname text NULL,
receiveruserprofileid text NULL,
receiveruserprofileidname text NULL,
role integer NULL,
rolename text NULL,
notificationcontent text NULL,
notificationstatus text NULL,
notificationtype text NULL,
createdon timestamp without time zone NULL,
statuscode text NULL,
statusreason text NULL,
statecode text NULL,
statecodename text NULL,
modifieddate timestamp without time zone NULL,
    PRIMARY KEY (notificationsid)
);
CREATE TABLE IF NOT EXISTS public.etown_offer_views (
id uuid NOT NULL DEFAULT gen_random_uuid(),
offersid text NOT NULL,
userprofileid text NOT NULL,
name text NULL,
created_on timestamp with time zone NULL DEFAULT CURRENT_TIMESTAMP,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (offersid, userprofileid),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_offerings (
offeringsid text NOT NULL DEFAULT (gen_random_uuid())::text,
userprofileid text NOT NULL,
businessname text NOT NULL,
businesstype text NOT NULL,
businesssubtype text NOT NULL,
businessdescription text NULL,
createdon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
items jsonb NOT NULL DEFAULT '[]'::jsonb,
description text NULL,
business_details jsonb NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (offeringsid),
    UNIQUE (userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_offerings_conversion_jobs (
jobid text NOT NULL DEFAULT (gen_random_uuid())::text,
batchid text NOT NULL,
userprofileid text NOT NULL,
status text NOT NULL DEFAULT 'pending'::text,
force boolean NOT NULL DEFAULT false,
preview jsonb NULL,
reason text NULL,
items_added integer NULL,
createdon timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
updatedon timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
confirmedon timestamp with time zone NULL,
    PRIMARY KEY (jobid),
    CHECK ((status = ANY (ARRAY['pending'::text, 'processing'::text, 'ready'::text, 'confirmed'::text, 'failed'::text, 'skipped'::text, 'member_account'::text]))),
    UNIQUE (batchid, userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_offers (
offersid text NOT NULL,
businessname text NOT NULL,
businessprofileimageurl text NULL,
colorcode text NULL,
contactid text NULL,
contactidname text NULL,
userprofileid text NULL,
userprofileidname text NULL,
offerunicid text NULL,
offertype text NULL,
offertypename text NULL,
fontfamily text NULL,
description text NULL,
thumbnailfileurl text NULL,
offerfile1 text NULL,
offerfile2 text NULL,
offerfile3 text NULL,
offerfile4 text NULL,
offerfile5 text NULL,
offervideofile text NULL,
validity text NULL,
createdtime timestamp without time zone NULL,
scheduledtime timestamp without time zone NULL,
scheduledendtime timestamp without time zone NULL,
latitude text NULL,
longitude text NULL,
createdon timestamp without time zone NULL,
tags text NULL,
vieweduserprofileid text NULL,
isactive text NULL,
languagecode text NULL,
title text NULL,
fontsize text NULL,
fontstyle text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (offersid)
);
CREATE TABLE IF NOT EXISTS public.etown_popular_category_pin (
popularcategorypinid uuid NOT NULL DEFAULT gen_random_uuid(),
contactid text NOT NULL,
userprofileid text NOT NULL,
latitude numeric NOT NULL,
longitude numeric NOT NULL,
categoryid uuid NOT NULL,
ispinned boolean NOT NULL DEFAULT true,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'utc'::text),
modifiedon timestamp without time zone NULL,
    PRIMARY KEY (popularcategorypinid)
);
CREATE TABLE IF NOT EXISTS public.etown_provider (
providerid text NOT NULL,
businessname text NULL,
businesscategory text NULL,
address text NULL,
statecode text NULL,
city text NULL,
contactid text NULL,
contactidname text NULL,
userprofileid text NULL,
userprofileidname text NULL,
firstname text NULL,
email text NULL,
creationdate timestamp without time zone NULL,
mobilenumber text NULL,
providertype text NULL,
publicsearchprovider text NULL,
role integer NULL,
rolename text NULL,
yearsinbusiness text NULL,
serviceimageurl text NULL,
additionaladdressinfo text NULL,
description text NULL,
availability text NULL,
areaname text NULL,
townname text NULL,
latitude double precision NULL,
longitude double precision NULL,
content_vector vector NULL,
trusted boolean NULL DEFAULT false,
boosted boolean NOT NULL DEFAULT false,
marketing_person_name text NULL,
source text NULL,
servicetype text NULL,
websiteurl text NULL,
whatsapp boolean NOT NULL DEFAULT true,
content_vector_hash text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
plan_code text NOT NULL DEFAULT 'free'::text,
subscription_status text NOT NULL DEFAULT 'active'::text,
plan_started_on date NULL,
plan_expires_on date NULL,
background_image text NULL,
disabled boolean NOT NULL DEFAULT false,
alert boolean NOT NULL DEFAULT true,
alert_dismissed_on date NULL,
approve boolean NULL,
alternatemobile text NULL,
interested text NULL,
marketing_mobilenumber text NULL,
image_stack text[] NOT NULL DEFAULT '{}'::text[],
    CHECK (((latitude >= ('-90.0'::numeric)::double precision) AND (latitude <= (90.0)::double precision))),
    CHECK (((longitude >= ('-180.0'::numeric)::double precision) AND (longitude <= (180.0)::double precision))),
    CHECK ((subscription_status = ANY (ARRAY['active'::text, 'cancelled'::text, 'expired'::text, 'suspended'::text]))),
    PRIMARY KEY (providerid)
);
CREATE TABLE IF NOT EXISTS public.etown_provider_details (
providerid text NOT NULL,
place_id text NULL,
google_rating double precision NULL,
google_rating_count integer NULL,
business_status text NULL,
website_uri text NULL,
google_maps_uri text NULL,
opening_hours jsonb NULL,
reviews jsonb NULL,
images jsonb NULL,
comments jsonb NULL,
createdon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updatedon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
photo_blob_urls jsonb NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (providerid)
);
CREATE TABLE IF NOT EXISTS public.etown_provider_geoapify (
id bigint NOT NULL DEFAULT nextval('etown_provider_geoapify_id_seq'::regclass),
providerid text NOT NULL,
geoapify_place_id text NULL,
name text NULL,
website text NULL,
phone text NULL,
email text NULL,
opening_hours text NULL,
description text NULL,
brand text NULL,
operator text NULL,
branch text NULL,
network text NULL,
owner text NULL,
categories jsonb NULL,
contact jsonb NULL,
facilities jsonb NULL,
category_specific jsonb NULL,
address jsonb NULL,
timezone jsonb NULL,
raw_properties jsonb NULL,
raw_geometry jsonb NULL,
createdon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updatedon timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE (providerid),
    UNIQUE (providerid)
);
CREATE TABLE IF NOT EXISTS public.etown_provider_review (
reviewid text NOT NULL,
review text NULL,
action_mode text NOT NULL,
mobilenumber text NOT NULL,
marketing_person_name text NULL,
businessname text NULL,
description text NULL,
types text NULL,
formattedaddress text NULL,
latitude text NULL,
longitude text NULL,
businesslogo text NULL,
business_category text NULL,
service_category text NULL,
city text NULL,
additionaladdressinfo text NULL,
services text NULL,
area_location text NULL,
servicetype text NULL,
state text NULL,
language text NULL,
email text NULL,
servicedetails text NOT NULL DEFAULT '[]'::text,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'utc'::text),
providerid text NULL,
rejectedreason text NULL,
alternatemobile text NULL,
interested text NULL,
    PRIMARY KEY (reviewid)
);
CREATE TABLE IF NOT EXISTS public.etown_provider_search_appearances (
id bigint NOT NULL DEFAULT nextval('etown_provider_search_appearances_id_seq'::regclass),
providerid text NOT NULL,
userprofileid text NULL,
contact_id text NOT NULL,
query text NOT NULL,
normalized_query text NOT NULL,
search_date date NOT NULL,
endpoint text NOT NULL,
appearances integer NOT NULL DEFAULT 1,
best_position integer NULL,
first_seen_at timestamp with time zone NOT NULL DEFAULT now(),
last_seen_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CHECK ((char_length(query) <= 500))
);
CREATE TABLE IF NOT EXISTS public.etown_provider_seo_sync_queue (
providerid text NOT NULL,
enqueued_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
next_attempt_at timestamp with time zone NOT NULL DEFAULT now(),
claimed_until timestamp with time zone NULL,
attempts integer NOT NULL DEFAULT 0,
last_error text NULL,
    PRIMARY KEY (providerid)
);
CREATE TABLE IF NOT EXISTS public.etown_provider_tracking (
hit_date date NOT NULL DEFAULT CURRENT_DATE,
active_provider bigint NOT NULL DEFAULT 0,
feeds bigint NOT NULL DEFAULT 0,
mytown_suggestions bigint NOT NULL DEFAULT 0,
ai_search_v2 bigint NOT NULL DEFAULT 0,
chaatie bigint NOT NULL DEFAULT 0,
prompt_suggestion bigint NOT NULL DEFAULT 0,
nearme_list bigint NOT NULL DEFAULT 0,
provider_dashboard bigint NOT NULL DEFAULT 0,
rangesummary bigint NOT NULL DEFAULT 0,
pending_invitation bigint NOT NULL DEFAULT 0,
user_exists bigint NOT NULL DEFAULT 0,
etown_lookup bigint NOT NULL DEFAULT 0,
collections bigint NOT NULL DEFAULT 0,
groups bigint NOT NULL DEFAULT 0,
offers_created bigint NOT NULL DEFAULT 0,
notification_count bigint NOT NULL DEFAULT 0,
invite_notification bigint NOT NULL DEFAULT 0,
mycircle bigint NOT NULL DEFAULT 0,
inbox bigint NOT NULL DEFAULT 0,
request_list bigint NOT NULL DEFAULT 0,
createdon timestamp without time zone NOT NULL DEFAULT now(),
modifieddate timestamp without time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (hit_date)
);
CREATE TABLE IF NOT EXISTS public.etown_provider_views (
id bigint NOT NULL DEFAULT nextval('etown_provider_views_id_seq'::regclass),
providerprofileid text NOT NULL,
userprofileid text NULL,
created_on timestamp with time zone NULL DEFAULT CURRENT_TIMESTAMP,
source text NULL,
    PRIMARY KEY (id),
    UNIQUE (providerprofileid, userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_providerrelation (
providerrelationid text NOT NULL,
relationshiptype integer NULL,
relationshiptypename text NULL,
favorite integer NULL,
favoritename text NULL,
fromcontactid text NULL,
fromcontactidname text NULL,
fromuserprofileid text NULL,
fromuserprofileidname text NULL,
fromproviderid text NULL,
fromprovideridname text NULL,
tocontactid text NULL,
tocontactidname text NULL,
touserprofileid text NULL,
touserprofileidname text NULL,
invite_relationshiptype text NULL,
invite_relationshiptypename text NULL,
createdon timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
    PRIMARY KEY (providerrelationid)
);
CREATE TABLE IF NOT EXISTS public.etown_providerservices (
providerservicesid text NOT NULL,
businessname text NULL,
businesscategory text NULL,
address text NULL,
availability text NULL,
city text NULL,
contactid text NULL,
contactidname text NULL,
email text NULL,
mobilenumber text NULL,
pricerange text NULL,
providerid text NULL,
providertype text NULL,
publicsearchprovider text NULL,
servicecategory text NULL,
servicedescription text NULL,
serviceimageurl text NULL,
latitude text NULL,
longitude text NULL,
services text NULL,
statecode text NULL,
userprofileid text NULL,
userprofileidname text NULL,
websiteurl text NULL,
yearsinbusiness text NULL,
createdon timestamp without time zone NULL,
additionaladdressinfo text NULL,
areaname text NULL,
serviceimageurl1 text NULL,
serviceimageurl2 text NULL,
serviceimageurl3 text NULL,
quantity text NULL,
generalcategory text NULL,
source text NULL,
serviceitems jsonb NULL,
normalized_category text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (providerservicesid)
);
CREATE TABLE IF NOT EXISTS public.etown_providerservices_copy (
providerservicesid text NOT NULL,
businessname text NULL,
businesscategory text NULL,
address text NULL,
availability text NULL,
city text NULL,
contactid text NULL,
contactidname text NULL,
email text NULL,
mobilenumber text NULL,
pricerange text NULL,
providerid text NULL,
providertype text NULL,
publicsearchprovider text NULL,
servicecategory text NULL,
servicedescription text NULL,
serviceimageurl text NULL,
services text NULL,
statecode text NULL,
userprofileid text NULL,
userprofileidname text NULL,
websiteurl text NULL,
yearsinbusiness text NULL,
createdon timestamp with time zone NULL,
additionaladdressinfo text NULL,
areaname text NULL,
updated_at timestamp with time zone NULL DEFAULT now(),
serviceimageurl1 text NULL,
serviceimageurl2 text NULL,
serviceimageurl3 text NULL,
normalized_category character varying(100) NULL,
latitude double precision NULL,
longitude double precision NULL,
quantity text NULL,
content_vector vector NULL,
generalcategory text NULL,
source text NULL,
serviceitems jsonb NULL,
content_vector_hash text NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    CHECK (((latitude >= ('-90.0'::numeric)::double precision) AND (latitude <= (90.0)::double precision))),
    CHECK (((longitude >= ('-180.0'::numeric)::double precision) AND (longitude <= (180.0)::double precision))),
    PRIMARY KEY (providerservicesid)
);
CREATE TABLE IF NOT EXISTS public.etown_providerservicesadf (
providerservicesid text NOT NULL,
contact_id text NULL,
user_profile_id text NULL,
business_name text NULL,
contact jsonb NULL,
item jsonb NULL,
availability text NULL,
business_image text NULL,
provider_type text NULL,
location jsonb NULL,
experience integer NULL,
content_vector vector NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (providerservicesid)
);
CREATE TABLE IF NOT EXISTS public.etown_query_expansions (
id bigint NOT NULL DEFAULT nextval('etown_query_expansions_id_seq'::regclass),
term text NOT NULL,
expansion text NOT NULL,
language text NOT NULL DEFAULT 'en'::text,
confidence numeric NOT NULL DEFAULT 1.0,
source text NOT NULL DEFAULT 'curated'::text,
is_active boolean NOT NULL DEFAULT true,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (term, expansion, language)
);
CREATE TABLE IF NOT EXISTS public.etown_ratings (
ratingid text NOT NULL,
overallrating double precision NULL,
ratingcount integer NULL,
provideruserprofileid text NULL,
providercontactid text NULL,
providerbusinessname text NULL,
memberbookingcount integer NULL,
createdon timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (ratingid)
);
CREATE TABLE IF NOT EXISTS public.etown_requestservicelists (
requestservicelistsid text NOT NULL,
servicename text NULL,
bookingid text NULL,
serviceprovideraddress text NULL,
serviceprovidercurrentaddresslatitude text NULL,
serviceprovidercurrentaddresslongitude text NULL,
serviceproviderid text NULL,
serviceproviderlatitude text NULL,
serviceproviderlongitude text NULL,
quantity text NULL,
name text NULL,
providerservices text NULL,
createdon timestamp without time zone NULL,
serviceprovidercontactid text NULL,
serviceprovideruserprofileid text NULL,
serviceimageurl text NULL,
serviceprovidercurrentaddress text NULL,
price text NULL,
modifieddate timestamp without time zone NULL,
    PRIMARY KEY (requestservicelistsid)
);
CREATE TABLE IF NOT EXISTS public.etown_search_history (
id bigint NOT NULL DEFAULT nextval('etown_search_history_id_seq'::regclass),
contact_id text NOT NULL,
query text NOT NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CHECK ((char_length(query) <= 500))
);
CREATE TABLE IF NOT EXISTS public.etown_search_term_stats (
term text NOT NULL,
ndoc integer NOT NULL,
total_docs integer NOT NULL,
refreshed_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (term)
);
CREATE TABLE IF NOT EXISTS public.etown_social_insights_snapshots (
snapshotid uuid NOT NULL DEFAULT gen_random_uuid(),
platform character varying(16) NOT NULL,
feed_id text NOT NULL,
contactid text NOT NULL,
userprofileid text NULL,
media_id character varying(128) NOT NULL,
media_type character varying(16) NULL,
impressions integer NULL,
reach integer NULL,
likes integer NULL,
comments integer NULL,
shares integer NULL,
saved integer NULL,
total_interactions integer NULL,
views integer NULL,
profile_visits integer NULL,
follows integer NULL,
link_clicks integer NULL,
reactions integer NULL,
clicks integer NULL,
video_views integer NULL,
caption_link_clicks integer NOT NULL DEFAULT 0,
captured_at timestamp with time zone NOT NULL DEFAULT now(),
period_days smallint NOT NULL DEFAULT 30,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (snapshotid),
    UNIQUE (platform, feed_id, period_days)
);
CREATE TABLE IF NOT EXISTS public.etown_staff_numbers (
id uuid NOT NULL DEFAULT gen_random_uuid(),
mobilenumber text NOT NULL,
role text NOT NULL,
name text NULL,
is_active boolean NOT NULL DEFAULT true,
createdon timestamp with time zone NOT NULL DEFAULT now(),
modifiedon timestamp with time zone NOT NULL DEFAULT now(),
    CHECK ((mobilenumber ~ '^\d{10}$'::text)),
    UNIQUE (mobilenumber, role),
    PRIMARY KEY (id),
    CHECK ((role = ANY (ARRAY['staff'::text, 'csr'::text, 'fos'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_subscription_plan (
plan_code text NOT NULL,
plan_name text NOT NULL,
badge_label text NULL,
display_order smallint NOT NULL DEFAULT 0,
is_active boolean NOT NULL DEFAULT true,
max_gallery_photos integer NULL,
max_listings integer NULL,
max_towns integer NULL,
max_active_offers integer NULL,
max_offers_per_month integer NULL,
max_chaatie_generations_month integer NULL,
max_posts_per_month integer NULL,
whatsapp_quota_month integer NULL DEFAULT 0,
verified_badge boolean NOT NULL DEFAULT false,
full_business_profile boolean NOT NULL DEFAULT false,
post_audience_targeting boolean NOT NULL DEFAULT false,
booking_insights boolean NOT NULL DEFAULT false,
profile_view_insights text NOT NULL DEFAULT 'none'::text,
support_channel text NOT NULL DEFAULT 'email'::text,
support_response_hours integer NULL,
price_amount numeric NULL,
price_currency text NULL DEFAULT 'INR'::text,
billing_period_months smallint NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
provider_onboard integer NULL,
chattie_conversation integer NULL,
post_public_feed boolean NOT NULL DEFAULT true,
    CHECK ((profile_view_insights = ANY (ARRAY['none'::text, 'weekly'::text, 'monthly'::text, 'weekly_monthly'::text, 'daily'::text]))),
    CHECK (((COALESCE(max_gallery_photos, 0) >= 0) AND (COALESCE(max_listings, 0) >= 0) AND (COALESCE(max_towns, 0) >= 0) AND (COALESCE(max_active_offers, 0) >= 0) AND (COALESCE(max_offers_per_month, 0) >= 0) AND (COALESCE(max_chaatie_generations_month, 0) >= 0) AND (COALESCE(max_posts_per_month, 0) >= 0) AND (whatsapp_quota_month >= 0) AND (COALESCE(provider_onboard, 0) >= 0) AND (COALESCE(chattie_conversation, 0) >= 0))),
    PRIMARY KEY (plan_code)
);
CREATE TABLE IF NOT EXISTS public.etown_subscriptions (
subscriptionsid text NOT NULL,
name text NULL,
current_userprofileid text NULL,
opponent_userprofileid text NULL,
subscriptionstatus text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
current_userprofileidname text NULL,
opponent_userprofileidname text NULL,
    PRIMARY KEY (subscriptionsid)
);
CREATE TABLE IF NOT EXISTS public.etown_support_ticket (
ticketid text NOT NULL,
reference text NOT NULL,
contactid text NULL,
userprofileid text NULL,
role integer NULL,
category text NOT NULL DEFAULT 'general'::text,
subject text NOT NULL,
message text NOT NULL,
contact_email text NULL,
contact_phone text NULL,
contact_name text NULL,
plan_code text NULL,
support_channel text NOT NULL DEFAULT 'email'::text,
response_hours integer NULL,
due_at timestamp with time zone NULL,
source text NOT NULL DEFAULT 'api'::text,
status text NOT NULL DEFAULT 'open'::text,
email_sent boolean NOT NULL DEFAULT false,
resolved_at timestamp with time zone NULL,
resolution_notes text NULL,
deleted text NULL DEFAULT 'false'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CHECK ((COALESCE(response_hours, 0) >= 0)),
    CHECK ((source = ANY (ARRAY['api'::text, 'chaatie'::text]))),
    CHECK ((status = ANY (ARRAY['open'::text, 'in_progress'::text, 'waiting_on_user'::text, 'resolved'::text, 'closed'::text]))),
    PRIMARY KEY (ticketid),
    UNIQUE (reference)
);
CREATE TABLE IF NOT EXISTS public.etown_terminal_logs (
logid uuid NOT NULL DEFAULT gen_random_uuid(),
timestamp_ist timestamp with time zone NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'IST'::text),
log_line text NOT NULL,
source text NOT NULL,
created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (logid)
);
CREATE TABLE IF NOT EXISTS public.etown_termsandconditions (
termsandconditionsid text NOT NULL,
name text NULL,
termsandconditions text NULL,
type text NULL,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (termsandconditionsid)
);
CREATE TABLE IF NOT EXISTS public.etown_town_view_tracking (
id bigint NOT NULL DEFAULT nextval('etown_town_view_tracking_id_seq'::regclass),
townprofileid text NOT NULL,
userprofileid text NOT NULL,
created_on timestamp with time zone NULL DEFAULT CURRENT_TIMESTAMP,
view_date date NOT NULL DEFAULT CURRENT_DATE,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etown_townimages (
townimageid uuid NOT NULL,
townprofileid text NULL,
townimageurl text NULL,
userprofileid text NULL,
contactid text NULL,
createdon timestamp without time zone NULL,
verification text NULL DEFAULT 'pending'::text,
name text NULL,
caption text NULL,
    PRIMARY KEY (townimageid)
);
CREATE TABLE IF NOT EXISTS public.etown_townprofile (
townprofileid text NOT NULL,
townname text NULL,
townimage text NULL,
towndescription text NULL,
selfreferencingid text NULL,
townlatitude text NULL,
townlongitude text NULL,
townstatus text NULL,
createdon timestamp without time zone NULL,
apihit integer NULL DEFAULT 0,
cityname text NULL,
vieweduserprofileid character varying(255) NULL DEFAULT NULL::character varying,
content_vector vector NULL,
content_vector_hash text NULL,
townimage_compressed text NULL,
    PRIMARY KEY (townprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_townreview (
townreviewid uuid NOT NULL,
townprofileid text NOT NULL,
townname text NULL,
contactid text NOT NULL,
contactidname text NULL,
userprofileid text NOT NULL,
imageurl text NOT NULL,
description text NULL,
status text NOT NULL DEFAULT 'pending'::text,
createdon timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'utc'::text),
modifiedon timestamp without time zone NULL,
filename text NULL,
    PRIMARY KEY (townreviewid)
);
CREATE TABLE IF NOT EXISTS public.etown_tracking_seen (
hit_date date NOT NULL DEFAULT CURRENT_DATE,
metric text NOT NULL,
userprofileid text NOT NULL,
createdon timestamp without time zone NOT NULL DEFAULT now(),
contactid text NULL,
    PRIMARY KEY (hit_date, metric, userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etown_update (
updateid text NOT NULL,
townname text NOT NULL,
townprofileid text NOT NULL,
userprofileid text NOT NULL,
contactid text NOT NULL,
title text NOT NULL,
description text NOT NULL,
category text NULL,
latitude double precision NOT NULL,
longitude double precision NOT NULL,
location_name text NULL,
needed_at timestamp with time zone NULL,
attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
status text NOT NULL DEFAULT 'posted'::text,
deleted text NULL DEFAULT 'false'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
updated_at timestamp with time zone NOT NULL DEFAULT now(),
review text NULL,
view_count integer NOT NULL DEFAULT 0,
    CHECK ((jsonb_typeof(attachments) = 'array'::text)),
    CHECK (((latitude >= ('-90.0'::numeric)::double precision) AND (latitude <= (90.0)::double precision))),
    CHECK (((longitude >= ('-180.0'::numeric)::double precision) AND (longitude <= (180.0)::double precision))),
    PRIMARY KEY (updateid),
    CHECK ((status = ANY (ARRAY['draft'::text, 'posted'::text, 'closed'::text])))
);
CREATE TABLE IF NOT EXISTS public.etown_userlocationdetails (
userlocationdetailsid text NOT NULL,
additionaladdressinfo text NULL,
addresstype text NULL,
homeaddress text NULL,
workaddress text NULL,
landmark text NULL,
latitude double precision NULL,
longitude double precision NULL,
statecode text NULL,
city text NULL,
contactid text NULL,
contactidname text NULL,
createdon timestamp without time zone NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (userlocationdetailsid)
);
CREATE TABLE IF NOT EXISTS public.etown_userprofile (
userprofileid text NOT NULL,
name text NULL,
contactid text NULL,
contactidname text NULL,
email text NULL,
lastsignupstatus integer NULL,
lastsignupstatusname text NULL,
membersignup text NULL,
mobilenumber text NULL,
role integer NULL,
rolename text NULL,
createdon timestamp without time zone NULL,
verified boolean NULL,
modifieddate timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    PRIMARY KEY (userprofileid)
);
CREATE TABLE IF NOT EXISTS public.etowninfo (
id integer NOT NULL DEFAULT nextval('etowninfo_id_seq'::regclass),
prompt text NULL,
response text NULL,
content_vector vector NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etowntitlegenerationlogs (
id integer NOT NULL DEFAULT nextval('etowntitlegenerationlogs_id_seq'::regclass),
contact_id text NULL,
user_profile_id text NULL,
conversation_id text NULL,
input_text text NULL,
response text NULL,
model text NULL,
prompt_tokens integer NULL,
total_tokens integer NULL,
completion_tokens integer NULL,
date timestamp without time zone NULL,
last_message_time timestamp without time zone NULL,
deleted text NULL DEFAULT 'false'::text,
reactivation text NULL DEFAULT 'false'::text,
deactivated text NULL DEFAULT 'false'::text,
deletiondate timestamp without time zone NULL,
reactivationdate timestamp without time zone NULL,
deactivationdate timestamp without time zone NULL,
    UNIQUE (contact_id, user_profile_id, conversation_id),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS public.etownuserchaatiehistory (
id integer NOT NULL DEFAULT nextval('etownuserchaatiehistory_id_seq1'::regclass),
conversation_id character varying(255) NOT NULL,
user_id character varying(255) NOT NULL,
user_name character varying(255) NULL,
user_address character varying(500) NULL,
language character varying(50) NULL DEFAULT 'en'::character varying,
role character varying(20) NOT NULL,
content text NOT NULL,
tools_used jsonb NULL DEFAULT '[]'::jsonb,
metadata jsonb NULL DEFAULT '{}'::jsonb,
created_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
promptratings boolean NULL,
    PRIMARY KEY (id),
    CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying, 'system'::character varying])::text[])))
);
CREATE TABLE IF NOT EXISTS public.mem0migrations (
id uuid NOT NULL,
vector vector NULL,
payload jsonb NULL,
    PRIMARY KEY (id)
);

CREATE INDEX idx_etown_contact_not_deleted ON public.etown_contact USING btree (contactid) WHERE (deleted IS DISTINCT FROM 'true'::text);
CREATE INDEX idx_etown_userprofile_not_deleted ON public.etown_userprofile USING btree (contactid) WHERE (deleted IS DISTINCT FROM 'true'::text);
CREATE INDEX idx_etown_contact_not_deactivated ON public.etown_contact USING btree (contactid) WHERE (deactivated IS DISTINCT FROM 'true'::text);
CREATE INDEX idx_etown_userprofile_not_deactivated ON public.etown_userprofile USING btree (contactid) WHERE (deactivated IS DISTINCT FROM 'true'::text);
CREATE INDEX idx_etown_contact_retired ON public.etown_contact USING btree (contactid) WHERE ((deleted = 'true'::text) OR (deactivated = 'true'::text));
CREATE INDEX idx_etown_member_id_lower ON public.etown_member USING btree (lower(memberid));
CREATE INDEX idx_provider_businessname_normalized ON public.etown_provider USING btree (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(businessname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)));
CREATE INDEX idx_etown_provider_id_lower ON public.etown_provider USING btree (lower(providerid));
CREATE INDEX idx_pscopy_businessname_normalized ON public.etown_providerservices_copy USING btree (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(businessname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)));
CREATE INDEX idx_member_userprofileidname_normalized ON public.etown_member USING btree (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(userprofileidname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)));
CREATE INDEX idx_contact_firstname_normalized ON public.etown_contact USING btree (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(firstname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)));
CREATE INDEX idx_provider_name_trgm ON public.etown_provider USING gin (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(businessname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)) gin_trgm_ops);
CREATE INDEX idx_pscopy_name_trgm ON public.etown_providerservices_copy USING gin (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(businessname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)) gin_trgm_ops);
CREATE INDEX idx_etown_activeusers_mobilenumber ON public.etown_activeusers USING btree (mobilenumber);
CREATE UNIQUE INDEX idx_memory_decisions_turn ON public.etown_memory_decisions USING btree (turn_id);
CREATE INDEX idx_memory_decisions_user ON public.etown_memory_decisions USING btree (user_id, created_at DESC);
CREATE INDEX idx_memory_decisions_created ON public.etown_memory_decisions USING btree (created_at DESC);
CREATE UNIQUE INDEX uq_townprofile_name_lower ON public.etown_townprofile USING btree (lower(btrim(townname)));
CREATE INDEX idx_auth_challenge_identifier ON public.etown_auth_challenge USING btree (identifier, createdon DESC);
CREATE INDEX idx_auth_challenge_live ON public.etown_auth_challenge USING btree (expires_at) WHERE (consumed_at IS NULL);
CREATE INDEX idx_auth_challenge_secret ON public.etown_auth_challenge USING btree (secret_hash) WHERE (consumed_at IS NULL);
CREATE UNIQUE INDEX etown_auth_refresh_token_token_hash_key ON public.etown_auth_refresh_token USING btree (token_hash);
CREATE INDEX idx_auth_refresh_contact ON public.etown_auth_refresh_token USING btree (contactid, issued_at DESC);
CREATE INDEX idx_auth_refresh_live ON public.etown_auth_refresh_token USING btree (expires_at) WHERE (revoked_at IS NULL);
CREATE INDEX idx_logs_timestamp_ist ON public.etown_application_logs USING btree (timestamp_ist DESC);
CREATE INDEX idx_logs_log_level ON public.etown_application_logs USING btree (log_level);
CREATE INDEX idx_logs_userprofile_id ON public.etown_application_logs USING btree (userprofile_id);
CREATE INDEX idx_town_view_tracking_town_user_date ON public.etown_town_view_tracking USING btree (townprofileid, userprofileid, view_date);
CREATE INDEX idx_etown_activeusers_contactid ON public.etown_activeusers USING btree (contactid);
CREATE INDEX idx_pscopy_lat_lon ON public.etown_providerservices_copy USING btree (latitude, longitude) WHERE ((latitude IS NOT NULL) AND (longitude IS NOT NULL));
CREATE INDEX idx_provider_lat_lon ON public.etown_provider USING btree (latitude, longitude) WHERE ((latitude IS NOT NULL) AND (longitude IS NOT NULL));
CREATE INDEX idx_contact_mobile_last10 ON public.etown_contact USING btree ("right"(regexp_replace(COALESCE(mobilephone, ''::text), '\D'::text, ''::text, 'g'::text), 10)) WHERE (mobilephone IS NOT NULL);
CREATE INDEX idx_member_vector_ip ON public.etown_member USING hnsw (content_vector vector_ip_ops);
CREATE INDEX idx_townprofile_vector_ip ON public.etown_townprofile USING hnsw (content_vector vector_ip_ops);
CREATE INDEX idx_app_uninstalls_userprofile ON public.etown_app_uninstalls USING btree (userprofileid) WHERE (userprofileid IS NOT NULL);
CREATE INDEX idx_etown_contactdevices_probe ON public.etown_contactdevices USING btree (last_probed_at NULLS FIRST) WHERE ((device_token IS NOT NULL) AND (device_token <> ''::text));
CREATE UNIQUE INDEX uq_app_uninstalls_open_device ON public.etown_app_uninstalls USING btree (device_identity) WHERE (reinstalled_at IS NULL);
CREATE INDEX idx_app_uninstalls_contact ON public.etown_app_uninstalls USING btree (contactid, detected_at DESC) WHERE (contactid IS NOT NULL);
CREATE INDEX idx_app_uninstalls_open ON public.etown_app_uninstalls USING btree (detected_at DESC) WHERE (reinstalled_at IS NULL);
CREATE UNIQUE INDEX uq_provider_search_appearance ON public.etown_provider_search_appearances USING btree (providerid, contact_id, search_date, normalized_query);
CREATE INDEX idx_provider_search_appearance_provider ON public.etown_provider_search_appearances USING btree (providerid, search_date);
CREATE INDEX idx_provider_search_appearance_date ON public.etown_provider_search_appearances USING btree (search_date);
CREATE INDEX idx_contact_vector_ip ON public.etown_contact USING hnsw (content_vector vector_ip_ops);
CREATE INDEX idx_provider_views_source_created_on ON public.etown_provider_views USING btree (source, created_on);
CREATE UNIQUE INDEX uq_etownprovider_provider_town ON public.etown_etownprovider USING btree (providerid, townprofileid) WHERE (providerid IS NOT NULL);
CREATE INDEX ix_etownprovider_provider_primary ON public.etown_etownprovider USING btree (providerid, is_primary DESC);
CREATE INDEX idx_provider_alternatemobile ON public.etown_provider USING btree ("right"(regexp_replace(alternatemobile, '\D'::text, ''::text, 'g'::text), 10)) WHERE (alternatemobile IS NOT NULL);
CREATE INDEX idx_provider_review_pending_providerid ON public.etown_provider_review USING btree (providerid) WHERE ((providerid IS NOT NULL) AND (action_mode = 'create'::text) AND (lower(btrim(COALESCE(review, ''::text))) <> 'approved'::text));
CREATE INDEX idx_etown_allrequests_requestorid ON public.etown_allrequests USING btree (requestorid);
CREATE INDEX idx_etown_ai_onboarding_createdon ON public.etown_ai_onboarding USING btree (createdon DESC);
CREATE INDEX idx_etown_allrequests_requestoruserprofileid ON public.etown_allrequests USING btree (requestoruserprofileid);
CREATE INDEX idx_terminal_logs_timestamp_ist ON public.etown_terminal_logs USING btree (timestamp_ist DESC);
CREATE INDEX ix_provider_userprofile_plan ON public.etown_provider USING btree (userprofileid) INCLUDE (plan_code, subscription_status, plan_expires_on);
CREATE INDEX idx_logs_contact_id ON public.etown_application_logs USING btree (contact_id);
CREATE INDEX idx_logs_created_at ON public.etown_application_logs USING btree (created_at DESC);
CREATE INDEX idx_logs_endpoint_timestamp ON public.etown_application_logs USING btree (endpoint, timestamp_ist DESC);
CREATE INDEX idx_logs_request_id_correlation ON public.etown_application_logs USING btree (request_id);
CREATE UNIQUE INDEX etown_call_events_event_id_key ON public.etown_call_events USING btree (event_id);
CREATE UNIQUE INDEX etown_call_participants_participant_id_key ON public.etown_call_participants USING btree (participant_id);
CREATE INDEX idx_call_participants_status ON public.etown_call_participants USING btree (join_status);
CREATE INDEX idx_call_participants_user_session ON public.etown_call_participants USING btree (user_id, session_id);
CREATE UNIQUE INDEX etown_call_sessions_session_id_key ON public.etown_call_sessions USING btree (session_id);
CREATE INDEX idx_call_sessions_session_id ON public.etown_call_sessions USING btree (session_id);
CREATE INDEX idx_call_sessions_hms_room_id ON public.etown_call_sessions USING btree (hms_room_id);
CREATE INDEX idx_member_contactid_plan ON public.etown_member USING btree (contactid) WHERE (plan_code <> 'free'::text);
CREATE INDEX idx_call_sessions_initiator ON public.etown_call_sessions USING btree (initiator_user_id);
CREATE INDEX idx_call_sessions_target ON public.etown_call_sessions USING btree (target_user_id);
CREATE UNIQUE INDEX etown_staff_numbers_mobilenumber_role_key ON public.etown_staff_numbers USING btree (mobilenumber, role);
CREATE INDEX idx_call_sessions_status ON public.etown_call_sessions USING btree (call_status);
CREATE INDEX idx_call_sessions_created_at ON public.etown_call_sessions USING btree (created_at DESC);
CREATE UNIQUE INDEX etown_communitylike_postid_userprofileid_key ON public.etown_communitylike USING btree (postid, userprofileid);
CREATE INDEX idx_call_sessions_user_calls ON public.etown_call_sessions USING btree (initiator_user_id, created_at DESC);
CREATE INDEX idx_call_sessions_active ON public.etown_call_sessions USING btree (call_status, created_at) WHERE ((call_status)::text = ANY ((ARRAY['created'::character varying, 'ringing'::character varying, 'active'::character varying])::text[]));
CREATE UNIQUE INDEX etown_call_tokens_token_id_key ON public.etown_call_tokens USING btree (token_id);
CREATE INDEX ix_communitylike_post ON public.etown_communitylike USING btree (postid, updated_at DESC) WHERE (likemode = 1);
CREATE INDEX ix_communitycomment_post ON public.etown_communitycomment USING btree (postid, created_at DESC) WHERE (deleted = 'false'::text);
CREATE INDEX idx_call_tokens_token_id ON public.etown_call_tokens USING btree (token_id);
CREATE INDEX idx_call_tokens_session_id ON public.etown_call_tokens USING btree (session_id);
CREATE INDEX idx_call_tokens_user_id ON public.etown_call_tokens USING btree (user_id);
CREATE INDEX idx_call_tokens_expires_at ON public.etown_call_tokens USING btree (expires_at);
CREATE INDEX idx_call_tokens_active ON public.etown_call_tokens USING btree (session_id, user_id, is_used) WHERE (is_used = false);
CREATE UNIQUE INDEX etown_popular_category_pin_user_location_category_key ON public.etown_popular_category_pin USING btree (userprofileid, latitude, longitude, categoryid);
CREATE INDEX etown_popular_category_pin_pinned_idx ON public.etown_popular_category_pin USING btree (userprofileid, latitude, longitude) WHERE ispinned;
CREATE INDEX idx_etown_chathistory_contactfrom ON public.etown_chathistory USING btree (contactfrom);
CREATE INDEX idx_etown_chathistory_contactto ON public.etown_chathistory USING btree (contactto);
CREATE INDEX idx_etown_chathistory_userprofileid ON public.etown_chathistory USING btree (userprofileid);
CREATE INDEX idx_etown_chathistory_memberid ON public.etown_chathistory USING btree (memberid);
CREATE INDEX idx_etown_chathistory_providerid ON public.etown_chathistory USING btree (providerid);
CREATE INDEX idx_etown_staff_numbers_active_mobile ON public.etown_staff_numbers USING btree (mobilenumber) WHERE is_active;
CREATE INDEX idx_call_events_event_id ON public.etown_call_events USING btree (event_id);
CREATE INDEX idx_call_events_session_id ON public.etown_call_events USING btree (session_id);
CREATE INDEX idx_call_events_user_id ON public.etown_call_events USING btree (user_id);
CREATE INDEX idx_call_events_type ON public.etown_call_events USING btree (event_type);
CREATE INDEX idx_call_events_timestamp ON public.etown_call_events USING btree (timestamp_utc DESC);
CREATE INDEX idx_call_events_session_timeline ON public.etown_call_events USING btree (session_id, timestamp_utc DESC);
CREATE INDEX idx_call_participants_participant_id ON public.etown_call_participants USING btree (participant_id);
CREATE INDEX idx_call_participants_session_id ON public.etown_call_participants USING btree (session_id);
CREATE INDEX idx_pscopy_fts ON public.etown_providerservices_copy USING gin (to_tsvector('english'::regconfig, ((((((COALESCE(businessname, ''::text) || ' '::text) || COALESCE(services, ''::text)) || ' '::text) || COALESCE(areaname, ''::text)) || ' '::text) || COALESCE(city, ''::text))));
CREATE INDEX idx_provider_fts ON public.etown_provider USING gin (to_tsvector('english'::regconfig, ((((COALESCE(businessname, ''::text) || ' '::text) || COALESCE(areaname, ''::text)) || ' '::text) || COALESCE(city, ''::text))));
CREATE UNIQUE INDEX ux_contact_alternatemobile_live ON public.etown_contact USING btree ("right"(regexp_replace(alternatemobile, '\D'::text, ''::text, 'g'::text), 10)) WHERE ((alternatemobile IS NOT NULL) AND (deleted IS DISTINCT FROM 'true'::text));
CREATE INDEX idx_call_participants_user_id ON public.etown_call_participants USING btree (user_id);
CREATE INDEX idx_etown_chatmessages_contactid ON public.etown_chatmessages USING btree (contactid);
CREATE INDEX idx_etown_chatmessages_userprofileid ON public.etown_chatmessages USING btree (userprofileid);
CREATE INDEX idx_etown_chatmessages_memberid ON public.etown_chatmessages USING btree (memberid);
CREATE INDEX idx_etown_chatmessages_providerid ON public.etown_chatmessages USING btree (providerid);
CREATE INDEX ix_needresponse_need ON public.etown_needresponse USING btree (needid, created_at DESC);
CREATE INDEX ix_needresponse_userprofile ON public.etown_needresponse USING btree (userprofileid, created_at DESC);
CREATE UNIQUE INDEX etown_provider_views_anonymous_viewer_key ON public.etown_provider_views USING btree (providerprofileid) WHERE (userprofileid IS NULL);
CREATE INDEX idx_collectionitems_collection ON public.etown_collectionitems USING btree (collectionid);
CREATE INDEX idx_etown_collectionitems_usercontactid ON public.etown_collectionitems USING btree (usercontactid);
CREATE INDEX idx_etown_collectionitems_useruserprofileid ON public.etown_collectionitems USING btree (useruserprofileid);
CREATE INDEX idx_etown_collectionitems_providerid ON public.etown_collectionitems USING btree (providerid);
CREATE INDEX idx_etown_ai_onboarding_userprofile ON public.etown_ai_onboarding USING btree (userprofileid, createdon DESC);
CREATE INDEX idx_etown_ai_onboarding_bubble_conversation ON public.etown_ai_onboarding_bubble USING btree (conversationid, createdon DESC);
CREATE INDEX idx_etown_ai_onboarding_conversation ON public.etown_ai_onboarding USING btree (conversationid, createdon DESC);
CREATE INDEX idx_etown_ai_onboarding_bubble_userprofile ON public.etown_ai_onboarding_bubble USING btree (userprofileid, createdon DESC);
CREATE INDEX idx_collections_user ON public.etown_collections USING btree (usercontactid, useruserprofileid);
CREATE INDEX idx_etown_collections_useruserprofileid ON public.etown_collections USING btree (useruserprofileid);
CREATE INDEX idx_etown_connectycubedetails_contactid ON public.etown_connectycubedetails USING btree (contactid);
CREATE INDEX idx_etown_connectycubedetails_userprofileid ON public.etown_connectycubedetails USING btree (userprofileid);
CREATE INDEX idx_etown_ai_onboarding_turnid ON public.etown_ai_onboarding USING btree (turnid);
CREATE INDEX idx_etown_townreview_townprofileid_status ON public.etown_townreview USING btree (townprofileid, status);
CREATE INDEX idx_etown_townreview_contactid ON public.etown_townreview USING btree (contactid);
CREATE INDEX idx_etown_connectycubedetails_memberprofile ON public.etown_connectycubedetails USING btree (memberprofile);
CREATE INDEX idx_etown_connectycubedetails_providerprofile ON public.etown_connectycubedetails USING btree (providerprofile);
CREATE INDEX idx_etown_contact_contactid_lower ON public.etown_contact USING btree (lower(TRIM(BOTH FROM contactid)));
CREATE INDEX idx_contact_id_lower ON public.etown_contact USING btree (lower(contactid));
CREATE INDEX idx_contact_createdon ON public.etown_contact USING btree (createdon) WHERE (firstname IS NOT NULL);
CREATE INDEX idx_contact_lat_lon ON public.etown_contact USING btree (latitude, longitude) WHERE ((latitude IS NOT NULL) AND (longitude IS NOT NULL));
CREATE INDEX idx_contact_city_lower ON public.etown_contact USING btree (lower(TRIM(BOTH FROM city))) WHERE (city IS NOT NULL);
CREATE INDEX idx_etown_contact_phone_normalized ON public.etown_contact USING btree (regexp_replace(COALESCE(mobilephone, ''::text), '\D'::text, ''::text, 'g'::text)) WHERE (mobilephone IS NOT NULL);
CREATE INDEX idx_etown_contact_mobilephone_lookup ON public.etown_contact USING btree (mobilephone) WHERE (mobilephone IS NOT NULL);
CREATE INDEX idx_etown_contact_email_lookup ON public.etown_contact USING btree (email) WHERE (email IS NOT NULL);
CREATE INDEX idx_contact_mobilephone ON public.etown_contact USING btree (mobilephone);
CREATE INDEX idx_contact_email ON public.etown_contact USING btree (email);
CREATE INDEX idx_contact_email_lower ON public.etown_contact USING btree (lower(email));
CREATE INDEX idx_contact_firstname_lower ON public.etown_contact USING btree (lower(firstname));
CREATE INDEX idx_etown_townreview_pending_filename ON public.etown_townreview USING btree (townprofileid, contactid, userprofileid, filename) WHERE (status = 'pending'::text);
CREATE INDEX idx_etown_contactdevices_device_token ON public.etown_contactdevices USING btree (device_token) WHERE (device_token IS NOT NULL);
CREATE INDEX idx_etown_contactdevices_userprofile_token ON public.etown_contactdevices USING btree (userprofileid, device_token) WHERE (device_token IS NOT NULL);
CREATE UNIQUE INDEX uq_contactdevices_deviceguid ON public.etown_contactdevices USING btree (deviceguid) WHERE ((deviceguid IS NOT NULL) AND (deviceguid <> ''::text));
CREATE UNIQUE INDEX uq_contactdevices_device_id ON public.etown_contactdevices USING btree (device_id) WHERE ((device_id IS NOT NULL) AND (device_id <> ''::text));
CREATE INDEX idx_etown_contactdevices_userprofileid ON public.etown_contactdevices USING btree (userprofileid) WHERE (userprofileid IS NOT NULL);
CREATE INDEX idx_etown_contactdevices_contactid ON public.etown_contactdevices USING btree (contactid) WHERE (contactid IS NOT NULL);
CREATE INDEX idx_etown_contactdevices_voip_token ON public.etown_contactdevices USING btree (voip_device_token) WHERE (voip_device_token IS NOT NULL);
CREATE UNIQUE INDEX idx_etown_conversations_unique_pair ON public.etown_conversations USING btree (LEAST(lower(participant_1_id), lower(participant_2_id)), GREATEST(lower(participant_1_id), lower(participant_2_id)), LEAST(participant_1_type, participant_2_type), GREATEST(participant_1_type, participant_2_type));
CREATE UNIQUE INDEX idx_search_history_contact_query ON public.etown_search_history USING btree (contact_id, lower(query));
CREATE INDEX idx_etown_conversations_participant_1 ON public.etown_conversations USING btree (lower(participant_1_id), participant_1_type, updated_at DESC);
CREATE INDEX idx_etown_conversations_participant_2 ON public.etown_conversations USING btree (lower(participant_2_id), participant_2_type, updated_at DESC);
CREATE INDEX idx_etown_conversations_participant_1_id ON public.etown_conversations USING btree (participant_1_id);
CREATE INDEX idx_etown_conversations_participant_2_id ON public.etown_conversations USING btree (participant_2_id);
CREATE INDEX idx_etownprovider_townprofileid_providerid ON public.etown_etownprovider USING btree (townprofileid, providerid);
CREATE INDEX idx_etownprovider_providerid_townprofileid ON public.etown_etownprovider USING btree (providerid, townprofileid);
CREATE UNIQUE INDEX etown_facebook_posts_feed_id_key ON public.etown_facebook_posts USING btree (feed_id);
CREATE INDEX idx_etown_facebook_posts_contactid ON public.etown_facebook_posts USING btree (contactid);
CREATE INDEX idx_etown_facebook_posts_feed_id ON public.etown_facebook_posts USING btree (feed_id);
CREATE INDEX idx_etown_facebook_posts_userprofileid ON public.etown_facebook_posts USING btree (userprofileid);
CREATE UNIQUE INDEX etown_facebookconnections_contactid_userprofileid_fb_page_i_key ON public.etown_facebookconnections USING btree (contactid, userprofileid, fb_page_id);
CREATE UNIQUE INDEX etown_facebookconnections_contactid_userprofileid_page_key ON public.etown_facebookconnections USING btree (contactid, userprofileid, fb_page_id);
CREATE UNIQUE INDEX uq_support_ticket_reference ON public.etown_support_ticket USING btree (reference);
CREATE INDEX ix_support_ticket_contact ON public.etown_support_ticket USING btree (contactid, created_at DESC) WHERE (COALESCE(deleted, 'false'::text) <> 'true'::text);
CREATE INDEX ix_support_ticket_userprofile ON public.etown_support_ticket USING btree (userprofileid, created_at DESC) WHERE (COALESCE(deleted, 'false'::text) <> 'true'::text);
CREATE INDEX ix_support_ticket_open_due ON public.etown_support_ticket USING btree (due_at) WHERE (status = ANY (ARRAY['open'::text, 'in_progress'::text]));
CREATE INDEX idx_etown_facebookconnections_contactid ON public.etown_facebookconnections USING btree (contactid);
CREATE INDEX idx_etown_facebookconnections_userprofileid ON public.etown_facebookconnections USING btree (userprofileid);
CREATE INDEX idx_etown_facebookconnections_active ON public.etown_facebookconnections USING btree (contactid, is_active) WHERE (is_active = true);
CREATE INDEX idx_favouritetown_user ON public.etown_favouritetown USING btree (usercontactid, useruserprofileid) WHERE (isfavourite = 'Favourite'::text);
CREATE UNIQUE INDEX ux_communityview_public_daily ON public.etown_communityview USING btree (postid, userprofileid, view_date) WHERE (view_type = 'public'::text);
CREATE INDEX ix_communityview_post ON public.etown_communityview USING btree (postid, created_at DESC);
CREATE INDEX ix_communityview_created_at ON public.etown_communityview USING btree (created_at);
CREATE INDEX idx_etown_favouritetown_useruserprofileid ON public.etown_favouritetown USING btree (useruserprofileid);
CREATE UNIQUE INDEX uq_etown_provider_geoapify_providerid ON public.etown_provider_geoapify USING btree (providerid);
CREATE UNIQUE INDEX etown_provider_geoapify_providerid_key ON public.etown_provider_geoapify USING btree (providerid);
CREATE INDEX idx_classification_decisions_conv ON public.etown_classification_decisions USING btree (conversation_id, created_at);
CREATE INDEX idx_classification_decisions_intent ON public.etown_classification_decisions USING btree (predicted_intent, action, created_at);
CREATE UNIQUE INDEX etown_category_pin_user_place_category_key ON public.etown_category_pin USING btree (userprofileid, cityname, areaname, categoryid);
CREATE INDEX etown_category_pin_pinned_idx ON public.etown_category_pin USING btree (userprofileid, cityname, areaname) WHERE ispinned;
CREATE UNIQUE INDEX etown_category_categoryname_key ON public.etown_category USING btree (categoryname);
CREATE INDEX idx_etown_feed_createdon ON public.etown_feed USING btree (createdon DESC);
CREATE INDEX idx_etown_feed_feedid ON public.etown_feed USING btree (feedid);
CREATE INDEX idx_etown_feed_contactid ON public.etown_feed USING btree (contactid);
CREATE INDEX idx_etown_feed_userprofileid ON public.etown_feed USING btree (userprofileid);
CREATE INDEX idx_classification_decisions_reason ON public.etown_classification_decisions USING btree (decision_reason, created_at);
CREATE UNIQUE INDEX uq_etownprovider_one_primary ON public.etown_etownprovider USING btree (providerid) WHERE (is_primary AND (providerid IS NOT NULL));
CREATE INDEX idx_etown_feedcomments_feedid ON public.etown_feedcomments USING btree (feedid);
CREATE INDEX idx_etown_feedcomments_commentedbycontactid ON public.etown_feedcomments USING btree (commentedbycontactid);
CREATE INDEX idx_etown_feedcomments_commentedbyuserprofileid ON public.etown_feedcomments USING btree (commentedbyuserprofileid);
CREATE INDEX idx_etown_feedlikes_feedid ON public.etown_feedlikes USING btree (feedid);
CREATE INDEX idx_etown_feedlikes_user ON public.etown_feedlikes USING btree (likesbyuserprofileid);
CREATE INDEX idx_etown_feedlikes_user_feed ON public.etown_feedlikes USING btree (likesbyuserprofileid, feedid);
CREATE INDEX ix_provider_seo_sync_queue_due ON public.etown_provider_seo_sync_queue USING btree (next_attempt_at);
CREATE INDEX idx_etown_requestservicelists_bookingid ON public.etown_requestservicelists USING btree (bookingid);
CREATE INDEX idx_etown_feedlikes_likedbycontactid ON public.etown_feedlikes USING btree (likedbycontactid);
CREATE INDEX idx_etown_franchise_phone ON public.etown_franchise USING btree (phone);
CREATE INDEX idx_etown_franchise_email ON public.etown_franchise USING btree (email);
CREATE INDEX idx_etown_groupitems_user_contactid ON public.etown_groupitems USING btree (user_contactid);
CREATE INDEX idx_etown_groupitems_userprofile_id ON public.etown_groupitems USING btree (userprofile_id);
CREATE INDEX idx_etown_groupitems_user_userprofileid ON public.etown_groupitems USING btree (user_userprofileid);
CREATE INDEX idx_etown_groups_user_contactid ON public.etown_groups USING btree (user_contactid);
CREATE INDEX idx_etown_groups_user_userprofileid ON public.etown_groups USING btree (user_userprofileid);
CREATE UNIQUE INDEX etown_instagram_posts_feed_id_key ON public.etown_instagram_posts USING btree (feed_id);
CREATE INDEX idx_etown_instagram_posts_contactid ON public.etown_instagram_posts USING btree (contactid);
CREATE INDEX idx_etown_instagram_posts_feed_id ON public.etown_instagram_posts USING btree (feed_id);
CREATE INDEX idx_etown_instagram_posts_userprofileid ON public.etown_instagram_posts USING btree (userprofileid);
CREATE INDEX idx_etownprovider_townname_trim_lower ON public.etown_etownprovider USING btree (TRIM(BOTH FROM lower(townname))) WHERE (townname IS NOT NULL);
CREATE INDEX idx_favouritetown_townname_favourite ON public.etown_favouritetown USING btree (townname) WHERE (isfavourite = 'Favourite'::text);
CREATE INDEX idx_search_history_contact_created ON public.etown_search_history USING btree (contact_id, created_at DESC);
CREATE INDEX idx_etown_provider_marketing_mobilenumber_creationdate ON public.etown_provider USING btree (marketing_mobilenumber, creationdate) WHERE (marketing_mobilenumber IS NOT NULL);
CREATE UNIQUE INDEX etown_instagramconnections_contactid_userprofileid_ig_key ON public.etown_instagramconnections USING btree (contactid, userprofileid, instagram_business_id);
CREATE INDEX idx_etown_instagramconnections_contactid ON public.etown_instagramconnections USING btree (contactid);
CREATE INDEX idx_etown_instagramconnections_userprofileid ON public.etown_instagramconnections USING btree (userprofileid);
CREATE INDEX idx_etown_userprofile_mobile_last10 ON public.etown_userprofile USING btree ("right"(regexp_replace(COALESCE(mobilenumber, ''::text), '\D'::text, ''::text, 'g'::text), 10)) WHERE (mobilenumber IS NOT NULL);
CREATE INDEX idx_etown_member_mobile_last10 ON public.etown_member USING btree ("right"(regexp_replace(COALESCE(mobilenumber, ''::text), '\D'::text, ''::text, 'g'::text), 10)) WHERE (mobilenumber IS NOT NULL);
CREATE INDEX idx_etown_instagramconnections_active ON public.etown_instagramconnections USING btree (contactid, is_active) WHERE (is_active = true);
CREATE INDEX idx_member_name_trgm ON public.etown_member USING gin (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(userprofileidname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)) gin_trgm_ops);
CREATE INDEX idx_contact_firstname_trgm ON public.etown_contact USING gin (TRIM(BOTH FROM regexp_replace(regexp_replace(lower(firstname), '[!-/:-@[-^`{-~]'::text, ' '::text, 'g'::text), '\s+'::text, ' '::text, 'g'::text)) gin_trgm_ops);
CREATE INDEX idx_member_fts ON public.etown_member USING gin (to_tsvector('english'::regconfig, COALESCE(userprofileidname, ''::text)));
CREATE INDEX idx_contact_fts ON public.etown_contact USING gin (to_tsvector('english'::regconfig, ((COALESCE(firstname, ''::text) || ' '::text) || COALESCE(city, ''::text))));
CREATE INDEX idx_etown_mem0_memories_payload_user ON public.etown_mem0_memories USING btree (((payload ->> 'user_id'::text)));
CREATE INDEX idx_intent_examples_hnsw ON public.etown_intent_examples USING hnsw (content_vector vector_ip_ops);
CREATE INDEX idx_intent_examples_scope ON public.etown_intent_examples USING btree (agent_scope, is_active);
CREATE INDEX fki_invitercontact ON public.etown_invitation USING btree (invitercontact);
CREATE INDEX fki_invitecontact ON public.etown_invitation USING btree (invitecontact);
CREATE INDEX fki_inviteruserprofile ON public.etown_invitation USING btree (inviteruserprofile);
CREATE INDEX fki_inviteuserprofile ON public.etown_invitation USING btree (inviteuserprofile);
CREATE INDEX fki_invitercontactfkey ON public.etown_invitation USING btree (invitercontact);
CREATE INDEX fki_invitecontactfkey ON public.etown_invitation USING btree (invitecontact);
CREATE INDEX fki_inviteruserprofilefkey ON public.etown_invitation USING btree (inviteruserprofile);
CREATE INDEX fki_inviteuserprofilefkey ON public.etown_invitation USING btree (inviteuserprofile);
CREATE INDEX idx_etown_invitation_inviter_code ON public.etown_invitation USING btree (inviteruserprofile, invitationcode);
CREATE INDEX idx_etown_invitation_invitee_code ON public.etown_invitation USING btree (inviteuserprofile, invitationcode);
CREATE INDEX idx_invitation_inviter_invitee ON public.etown_invitation USING btree (inviteruserprofile, inviteuserprofile, invitationcode) WHERE (invitationcode = 'Requested'::text);
CREATE INDEX idx_invitation_bidirectional ON public.etown_invitation USING btree (inviteruserprofile, inviteuserprofile) WHERE (invitationcode IS NOT NULL);
CREATE INDEX idx_invitation_contacts ON public.etown_invitation USING btree (invitercontact, invitecontact, invitationcode);
CREATE INDEX idx_invitation_active_contacts ON public.etown_invitation USING btree (invitercontact, invitecontact) WHERE (invitationcode <> ALL (ARRAY['Declined'::text, 'Cancelled'::text, 'Error'::text]));
CREATE INDEX idx_invitation_inviter ON public.etown_invitation USING btree (inviteruserprofile);
CREATE INDEX idx_invitation_invitee ON public.etown_invitation USING btree (inviteuserprofile);
CREATE INDEX etown_mem0_memories_hnsw_idx ON public.etown_mem0_memories USING hnsw (vector vector_cosine_ops);
CREATE INDEX idx_etown_mem0_memories_userprofileid ON public.etown_mem0_memories USING btree (userprofileid);
CREATE INDEX fki_userprofileid ON public.etown_member USING btree (userprofileid);
CREATE INDEX fki_membertocontact ON public.etown_member USING btree (contactid);
CREATE INDEX fki_membertouserprofile ON public.etown_member USING btree (userprofileid);
CREATE INDEX fki_f ON public.etown_member USING btree (userprofileid);
CREATE INDEX fki_membercontactfkey ON public.etown_member USING btree (contactid);
CREATE INDEX fki_memberuserprofilefkey ON public.etown_member USING btree (userprofileid);
CREATE INDEX "fki_F" ON public.etown_memberrelation USING btree (from_contactid);
CREATE INDEX fki_to_contactid ON public.etown_memberrelation USING btree (to_contactid);
CREATE INDEX fki_from_userprofile ON public.etown_memberrelation USING btree (from_userprofile);
CREATE INDEX fki_to_userprofile ON public.etown_memberrelation USING btree (to_userprofile);
CREATE INDEX fki_fromcontactmemberrelation ON public.etown_memberrelation USING btree (from_contactid);
CREATE INDEX fki_tocontactmemberrelation ON public.etown_memberrelation USING btree (to_contactid);
CREATE INDEX fki_fromuserprofilememberrelation ON public.etown_memberrelation USING btree (from_userprofile);
CREATE INDEX fki_touserprofilememberrelation ON public.etown_memberrelation USING btree (to_userprofile);
CREATE INDEX fki_fromcontactfkey ON public.etown_memberrelation USING btree (from_contactid);
CREATE INDEX fki_tocontactfkey ON public.etown_memberrelation USING btree (to_contactid);
CREATE INDEX fki_fromuserprofilefkey ON public.etown_memberrelation USING btree (from_userprofile);
CREATE INDEX fki_touserprofilefkey ON public.etown_memberrelation USING btree (to_userprofile);
CREATE INDEX idx_etown_memberrelation_from_contact ON public.etown_memberrelation USING btree (from_contactid);
CREATE INDEX idx_etown_memberrelation_to_contact ON public.etown_memberrelation USING btree (to_contactid);
CREATE INDEX idx_memberrelation_from_contact_user ON public.etown_memberrelation USING btree (from_contactid, from_userprofile);
CREATE INDEX idx_memberrelation_to_contact_user ON public.etown_memberrelation USING btree (to_contactid, to_userprofile);
CREATE INDEX idx_memberrelation_bidirectional ON public.etown_memberrelation USING btree (from_contactid, to_contactid, from_userprofile, to_userprofile);
CREATE INDEX idx_memberrelation_userprofiles ON public.etown_memberrelation USING btree (from_userprofile, to_userprofile) WHERE ((from_userprofile IS NOT NULL) AND (to_userprofile IS NOT NULL));
CREATE INDEX idx_memberrelation_active ON public.etown_memberrelation USING btree (from_contactid, to_contactid) WHERE (relationshiptype IS NOT NULL);
CREATE INDEX idx_memberrelation_exclude_self ON public.etown_memberrelation USING btree (from_contactid, to_contactid) WHERE (from_contactid <> to_contactid);
CREATE INDEX idx_member_relation_from ON public.etown_memberrelation USING btree (from_userprofile);
CREATE INDEX idx_member_relation_to ON public.etown_memberrelation USING btree (to_userprofile);
CREATE INDEX idx_member_relation_from_contact ON public.etown_memberrelation USING btree (from_contactid);
CREATE INDEX idx_member_relation_to_contact ON public.etown_memberrelation USING btree (to_contactid);
CREATE INDEX idx_etown_memberrelation_from_memberid ON public.etown_memberrelation USING btree (from_memberid);
CREATE INDEX ix_provider_lifecycle_flagged ON public.etown_provider USING btree (contactid) INCLUDE (userprofileid, providerid) WHERE ((deleted = 'true'::text) OR (deactivated = 'true'::text));
CREATE INDEX idx_etown_messages_conversation_created ON public.etown_messages USING btree (conversation_id, created_at DESC, id DESC);
CREATE INDEX idx_etown_messages_unread_receiver ON public.etown_messages USING btree (conversation_id, lower(receiver_id), is_read) WHERE (is_read = false);
CREATE INDEX idx_etown_messages_not_deleted ON public.etown_messages USING btree (conversation_id, created_at) WHERE (is_deleted = false);
CREATE INDEX idx_etown_messages_sender_id ON public.etown_messages USING btree (sender_id);
CREATE INDEX idx_etown_messages_receiver_id ON public.etown_messages USING btree (receiver_id);
CREATE INDEX idx_etown_messages_undelivered_receiver ON public.etown_messages USING btree (conversation_id, lower(receiver_id)) WHERE (delivered_at IS NULL);
CREATE INDEX idx_etown_messages_receiver_deleted_at ON public.etown_messages USING btree (receiver_id, receiver_deleted_at);
CREATE UNIQUE INDEX etown_metadata_city_city_key ON public.etown_metadata_city USING btree (city);
CREATE INDEX idx_city_trgm ON public.etown_metadata_city USING gin (city gin_trgm_ops);
CREATE UNIQUE INDEX etown_metadata_term_term_key ON public.etown_metadata_term USING btree (term);
CREATE INDEX idx_term_trgm ON public.etown_metadata_term USING gin (term gin_trgm_ops);
CREATE INDEX idx_etown_mobileverification_mobilenumber ON public.etown_mobileverification USING btree (mobilenumber);
CREATE INDEX idx_notification_pool_type_active ON public.etown_notification_pool USING btree (type_key, is_active);
CREATE INDEX idx_etown_notifications_sendercontactid ON public.etown_notifications USING btree (sendercontactid);
CREATE INDEX idx_etown_notifications_receivercontactid ON public.etown_notifications USING btree (receivercontactid);
CREATE INDEX idx_etown_notifications_senderuserprofileid ON public.etown_notifications USING btree (senderuserprofileid);
CREATE INDEX idx_etown_notifications_receiveruserprofileid ON public.etown_notifications USING btree (receiveruserprofileid);
CREATE UNIQUE INDEX etown_offer_views_offersid_userprofileid_key ON public.etown_offer_views USING btree (offersid, userprofileid);
CREATE INDEX idx_etown_offer_views_userprofileid ON public.etown_offer_views USING btree (userprofileid);
CREATE UNIQUE INDEX etown_offerings_userprofileid_key ON public.etown_offerings USING btree (userprofileid);
CREATE UNIQUE INDEX uq_conv_jobs_batch_user ON public.etown_offerings_conversion_jobs USING btree (batchid, userprofileid);
CREATE INDEX idx_conv_jobs_batchid ON public.etown_offerings_conversion_jobs USING btree (batchid);
CREATE INDEX idx_conv_jobs_status ON public.etown_offerings_conversion_jobs USING btree (status);
CREATE INDEX idx_conv_jobs_user ON public.etown_offerings_conversion_jobs USING btree (userprofileid);
CREATE INDEX idx_etown_offers_contactid ON public.etown_offers USING btree (contactid);
CREATE INDEX idx_etown_offers_userprofileid ON public.etown_offers USING btree (userprofileid);
CREATE INDEX idx_etown_offers_vieweduserprofileid ON public.etown_offers USING btree (vieweduserprofileid);
CREATE INDEX fki_providercontact ON public.etown_provider USING btree (contactid);
CREATE INDEX fki_provideruserprofile ON public.etown_provider USING btree (userprofileid);
CREATE INDEX fki_provideruserprofilefkey ON public.etown_provider USING btree (userprofileid);
CREATE INDEX fki_providercontactidfkey ON public.etown_provider USING btree (contactid);
CREATE INDEX fki_provideruserprofileidfkey ON public.etown_provider USING btree (userprofileid);
CREATE INDEX idx_etown_provider_contactid_status ON public.etown_provider USING btree (contactid, publicsearchprovider);
CREATE INDEX idx_etown_provider_userprofileid_lower ON public.etown_provider USING btree (lower(TRIM(BOTH FROM userprofileid)));
CREATE INDEX idx_provider_publicsearch_business ON public.etown_provider USING btree (contactid, publicsearchprovider, businessname) WHERE (publicsearchprovider IS NOT NULL);
CREATE INDEX idx_provider_userprofileid ON public.etown_provider USING btree (lower(userprofileid), publicsearchprovider);
CREATE INDEX idx_provider_city_lower ON public.etown_provider USING btree (lower(TRIM(BOTH FROM city))) WHERE (city IS NOT NULL);
CREATE INDEX idx_provider_areaname_lower ON public.etown_provider USING btree (lower(TRIM(BOTH FROM areaname))) WHERE (areaname IS NOT NULL);
CREATE INDEX idx_provider_type ON public.etown_provider USING btree (providertype);
CREATE INDEX idx_provider_location ON public.etown_provider USING btree (latitude, longitude) WHERE ((latitude IS NOT NULL) AND (longitude IS NOT NULL));
CREATE INDEX idx_provider_userprofile_contact ON public.etown_provider USING btree (userprofileid, contactid, latitude, longitude);
CREATE INDEX idx_etown_provider_mobile_last10 ON public.etown_provider USING btree ("right"(regexp_replace(mobilenumber, '[^0-9]'::text, ''::text, 'g'::text), 10));
CREATE INDEX idx_provider_details_placeid ON public.etown_provider_details USING btree (place_id);
CREATE UNIQUE INDEX etown_provider_views_providerprofileid_userprofileid_key ON public.etown_provider_views USING btree (providerprofileid, userprofileid);
CREATE UNIQUE INDEX unique_provider_user_view ON public.etown_provider_views USING btree (providerprofileid, userprofileid);
CREATE INDEX idx_provider_views_providerprofileid ON public.etown_provider_views USING btree (providerprofileid);
CREATE INDEX idx_provider_views_userprofileid ON public.etown_provider_views USING btree (userprofileid);
CREATE INDEX fki_providerrelationfromcontact ON public.etown_providerrelation USING btree (fromcontactid);
CREATE INDEX fki_providerrelationtocontact ON public.etown_providerrelation USING btree (tocontactid);
CREATE INDEX fki_providerrelationtouserprofile ON public.etown_providerrelation USING btree (touserprofileid);
CREATE INDEX "fki_ providerrelationfromprovider" ON public.etown_providerrelation USING btree (fromproviderid);
CREATE INDEX fki_providerfromcontactfkey ON public.etown_providerrelation USING btree (fromcontactid);
CREATE INDEX fki_providertocontactfkey ON public.etown_providerrelation USING btree (tocontactid);
CREATE INDEX fki_providerfromuserprofilefkey ON public.etown_providerrelation USING btree (fromuserprofileid);
CREATE INDEX fki_providertouserprofilefkey ON public.etown_providerrelation USING btree (touserprofileid);
CREATE INDEX fki_p ON public.etown_providerrelation USING btree (fromproviderid);
CREATE INDEX fki_fromcontactidfkey ON public.etown_providerrelation USING btree (fromcontactid);
CREATE INDEX fki_fromuserprofileidfkey ON public.etown_providerrelation USING btree (fromuserprofileid);
CREATE INDEX fki_tocontactidfkey ON public.etown_providerrelation USING btree (tocontactid);
CREATE INDEX fki_touserprofileidfkey ON public.etown_providerrelation USING btree (touserprofileid);
CREATE INDEX fki_fromprovideridfkey ON public.etown_providerrelation USING btree (fromproviderid);
CREATE INDEX idx_etown_providerrelation_from ON public.etown_providerrelation USING btree (fromcontactid, fromuserprofileid);
CREATE INDEX idx_etown_providerrelation_to ON public.etown_providerrelation USING btree (tocontactid, touserprofileid);
CREATE INDEX idx_providerrelation_from_contact_user ON public.etown_providerrelation USING btree (fromcontactid, fromuserprofileid);
CREATE INDEX idx_providerrelation_to_contact_user ON public.etown_providerrelation USING btree (tocontactid, touserprofileid);
CREATE INDEX idx_providerrelation_bidirectional ON public.etown_providerrelation USING btree (fromcontactid, tocontactid, fromuserprofileid, touserprofileid);
CREATE INDEX idx_providerrelation_userprofiles ON public.etown_providerrelation USING btree (fromuserprofileid, touserprofileid) WHERE ((fromuserprofileid IS NOT NULL) AND (touserprofileid IS NOT NULL));
CREATE INDEX idx_provider_relation_from ON public.etown_providerrelation USING btree (fromuserprofileid);
CREATE INDEX idx_provider_relation_to ON public.etown_providerrelation USING btree (touserprofileid);
CREATE INDEX idx_provider_relation_from_contact ON public.etown_providerrelation USING btree (fromcontactid);
CREATE INDEX idx_provider_relation_to_contact ON public.etown_providerrelation USING btree (tocontactid);
CREATE INDEX fki_contactid ON public.etown_providerservices USING btree (contactid);
CREATE INDEX fki_providerservicecontact ON public.etown_providerservices USING btree (contactid);
CREATE INDEX fki_providerserviceuserprofile ON public.etown_providerservices USING btree (userprofileid);
CREATE INDEX fki_providerserviceproviderid ON public.etown_providerservices USING btree (providerid);
CREATE INDEX fki_providerservicecontactfkey ON public.etown_providerservices USING btree (contactid);
CREATE INDEX fki_providerserviceuserprofilefkey ON public.etown_providerservices USING btree (userprofileid);
CREATE INDEX fki_providerserviceproviderfkey ON public.etown_providerservices USING btree (providerid);
CREATE INDEX fki_providercontactfkey ON public.etown_providerservices USING btree (contactid);
CREATE INDEX fki_provideridfkey ON public.etown_providerservices USING btree (providerid);
CREATE INDEX etown_providerservices_copy_contactid_idx ON public.etown_providerservices_copy USING btree (contactid) WITH (fillfactor='90');
CREATE INDEX etown_providerservices_copy_providerid_idx ON public.etown_providerservices_copy USING btree (providerid) WITH (fillfactor='90');
CREATE INDEX idx_provider_contactid ON public.etown_providerservices_copy USING btree (contactid);
CREATE INDEX idx_provider_businessname_lower ON public.etown_providerservices_copy USING btree (lower(businessname));
CREATE INDEX idx_provider_category_lower ON public.etown_providerservices_copy USING btree (lower(businesscategory));
CREATE INDEX etown_providerservices_copy_userprofileid_idx ON public.etown_providerservices_copy USING btree (userprofileid) WITH (fillfactor='90');
CREATE INDEX idx_etown_providerservicesadf_contact_id ON public.etown_providerservicesadf USING btree (contact_id);
CREATE INDEX idx_etown_providerservicesadf_user_profile_id ON public.etown_providerservicesadf USING btree (user_profile_id);
CREATE UNIQUE INDEX etown_query_expansions_term_expansion_language_key ON public.etown_query_expansions USING btree (term, expansion, language);
CREATE INDEX idx_query_expansions_lookup ON public.etown_query_expansions USING btree (term, language, is_active);
CREATE INDEX idx_etown_ratings_provideruserprofileid ON public.etown_ratings USING btree (provideruserprofileid);
CREATE INDEX idx_ratings_provider ON public.etown_ratings USING btree (lower(TRIM(BOTH FROM provideruserprofileid)));
CREATE INDEX idx_etown_ratings_providercontactid ON public.etown_ratings USING btree (providercontactid);
CREATE INDEX idx_etown_requestservicelists_serviceprovidercontactid ON public.etown_requestservicelists USING btree (serviceprovidercontactid);
CREATE INDEX idx_etown_requestservicelists_serviceprovideruserprofileid ON public.etown_requestservicelists USING btree (serviceprovideruserprofileid);
CREATE INDEX idx_etown_requestservicelists_serviceproviderid ON public.etown_requestservicelists USING btree (serviceproviderid);
CREATE INDEX idx_social_insights_feed_id ON public.etown_social_insights_snapshots USING btree (feed_id);
CREATE INDEX idx_social_insights_platform ON public.etown_social_insights_snapshots USING btree (platform);
CREATE INDEX idx_social_insights_contactid ON public.etown_social_insights_snapshots USING btree (contactid);
CREATE INDEX idx_social_insights_captured_at ON public.etown_social_insights_snapshots USING btree (captured_at DESC);
CREATE INDEX idx_social_insights_platform_feed ON public.etown_social_insights_snapshots USING btree (platform, feed_id, captured_at DESC);
CREATE UNIQUE INDEX uq_social_insights_platform_feed_period ON public.etown_social_insights_snapshots USING btree (platform, feed_id, period_days);
CREATE INDEX idx_etown_social_insights_snapshots_userprofileid ON public.etown_social_insights_snapshots USING btree (userprofileid);
CREATE INDEX idx_etown_subscriptions_current_userprofileid ON public.etown_subscriptions USING btree (current_userprofileid);
CREATE INDEX idx_etown_subscriptions_opponent_userprofileid ON public.etown_subscriptions USING btree (opponent_userprofileid);
CREATE INDEX idx_town_view_tracking_townprofileid ON public.etown_town_view_tracking USING btree (townprofileid);
CREATE INDEX idx_town_view_tracking_userprofileid ON public.etown_town_view_tracking USING btree (userprofileid);
CREATE INDEX idx_townprofile_lat_lon ON public.etown_townprofile USING btree (townlatitude, townlongitude) WHERE ((townlatitude IS NOT NULL) AND (townlongitude IS NOT NULL));
CREATE INDEX idx_townprofile_name_lower ON public.etown_townprofile USING btree (lower(TRIM(BOTH FROM townname))) WHERE (townname IS NOT NULL);
CREATE INDEX idx_etown_townprofile_vieweduserprofileid ON public.etown_townprofile USING btree (vieweduserprofileid);
CREATE INDEX idx_etown_userlocationdetails_contactid ON public.etown_userlocationdetails USING btree (contactid);
CREATE INDEX fki_c ON public.etown_userprofile USING btree (contactid);
CREATE INDEX fki_contactuserprofile ON public.etown_userprofile USING btree (contactid);
CREATE INDEX fki_contactidfkey ON public.etown_userprofile USING btree (contactid);
CREATE INDEX idx_etown_userprofile_contactid ON public.etown_userprofile USING btree (contactid);
CREATE INDEX idx_userprofile_id_lower ON public.etown_userprofile USING btree (lower(userprofileid));
CREATE INDEX idx_userprofile_contactid_role ON public.etown_userprofile USING btree (lower(contactid), role, userprofileid);
CREATE INDEX idx_userprofile_role ON public.etown_userprofile USING btree (role, userprofileid);
CREATE INDEX idx_etown_userprofile_role_createdon ON public.etown_userprofile USING btree (role, createdon);
CREATE INDEX idx_etown_userprofile_contactid_lookup ON public.etown_userprofile USING btree (contactid);
CREATE INDEX idx_userprofile_contactid ON public.etown_userprofile USING btree (contactid);
CREATE UNIQUE INDEX etowntitlegenerationlogs_contact_id_user_profile_id_convers_key ON public.etowntitlegenerationlogs USING btree (contact_id, user_profile_id, conversation_id);
CREATE UNIQUE INDEX unique_log_key ON public.etowntitlegenerationlogs USING btree (contact_id, user_profile_id, conversation_id);
CREATE INDEX idx_etowntitlegenerationlogs_user_profile_id ON public.etowntitlegenerationlogs USING btree (user_profile_id);
CREATE INDEX idx_user_id ON public.etownuserchaatiehistory USING btree (user_id);
CREATE INDEX idx_created_at ON public.etownuserchaatiehistory USING btree (created_at DESC);
CREATE INDEX idx_conversation_id ON public.etownuserchaatiehistory USING btree (conversation_id);
CREATE INDEX idx_user_conversation ON public.etownuserchaatiehistory USING btree (user_id, conversation_id, created_at);
CREATE INDEX mem0migrations_hnsw_idx ON public.mem0migrations USING hnsw (vector vector_cosine_ops);
CREATE INDEX ix_need_town_posted ON public.etown_need USING btree (townprofileid, created_at DESC) WHERE ((status = 'posted'::text) AND (COALESCE(deleted, 'false'::text) <> 'true'::text));
CREATE INDEX ix_need_userprofile ON public.etown_need USING btree (userprofileid, created_at DESC) WHERE (COALESCE(deleted, 'false'::text) <> 'true'::text);
CREATE INDEX ix_update_town_posted ON public.etown_update USING btree (townprofileid, created_at DESC) WHERE ((status = 'posted'::text) AND (COALESCE(deleted, 'false'::text) <> 'true'::text));
CREATE INDEX ix_update_userprofile ON public.etown_update USING btree (userprofileid, created_at DESC) WHERE (COALESCE(deleted, 'false'::text) <> 'true'::text);
CREATE INDEX ix_event_town_posted ON public.etown_event USING btree (townprofileid, created_at DESC) WHERE ((status = 'posted'::text) AND (COALESCE(deleted, 'false'::text) <> 'true'::text));
CREATE INDEX ix_event_userprofile ON public.etown_event USING btree (userprofileid, created_at DESC) WHERE (COALESCE(deleted, 'false'::text) <> 'true'::text);

CREATE TRIGGER update_etownuserchaatiehistory_updated_at BEFORE UPDATE ON public.etownuserchaatiehistory FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_set_normalized_category_copy BEFORE INSERT OR UPDATE ON public.etown_providerservices_copy FOR EACH ROW EXECUTE FUNCTION trg_set_normalized_category();
CREATE TRIGGER trg_capture_etown_category_orig AFTER INSERT OR UPDATE ON public.etown_providerservices FOR EACH ROW EXECUTE FUNCTION trg_capture_etown_category();
CREATE TRIGGER trg_capture_etown_category_copy AFTER INSERT OR UPDATE ON public.etown_providerservices_copy FOR EACH ROW EXECUTE FUNCTION trg_capture_etown_category();
CREATE TRIGGER trg_sync_mem0_columns BEFORE INSERT OR UPDATE ON public.etown_mem0_memories FOR EACH ROW EXECUTE FUNCTION sync_mem0_columns();
CREATE TRIGGER update_call_sessions_updated_at BEFORE UPDATE ON public.etown_call_sessions FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_call_participants_updated_at BEFORE UPDATE ON public.etown_call_participants FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_provider_seo_sync_service_ins_del AFTER INSERT OR DELETE ON public.etown_providerservices FOR EACH ROW EXECUTE FUNCTION trg_provider_seo_sync_service();
CREATE TRIGGER trg_set_normalized_category_orig BEFORE INSERT OR UPDATE ON public.etown_providerservices FOR EACH ROW EXECUTE FUNCTION trg_set_normalized_category();
CREATE TRIGGER trg_provider_seo_sync_provider_ins_del AFTER INSERT OR DELETE ON public.etown_provider FOR EACH ROW EXECUTE FUNCTION trg_provider_seo_sync_provider();
CREATE TRIGGER trg_provider_seo_sync_provider_upd AFTER UPDATE OF providerid, businessname, providertype, role, description, address, city, townname, email, mobilenumber, serviceimageurl, publicsearchprovider, creationdate, userprofileid, deleted ON public.etown_provider FOR EACH ROW WHEN ((((((((((((((((old.providerid IS DISTINCT FROM new.providerid) OR (old.businessname IS DISTINCT FROM new.businessname)) OR (old.providertype IS DISTINCT FROM new.providertype)) OR (old.role IS DISTINCT FROM new.role)) OR (old.description IS DISTINCT FROM new.description)) OR (old.address IS DISTINCT FROM new.address)) OR (old.city IS DISTINCT FROM new.city)) OR (old.townname IS DISTINCT FROM new.townname)) OR (old.email IS DISTINCT FROM new.email)) OR (old.mobilenumber IS DISTINCT FROM new.mobilenumber)) OR (old.serviceimageurl IS DISTINCT FROM new.serviceimageurl)) OR (old.publicsearchprovider IS DISTINCT FROM new.publicsearchprovider)) OR (old.creationdate IS DISTINCT FROM new.creationdate)) OR (old.userprofileid IS DISTINCT FROM new.userprofileid)) OR (old.deleted IS DISTINCT FROM new.deleted))) EXECUTE FUNCTION trg_provider_seo_sync_provider();
CREATE TRIGGER trg_provider_seo_sync_service_upd AFTER UPDATE OF providerid, services, serviceimageurl1, serviceimageurl2, serviceimageurl3, deleted ON public.etown_providerservices FOR EACH ROW WHEN (((((((old.providerid IS DISTINCT FROM new.providerid) OR (old.services IS DISTINCT FROM new.services)) OR (old.serviceimageurl1 IS DISTINCT FROM new.serviceimageurl1)) OR (old.serviceimageurl2 IS DISTINCT FROM new.serviceimageurl2)) OR (old.serviceimageurl3 IS DISTINCT FROM new.serviceimageurl3)) OR (old.deleted IS DISTINCT FROM new.deleted))) EXECUTE FUNCTION trg_provider_seo_sync_service();
CREATE TRIGGER trg_provider_seo_sync_rating_del AFTER DELETE ON public.etown_allrequests FOR EACH ROW WHEN ((old.ratingvalue IS NOT NULL)) EXECUTE FUNCTION trg_provider_seo_sync_rating();
CREATE TRIGGER trg_provider_seo_sync_rating_upd AFTER UPDATE OF ratingvalue, "isRating", requestorname, requeststatus, bookingcompletedtime ON public.etown_allrequests FOR EACH ROW WHEN (((((old.ratingvalue IS DISTINCT FROM new.ratingvalue) OR (old."isRating" IS DISTINCT FROM new."isRating")) OR (old.requestorname IS DISTINCT FROM new.requestorname)) OR ((new.ratingvalue IS NOT NULL) AND ((old.requeststatus IS DISTINCT FROM new.requeststatus) OR (old.bookingcompletedtime IS DISTINCT FROM new.bookingcompletedtime))))) EXECUTE FUNCTION trg_provider_seo_sync_rating();
CREATE TRIGGER trg_provider_image_stack_provider BEFORE INSERT OR UPDATE OF providerid, serviceimageurl, background_image ON public.etown_provider FOR EACH ROW EXECUTE FUNCTION trg_provider_image_stack_provider();
CREATE TRIGGER trg_provider_image_stack_service AFTER INSERT OR DELETE OR UPDATE OF providerid, serviceimageurl1, serviceimageurl2, serviceimageurl3, deleted, createdon ON public.etown_providerservices FOR EACH ROW EXECUTE FUNCTION trg_provider_image_stack_service();

ALTER TABLE public.etown_call_participants ADD FOREIGN KEY (session_id) REFERENCES etown_call_sessions(session_id) ON DELETE CASCADE;
ALTER TABLE public.etown_call_tokens ADD FOREIGN KEY (session_id) REFERENCES etown_call_sessions(session_id) ON DELETE CASCADE;
ALTER TABLE public.etown_call_events ADD FOREIGN KEY (session_id) REFERENCES etown_call_sessions(session_id) ON DELETE CASCADE;
ALTER TABLE public.etown_provider_details ADD FOREIGN KEY (providerid) REFERENCES etown_provider(providerid) ON DELETE CASCADE;
ALTER TABLE public.etown_messages ADD FOREIGN KEY (conversation_id) REFERENCES etown_conversations(id) ON DELETE CASCADE;
ALTER TABLE public.etown_conversations ADD FOREIGN KEY (last_message_id) REFERENCES etown_messages(id) ON DELETE SET NULL;
ALTER TABLE public.etown_messages ADD FOREIGN KEY (reply_to_message_id) REFERENCES etown_messages(id) ON DELETE SET NULL;
ALTER TABLE public.etown_auth_refresh_token ADD FOREIGN KEY (rotated_to) REFERENCES etown_auth_refresh_token(id);
ALTER TABLE public.etown_providerservices_copy ADD FOREIGN KEY (providerservicesid) REFERENCES etown_providerservices(providerservicesid) ON DELETE CASCADE;
ALTER TABLE public.etown_provider ADD FOREIGN KEY (plan_code) REFERENCES etown_subscription_plan(plan_code);
ALTER TABLE public.etown_category_pin ADD FOREIGN KEY (categoryid) REFERENCES etown_category(categoryid);
ALTER TABLE public.etown_popular_category_pin ADD FOREIGN KEY (categoryid) REFERENCES etown_category(categoryid);

CREATE OR REPLACE VIEW public.conversation_summaries AS
 SELECT conversation_id,
    user_id,
    user_name,
    count(*) AS message_count,
    min(created_at) AS started_at,
    max(created_at) AS last_message_at,
    (max(created_at) - min(created_at)) AS duration
   FROM etownuserchaatiehistory
  GROUP BY conversation_id, user_id, user_name
  ORDER BY (max(created_at)) DESC;
CREATE OR REPLACE VIEW public.etown_app_uninstalled_users AS
 SELECT contactid,
    min(contactidname) AS contactidname,
    count(*) AS devices_uninstalled,
    min(detected_at) AS first_detected_at,
    max(detected_at) AS last_detected_at,
    max(last_seen_at) AS last_seen_at
   FROM etown_app_uninstalls u
  WHERE ((reinstalled_at IS NULL) AND (detection_source <> 'baseline'::text) AND (NULLIF(contactid, ''::text) IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
           FROM etown_contactdevices d
          WHERE ((d.contactid = u.contactid) AND (COALESCE(NULLIF(d.device_token, ''::text), NULLIF(d.deviceguid, ''::text)) IS NOT NULL))))))
  GROUP BY contactid;
CREATE OR REPLACE VIEW public.etown_daily_metrics AS
 WITH excluded_users AS (
         SELECT DISTINCT c.contactid,
            u.userprofileid
           FROM (etown_contact c
             LEFT JOIN etown_userprofile u ON ((u.contactid = c.contactid)))
          WHERE (c.mobilephone = ANY (ARRAY['6238097293'::text, '9074974082'::text, '8281530034'::text, '7559002895'::text, '8578028430'::text, '9080151816'::text, '7407364654'::text, '8205013449'::text, '8927249938'::text, '8056679788'::text, '9787412170'::text, '6239630924'::text, '9551554424'::text, '9629444109'::text, '7339142962'::text, '7550027182'::text, '9677228913'::text, '7548829022'::text, '9489707091'::text, '6384847010'::text]))
        UNION
         SELECT DISTINCT u.contactid,
            u.userprofileid
           FROM etown_userprofile u
          WHERE (u.mobilenumber = ANY (ARRAY['6238097293'::text, '9074974082'::text, '8281530034'::text, '7559002895'::text, '8578028430'::text, '9080151816'::text, '7407364654'::text, '8205013449'::text, '8927249938'::text, '8056679788'::text, '9787412170'::text, '6239630924'::text, '9551554424'::text, '9629444109'::text, '7339142962'::text, '7550027182'::text, '9677228913'::text, '7548829022'::text, '9489707091'::text, '6384847010'::text]))
        ), contact_daily AS (
         SELECT date(c.createdon) AS metric_date,
            count(*) FILTER (WHERE (c.verified = true)) AS verified_users,
            count(*) FILTER (WHERE ((c.verified = false) OR (c.verified IS NULL))) AS unverified_users
           FROM etown_contact c
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = c.contactid))))
          GROUP BY (date(c.createdon))
        ), active_users_daily AS (
         SELECT date(c.lasttriggeredtime) AS metric_date,
            count(*) AS active_users
           FROM etown_contact c
          WHERE ((c.lasttriggeredtime IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = c.contactid)))))
          GROUP BY (date(c.lasttriggeredtime))
        ), userprofile_daily AS (
         SELECT date(u.createdon) AS metric_date,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM u.rolename)) = 'member'::text)) AS total_members,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM u.rolename)) = 'provider'::text)) AS total_providers,
            count(*) FILTER (WHERE ((lower(TRIM(BOTH FROM u.rolename)) = 'provider'::text) AND (u.verified = true))) AS verified_providers,
            count(*) FILTER (WHERE ((lower(TRIM(BOTH FROM u.rolename)) = 'provider'::text) AND ((u.verified = false) OR (u.verified IS NULL)))) AS unverified_providers,
            count(*) FILTER (WHERE ((lower(TRIM(BOTH FROM u.rolename)) = 'member'::text) AND (u.verified = true))) AS verified_members,
            count(*) FILTER (WHERE ((lower(TRIM(BOTH FROM u.rolename)) = 'member'::text) AND ((u.verified = false) OR (u.verified IS NULL)))) AS unverified_members
           FROM etown_userprofile u
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.userprofileid = u.userprofileid))))
          GROUP BY (date(u.createdon))
        ), bookings_daily AS (
         SELECT date(r.createdon) AS metric_date,
            count(*) AS total_bookings
           FROM etown_allrequests r
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE ((e.contactid = r.requestorid) OR (e.userprofileid = r.requestoruserprofileid)))))
          GROUP BY (date(r.createdon))
        ), invites_daily AS (
         SELECT date(i.createdon) AS metric_date,
            count(*) AS invites_shared,
            ((count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.status)) = 'accepted'::text)))::numeric / (NULLIF(count(*), 0))::numeric) AS invite_acceptance_rate,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.invitationtype)) = 'internal'::text)) AS internal_invites,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.invitationtype)) = 'booking'::text)) AS booking_invites,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.invitationtype)) = 'added to mycircle'::text)) AS added_to_mycircle_invites,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.invitationtype)) = 'externalapp_share'::text)) AS externalapp_share_invites,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.invitationtype)) = 'qr'::text)) AS qr_invites,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM i.invitationtype)) = 'external'::text)) AS external_invites
           FROM etown_invitation i
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE ((e.contactid = i.invitercontact) OR (e.userprofileid = i.inviteruserprofile)))))
          GROUP BY (date(i.createdon))
        ), feeds_daily AS (
         SELECT date(f.createdon) AS metric_date,
            count(*) AS total_posts
           FROM etown_feed f
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE ((e.contactid = f.contactid) OR (e.userprofileid = f.userprofileid)))))
          GROUP BY (date(f.createdon))
        ), offers_daily AS (
         SELECT date(o.createdon) AS metric_date,
            count(*) AS total_offers
           FROM etown_offers o
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE ((e.contactid = o.contactid) OR (e.userprofileid = o.userprofileid)))))
          GROUP BY (date(o.createdon))
        ), chaatie_daily AS (
         SELECT date(ch.created_at) AS metric_date,
            count(*) AS chaatie_queries_count
           FROM etownuserchaatiehistory ch
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = (ch.user_id)::text))))
          GROUP BY (date(ch.created_at))
        ), town_viewed_daily AS (
         SELECT date(tv.created_on) AS metric_date,
            count(*) AS town_viewed
           FROM etown_town_view_tracking tv
          WHERE (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.userprofileid = tv.userprofileid))))
          GROUP BY (date(tv.created_on))
        ), active_members_daily AS (
         SELECT mt.hit_date AS metric_date,
            max(mt.active_member) AS active_member
           FROM etown_member_tracking mt
          WHERE (mt.hit_date IS NOT NULL)
          GROUP BY mt.hit_date
        ), active_providers_daily AS (
         SELECT pt.hit_date AS metric_date,
            max(pt.active_provider) AS active_provider
           FROM etown_provider_tracking pt
          WHERE (pt.hit_date IS NOT NULL)
          GROUP BY pt.hit_date
        ), provider_subscription_daily AS (
         SELECT p.plan_started_on AS metric_date,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM p.plan_code)) <> 'free'::text)) AS total_subscribers,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM p.plan_code)) = 'silver'::text)) AS silver_subscribers
           FROM etown_provider p
          WHERE ((p.plan_started_on IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = p.contactid)))))
          GROUP BY p.plan_started_on
        ), free_users_daily AS (
         SELECT date(p.creationdate) AS metric_date,
            count(*) AS total_freeusers
           FROM etown_provider p
          WHERE ((lower(TRIM(BOTH FROM p.plan_code)) = 'free'::text) AND (p.creationdate IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = p.contactid)))))
          GROUP BY (date(p.creationdate))
        ), expired_subscription_daily AS (
         SELECT p.plan_expires_on AS metric_date,
            count(*) FILTER (WHERE (lower(TRIM(BOTH FROM p.subscription_status)) = 'expired'::text)) AS expired_subscription
           FROM etown_provider p
          WHERE ((p.plan_expires_on IS NOT NULL) AND (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = p.contactid)))))
          GROUP BY p.plan_expires_on
        ), all_metric_dates AS (
         SELECT contact_daily.metric_date
           FROM contact_daily
        UNION
         SELECT active_users_daily.metric_date
           FROM active_users_daily
        UNION
         SELECT userprofile_daily.metric_date
           FROM userprofile_daily
        UNION
         SELECT bookings_daily.metric_date
           FROM bookings_daily
        UNION
         SELECT invites_daily.metric_date
           FROM invites_daily
        UNION
         SELECT feeds_daily.metric_date
           FROM feeds_daily
        UNION
         SELECT offers_daily.metric_date
           FROM offers_daily
        UNION
         SELECT chaatie_daily.metric_date
           FROM chaatie_daily
        UNION
         SELECT town_viewed_daily.metric_date
           FROM town_viewed_daily
        UNION
         SELECT active_members_daily.metric_date
           FROM active_members_daily
        UNION
         SELECT active_providers_daily.metric_date
           FROM active_providers_daily
        UNION
         SELECT provider_subscription_daily.metric_date
           FROM provider_subscription_daily
        UNION
         SELECT free_users_daily.metric_date
           FROM free_users_daily
        UNION
         SELECT expired_subscription_daily.metric_date
           FROM expired_subscription_daily
        ), subscription_status_daily AS (
         SELECT d_1.metric_date,
            count(*) AS active_subscribers
           FROM (all_metric_dates d_1
             LEFT JOIN etown_provider p ON (((lower(TRIM(BOTH FROM p.subscription_status)) = 'active'::text) AND (lower(TRIM(BOTH FROM p.plan_code)) = 'silver'::text) AND (p.plan_started_on <= d_1.metric_date) AND (p.plan_expires_on >= d_1.metric_date) AND (NOT (EXISTS ( SELECT 1
                   FROM excluded_users e
                  WHERE (e.contactid = p.contactid)))))))
          GROUP BY d_1.metric_date
        )
 SELECT d.metric_date,
    ((COALESCE(cd.verified_users, (0)::bigint) + COALESCE(up.unverified_providers, (0)::bigint)) + COALESCE(up.unverified_members, (0)::bigint)) AS total_users,
    COALESCE(up.total_members, (0)::bigint) AS total_members,
    COALESCE(up.total_providers, (0)::bigint) AS total_providers,
    COALESCE(aud.active_users, (0)::bigint) AS active_users,
    COALESCE(up.verified_providers, (0)::bigint) AS verified_providers,
    COALESCE(up.unverified_providers, (0)::bigint) AS unverified_providers,
    COALESCE(up.verified_members, (0)::bigint) AS verified_members,
    COALESCE(up.unverified_members, (0)::bigint) AS unverified_members,
    COALESCE(bd.total_bookings, (0)::bigint) AS total_bookings,
    COALESCE(id.invites_shared, (0)::bigint) AS invites_shared,
    COALESCE(id.invite_acceptance_rate, (0)::numeric) AS invite_acceptance_rate,
    COALESCE(id.internal_invites, (0)::bigint) AS internal_invites,
    COALESCE(id.booking_invites, (0)::bigint) AS booking_invites,
    COALESCE(id.added_to_mycircle_invites, (0)::bigint) AS added_to_mycircle_invites,
    COALESCE(id.externalapp_share_invites, (0)::bigint) AS externalapp_share_invites,
    COALESCE(id.qr_invites, (0)::bigint) AS qr_invites,
    COALESCE(id.external_invites, (0)::bigint) AS external_invites,
    COALESCE(fd.total_posts, (0)::bigint) AS total_posts,
    COALESCE(od.total_offers, (0)::bigint) AS total_offers,
    COALESCE(chd.chaatie_queries_count, (0)::bigint) AS chaatie_queries_count,
    COALESCE(tvd.town_viewed, (0)::bigint) AS town_viewed,
    COALESCE(cd.verified_users, (0)::bigint) AS verified_users,
    COALESCE(cd.unverified_users, (0)::bigint) AS unverified_users,
    COALESCE(amd.active_member, (0)::bigint) AS active_member,
    COALESCE(apd.active_provider, (0)::bigint) AS active_provider,
    COALESCE(psd.total_subscribers, (0)::bigint) AS total_subscribers,
    COALESCE(psd.silver_subscribers, (0)::bigint) AS silver_subscribers,
    COALESCE(fud.total_freeusers, (0)::bigint) AS total_freeusers,
    COALESCE(ssd.active_subscribers, (0)::bigint) AS active_subscribers,
    COALESCE(esd.expired_subscription, (0)::bigint) AS expired_subscription
   FROM (((((((((((((((all_metric_dates d
     LEFT JOIN contact_daily cd ON ((cd.metric_date = d.metric_date)))
     LEFT JOIN active_users_daily aud ON ((aud.metric_date = d.metric_date)))
     LEFT JOIN userprofile_daily up ON ((up.metric_date = d.metric_date)))
     LEFT JOIN bookings_daily bd ON ((bd.metric_date = d.metric_date)))
     LEFT JOIN invites_daily id ON ((id.metric_date = d.metric_date)))
     LEFT JOIN feeds_daily fd ON ((fd.metric_date = d.metric_date)))
     LEFT JOIN offers_daily od ON ((od.metric_date = d.metric_date)))
     LEFT JOIN chaatie_daily chd ON ((chd.metric_date = d.metric_date)))
     LEFT JOIN town_viewed_daily tvd ON ((tvd.metric_date = d.metric_date)))
     LEFT JOIN active_members_daily amd ON ((amd.metric_date = d.metric_date)))
     LEFT JOIN active_providers_daily apd ON ((apd.metric_date = d.metric_date)))
     LEFT JOIN provider_subscription_daily psd ON ((psd.metric_date = d.metric_date)))
     LEFT JOIN free_users_daily fud ON ((fud.metric_date = d.metric_date)))
     LEFT JOIN subscription_status_daily ssd ON ((ssd.metric_date = d.metric_date)))
     LEFT JOIN expired_subscription_daily esd ON ((esd.metric_date = d.metric_date)))
  ORDER BY d.metric_date;
CREATE OR REPLACE VIEW public.etown_retired_identity AS
 WITH retired AS (
         SELECT c.contactid,
            c.mobilephone,
            c.email,
            c.deleted,
            c.deletiondate,
            c.deactivated,
            c.deactivationdate,
            c.reactivation,
            c.reactivationdate
           FROM etown_contact c
          WHERE ((c.deleted = 'true'::text) OR (c.deactivated = 'true'::text))
        )
 SELECT r.contactid,
    r.contactid AS identity_id,
    'contact'::text AS identity_type,
    r.mobilephone,
    r.email,
    r.deleted,
    r.deletiondate,
    r.deactivated,
    r.deactivationdate,
    r.reactivation,
    r.reactivationdate
   FROM retired r
UNION ALL
 SELECT r.contactid,
    u.userprofileid AS identity_id,
    'userprofile'::text AS identity_type,
    r.mobilephone,
    r.email,
    r.deleted,
    r.deletiondate,
    r.deactivated,
    r.deactivationdate,
    r.reactivation,
    r.reactivationdate
   FROM (retired r
     JOIN etown_userprofile u ON ((u.contactid = r.contactid)))
  WHERE (u.userprofileid IS NOT NULL)
UNION ALL
 SELECT r.contactid,
    m.memberid AS identity_id,
    'member'::text AS identity_type,
    r.mobilephone,
    r.email,
    r.deleted,
    r.deletiondate,
    r.deactivated,
    r.deactivationdate,
    r.reactivation,
    r.reactivationdate
   FROM (retired r
     JOIN etown_member m ON ((m.contactid = r.contactid)))
  WHERE (m.memberid IS NOT NULL)
UNION ALL
 SELECT r.contactid,
    p.providerid AS identity_id,
    'provider'::text AS identity_type,
    r.mobilephone,
    r.email,
    r.deleted,
    r.deletiondate,
    r.deactivated,
    r.deactivationdate,
    r.reactivation,
    r.reactivationdate
   FROM (retired r
     JOIN etown_provider p ON ((p.contactid = r.contactid)))
  WHERE (p.providerid IS NOT NULL)
UNION ALL
 SELECT p.contactid,
    p.userprofileid AS identity_id,
    'userprofile'::text AS identity_type,
    c.mobilephone,
    c.email,
    p.deleted,
    p.deletiondate,
    p.deactivated,
    p.deactivationdate,
    p.reactivation,
    p.reactivationdate
   FROM (etown_provider p
     LEFT JOIN etown_contact c ON ((c.contactid = p.contactid)))
  WHERE (((p.deleted = 'true'::text) OR (p.deactivated = 'true'::text)) AND (p.userprofileid IS NOT NULL) AND (COALESCE(c.deleted, 'false'::text) <> 'true'::text) AND (COALESCE(c.deactivated, 'false'::text) <> 'true'::text))
UNION ALL
 SELECT p.contactid,
    p.providerid AS identity_id,
    'provider'::text AS identity_type,
    c.mobilephone,
    c.email,
    p.deleted,
    p.deletiondate,
    p.deactivated,
    p.deactivationdate,
    p.reactivation,
    p.reactivationdate
   FROM (etown_provider p
     LEFT JOIN etown_contact c ON ((c.contactid = p.contactid)))
  WHERE (((p.deleted = 'true'::text) OR (p.deactivated = 'true'::text)) AND (p.providerid IS NOT NULL) AND (COALESCE(c.deleted, 'false'::text) <> 'true'::text) AND (COALESCE(c.deactivated, 'false'::text) <> 'true'::text));
CREATE OR REPLACE VIEW public.etown_weekly_metrics AS
 WITH weekly_data AS (
         SELECT (date_trunc('week'::text, (etown_daily_metrics.metric_date)::timestamp with time zone))::date AS week_start,
            sum(etown_daily_metrics.total_users) AS total_users,
            sum(etown_daily_metrics.total_members) AS total_members,
            sum(etown_daily_metrics.total_providers) AS total_providers,
            sum(etown_daily_metrics.active_users) AS active_users,
            sum(etown_daily_metrics.verified_providers) AS verified_providers,
            sum(etown_daily_metrics.unverified_providers) AS unverified_providers,
            sum(etown_daily_metrics.verified_members) AS verified_members,
            sum(etown_daily_metrics.unverified_members) AS unverified_members,
            sum(etown_daily_metrics.total_bookings) AS total_bookings,
            sum(etown_daily_metrics.invites_shared) AS invites_shared,
            avg(etown_daily_metrics.invite_acceptance_rate) AS invite_acceptance_rate,
            sum(etown_daily_metrics.internal_invites) AS internal_invites,
            sum(etown_daily_metrics.booking_invites) AS booking_invites,
            sum(etown_daily_metrics.added_to_mycircle_invites) AS added_to_mycircle_invites,
            sum(etown_daily_metrics.externalapp_share_invites) AS externalapp_share_invites,
            sum(etown_daily_metrics.qr_invites) AS qr_invites,
            sum(etown_daily_metrics.external_invites) AS external_invites,
            sum(etown_daily_metrics.total_posts) AS total_posts,
            sum(etown_daily_metrics.total_offers) AS total_offers,
            sum(etown_daily_metrics.chaatie_queries_count) AS chaatie_queries_count,
            sum(etown_daily_metrics.town_viewed) AS town_viewed
           FROM etown_daily_metrics
          WHERE ((etown_daily_metrics.metric_date >= (date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone) - '7 days'::interval)) AND (etown_daily_metrics.metric_date < (date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone) + '7 days'::interval)))
          GROUP BY (date_trunc('week'::text, (etown_daily_metrics.metric_date)::timestamp with time zone))
        ), metric_values AS (
         SELECT wd.week_start,
            metric_data.sort_order,
            metric_data.metric,
            metric_data.metric_value
           FROM (weekly_data wd
             CROSS JOIN LATERAL ( VALUES (1,'Total Users'::text,wd.total_users), (2,'Total Members'::text,wd.total_members), (3,'Total Providers'::text,wd.total_providers), (4,'Active Users'::text,wd.active_users), (5,'Verified Providers'::text,wd.verified_providers), (6,'Unverified Providers'::text,wd.unverified_providers), (7,'Verified Members'::text,wd.verified_members), (8,'Unverified Members'::text,wd.unverified_members), (9,'Total Bookings'::text,wd.total_bookings), (10,'Invites Shared'::text,wd.invites_shared), (11,'Invite Acceptance Rate'::text,wd.invite_acceptance_rate), (12,'Internal Invites'::text,wd.internal_invites), (13,'Booking Invites'::text,wd.booking_invites), (14,'Added to MyCircle'::text,wd.added_to_mycircle_invites), (15,'External App Share'::text,wd.externalapp_share_invites), (16,'QR Invites'::text,wd.qr_invites), (17,'External Invites'::text,wd.external_invites), (18,'Total Posts'::text,wd.total_posts), (19,'Total Offers'::text,wd.total_offers), (20,'Chaatie Queries'::text,wd.chaatie_queries_count), (21,'Town Viewed'::text,wd.town_viewed)) metric_data(sort_order, metric, metric_value))
        ), pivoted_data AS (
         SELECT metric_values.sort_order,
            metric_values.metric,
            max(metric_values.metric_value) FILTER (WHERE (metric_values.week_start = (date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone))::date)) AS current_week,
            max(metric_values.metric_value) FILTER (WHERE (metric_values.week_start = ((date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone) - '7 days'::interval))::date)) AS last_week
           FROM metric_values
          GROUP BY metric_values.sort_order, metric_values.metric
        )
 SELECT metric,
    current_week,
    last_week,
        CASE
            WHEN ((current_week IS NULL) OR (last_week IS NULL)) THEN NULL::text
            WHEN ((last_week = (0)::numeric) AND (current_week = (0)::numeric)) THEN '0.0%'::text
            WHEN (last_week = (0)::numeric) THEN 'N/A'::text
            ELSE concat(
            CASE
                WHEN (current_week > last_week) THEN '+'::text
                ELSE ''::text
            END, round((((current_week - last_week) / NULLIF(last_week, (0)::numeric)) * (100)::numeric), 1), '%')
        END AS change
   FROM pivoted_data
  ORDER BY sort_order;
CREATE OR REPLACE VIEW public.pg_stat_statements AS
 SELECT userid,
    dbid,
    toplevel,
    queryid,
    query,
    plans,
    total_plan_time,
    min_plan_time,
    max_plan_time,
    mean_plan_time,
    stddev_plan_time,
    calls,
    total_exec_time,
    min_exec_time,
    max_exec_time,
    mean_exec_time,
    stddev_exec_time,
    rows,
    shared_blks_hit,
    shared_blks_read,
    shared_blks_dirtied,
    shared_blks_written,
    local_blks_hit,
    local_blks_read,
    local_blks_dirtied,
    local_blks_written,
    temp_blks_read,
    temp_blks_written,
    blk_read_time,
    blk_write_time,
    temp_blk_read_time,
    temp_blk_write_time,
    wal_records,
    wal_fpi,
    wal_bytes,
    jit_functions,
    jit_generation_time,
    jit_inlining_count,
    jit_inlining_time,
    jit_optimization_count,
    jit_optimization_time,
    jit_emission_count,
    jit_emission_time
   FROM pg_stat_statements(true) pg_stat_statements(userid, dbid, toplevel, queryid, query, plans, total_plan_time, min_plan_time, max_plan_time, mean_plan_time, stddev_plan_time, calls, total_exec_time, min_exec_time, max_exec_time, mean_exec_time, stddev_exec_time, rows, shared_blks_hit, shared_blks_read, shared_blks_dirtied, shared_blks_written, local_blks_hit, local_blks_read, local_blks_dirtied, local_blks_written, temp_blks_read, temp_blks_written, blk_read_time, blk_write_time, temp_blk_read_time, temp_blk_write_time, wal_records, wal_fpi, wal_bytes, jit_functions, jit_generation_time, jit_inlining_count, jit_inlining_time, jit_optimization_count, jit_optimization_time, jit_emission_count, jit_emission_time);
CREATE OR REPLACE VIEW public.pg_stat_statements_info AS
 SELECT dealloc,
    stats_reset
   FROM pg_stat_statements_info() pg_stat_statements_info(dealloc, stats_reset);
CREATE OR REPLACE VIEW public.vw_etown_contact_monthly_stats AS
 WITH months AS (
         SELECT (generate_series((date_trunc('month'::text, (CURRENT_DATE)::timestamp with time zone) - '5 mons'::interval), date_trunc('month'::text, (CURRENT_DATE)::timestamp with time zone), '1 mon'::interval))::date AS month
        ), monthly_counts AS (
         SELECT m.month,
            count(ec.*) AS total_count,
            count(ec.*) FILTER (WHERE (ec.verified = true)) AS verified_count,
            count(ec.*) FILTER (WHERE (ec.verified IS NOT TRUE)) AS unverified_count
           FROM (months m
             LEFT JOIN etown_contact ec ON ((ec.createdon < (m.month + '1 mon'::interval))))
          GROUP BY m.month
        ), with_previous AS (
         SELECT monthly_counts.month,
            monthly_counts.total_count,
            monthly_counts.verified_count,
            monthly_counts.unverified_count,
            lag(monthly_counts.total_count) OVER (ORDER BY monthly_counts.month) AS previous_total_count,
            lag(monthly_counts.verified_count) OVER (ORDER BY monthly_counts.month) AS previous_verified_count,
            lag(monthly_counts.unverified_count) OVER (ORDER BY monthly_counts.month) AS previous_unverified_count
           FROM monthly_counts
        )
 SELECT month,
    total_count,
    verified_count,
    unverified_count,
    round(((((total_count - previous_total_count))::numeric / (NULLIF(previous_total_count, 0))::numeric) * (100)::numeric), 2) AS total_percentage_change,
    round(((((verified_count - previous_verified_count))::numeric / (NULLIF(previous_verified_count, 0))::numeric) * (100)::numeric), 2) AS verified_percentage_change,
    round(((((unverified_count - previous_unverified_count))::numeric / (NULLIF(previous_unverified_count, 0))::numeric) * (100)::numeric), 2) AS unverified_percentage_change
   FROM with_previous
  ORDER BY month;
CREATE OR REPLACE VIEW public.vw_etown_daily_bookings AS
 WITH curr AS (
         SELECT count(*) FILTER (WHERE ((etown_allrequests.createdon)::date = (CURRENT_DATE - 1))) AS requested,
            count(*) FILTER (WHERE ((etown_allrequests.bookingacceptedtime)::date = (CURRENT_DATE - 1))) AS accepted,
            count(*) FILTER (WHERE ((etown_allrequests.bookingcompletedtime)::date = (CURRENT_DATE - 1))) AS completed,
            count(*) FILTER (WHERE ((etown_allrequests.bookingcancelledtime)::date = (CURRENT_DATE - 1))) AS cancelled
           FROM etown_allrequests
        ), prev AS (
         SELECT count(*) FILTER (WHERE ((etown_allrequests.createdon)::date = (CURRENT_DATE - 2))) AS requested,
            count(*) FILTER (WHERE ((etown_allrequests.bookingacceptedtime)::date = (CURRENT_DATE - 2))) AS accepted,
            count(*) FILTER (WHERE ((etown_allrequests.bookingcompletedtime)::date = (CURRENT_DATE - 2))) AS completed,
            count(*) FILTER (WHERE ((etown_allrequests.bookingcancelledtime)::date = (CURRENT_DATE - 2))) AS cancelled
           FROM etown_allrequests
        )
 SELECT curr.requested,
    prev.requested AS requested_prev,
    pct_change((curr.requested)::numeric, (prev.requested)::numeric) AS requested_chg_pct,
    curr.accepted,
    prev.accepted AS accepted_prev,
    pct_change((curr.accepted)::numeric, (prev.accepted)::numeric) AS accepted_chg_pct,
    curr.completed,
    prev.completed AS completed_prev,
    pct_change((curr.completed)::numeric, (prev.completed)::numeric) AS completed_chg_pct,
    curr.cancelled,
    prev.cancelled AS cancelled_prev,
    pct_change((curr.cancelled)::numeric, (prev.cancelled)::numeric) AS cancelled_chg_pct
   FROM (curr
     CROSS JOIN prev);
CREATE OR REPLACE VIEW public.vw_etown_daily_invites_by_channel AS
 WITH curr AS (
         SELECT etown_daily_metrics.metric_date,
            etown_daily_metrics.total_users,
            etown_daily_metrics.total_members,
            etown_daily_metrics.total_providers,
            etown_daily_metrics.active_users,
            etown_daily_metrics.verified_providers,
            etown_daily_metrics.unverified_providers,
            etown_daily_metrics.verified_members,
            etown_daily_metrics.unverified_members,
            etown_daily_metrics.total_bookings,
            etown_daily_metrics.invites_shared,
            etown_daily_metrics.invite_acceptance_rate,
            etown_daily_metrics.internal_invites,
            etown_daily_metrics.booking_invites,
            etown_daily_metrics.added_to_mycircle_invites,
            etown_daily_metrics.externalapp_share_invites,
            etown_daily_metrics.qr_invites,
            etown_daily_metrics.external_invites,
            etown_daily_metrics.total_posts,
            etown_daily_metrics.total_offers,
            etown_daily_metrics.chaatie_queries_count,
            etown_daily_metrics.town_viewed
           FROM etown_daily_metrics
          WHERE (etown_daily_metrics.metric_date = (CURRENT_DATE - 1))
        ), prev AS (
         SELECT etown_daily_metrics.metric_date,
            etown_daily_metrics.total_users,
            etown_daily_metrics.total_members,
            etown_daily_metrics.total_providers,
            etown_daily_metrics.active_users,
            etown_daily_metrics.verified_providers,
            etown_daily_metrics.unverified_providers,
            etown_daily_metrics.verified_members,
            etown_daily_metrics.unverified_members,
            etown_daily_metrics.total_bookings,
            etown_daily_metrics.invites_shared,
            etown_daily_metrics.invite_acceptance_rate,
            etown_daily_metrics.internal_invites,
            etown_daily_metrics.booking_invites,
            etown_daily_metrics.added_to_mycircle_invites,
            etown_daily_metrics.externalapp_share_invites,
            etown_daily_metrics.qr_invites,
            etown_daily_metrics.external_invites,
            etown_daily_metrics.total_posts,
            etown_daily_metrics.total_offers,
            etown_daily_metrics.chaatie_queries_count,
            etown_daily_metrics.town_viewed
           FROM etown_daily_metrics
          WHERE (etown_daily_metrics.metric_date = (CURRENT_DATE - 2))
        )
 SELECT channel,
    curr_count,
    prev_count,
    pct_change((curr_count)::numeric, (prev_count)::numeric) AS chg_pct
   FROM ( SELECT 'Internal'::text AS channel,
            curr.internal_invites AS curr_count,
            prev.internal_invites AS prev_count
           FROM (curr
             CROSS JOIN prev)
        UNION ALL
         SELECT 'Booking'::text,
            curr.booking_invites,
            prev.booking_invites
           FROM (curr
             CROSS JOIN prev)
        UNION ALL
         SELECT 'Added to MyCircle'::text,
            curr.added_to_mycircle_invites,
            prev.added_to_mycircle_invites
           FROM (curr
             CROSS JOIN prev)
        UNION ALL
         SELECT 'External App Share'::text,
            curr.externalapp_share_invites,
            prev.externalapp_share_invites
           FROM (curr
             CROSS JOIN prev)
        UNION ALL
         SELECT 'QR'::text,
            curr.qr_invites,
            prev.qr_invites
           FROM (curr
             CROSS JOIN prev)
        UNION ALL
         SELECT 'External'::text,
            curr.external_invites,
            prev.external_invites
           FROM (curr
             CROSS JOIN prev)) x
  ORDER BY curr_count DESC;
CREATE OR REPLACE VIEW public.vw_etown_daily_kpis AS
 WITH curr AS (
         SELECT etown_daily_metrics.metric_date,
            etown_daily_metrics.total_users,
            etown_daily_metrics.total_members,
            etown_daily_metrics.total_providers,
            etown_daily_metrics.active_users,
            etown_daily_metrics.verified_providers,
            etown_daily_metrics.unverified_providers,
            etown_daily_metrics.verified_members,
            etown_daily_metrics.unverified_members,
            etown_daily_metrics.total_bookings,
            etown_daily_metrics.invites_shared,
            etown_daily_metrics.invite_acceptance_rate,
            etown_daily_metrics.internal_invites,
            etown_daily_metrics.booking_invites,
            etown_daily_metrics.added_to_mycircle_invites,
            etown_daily_metrics.externalapp_share_invites,
            etown_daily_metrics.qr_invites,
            etown_daily_metrics.external_invites,
            etown_daily_metrics.total_posts,
            etown_daily_metrics.total_offers,
            etown_daily_metrics.chaatie_queries_count,
            etown_daily_metrics.town_viewed
           FROM etown_daily_metrics
          WHERE (etown_daily_metrics.metric_date = (CURRENT_DATE - 1))
        ), prev AS (
         SELECT etown_daily_metrics.metric_date,
            etown_daily_metrics.total_users,
            etown_daily_metrics.total_members,
            etown_daily_metrics.total_providers,
            etown_daily_metrics.active_users,
            etown_daily_metrics.verified_providers,
            etown_daily_metrics.unverified_providers,
            etown_daily_metrics.verified_members,
            etown_daily_metrics.unverified_members,
            etown_daily_metrics.total_bookings,
            etown_daily_metrics.invites_shared,
            etown_daily_metrics.invite_acceptance_rate,
            etown_daily_metrics.internal_invites,
            etown_daily_metrics.booking_invites,
            etown_daily_metrics.added_to_mycircle_invites,
            etown_daily_metrics.externalapp_share_invites,
            etown_daily_metrics.qr_invites,
            etown_daily_metrics.external_invites,
            etown_daily_metrics.total_posts,
            etown_daily_metrics.total_offers,
            etown_daily_metrics.chaatie_queries_count,
            etown_daily_metrics.town_viewed
           FROM etown_daily_metrics
          WHERE (etown_daily_metrics.metric_date = (CURRENT_DATE - 2))
        )
 SELECT curr.metric_date AS report_date,
    prev.metric_date AS compare_date,
    curr.total_users,
    prev.total_users AS total_users_prev,
    pct_change((curr.total_users)::numeric, (prev.total_users)::numeric) AS total_users_chg_pct,
    curr.active_users,
    prev.active_users AS active_users_prev,
    pct_change((curr.active_users)::numeric, (prev.active_users)::numeric) AS active_users_chg_pct,
    (curr.verified_members + curr.verified_providers) AS verified_users,
    round(((((curr.verified_members + curr.verified_providers))::numeric / (curr.total_users)::numeric) * (100)::numeric), 1) AS verified_users_pct,
    (prev.verified_members + prev.verified_providers) AS verified_users_prev,
    pct_change(((curr.verified_members + curr.verified_providers))::numeric, ((prev.verified_members + prev.verified_providers))::numeric) AS verified_users_chg_pct,
    (curr.unverified_members + curr.unverified_providers) AS non_verified_users,
    round(((((curr.unverified_members + curr.unverified_providers))::numeric / (curr.total_users)::numeric) * (100)::numeric), 1) AS non_verified_users_pct,
    (prev.unverified_members + prev.unverified_providers) AS non_verified_users_prev,
    pct_change(((curr.unverified_members + curr.unverified_providers))::numeric, ((prev.unverified_members + prev.unverified_providers))::numeric) AS non_verified_users_chg_pct,
    curr.total_members,
    prev.total_members AS total_members_prev,
    pct_change((curr.total_members)::numeric, (prev.total_members)::numeric) AS total_members_chg_pct,
    curr.verified_members,
    round((((curr.verified_members)::numeric / (curr.total_members)::numeric) * (100)::numeric), 1) AS verified_members_pct,
    prev.verified_members AS verified_members_prev,
    pct_change((curr.verified_members)::numeric, (prev.verified_members)::numeric) AS verified_members_chg_pct,
    curr.unverified_members,
    round((((curr.unverified_members)::numeric / (curr.total_members)::numeric) * (100)::numeric), 1) AS unverified_members_pct,
    prev.unverified_members AS unverified_members_prev,
    pct_change((curr.unverified_members)::numeric, (prev.unverified_members)::numeric) AS unverified_members_chg_pct,
    curr.total_providers,
    prev.total_providers AS total_providers_prev,
    pct_change((curr.total_providers)::numeric, (prev.total_providers)::numeric) AS total_providers_chg_pct,
    curr.verified_providers,
    round((((curr.verified_providers)::numeric / (curr.total_providers)::numeric) * (100)::numeric), 1) AS verified_providers_pct,
    prev.verified_providers AS verified_providers_prev,
    pct_change((curr.verified_providers)::numeric, (prev.verified_providers)::numeric) AS verified_providers_chg_pct,
    curr.unverified_providers,
    round((((curr.unverified_providers)::numeric / (curr.total_providers)::numeric) * (100)::numeric), 1) AS unverified_providers_pct,
    prev.unverified_providers AS unverified_providers_prev,
    pct_change((curr.unverified_providers)::numeric, (prev.unverified_providers)::numeric) AS unverified_providers_chg_pct,
    curr.invites_shared AS invites_sent,
    prev.invites_shared AS invites_sent_prev,
    pct_change((curr.invites_shared)::numeric, (prev.invites_shared)::numeric) AS invites_sent_chg_pct,
    curr.invite_acceptance_rate,
    prev.invite_acceptance_rate AS invite_acceptance_rate_prev,
    curr.total_posts AS provider_posts,
    prev.total_posts AS provider_posts_prev,
    pct_change((curr.total_posts)::numeric, (prev.total_posts)::numeric) AS provider_posts_chg_pct,
    curr.total_offers AS offers_created,
    prev.total_offers AS offers_created_prev,
    pct_change((curr.total_offers)::numeric, (prev.total_offers)::numeric) AS offers_created_chg_pct,
    curr.chaatie_queries_count AS ai_conversations,
    prev.chaatie_queries_count AS ai_conversations_prev,
    pct_change((curr.chaatie_queries_count)::numeric, (prev.chaatie_queries_count)::numeric) AS ai_conversations_chg_pct,
    curr.town_viewed,
    prev.town_viewed AS town_viewed_prev,
    pct_change((curr.town_viewed)::numeric, (prev.town_viewed)::numeric) AS town_viewed_chg_pct
   FROM (curr
     CROSS JOIN prev);
CREATE OR REPLACE VIEW public.vw_etown_fos_agent_daily_summary AS
 SELECT s.capture_date_ist,
    s.agent_name,
    s.captures,
    s.live_captures,
    s.first_capture_ist,
    s.last_capture_ist,
    s.active_minutes,
    s.median_gap_min,
    s.gaps_over_8_min,
    s.km_walked,
    s.approved,
    s.rejected,
    s.review_pending,
    s.app_installed,
    s.owner_verified,
    s.real_services,
    s.placeholder_services,
    s.with_email,
    s.with_alt_mobile,
    s.avg_quality_score,
    s.new_to_etown,
    s.repeated_numbers,
    s.areas,
    s.capture_sources,
    COALESCE(t.test_captures, (0)::bigint) AS test_captures
   FROM (( SELECT vw_etown_fos_capture_report.capture_date_ist,
            vw_etown_fos_capture_report.agent_name,
            count(*) AS captures,
            count(*) FILTER (WHERE (NOT vw_etown_fos_capture_report.deleted)) AS live_captures,
            (min(vw_etown_fos_capture_report.captured_at_ist))::time without time zone AS first_capture_ist,
            (max(vw_etown_fos_capture_report.captured_at_ist))::time without time zone AS last_capture_ist,
            round((EXTRACT(epoch FROM (max(vw_etown_fos_capture_report.captured_at_ist) - min(vw_etown_fos_capture_report.captured_at_ist))) / 60.0), 0) AS active_minutes,
            round((percentile_cont((0.5)::double precision) WITHIN GROUP (ORDER BY ((vw_etown_fos_capture_report.gap_min)::double precision)))::numeric, 1) AS median_gap_min,
            count(*) FILTER (WHERE (vw_etown_fos_capture_report.gap_min > (8)::numeric)) AS gaps_over_8_min,
            round(COALESCE(sum(vw_etown_fos_capture_report.km_from_prev), (0)::numeric), 2) AS km_walked,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.approved) AS approved,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.rejected) AS rejected,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.review_pending) AS review_pending,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.app_installed) AS app_installed,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.owner_verified) AS owner_verified,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.has_real_services) AS real_services,
            count(*) FILTER (WHERE (NOT vw_etown_fos_capture_report.has_real_services)) AS placeholder_services,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.has_email) AS with_email,
            count(*) FILTER (WHERE vw_etown_fos_capture_report.has_alt_mobile) AS with_alt_mobile,
            round(avg(vw_etown_fos_capture_report.quality_score), 1) AS avg_quality_score,
            count(*) FILTER (WHERE ((NOT vw_etown_fos_capture_report.number_known_before_capture) AND (NOT vw_etown_fos_capture_report.member_before_capture))) AS new_to_etown,
            count(*) FILTER (WHERE (vw_etown_fos_capture_report.same_number_fos_captures > 1)) AS repeated_numbers,
            string_agg(DISTINCT vw_etown_fos_capture_report.area, ', '::text) AS areas,
            string_agg(DISTINCT vw_etown_fos_capture_report.capture_source, ', '::text) AS capture_sources
           FROM vw_etown_fos_capture_report
          WHERE (NOT vw_etown_fos_capture_report.is_test)
          GROUP BY vw_etown_fos_capture_report.capture_date_ist, vw_etown_fos_capture_report.agent_name) s
     LEFT JOIN ( SELECT vw_etown_fos_capture_report.capture_date_ist,
            vw_etown_fos_capture_report.agent_name,
            count(*) AS test_captures
           FROM vw_etown_fos_capture_report
          WHERE vw_etown_fos_capture_report.is_test
          GROUP BY vw_etown_fos_capture_report.capture_date_ist, vw_etown_fos_capture_report.agent_name) t ON (((t.capture_date_ist = s.capture_date_ist) AND (t.agent_name = s.agent_name))));
CREATE OR REPLACE VIEW public.vw_etown_fos_capture_report AS
 WITH fos AS (
         SELECT p.providerid,
            p.businessname,
            p.businesscategory,
            p.address,
            p.statecode,
            p.city,
            p.contactid,
            p.contactidname,
            p.userprofileid,
            p.userprofileidname,
            p.firstname,
            p.email,
            p.creationdate,
            p.mobilenumber,
            p.providertype,
            p.publicsearchprovider,
            p.role,
            p.rolename,
            p.yearsinbusiness,
            p.serviceimageurl,
            p.additionaladdressinfo,
            p.description,
            p.availability,
            p.areaname,
            p.townname,
            p.latitude,
            p.longitude,
            p.content_vector,
            p.trusted,
            p.boosted,
            p.marketing_person_name,
            p.source,
            p.servicetype,
            p.websiteurl,
            p.whatsapp,
            p.content_vector_hash,
            p.modifieddate,
            p.deleted,
            p.reactivation,
            p.deactivated,
            p.deletiondate,
            p.reactivationdate,
            p.deactivationdate,
            p.plan_code,
            p.subscription_status,
            p.plan_started_on,
            p.plan_expires_on,
            p.background_image,
            p.disabled,
            p.alert,
            p.alert_dismissed_on,
            p.approve,
            p.alternatemobile,
            NULLIF("right"(regexp_replace(p.mobilenumber, '[^0-9]'::text, ''::text, 'g'::text), 10), ''::text) AS mobile10,
            initcap(btrim(COALESCE(NULLIF(btrim(p.marketing_person_name), ''::text), 'Unknown'::text))) AS agent_name,
            ((p.creationdate AT TIME ZONE 'UTC'::text) AT TIME ZONE 'Asia/Kolkata'::text) AS captured_at_ist
           FROM etown_provider p
          WHERE (p.source = ANY (ARRAY['FOS'::text, 'CSR'::text, 'STAFF'::text]))
        ), enriched AS (
         SELECT f.providerid,
            f.businessname,
            f.businesscategory,
            f.address,
            f.statecode,
            f.city,
            f.contactid,
            f.contactidname,
            f.userprofileid,
            f.userprofileidname,
            f.firstname,
            f.email,
            f.creationdate,
            f.mobilenumber,
            f.providertype,
            f.publicsearchprovider,
            f.role,
            f.rolename,
            f.yearsinbusiness,
            f.serviceimageurl,
            f.additionaladdressinfo,
            f.description,
            f.availability,
            f.areaname,
            f.townname,
            f.latitude,
            f.longitude,
            f.content_vector,
            f.trusted,
            f.boosted,
            f.marketing_person_name,
            f.source,
            f.servicetype,
            f.websiteurl,
            f.whatsapp,
            f.content_vector_hash,
            f.modifieddate,
            f.deleted,
            f.reactivation,
            f.deactivated,
            f.deletiondate,
            f.reactivationdate,
            f.deactivationdate,
            f.plan_code,
            f.subscription_status,
            f.plan_started_on,
            f.plan_expires_on,
            f.background_image,
            f.disabled,
            f.alert,
            f.alert_dismissed_on,
            f.approve,
            f.alternatemobile,
            f.mobile10,
            f.agent_name,
            f.captured_at_ist,
            svc.normalized_category,
            svc.services_text,
            COALESCE(svc.has_real_services, false) AS has_real_services,
            rv.review_state,
            rv.review_created_at_ist,
            COALESCE(rv.review_pending, false) AS review_pending,
            rv.rejected_reason,
            own.contact_created_at,
            (COALESCE(own.contact_verified, false) OR prof.profile_verified) AS owner_verified,
            dev.first_device_at,
            prev.previous_listing_sources,
            dup.same_number_fos_captures,
            dup.same_number_captured_by,
            (EXISTS ( SELECT 1
                   FROM etown_member m
                  WHERE ((m.mobilenumber IS NOT NULL) AND ("right"(regexp_replace(COALESCE(m.mobilenumber, ''::text), '\D'::text, ''::text, 'g'::text), 10) = f.mobile10) AND (m.dateofcreation < (f.creationdate - '00:05:00'::interval))))) AS member_before_capture,
            lag(f.creationdate) OVER w AS prev_creationdate,
            lag(f.latitude) OVER w AS prev_latitude,
            lag(f.longitude) OVER w AS prev_longitude
           FROM (((((((fos f
             LEFT JOIN LATERAL ( SELECT string_agg(DISTINCT s.normalized_category, ', '::text) AS normalized_category,
                    string_agg(DISTINCT "left"(s.services, 80), ' | '::text) AS services_text,
                    bool_or((((jsonb_typeof(s.serviceitems) = 'array'::text) AND (jsonb_array_length(s.serviceitems) > 0)) OR (lower(btrim(COALESCE(s.services, ''::text))) <> ALL (ARRAY[''::text, '1'::text, 'test'::text])))) AS has_real_services
                   FROM etown_providerservices s
                  WHERE ((s.providerid = f.providerid) AND (lower(COALESCE(s.deleted, 'false'::text)) <> 'true'::text))) svc ON (true))
             LEFT JOIN LATERAL ( SELECT string_agg(((COALESCE(NULLIF(btrim(r.review), ''::text), 'pending'::text) || '/'::text) || COALESCE(r.action_mode, ''::text)), '; '::text ORDER BY r.createdon) AS review_state,
                    ((min(r.createdon) AT TIME ZONE 'UTC'::text) AT TIME ZONE 'Asia/Kolkata'::text) AS review_created_at_ist,
                    bool_or((lower(btrim(COALESCE(r.review, ''::text))) <> ALL (ARRAY['approved'::text, '1'::text, 'rejected'::text, '2'::text]))) AS review_pending,
                    string_agg(NULLIF(btrim(r.rejectedreason), ''::text), '; '::text) AS rejected_reason
                   FROM etown_provider_review r
                  WHERE (r.providerid = f.providerid)) rv ON (true))
             LEFT JOIN LATERAL ( SELECT min(c.createdon) AS contact_created_at,
                    bool_or((c.verified IS TRUE)) AS contact_verified,
                    array_agg(c.contactid) AS contactids
                   FROM etown_contact c
                  WHERE ((c.contactid = f.contactid) OR ((c.mobilephone IS NOT NULL) AND ("right"(regexp_replace(COALESCE(c.mobilephone, ''::text), '\D'::text, ''::text, 'g'::text), 10) = f.mobile10)))) own ON (true))
             LEFT JOIN LATERAL ( SELECT (EXISTS ( SELECT 1
                           FROM etown_userprofile u
                          WHERE ((u.contactid = ANY (COALESCE(own.contactids, ARRAY[f.contactid]))) AND (u.verified IS TRUE)))) AS profile_verified) prof ON (true))
             LEFT JOIN LATERAL ( SELECT min(d.createdon) AS first_device_at
                   FROM etown_contactdevices d
                  WHERE ((d.contactid IS NOT NULL) AND (d.contactid = ANY (COALESCE(own.contactids, ARRAY[f.contactid]))))) dev ON (true))
             LEFT JOIN LATERAL ( SELECT string_agg(DISTINCT COALESCE(o.source, '?'::text), ', '::text) AS previous_listing_sources
                   FROM etown_provider o
                  WHERE (("right"(regexp_replace(o.mobilenumber, '[^0-9]'::text, ''::text, 'g'::text), 10) = f.mobile10) AND (o.providerid <> f.providerid) AND (o.creationdate < f.creationdate))) prev ON (true))
             LEFT JOIN LATERAL ( SELECT count(*) AS same_number_fos_captures,
                    string_agg(DISTINCT o.marketing_person_name, ', '::text) FILTER (WHERE (o.providerid <> f.providerid)) AS same_number_captured_by
                   FROM etown_provider o
                  WHERE (("right"(regexp_replace(o.mobilenumber, '[^0-9]'::text, ''::text, 'g'::text), 10) = f.mobile10) AND (o.source = ANY (ARRAY['FOS'::text, 'CSR'::text, 'STAFF'::text])))) dup ON (true))
          WINDOW w AS (PARTITION BY f.agent_name, ((f.captured_at_ist)::date) ORDER BY f.creationdate)
        )
 SELECT (captured_at_ist)::date AS capture_date_ist,
    agent_name,
    marketing_person_name AS agent_name_stored,
    captured_at_ist,
    businessname,
    mobile10 AS mobile,
    NULLIF(btrim(alternatemobile), ''::text) AS alternatemobile,
    businesscategory AS category,
    servicetype,
    normalized_category,
    services_text,
    areaname AS area,
    townname AS town,
    city,
    latitude,
    longitude,
    NULLIF(btrim(email), ''::text) AS email,
    (approve IS TRUE) AS approved,
        CASE
            WHEN (approve IS TRUE) THEN ((modifieddate AT TIME ZONE 'UTC'::text) AT TIME ZONE 'Asia/Kolkata'::text)
            ELSE NULL::timestamp without time zone
        END AS approved_at_ist,
    (approve IS FALSE) AS rejected,
        CASE
            WHEN (approve IS FALSE) THEN ((modifieddate AT TIME ZONE 'UTC'::text) AT TIME ZONE 'Asia/Kolkata'::text)
            ELSE NULL::timestamp without time zone
        END AS rejected_at_ist,
    review_state,
    review_created_at_ist,
    review_pending,
    rejected_reason,
    (first_device_at IS NOT NULL) AS app_installed,
    ((first_device_at AT TIME ZONE 'UTC'::text) AT TIME ZONE 'Asia/Kolkata'::text) AS installed_at_ist,
    owner_verified,
    providertype,
    plan_code,
    (NULLIF(btrim(COALESCE(businesscategory, ''::text)), ''::text) IS NOT NULL) AS has_category,
    (NULLIF(btrim(COALESCE(servicetype, ''::text)), ''::text) IS NOT NULL) AS has_servicetype,
    (length(btrim(COALESCE(description, ''::text))) >= 20) AS has_description,
    has_real_services,
    ((latitude IS NOT NULL) AND (longitude IS NOT NULL)) AS has_gps,
    (NULLIF(btrim(COALESCE(areaname, address, ''::text)), ''::text) IS NOT NULL) AS has_area,
    (NULLIF(btrim(COALESCE(email, ''::text)), ''::text) IS NOT NULL) AS has_email,
    (NULLIF(btrim(COALESCE(alternatemobile, ''::text)), ''::text) IS NOT NULL) AS has_alt_mobile,
    (NULLIF(btrim(COALESCE(serviceimageurl, background_image, ''::text)), ''::text) IS NOT NULL) AS has_logo_or_image,
    ((((((((((NULLIF(btrim(COALESCE(businesscategory, ''::text)), ''::text) IS NOT NULL))::integer + ((NULLIF(btrim(COALESCE(servicetype, ''::text)), ''::text) IS NOT NULL))::integer) + ((length(btrim(COALESCE(description, ''::text))) >= 20))::integer) + (has_real_services)::integer) + (((latitude IS NOT NULL) AND (longitude IS NOT NULL)))::integer) + ((NULLIF(btrim(COALESCE(areaname, address, ''::text)), ''::text) IS NOT NULL))::integer) + ((NULLIF(btrim(COALESCE(email, ''::text)), ''::text) IS NOT NULL))::integer) + ((NULLIF(btrim(COALESCE(alternatemobile, ''::text)), ''::text) IS NOT NULL))::integer) + ((NULLIF(btrim(COALESCE(serviceimageurl, background_image, ''::text)), ''::text) IS NOT NULL))::integer) AS quality_score,
    COALESCE(same_number_fos_captures, (1)::bigint) AS same_number_fos_captures,
    same_number_captured_by,
    (previous_listing_sources IS NOT NULL) AS number_known_before_capture,
    previous_listing_sources,
    COALESCE((contact_created_at < (creationdate - '00:05:00'::interval)), false) AS contact_before_capture,
    member_before_capture,
    round((EXTRACT(epoch FROM (creationdate - prev_creationdate)) / 60.0), 1) AS gap_min,
    round(((((6371.0 * (2)::numeric))::double precision * asin(sqrt((power(sin((radians((latitude - prev_latitude)) / (2)::double precision)), (2)::double precision) + ((cos(radians(prev_latitude)) * cos(radians(latitude))) * power(sin((radians((longitude - prev_longitude)) / (2)::double precision)), (2)::double precision)))))))::numeric, 3) AS km_from_prev,
    (lower(COALESCE(deleted, 'false'::text)) = 'true'::text) AS deleted,
    disabled,
    providerid,
    contactid,
    userprofileid,
    source AS capture_source,
    ((businessname ~* '\mtest\M'::text) OR (mobile10 IN ( SELECT sn.mobilenumber
           FROM etown_staff_numbers sn))) AS is_test
   FROM enriched e;
CREATE OR REPLACE VIEW public.vw_etown_provider_onboarding_by_town AS
 WITH curr AS (
         SELECT etown_provider.townname,
            count(*) AS new_providers
           FROM etown_provider
          WHERE ((etown_provider.creationdate)::date = (CURRENT_DATE - 1))
          GROUP BY etown_provider.townname
        ), prev AS (
         SELECT etown_provider.townname,
            count(*) AS new_providers
           FROM etown_provider
          WHERE ((etown_provider.creationdate)::date = (CURRENT_DATE - 2))
          GROUP BY etown_provider.townname
        )
 SELECT rank() OVER (ORDER BY c.new_providers DESC) AS rank,
    c.townname AS town,
    c.new_providers AS new_providers_curr,
    COALESCE(pr.new_providers, (0)::bigint) AS new_providers_prev,
    pct_change((c.new_providers)::numeric, (pr.new_providers)::numeric) AS chg_pct
   FROM (curr c
     LEFT JOIN prev pr ON ((pr.townname = c.townname)))
  ORDER BY c.new_providers DESC
 LIMIT 5;
CREATE OR REPLACE VIEW public.vw_etown_rep_performance AS
 WITH members_onboarded AS (
         SELECT etown_member.marketing_person_name AS rep_name,
            count(*) AS members_onboarded
           FROM etown_member
          WHERE ((etown_member.dateofcreation)::date = (CURRENT_DATE - 1))
          GROUP BY etown_member.marketing_person_name
        ), providers_onboarded AS (
         SELECT etown_provider.marketing_person_name AS rep_name,
            count(*) AS providers_onboarded
           FROM etown_provider
          WHERE ((etown_provider.creationdate)::date = (CURRENT_DATE - 1))
          GROUP BY etown_provider.marketing_person_name
        ), invites AS (
         SELECT COALESCE(m.marketing_person_name, p.marketing_person_name) AS rep_name,
            count(*) AS invites_sent,
            count(*) FILTER (WHERE (i.status ~~* 'accepted%'::text)) AS invites_accepted
           FROM ((etown_invitation i
             LEFT JOIN etown_member m ON ((m.userprofileid = i.inviteruserprofile)))
             LEFT JOIN etown_provider p ON ((p.userprofileid = i.inviteruserprofile)))
          WHERE ((i.createdon)::date = (CURRENT_DATE - 1))
          GROUP BY COALESCE(m.marketing_person_name, p.marketing_person_name)
        )
 SELECT COALESCE(mo.rep_name, po.rep_name, iv.rep_name) AS rep_name,
    COALESCE(mo.members_onboarded, (0)::bigint) AS members_onboarded,
    COALESCE(po.providers_onboarded, (0)::bigint) AS providers_onboarded,
    COALESCE(iv.invites_sent, (0)::bigint) AS invites_sent,
    COALESCE(iv.invites_accepted, (0)::bigint) AS invites_accepted
   FROM ((members_onboarded mo
     FULL JOIN providers_onboarded po ON ((po.rep_name = mo.rep_name)))
     FULL JOIN invites iv ON ((iv.rep_name = COALESCE(mo.rep_name, po.rep_name))))
  WHERE (COALESCE(mo.rep_name, po.rep_name, iv.rep_name) IS NOT NULL)
  ORDER BY (COALESCE(mo.members_onboarded, (0)::bigint) + COALESCE(po.providers_onboarded, (0)::bigint)) DESC;
CREATE OR REPLACE VIEW public.vw_etown_top_providers_viewed AS
 WITH curr AS (
         SELECT v.providerprofileid,
            p.businessname,
            p.townname,
            count(DISTINCT v.userprofileid) AS views
           FROM ((etown_provider_views v
             JOIN etown_provider p ON ((p.userprofileid = v.providerprofileid)))
             LEFT JOIN etown_userprofile up ON ((up.userprofileid = v.userprofileid)))
          WHERE (((v.created_on)::date = (CURRENT_DATE - 1)) AND (v.userprofileid IS NOT NULL) AND (v.userprofileid <> v.providerprofileid) AND (NOT etown_is_deleted(v.userprofileid)) AND (NOT etown_is_deleted(up.contactid)))
          GROUP BY v.providerprofileid, p.businessname, p.townname
        ), prev AS (
         SELECT v.providerprofileid,
            count(DISTINCT v.userprofileid) AS views
           FROM (etown_provider_views v
             LEFT JOIN etown_userprofile up ON ((up.userprofileid = v.userprofileid)))
          WHERE (((v.created_on)::date = (CURRENT_DATE - 2)) AND (v.userprofileid IS NOT NULL) AND (v.userprofileid <> v.providerprofileid) AND (NOT etown_is_deleted(v.userprofileid)) AND (NOT etown_is_deleted(up.contactid)))
          GROUP BY v.providerprofileid
        )
 SELECT rank() OVER (ORDER BY c.views DESC) AS rank,
    c.businessname AS provider_name,
    c.townname AS town,
    c.views AS views_curr,
    COALESCE(pr.views, (0)::bigint) AS views_prev,
    pct_change((c.views)::numeric, (pr.views)::numeric) AS chg_pct
   FROM (curr c
     LEFT JOIN prev pr ON ((pr.providerprofileid = c.providerprofileid)))
  ORDER BY c.views DESC
 LIMIT 5;
CREATE OR REPLACE VIEW public.vw_etown_top_towns_viewed AS
 WITH curr AS (
         SELECT tv.townprofileid,
            t.townname,
            count(*) AS views
           FROM (etown_town_view_tracking tv
             JOIN etown_townprofile t ON ((t.townprofileid = tv.townprofileid)))
          WHERE (tv.view_date = (CURRENT_DATE - 1))
          GROUP BY tv.townprofileid, t.townname
        ), prev AS (
         SELECT etown_town_view_tracking.townprofileid,
            count(*) AS views
           FROM etown_town_view_tracking
          WHERE (etown_town_view_tracking.view_date = (CURRENT_DATE - 2))
          GROUP BY etown_town_view_tracking.townprofileid
        )
 SELECT rank() OVER (ORDER BY c.views DESC) AS rank,
    c.townname AS town,
    c.views AS views_curr,
    COALESCE(pr.views, (0)::bigint) AS views_prev,
    pct_change((c.views)::numeric, (pr.views)::numeric) AS chg_pct
   FROM (curr c
     LEFT JOIN prev pr ON ((pr.townprofileid = c.townprofileid)))
  ORDER BY c.views DESC
 LIMIT 5;
CREATE OR REPLACE VIEW public.vw_etown_uninstalled_user_tenure AS
 WITH first_device AS (
         SELECT etown_contactdevices.contactid,
            min(etown_contactdevices.createdon) AS first_registered_on
           FROM etown_contactdevices
          WHERE (NULLIF(etown_contactdevices.contactid, ''::text) IS NOT NULL)
          GROUP BY etown_contactdevices.contactid
        ), tenure AS (
         SELECT v.contactid,
            COALESCE(NULLIF(c.firstname, ''::text), NULLIF(v.contactidname, ''::text)) AS user_name,
            (COALESCE(fd.first_registered_on, c.createdon) AT TIME ZONE 'UTC'::text) AS first_installed_at,
            (c.createdon AT TIME ZONE 'UTC'::text) AS account_created_at,
            v.last_detected_at AS uninstalled_at,
            v.first_detected_at,
            v.last_seen_at,
            v.devices_uninstalled
           FROM ((etown_app_uninstalled_users v
             LEFT JOIN etown_contact c ON ((c.contactid = v.contactid)))
             LEFT JOIN first_device fd ON ((fd.contactid = v.contactid)))
        )
 SELECT contactid,
    user_name,
    first_installed_at,
    account_created_at,
    uninstalled_at,
    first_detected_at,
    last_seen_at,
    devices_uninstalled,
    round((EXTRACT(epoch FROM (uninstalled_at - first_installed_at)) / 86400.0), 1) AS days_installed,
    round((EXTRACT(epoch FROM (last_seen_at - first_installed_at)) / 86400.0), 1) AS days_active
   FROM tenure
  ORDER BY uninstalled_at DESC;
CREATE OR REPLACE VIEW public.vw_six_month_user_report AS
 WITH months AS (
         SELECT (generate_series(('2026-01-01'::date)::timestamp with time zone, ('2026-06-01'::date)::timestamp with time zone, '1 mon'::interval))::date AS month_start
        ), user_summary AS (
         SELECT m.month_start,
            count(c.contactid) AS total_users,
            count(*) FILTER (WHERE (c.verified = true)) AS verified_users
           FROM (months m
             LEFT JOIN etown_contact c ON ((c.createdon < (m.month_start + '1 mon'::interval))))
          GROUP BY m.month_start
        ), daily_active AS (
         SELECT (date_trunc('month'::text, etown_activeusers.createdon))::date AS month_start,
            (etown_activeusers.createdon)::date AS activity_date,
            count(DISTINCT etown_activeusers.contactid) AS active_users
           FROM etown_activeusers
          WHERE ((etown_activeusers.createdon >= '2026-01-01'::date) AND (etown_activeusers.createdon < '2026-07-01'::date))
          GROUP BY (date_trunc('month'::text, etown_activeusers.createdon)), ((etown_activeusers.createdon)::date)
        ), active_summary AS (
         SELECT daily_active.month_start,
            (round(avg(daily_active.active_users), 0))::integer AS avg_daily_active
           FROM daily_active
          GROUP BY daily_active.month_start
        ), base AS (
         SELECT m.month_start,
            to_char((m.month_start)::timestamp with time zone, 'Mon'::text) AS month,
            us.total_users,
            COALESCE(a.avg_daily_active, 0) AS avg_daily_active,
            round(
                CASE
                    WHEN (us.total_users = 0) THEN (0)::numeric
                    ELSE (((us.verified_users)::numeric * 100.0) / (us.total_users)::numeric)
                END, 1) AS verified_percent
           FROM ((months m
             LEFT JOIN user_summary us ON ((m.month_start = us.month_start)))
             LEFT JOIN active_summary a ON ((m.month_start = a.month_start)))
        )
 SELECT month,
    total_users,
    round(((((total_users - lag(total_users) OVER (ORDER BY month_start)))::numeric * 100.0) / (NULLIF(lag(total_users) OVER (ORDER BY month_start), 0))::numeric), 1) AS total_users_growth_pct,
    avg_daily_active,
    round(((((avg_daily_active - lag(avg_daily_active) OVER (ORDER BY month_start)))::numeric * 100.0) / (NULLIF(lag(avg_daily_active) OVER (ORDER BY month_start), 0))::numeric), 1) AS active_growth_pct,
    verified_percent,
    round((verified_percent - lag(verified_percent) OVER (ORDER BY month_start)), 1) AS verified_pp_change
   FROM base
  ORDER BY month_start;
CREATE OR REPLACE VIEW public.weekly_unverified_provider_search AS
 WITH week_bounds AS (
         SELECT (date_trunc('week'::text, (now() AT TIME ZONE 'Asia/Kolkata'::text)))::date AS week_start,
            ((date_trunc('week'::text, (now() AT TIME ZONE 'Asia/Kolkata'::text)))::date + 7) AS week_end_exclusive
        ), live_unverified AS (
         SELECT p.providerid,
            p.userprofileid,
            p.contactid,
            p.businessname,
            p.mobilenumber,
            p.businesscategory,
            p.servicetype,
            p.townname,
            p.areaname,
            p.city,
            p.providertype,
            p.source,
            p.marketing_person_name,
            p.creationdate
           FROM etown_provider p
          WHERE ((p.providertype IS DISTINCT FROM 'etown_verified'::text) AND (p.deleted IS DISTINCT FROM 'true'::text) AND ((p.deactivated IS DISTINCT FROM 'true'::text) OR (p.reactivation = 'true'::text)) AND (NOT p.disabled))
        ), searched AS (
         SELECT a.providerid,
            sum(a.appearances) AS search_appearances,
            count(DISTINCT a.contact_id) AS distinct_searchers,
            min(a.best_position) AS best_position,
            (array_agg(DISTINCT a.query))[1:10] AS search_terms,
            array_agg(DISTINCT a.endpoint) AS search_surfaces,
            min(a.first_seen_at) AS first_searched_at,
            max(a.last_seen_at) AS last_searched_at
           FROM (etown_provider_search_appearances a
             CROSS JOIN week_bounds w_1)
          WHERE ((a.search_date >= w_1.week_start) AND (a.search_date < w_1.week_end_exclusive))
          GROUP BY a.providerid
        ), opened AS (
         SELECT v.providerprofileid,
            count(DISTINCT v.userprofileid) AS profile_opens,
            min(v.created_on) AS first_opened_at,
            max(v.created_on) AS last_opened_at
           FROM ((etown_provider_views v
             LEFT JOIN etown_userprofile up ON ((up.userprofileid = v.userprofileid)))
             CROSS JOIN week_bounds w_1)
          WHERE ((v.source = 'profile_details'::text) AND (v.created_on >= ((w_1.week_start)::timestamp without time zone AT TIME ZONE 'Asia/Kolkata'::text)) AND (v.created_on < ((w_1.week_end_exclusive)::timestamp without time zone AT TIME ZONE 'Asia/Kolkata'::text)) AND (v.userprofileid IS NOT NULL) AND (v.userprofileid <> v.providerprofileid) AND (NOT etown_is_deleted(v.userprofileid)) AND (NOT etown_is_deleted(up.contactid)))
          GROUP BY v.providerprofileid
        )
 SELECT w.week_start,
    (w.week_end_exclusive - 1) AS week_end,
    lu.providerid,
    lu.userprofileid,
    lu.contactid,
    lu.businessname,
    lu.mobilenumber,
    lu.businesscategory,
    lu.servicetype,
    lu.townname,
    lu.areaname,
    lu.city,
    lu.providertype,
    lu.source,
    lu.marketing_person_name,
    lu.creationdate,
    COALESCE(s.search_appearances, (0)::bigint) AS search_appearances,
    COALESCE(s.distinct_searchers, (0)::bigint) AS distinct_searchers,
    s.best_position,
    COALESCE(s.search_terms, ARRAY[]::text[]) AS search_terms,
    COALESCE(s.search_surfaces, ARRAY[]::text[]) AS search_surfaces,
    COALESCE(o.profile_opens, (0)::bigint) AS profile_opens,
    (COALESCE(s.search_appearances, (0)::bigint) + COALESCE(o.profile_opens, (0)::bigint)) AS total_engagement,
    LEAST(s.first_searched_at, o.first_opened_at) AS first_seen_at,
    GREATEST(s.last_searched_at, o.last_opened_at) AS last_seen_at
   FROM (((live_unverified lu
     CROSS JOIN week_bounds w)
     LEFT JOIN searched s ON ((s.providerid = lu.providerid)))
     LEFT JOIN opened o ON ((o.providerprofileid = lu.userprofileid)))
  WHERE ((s.providerid IS NOT NULL) OR (o.providerprofileid IS NOT NULL))
  ORDER BY (COALESCE(s.search_appearances, (0)::bigint) + COALESCE(o.profile_opens, (0)::bigint)) DESC, lu.businessname;

-- ============================================================
-- Schema: notifications
-- ============================================================

CREATE SCHEMA IF NOT EXISTS notifications;

CREATE TABLE IF NOT EXISTS notifications.etown_app_events (
id bigint NOT NULL,
userprofileid text NOT NULL,
event_type text NOT NULL,
event_date date NOT NULL,
dedupe_key text NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_booking_alert_excluded_mobiles (
mobile_number text NOT NULL,
enabled boolean NOT NULL DEFAULT true,
display_name text NULL,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
updated_by text NULL DEFAULT 'system'::text,
    PRIMARY KEY (mobile_number)
);
CREATE TABLE IF NOT EXISTS notifications.etown_booking_alert_sms_recipients (
mobile_number text NOT NULL,
enabled boolean NOT NULL DEFAULT true,
display_name text NULL,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
updated_by text NULL DEFAULT 'system'::text,
    PRIMARY KEY (mobile_number)
);
CREATE TABLE IF NOT EXISTS notifications.etown_feed_last_seen (
userprofileid text NOT NULL,
last_seen_feed_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (userprofileid)
);
CREATE TABLE IF NOT EXISTS notifications.etown_feed_nudge_queue (
contactid text NOT NULL,
userprofileid text NOT NULL,
opened_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (contactid)
);
CREATE TABLE IF NOT EXISTS notifications.etown_member_provider_wa_nudge (
id uuid NOT NULL DEFAULT gen_random_uuid(),
customer_userprofileid text NOT NULL,
customer_contactid text NOT NULL,
provider_userprofileid text NOT NULL,
provider_contactid text NOT NULL,
mobilenumber text NULL,
businessname text NULL,
sent_at timestamp with time zone NOT NULL DEFAULT now(),
status text NOT NULL DEFAULT 'sent'::text,
error_detail text NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_campaign_runs (
campaign_id text NOT NULL,
claimed_at timestamp with time zone NOT NULL DEFAULT now(),
completed_at timestamp with time zone NULL,
sent_count integer NULL,
    PRIMARY KEY (campaign_id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_config (
notification_type text NOT NULL,
enabled boolean NOT NULL DEFAULT false,
channels text[] NOT NULL DEFAULT '{}'::text[],
priority smallint NOT NULL DEFAULT 50,
per_user_daily_cap_override integer NULL,
can_preempt_cap boolean NOT NULL DEFAULT false,
respect_quiet_hours boolean NOT NULL DEFAULT true,
defer_if_quiet boolean NOT NULL DEFAULT true,
target_segments text[] NOT NULL DEFAULT '{}'::text[],
min_tier text NULL,
proximity_km numeric NULL DEFAULT 25,
rollout_percent smallint NOT NULL DEFAULT 100,
cron_ist text NULL,
template_key text NULL,
config jsonb NOT NULL DEFAULT '{}'::jsonb,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
updated_by text NULL,
category text NULL,
queue text NULL,
    PRIMARY KEY (notification_type)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_deferred (
id uuid NOT NULL DEFAULT gen_random_uuid(),
userprofileid text NOT NULL,
payload jsonb NOT NULL,
notification_type text NOT NULL,
channel text NOT NULL,
priority smallint NOT NULL,
deferred_reason text NOT NULL DEFAULT 'quiet_hours'::text,
deliver_after timestamp with time zone NOT NULL,
dedupe_key text NULL,
status text NOT NULL DEFAULT 'pending'::text,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_global_caps (
id text NOT NULL DEFAULT 'global'::text,
push_daily_cap integer NOT NULL DEFAULT 3,
wa_daily_cap integer NOT NULL DEFAULT 2,
wa_weekly_per_business_cap integer NOT NULL DEFAULT 1,
inactivity_throttle_enabled boolean NOT NULL DEFAULT true,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
updated_by text NULL,
inactivity_throttle_exempt_lanes text[] NOT NULL DEFAULT '{social}'::text[],
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_ledger (
id uuid NOT NULL DEFAULT gen_random_uuid(),
userprofileid text NOT NULL,
contactid text NULL,
channel text NOT NULL,
notification_type text NOT NULL,
feature text NULL,
sender_businessid text NULL,
priority smallint NOT NULL DEFAULT 50,
dedupe_key text NULL,
status text NOT NULL,
suppression_reason text NULL,
sent_on date NOT NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
opened_at timestamp with time zone NULL,
wamid text NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_preferences (
userprofileid text NOT NULL,
push_enabled boolean NOT NULL DEFAULT true,
whatsapp_opt_in boolean NOT NULL DEFAULT false,
inapp_enabled boolean NOT NULL DEFAULT true,
whatsapp_stopped_at timestamp with time zone NULL,
push_disabled_at timestamp with time zone NULL,
type_overrides jsonb NOT NULL DEFAULT '{}'::jsonb,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (userprofileid)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_queue (
id uuid NOT NULL DEFAULT gen_random_uuid(),
queue text NOT NULL,
notification_type text NOT NULL,
priority smallint NOT NULL DEFAULT 50,
payload jsonb NOT NULL,
dedupe_key text NULL,
status text NOT NULL DEFAULT 'queued'::text,
attempts smallint NOT NULL DEFAULT 0,
max_attempts smallint NOT NULL DEFAULT 5,
run_after timestamp with time zone NOT NULL DEFAULT now(),
locked_at timestamp with time zone NULL,
last_error text NULL,
created_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_notification_queue_control (
queue text NOT NULL,
paused boolean NOT NULL DEFAULT false,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
updated_by text NULL,
    PRIMARY KEY (queue)
);
CREATE TABLE IF NOT EXISTS notifications.etown_plan_change (
id uuid NOT NULL DEFAULT gen_random_uuid(),
userprofileid text NOT NULL,
providerid text NULL,
contactid text NULL,
from_plan_code text NULL,
to_plan_code text NOT NULL,
from_status text NULL,
to_status text NOT NULL,
plan_started_on date NULL,
plan_expires_on date NULL,
changed_by text NULL,
reason text NULL,
changed_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_plan_refusal (
id uuid NOT NULL DEFAULT gen_random_uuid(),
userprofileid text NULL,
plan_code text NOT NULL,
limit_column text NOT NULL,
limit_value integer NULL,
used_value integer NULL,
refused_at timestamp with time zone NOT NULL DEFAULT now(),
contactid text NULL,
    CHECK (((userprofileid IS NOT NULL) OR (contactid IS NOT NULL))),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_provider_plan_conversation (
userprofileid text NOT NULL,
conversation_id text NOT NULL,
charged_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (userprofileid, conversation_id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_provider_plan_usage (
userprofileid text NOT NULL,
metric text NOT NULL,
period_start date NOT NULL,
used_count integer NOT NULL DEFAULT 0,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CHECK ((used_count >= 0)),
    CHECK ((metric = ANY (ARRAY['offers_created'::text, 'chaatie_generations'::text, 'posts_created'::text, 'whatsapp_sends'::text, 'chattie_conversation'::text]))),
    PRIMARY KEY (userprofileid, metric, period_start)
);
CREATE TABLE IF NOT EXISTS notifications.etown_unverified_provider_wa_nudge (
id uuid NOT NULL DEFAULT gen_random_uuid(),
providerservicesid uuid NOT NULL,
mobilenumber text NOT NULL,
businessname text NULL,
sent_at timestamp with time zone NOT NULL DEFAULT now(),
status text NOT NULL DEFAULT 'sent'::text,
error_detail text NULL,
wamid text NULL,
fallback_for_wamid text NULL,
triggered_by_contactid text NULL,
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_whatsapp_button_clicks (
id uuid NOT NULL DEFAULT gen_random_uuid(),
wamid text NOT NULL,
context_wamid text NULL,
from_number text NOT NULL,
phone_number_id text NULL,
button_text text NULL,
button_id text NULL,
button_type text NOT NULL,
clicked_at timestamp with time zone NOT NULL DEFAULT now(),
raw jsonb NULL,
    CHECK ((button_type = ANY (ARRAY['button'::text, 'button_reply'::text]))),
    PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS notifications.etown_whatsapp_message_status (
id uuid NOT NULL DEFAULT gen_random_uuid(),
wamid text NOT NULL,
status text NOT NULL,
recipient_id text NULL,
phone_number_id text NULL,
wa_timestamp text NULL,
errors jsonb NULL,
received_at timestamp with time zone NOT NULL DEFAULT now(),
raw jsonb NULL,
    PRIMARY KEY (id),
    CHECK ((status = ANY (ARRAY['sent'::text, 'delivered'::text, 'read'::text, 'failed'::text])))
);
CREATE TABLE IF NOT EXISTS notifications.etown_whatsapp_template_config (
template_key text NOT NULL,
enabled boolean NOT NULL DEFAULT true,
display_name text NOT NULL,
description text NULL,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
updated_by text NULL DEFAULT 'system'::text,
    PRIMARY KEY (template_key)
);

CREATE INDEX idx_notif_ledger_contactid_channel_senton_status ON notifications.etown_notification_ledger USING btree (contactid, channel, sent_on, status) WHERE (contactid IS NOT NULL);
CREATE INDEX ix_ledger_user_day ON notifications.etown_notification_ledger USING btree (userprofileid, channel, sent_on) WHERE (status = 'sent'::text);
CREATE INDEX ix_ledger_user_business_week ON notifications.etown_notification_ledger USING btree (userprofileid, sender_businessid, channel) WHERE ((status = 'sent'::text) AND (channel = 'whatsapp'::text));
CREATE INDEX idx_wa_btn_clicks_from_number ON notifications.etown_whatsapp_button_clicks USING btree (from_number);
CREATE INDEX idx_wa_btn_clicks_context_wamid ON notifications.etown_whatsapp_button_clicks USING btree (context_wamid) WHERE (context_wamid IS NOT NULL);
CREATE UNIQUE INDEX ux_ledger_dedupe ON notifications.etown_notification_ledger USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL);
CREATE INDEX ix_deferred_due ON notifications.etown_notification_deferred USING btree (deliver_after) WHERE (status = 'pending'::text);
CREATE INDEX ix_queue_claim ON notifications.etown_notification_queue USING btree (queue, priority, run_after) WHERE (status = ANY (ARRAY['queued'::text, 'retry'::text]));
CREATE INDEX ix_queue_stale ON notifications.etown_notification_queue USING btree (locked_at) WHERE (status = 'processing'::text);
CREATE UNIQUE INDEX ux_queue_dedupe ON notifications.etown_notification_queue USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL);
CREATE INDEX idx_wa_btn_clicks_clicked_at ON notifications.etown_whatsapp_button_clicks USING btree (clicked_at DESC);
CREATE INDEX idx_wa_btn_clicks_button_id ON notifications.etown_whatsapp_button_clicks USING btree (button_id) WHERE (button_id IS NOT NULL);
CREATE INDEX ix_appevents_user_date ON notifications.etown_app_events USING btree (userprofileid, event_date);
CREATE INDEX ix_appevents_dedupe ON notifications.etown_app_events USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL);
CREATE INDEX idx_wa_msg_status_wamid ON notifications.etown_whatsapp_message_status USING btree (wamid);
CREATE INDEX idx_wa_msg_status_status ON notifications.etown_whatsapp_message_status USING btree (status, received_at DESC);
CREATE INDEX idx_ledger_wamid ON notifications.etown_notification_ledger USING btree (wamid) WHERE (wamid IS NOT NULL);
CREATE INDEX ix_unverified_wa_nudge_wamid ON notifications.etown_unverified_provider_wa_nudge USING btree (wamid) WHERE (wamid IS NOT NULL);
CREATE UNIQUE INDEX ux_unverified_wa_nudge_fallback_for_wamid ON notifications.etown_unverified_provider_wa_nudge USING btree (fallback_for_wamid) WHERE (fallback_for_wamid IS NOT NULL);
CREATE INDEX ix_plan_usage_period ON notifications.etown_provider_plan_usage USING btree (period_start);
CREATE INDEX ix_unverified_wa_nudge_mobile_sent ON notifications.etown_unverified_provider_wa_nudge USING btree (mobilenumber, sent_at DESC);
CREATE INDEX ix_unverified_wa_nudge_psid_sent ON notifications.etown_unverified_provider_wa_nudge USING btree (providerservicesid, sent_at DESC);
CREATE INDEX ix_plan_conversation_charged_at ON notifications.etown_provider_plan_conversation USING btree (charged_at);
CREATE INDEX ix_plan_change_provider ON notifications.etown_plan_change USING btree (userprofileid, changed_at DESC);
CREATE INDEX ix_plan_change_at ON notifications.etown_plan_change USING btree (changed_at DESC);
CREATE INDEX ix_plan_refusal_rollup ON notifications.etown_plan_refusal USING btree (plan_code, limit_column, refused_at DESC);
CREATE INDEX ix_plan_usage_metric_period ON notifications.etown_provider_plan_usage USING btree (metric, period_start);
CREATE UNIQUE INDEX ux_member_provider_wa_nudge ON notifications.etown_member_provider_wa_nudge USING btree (customer_userprofileid, provider_userprofileid);
CREATE INDEX ix_member_provider_wa_nudge_provider ON notifications.etown_member_provider_wa_nudge USING btree (provider_userprofileid, sent_at DESC);
CREATE INDEX ix_plan_refusal_contact ON notifications.etown_plan_refusal USING btree (contactid, limit_column, refused_at DESC) WHERE (contactid IS NOT NULL);

-- ============================================================
-- Schema: scheduler
-- ============================================================

CREATE SCHEMA IF NOT EXISTS scheduler;

CREATE TABLE IF NOT EXISTS scheduler.etown_job_config (
job_name text NOT NULL,
enabled boolean NOT NULL DEFAULT true,
hour smallint NOT NULL DEFAULT 9,
minute smallint NOT NULL DEFAULT 0,
params jsonb NOT NULL DEFAULT '{}'::jsonb,
updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CHECK (((hour >= 0) AND (hour <= 23))),
    CHECK (((minute >= 0) AND (minute <= 59))),
    PRIMARY KEY (job_name)
);
