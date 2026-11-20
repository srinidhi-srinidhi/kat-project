"""Embeddings Endpoints - HTTP routes that build and refresh pgvector content_vector embeddings.

HTTP layer for /api/embeddings: generate-embeddings upserts a Gemini embedding for a provider-service payload into etown_providerservices_copy, and the bearer-protected aisearch-embeddings re-embeds one row of a whitelisted table, skipping rows whose source text is unchanged.

Features:
    - Embedding generation: Gemini-based vector embedding creation (768 dimensions)
    - Provider indexing: Upserts provider text embeddings into etown_providerservices_copy.content_vector
    - Table-driven re-embedding: Per-table field mappings choose the columns combined into embedding text
    - Idempotent guard: content_vector_hash skips re-embedding when source text is unchanged
    - Dimension validation: Embedding size verified against EMBEDDING_DIMENSIONS before storage
    - Timezone handling: createdon values normalized to UTC before storage
    - Error handling: Robust embedding failure management with detailed logging

Endpoints:
    POST /generate-embeddings: Create and store vector embeddings for providers
    POST /aisearch-embeddings: Rebuild one row's content_vector from its mapped columns

Classes:
    EmbeddingGenerationRequest(): Provider data model for embedding generation
    EmbeddingGenerationResponse(): Response model with embedding generation results
    AISearchEmbeddingRequest(): Request model for the table-driven re-embed endpoint (table_name, unique_id, force)

Functions:
    generate_embeddings(): Process provider data and generate semantic embeddings
    aisearch_embeddings(): Re-embed a single row, skipping rows whose text is unchanged

Dependencies:
    - fastapi: API framework and HTTP exception handling
    - pydantic: Data validation and serialization
    - app.utils.embeddings: Gemini embedding generation utilities
    - app.core.embedding_config: Embedding dimension configuration
    - app.core.security: Bearer token verification for /aisearch-embeddings
    - app.db.client: Database operations for vector storage

Updates:
    23-Feb-2026: Enhanced documentation with vector search capabilities
    06-Aug-2026: /aisearch-embeddings is now idempotent.

Notes:
    Uses Google Gemini gemini-embedding-001 model for 768-dimensional vectors.
    Integrates with PostgreSQL pgvector extension for efficient semantic search.
    Includes comprehensive data validation for provider information quality.
"""
import hashlib
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime, timezone
import pytz
from dateutil import parser as date_parser

from app.utils.embeddings import generate_embedding, validate_embedding_dimensions
from app.core.embedding_config import EMBEDDING_DIMENSIONS
from app.core.security import verify_token
from app.db.client import Database

logger = logging.getLogger(__name__)

router = APIRouter(tags=["embeddings"])

TABLE_FIELD_MAPPING = {
    "etown_contact": ["firstname", "city"],
    "etown_providerservices_copy": ["businessname", "servicedescription", "address"],
    "etown_member": ["userprofileidname", "contactidname"],
    "etown_provider": ["businessname", "address", "city", "description", "contactidname", "userprofileidname"],
    "etown_townprofile": ["townname", "cityname"]
}

TABLE_ID_COLUMNS = {
    "etown_contact": "contactid",
    "etown_providerservices_copy": "providerservicesid",
    "etown_member": "memberid",
    "etown_provider": "providerid",
    "etown_townprofile": "townprofileid",
}

HASH_COLUMN = "content_vector_hash"
_tables_with_hash_column: dict[str, bool] = {}


class EmbeddingGenerationRequest(BaseModel):
    """Request model for embedding generation."""
    providerservicesid: str
    businessname: str
    businesscategory: Optional[str] = None
    address: Optional[str] = None
    availability: Optional[str] = None
    city: Optional[str] = None
    contactid: Optional[str] = None
    contactidname: Optional[str] = None
    email: Optional[str] = None
    mobilenumber: Optional[str] = None
    pricerange: Optional[str] = None
    providerid: Optional[str] = None
    providertype: Optional[str] = None
    publicsearchprovider: Optional[str] = None
    servicecategory: Optional[str] = None
    servicedescription: Optional[str] = None
    serviceimageurl: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    services: Optional[str] = None
    statecode: Optional[str] = None
    userprofileid: Optional[str] = None
    userprofileidname: Optional[str] = None
    websiteurl: Optional[str] = None
    yearsinbusiness: Optional[str] = None
    createdon: Optional[str] = None
    additionaladdressinfo: Optional[str] = None
    areaname: Optional[str] = None
    
    @field_validator('latitude', 'longitude', mode='before')
    @classmethod
    def validate_coordinates(cls, v):
        """Coerce a latitude or longitude to float, mapping None or empty string to None."""
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid coordinate value: {v}")


class EmbeddingGenerationResponse(BaseModel):
    """Response model for embedding generation."""
    status: str
    message: str
    providerservicesid: str
    embedding_generated: bool
    timestamp: datetime


class AISearchEmbeddingRequest(BaseModel):
    """Request model for AI search embedding generation."""
    table_name: str
    unique_id: str
    force: bool = False

    @field_validator('table_name')
    @classmethod
    def validate_table_name(cls, v):
        """Lowercase the table name and require an etown_ prefix, at most 50 chars and identifier characters."""
        if not v or not isinstance(v, str):
            raise ValueError("Table name must be a non-empty string")
        
        v = v.strip().lower()
        
        if not v.startswith("etown_"):
            raise ValueError("Table name must start with 'etown_'")
            
        if len(v) > 50:
            raise ValueError("Table name too long")
            
        import re
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', v):
            raise ValueError("Table name contains invalid characters")
            
        return v
    
    @field_validator('unique_id')
    @classmethod
    def validate_unique_id(cls, v):
        """Strip the unique id and require it to be non-empty and at most 100 chars."""
        if not v or not isinstance(v, str):
            raise ValueError("Unique ID must be a non-empty string")
            
        v = v.strip()
        if not v:
            raise ValueError("Unique ID cannot be empty or whitespace only")
            
        if len(v) > 100:
            raise ValueError("Unique ID too long")
            
        return v


@router.post("/generate-embeddings", response_model=EmbeddingGenerationResponse)
async def generate_embeddings(request: EmbeddingGenerationRequest):
    """Handle POST /api/embeddings/generate-embeddings: embed a provider service and upsert it into etown_providerservices_copy."""
    try:
        logger.info(f"Generating embeddings for provider service: {request.providerservicesid}")
        embedding_fields = []
        
        if request.businessname:
            embedding_fields.append(f"Business: {request.businessname}")
        
        if request.businesscategory:
            embedding_fields.append(f"Category: {request.businesscategory}")
        
        if request.servicecategory:
            embedding_fields.append(f"Service Category: {request.servicecategory}")
        
        if request.servicedescription:
            embedding_fields.append(f"Description: {request.servicedescription}")
        
        if request.services:
            embedding_fields.append(f"Services: {request.services}")
        
        if not embedding_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one of the embedding fields (businessname, businesscategory, servicecategory, servicedescription, services) must be provided"
            )
        combined_text = " | ".join(embedding_fields)
        logger.info(f"Combined text for embedding: {combined_text[:200]}...")
        
        embedding_vector = await generate_embedding(combined_text)
        
        if not validate_embedding_dimensions(embedding_vector):
            logger.error(f"Embedding dimension validation failed: {len(embedding_vector)} != {EMBEDDING_DIMENSIONS}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Generated embedding has incorrect dimensions: {len(embedding_vector)} (expected {EMBEDDING_DIMENSIONS})"
            )
        
        logger.info(f"Generated {len(embedding_vector)}-dimension embedding for provider: {request.providerservicesid}")
        embedding_string = '[' + ','.join(map(str, embedding_vector)) + ']'
        createdon_datetime = None
        if request.createdon:
            try:
                createdon_datetime = date_parser.parse(request.createdon)
                if createdon_datetime.tzinfo is None:
                    createdon_datetime = pytz.utc.localize(createdon_datetime)
                else:
                    createdon_datetime = createdon_datetime.astimezone(pytz.utc)
                createdon_datetime = createdon_datetime.replace(tzinfo=None)
            except Exception as e:
                logger.warning(f"Failed to parse createdon date: {request.createdon}, error: {e}")
                createdon_datetime = None
        upsert_query = """
            WITH updated AS (
                UPDATE etown_providerservices_copy SET
                    businessname = $2,
                    businesscategory = $3,
                    address = $4,
                    availability = $5,
                    city = $6,
                    contactid = $7,
                    contactidname = $8,
                    email = $9,
                    mobilenumber = $10,
                    pricerange = $11,
                    providerid = $12,
                    providertype = $13,
                    publicsearchprovider = $14,
                    servicecategory = $15,
                    servicedescription = $16,
                    serviceimageurl = $17,
                    latitude = $18,
                    longitude = $19,
                    services = $20,
                    statecode = $21,
                    userprofileid = $22,
                    userprofileidname = $23,
                    websiteurl = $24,
                    yearsinbusiness = $25,
                    createdon = $26,
                    additionaladdressinfo = $27,
                    areaname = $28,
                    content_vector = $29::vector,
                    updated_at = NOW(),
                    modifieddate = NOW()
                WHERE providerservicesid = $1
                RETURNING providerservicesid
            )
            INSERT INTO etown_providerservices_copy (
                providerservicesid, businessname, businesscategory, address,
                availability, city, contactid, contactidname, email, mobilenumber,
                pricerange, providerid, providertype, publicsearchprovider,
                servicecategory, servicedescription, serviceimageurl, latitude,
                longitude, services, statecode, userprofileid, userprofileidname,
                websiteurl, yearsinbusiness, createdon, additionaladdressinfo,
                areaname, content_vector, updated_at
            )
            SELECT
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
                $21, $22, $23, $24, $25, $26, $27, $28, $29::vector, NOW()
            WHERE NOT EXISTS (SELECT 1 FROM updated)
              AND EXISTS (
                  SELECT 1 FROM etown_providerservices
                  WHERE providerservicesid = $1
              )
        """

        await Database.execute(
            upsert_query,
            request.providerservicesid,
            request.businessname,
            request.businesscategory,
            request.address,
            request.availability,
            request.city,
            request.contactid,
            request.contactidname,
            request.email,
            request.mobilenumber,
            request.pricerange,
            request.providerid,
            request.providertype,
            request.publicsearchprovider,
            request.servicecategory,
            request.servicedescription,
            request.serviceimageurl,
            request.latitude,
            request.longitude,
            request.services,
            request.statecode,
            request.userprofileid,
            request.userprofileidname,
            request.websiteurl,
            request.yearsinbusiness,
            createdon_datetime,
            request.additionaladdressinfo,
            request.areaname,
            embedding_string
        )
        logger.info(f"Upserted record for providerservicesid: {request.providerservicesid}")
        
        logger.info(f"Successfully generated and stored {EMBEDDING_DIMENSIONS}-dimension embeddings for provider service: {request.providerservicesid}")
        
        return EmbeddingGenerationResponse(
            status="success",
            message="Embeddings generated and stored successfully",
            providerservicesid=request.providerservicesid,
            embedding_generated=True,
            timestamp=datetime.now(timezone.utc)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating embeddings: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate embeddings: {str(e)}"
        )


@router.post("/aisearch-embeddings", dependencies=[Depends(verify_token)])
async def aisearch_embeddings(request: AISearchEmbeddingRequest):
    """Handle POST /api/embeddings/aisearch-embeddings: re-embed one record from its mapped fields, skipping unchanged text."""

    try:
        logger.info(f"Generating embeddings for table: {request.table_name}, ID: {request.unique_id}")

        table_name = request.table_name
        unique_id = request.unique_id

        if not table_name.startswith("etown_"):
            raise HTTPException(status_code=400, detail="Invalid table name")

        allowed_fields = TABLE_FIELD_MAPPING.get(table_name)
        if not allowed_fields:
            raise HTTPException(status_code=400, detail="No field mapping defined for this table")

        base_name = table_name.replace("etown_", "")
        id_column = TABLE_ID_COLUMNS.get(table_name, f"{base_name}id")

        has_hash_column = _tables_with_hash_column.get(table_name)
        if has_hash_column is None:
            has_hash_column = bool(await Database.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
                """,
                table_name, HASH_COLUMN,
            ))
            _tables_with_hash_column[table_name] = has_hash_column
            if not has_hash_column:
                logger.warning(
                    f"{table_name}.{HASH_COLUMN} is missing — every request will re-embed. "
                    f"Apply scripts/2026_08_embedding_text_hash.sql to enable the guard."
                )

        columns = ", ".join(allowed_fields)

        guard_columns = ("content_vector IS NOT NULL AS has_vector, "
                         "vector_dims(content_vector) AS vector_dims")
        if has_hash_column:
            guard_columns += f", {HASH_COLUMN} AS stored_hash"

        select_query = f"""
            SELECT {columns}, {guard_columns}
            FROM {table_name}
            WHERE {id_column} = $1
        """

        record = await Database.fetchrow(select_query, unique_id)

        if not record:
            logger.warning(f"Record not found: {table_name}.{id_column} = {unique_id}")
            raise HTTPException(status_code=404, detail="Record not found")

        text_parts: List[str] = []

        for field in allowed_fields:
            value = record.get(field)

            if value is not None and str(value).strip():
                if isinstance(value, (int, float)):
                    text_parts.append(f"{field}: {value}")
                elif isinstance(value, bool):
                    text_parts.append(f"{field}: {str(value).lower()}")
                elif isinstance(value, str):
                    cleaned_value = ' '.join(value.split())
                    if cleaned_value:
                        text_parts.append(f"{field}: {cleaned_value}")
                else:
                    text_parts.append(f"{field}: {str(value)}")

        if not text_parts:
            raise HTTPException(
                status_code=400,
                detail="No valid data found for embedding generation"
            )

        text_data = " | ".join(text_parts)
        text_hash = hashlib.sha256(text_data.encode("utf-8")).hexdigest()

        if (
            has_hash_column
            and not request.force
            and record["has_vector"]
            and record["stored_hash"] == text_hash
        ):
            logger.info(f"Embedding already current for {table_name}.{id_column} = {unique_id} — skipped")
            return {
                "status": "success",
                "message": "Embedding already up to date; source text unchanged",
                "table_name": table_name,
                "id_column": id_column,
                "unique_id": unique_id,
                "embedding_dimensions": record["vector_dims"],
                "text_length": len(text_data),
                "skipped": True,
            }

        logger.debug(f"Embedding text ({len(text_data)} chars): {text_data[:200]}...")

        embedding_vector = await generate_embedding(text_data)

        if not validate_embedding_dimensions(embedding_vector):
            logger.error(f"Invalid embedding size: {len(embedding_vector)}")
            raise HTTPException(
                status_code=500,
                detail=f"Invalid embedding dimensions: {len(embedding_vector)}"
            )

        embedding_string = "[" + ",".join(map(str, embedding_vector)) + "]"

        if has_hash_column:
            update_query = f"""
                UPDATE {table_name}
                SET content_vector = $1::vector, {HASH_COLUMN} = $2
                WHERE {id_column} = $3
            """
            await Database.execute(update_query, embedding_string, text_hash, unique_id)
        else:
            update_query = f"""
                UPDATE {table_name}
                SET content_vector = $1::vector
                WHERE {id_column} = $2
            """
            await Database.execute(update_query, embedding_string, unique_id)

        logger.info(f"Embedding stored for {table_name}.{id_column} = {unique_id}")

        return {
            "status": "success",
            "message": "Embedding generated and stored successfully",
            "table_name": table_name,
            "id_column": id_column,
            "unique_id": unique_id,
            "embedding_dimensions": len(embedding_vector),
            "text_length": len(text_data),
            "skipped": False,
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.error(
            f"Error generating embeddings for {request.table_name}.{request.unique_id}: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate embeddings: {str(e)}"
        )