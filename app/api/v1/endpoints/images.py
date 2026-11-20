"""Image Endpoints - HTTP route to generate an AI logo, poster, feed post or image with or without the watermark.

HTTP layer for /api/images behind the shared bearer token: validates the request and delegates to handle_generate_image, which plans, generates, checks, typesets, optionally watermarks, and stores in Azure Blob or returns the image inline.

Endpoints:
    POST /generate: Generate an image of one type; watermark and store flags choose the Etown mark and Azure Blob upload.

Classes:
    GenerateImageRequest(): Body for POST /generate — prompt, image type, watermark and store flags, size, seed, provider, previous
    GenerateImageResponse(): Response for POST /generate — image or data URL, blob path, printed text, design, seed, flags

Functions:
    generate_image(): Generate one image and return its URL or inline data URL, design and flags.

Dependencies:
    - app.core.security.verify_token: Shared bearer token dependency.
    - app.agent_infrastructure.tools.tools_utils.generate_image: handle_generate_image pipeline shared with the Chaatie tool.
    - app.core.logging: centralized logging.

Updates:
    30-Sep-2026: Initial creation with POST /generate, a watermark option and a store option.
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agent_infrastructure.tools.tools_utils.generate_image import handle_generate_image
from app.core.logging import logger
from app.core.security import verify_token

router = APIRouter(tags=["images"])


class GenerateImageRequest(BaseModel):
    """Body for POST /api/images/generate."""

    prompt: str = Field(..., min_length=1, max_length=2000, description="What the image should show, in the user's words.")
    image_type: Literal["logo", "offer", "feed", "general"] = Field("general", description="logo, offer poster, feed post or general image.")
    watermark: bool = Field(True, description="True stamps the Etown mark bottom-right; false returns a clean image. AI-generated metadata is embedded either way.")
    store: bool = Field(True, description="True uploads to Azure Blob and returns its URL; false stores nothing and returns the image inline as a base64 data URL.")
    width: int = Field(1024, ge=256, le=1280, description="Output width in pixels, snapped down to a multiple of 16.")
    height: int = Field(1024, ge=256, le=1280, description="Output height in pixels, snapped down to a multiple of 16.")
    seed: Optional[int] = Field(None, ge=0, description="Fixed seed for a repeatable image; omit for a random one.")
    user_profile_id: Optional[str] = Field(None, description="Provider userprofileid whose business name, profile image and brand colour feed the design.")
    previous: Optional[Dict[str, Any]] = Field(None, description="enhanced_prompt, design and seed of an earlier response, to refine that image.")


class GenerateImageResponse(BaseModel):
    """Response for POST /api/images/generate."""

    status: str
    message: str
    image_url: str
    blob_path: Optional[str] = None
    image_type: str
    watermarked: bool
    stored: bool
    text_on_image: List[str] = []
    design: Optional[Dict[str, Any]] = None
    enhanced_prompt: Optional[str] = None
    seed: Optional[int] = None
    width: int
    height: int
    brand_logo: bool = False
    text_check: Optional[Dict[str, Any]] = None


@router.post(
    "/generate",
    response_model=GenerateImageResponse,
    status_code=200,
    summary="Generate Image",
    description=(
        "Generate a logo, offer poster, feed post or general image and store it in Azure Blob. "
        "Every word on the image is typeset from the request, never drawn by the model, and text "
        "the request does not support is left out. Set watermark=false for a clean image without "
        "the Etown mark; the AI-generated metadata is embedded either way. Set store=false to keep "
        "the image out of Azure Blob: image_url is then a base64 data URL (PNG for logos, JPEG "
        "otherwise), blob_path is null and stored is false. Pass a provider's "
        "user_profile_id to use their business name, profile image and brand colour, and pass "
        "enhanced_prompt, design and seed from an earlier response as previous to refine that image. "
        "A generation takes about 20 seconds, up to about 40 when a picture with stray lettering is "
        "regenerated. 502 means the image model failed."
    ),
)
async def generate_image(payload: GenerateImageRequest, current_user: str = Depends(verify_token)):
    """Generate one image and return its URL or inline data URL, design and flags.

    Args:
        payload: The generation request.
        current_user: Bearer token accepted by verify_token.

    Returns:
        GenerateImageResponse for the stored image.

    Raises:
        HTTPException: 502 when the image model or pipeline fails.
    """
    try:
        message, output = await handle_generate_image(
            prompt=payload.prompt,
            image_type=payload.image_type,
            width=payload.width,
            height=payload.height,
            seed=payload.seed,
            store_format="png" if payload.image_type == "logo" else "jpeg",
            store_quality=88,
            user_profile_id=payload.user_profile_id,
            previous=payload.previous,
            watermark=payload.watermark,
            store=payload.store,
        )
    except Exception as exc:
        logger.error("IMAGE_ENDPOINT_FAILED | type=%s | error=%s", payload.image_type, exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Image generation failed. Please try again.")
    if not output:
        raise HTTPException(status_code=502, detail=message)
    return GenerateImageResponse(
        status="success",
        message=message,
        image_url=output["image_url"],
        blob_path=output.get("blob_path"),
        image_type=output["image_type"],
        watermarked=output.get("watermarked", payload.watermark),
        stored=bool(output.get("stored")),
        text_on_image=output.get("text_on_image") or [],
        design=output.get("design"),
        enhanced_prompt=output.get("enhanced_prompt"),
        seed=output.get("seed"),
        width=output["width"],
        height=output["height"],
        brand_logo=bool(output.get("brand_logo")),
        text_check=output.get("text_check"),
    )
