"""Create Post Tool - opens the app's own post composer without publishing anything.

Member- and provider-agent tool behind create_post: it writes nothing to the feed, only returns a UI trigger that opens the mobile post composer plus a disclaimer steering the model to create_post_from_chat for real publishing.

Features:
    - UI Trigger Only: Returns ui_trigger='create_post_view'; no etown_feed row is written
    - Emphatic No-op Messaging: Text opens "NO POST HAS BEEN CREATED" and forbids claiming a publish
    - Points to the Real Tool: Tells the model to use create_post_from_chat to publish

Classes:
    No classes defined (module exposes a single UI-trigger handler function)

Functions:
    handle_create_post(): Returns the no-op disclaimer text and create_post_view UI trigger

Dependencies:
    No external dependencies (pure Python; only stdlib typing hints)

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    17-Aug-2026: Reworded the returned ToolMessage to state that no post was created.
"""

from typing import Tuple, Any, Dict
async def handle_create_post() -> Tuple[str, Dict[str, Any]]:
    """Return a no-post-created message and a create_post_view UI trigger that opens the app's composer.

    Returns:
        Tool message text stating nothing was published, and the status/ui_trigger dict.
    """
    result_text = (
        "NO POST HAS BEEN CREATED. Nothing was published and nothing was saved to the feed. "
        "This tool only asked the mobile app to open its post-composer screen so the user can "
        "write and publish the post themselves. Tell the user the composer has been opened for "
        "them and that they still need to write and publish it there. Do NOT tell them their "
        "post is live, posted, shared, or published. If they wanted you to publish it for them, "
        "use create_post_from_chat instead."
    )
    implicit_output = {
        "status": "success",
        "ui_trigger": "create_post_view"
    }
            
    return result_text, implicit_output