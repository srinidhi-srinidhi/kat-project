"""Set Profile Image Tool - sets a provider's last generated image as their business profile image.

Provider-agent tool behind set_profile_image_from_chat: once the provider agrees, stores the conversation's most recent generate_image blob path as serviceimageurl on etown_provider and both service tables and mirrors ets_serviceimageurl to Dataverse.

Features:
    - Single Step: Updates the business image as soon as the provider agrees; the UI shows any confirmation
    - Text-only Reply: Returns only assistant text; no implicit_output or response_type reaches the UI
    - Last Image Only: Uses the stored blob path of the last generated image; no image argument is accepted
    - Inline Image Refusal: An image without a blob path is refused rather than stored as a data URL
    - Image-only Mirror: The Dataverse PATCH carries only ets_serviceimageurl for provider and services
    - Tracked Mirror Task: The background sync task is held until it finishes so it cannot be collected
    - Guardrails: Missing account, no generated image or no business profile return a guiding message

Classes:
    No classes defined (module exposes the draft/confirm handler)

Functions:
    handle_set_profile_image_from_chat(): Handler that sets the last generated image as the business image

Dependencies:
    - app.etown.provider.provider_utils.ProviderService: edit_provider_profile_workflow for the Postgres update
    - app.etown.provider.provider_syncing: sync_provider_profile_update_to_dataverse for the CRM mirror
    - app.utils.blob_storage: is_stored_media_url

Updates:
    28-Sep-2026: Initial creation of the single-step business profile image tool.
"""

import asyncio
import logging
from typing import Any, Optional, Tuple

from app.etown.provider.provider_syncing import sync_provider_profile_update_to_dataverse
from app.etown.provider.provider_utils import ProviderService
from app.utils.blob_storage import is_stored_media_url

logger = logging.getLogger(__name__)

_background_tasks: set = set()


async def handle_set_profile_image_from_chat(
    *,
    contact_id: Optional[str],
    userprofile_id: Optional[str],
    last_image: Any,
) -> Tuple[str, bool]:
    """Set the last generated image as the provider's business profile image and mirror it to Dataverse.

    Args:
        contact_id: Contact ID of the provider, from the execution context.
        userprofile_id: Provider userprofile ID, from the execution context.
        last_image: The most recent generated image dict, or None.

    Returns:
        Tuple of (assistant text, True when the business image was updated).
    """
    if not contact_id or not userprofile_id:
        return (
            "I couldn't find your provider account details. Please make sure you're "
            "logged in with your provider account before changing your business profile image.",
            False,
        )

    if not isinstance(last_image, dict) or not (last_image.get("blob_path") or last_image.get("image_url")):
        return (
            "I don't have a generated image to use yet. Ask me to generate an image first, "
            "and then I can set it as your business profile image.",
            False,
        )

    blob_path = last_image.get("blob_path")
    if not blob_path or not isinstance(blob_path, str) or not is_stored_media_url(blob_path):
        return (
            "That image wasn't saved to storage, so I can't use it as your business profile image. "
            "Please ask me to generate the image again and then set it.",
            False,
        )

    try:
        message, provider_data = await ProviderService.edit_provider_profile_workflow(
            contact_id=contact_id,
            provider_userprofile=userprofile_id,
            updates={"service_image_url": blob_path},
        )
    except Exception as exc:
        logger.error("set_profile_image_from_chat: failed to update business image: %s", exc, exc_info=True)
        return (
            "Sorry, something went wrong while updating your business profile image. Please try again in a moment.",
            False,
        )

    if provider_data is None:
        logger.info(
            "set_profile_image_from_chat: no provider for contact_id=%s userprofile_id=%s: %s",
            contact_id, userprofile_id, message,
        )
        return (
            "I couldn't find your business profile, so the image wasn't set. "
            "Please make sure you're using your provider account.",
            False,
        )

    stored_image = (provider_data.get("resolved") or {}).get("serviceimageurl") or blob_path
    image_payload = {"ets_serviceimageurl": stored_image}
    sync_task = asyncio.create_task(
        sync_provider_profile_update_to_dataverse(
            provider_id=provider_data["providerid"],
            providerservices_ids=provider_data.get("providerservices_ids") or [],
            provider_payload=dict(image_payload),
            service_payload=dict(image_payload),
        )
    )
    _background_tasks.add(sync_task)
    sync_task.add_done_callback(_background_tasks.discard)

    return "Done! Your business profile image has been updated to the image you generated.", True
