"""API Router - mounts every endpoint router under its prefix and OpenAPI tag.

Routing layer that builds api_router from each endpoint module with its /<prefix> and tag; main.py mounts it at /api, so routes are /api/<prefix>/... rather than /api/v1/.

Features:
    - Centralized Routing: Single place where every endpoint router is included
    - Prefixes: Each module mounts at /api/<prefix>; there is no versioned path
    - Tag Management: One OpenAPI tag per endpoint module for the docs
    - Testing Routes: The testing router is included alongside the production ones

Classes:
    No classes defined (module uses FastAPI router configuration)

Functions:
    No standalone functions (module uses declarative router configuration)

Dependencies:
    - fastapi: Web framework router and API organization
    - app.api.v1.endpoints.chat: Agent conversation endpoints (/chat)
    - app.api.v1.endpoints.users: User registration, profile and location endpoints (/users)
    - app.api.v1.endpoints.logs: Log management and monitoring endpoints (/logs)
    - app.api.v1.endpoints.voice_calls: Voice/video calling and 100ms.live integration with media controls (/call)
    - app.api.v1.endpoints.providers: Provider onboarding, edits, ratings and analytics endpoints (/provider)
    - app.api.v1.endpoints.dashboard: Dashboard/reporting endpoints (/dashboard)
    - app.api.v1.endpoints.conversations: Conversation management endpoints (/conversations)
    - app.api.v1.endpoints.feeds: Content feed and social endpoints (/feeds)
    - app.api.v1.endpoints.services: Service provider and business-intelligence endpoints (/services)
    - app.api.v1.endpoints.suggestions: Search suggestion endpoints (/suggestions)
    - app.api.v1.endpoints.embeddings: Vector embedding endpoints (/embeddings)
    - app.api.v1.endpoints.search: Advanced search endpoints (/search)
    - app.api.v1.endpoints.mytown: Geographic, location-based and community-needs endpoints (/mytown)
    - app.api.v1.endpoints.groups: Group management endpoints (/groups)
    - app.api.v1.endpoints.offers: Offer and promotion management endpoints (/offers)
    - app.api.v1.endpoints.offerings: Provider offerings endpoints (/offerings)
    - app.api.v1.endpoints.testing: Development and automation testing endpoints (/testing)
    - app.api.v1.endpoints.notifications: Notification listing, metrics and queue admin endpoints (/notifications)
    - app.api.v1.endpoints.whatsapp: WhatsApp Business Cloud API send + webhook endpoints (/whatsapp)
    - app.api.v1.endpoints.terms_and_conditions: Static terms, banners and subscription endpoints (/info)
    - app.api.v1.endpoints.invitation: Invitation management endpoints (/invitations)
    - app.api.v1.endpoints.otpverification: Mobile number OTP verification endpoints (/otp)
    - app.api.v1.endpoints.auth: Passwordless email login (OTP + magic link) and session management (/auth)
    - app.api.v1.endpoints.instagram: Instagram integration endpoints (/instagram)
    - app.api.v1.endpoints.facebook: Facebook integration endpoints (/facebook)
    - app.api.v1.endpoints.message: Direct messaging conversation endpoints (/message)
    - app.api.v1.endpoints.booking: Booking workflow endpoints (/book)
    - app.api.v1.endpoints.events: Event tracking endpoints (/events)
    - app.api.v1.endpoints.devices: Contact device token management endpoints (/devices)
    - app.api.v1.endpoints.account_deletion: Account deletion endpoints, sharing the /users prefix
    - app.api.v1.endpoints.dataverse_sync: Microsoft Dataverse synchronisation endpoints (/dataverse-sync)
    - app.api.v1.endpoints.plans: Subscription plan status, limits and usage endpoints (/plans)
    - app.api.v1.endpoints.invoices: MDA subscription invoices and GST price list (/invoices)
    - app.api.v1.endpoints.support: Contact-support ticket endpoints (/support)
    - app.api.v1.endpoints.memory: Chaatie long-term memory controls mounted at /memory
    - app.api.v1.endpoints.images: AI image generation with an optional watermark (/images)
    - app.api.v1.endpoints.debug_perf: Debug/performance endpoints mounted at the router root

Updates:
    27-Feb-2026: Updated the module documentation.
    09-Mar-2026: Added users router for user management endpoints
    12-Mar-2026: Added logs router for database log management and monitoring
    18-Mar-2026: Added voice_calls router for 100ms.live voice calling integration
    13-May-2026: Added notifications and terms_and_conditions routers.
    22-May-2026: Added invitations router for invitation management endpoints
    27-May-2026: Added otpverification and message routers.
    02-Jul-2026: Added devices router for contact device token management (SaveDeviceToken migration)
    06-Jul-2026: Added account_deletion router for DELETE /users/delete_account endpoint (DeleteDBData migration)
    02-Aug-2026: Added the auth router (/api/auth) for passwordless email login.
    04-Aug-2026: Added dataverse_sync router for Microsoft Dataverse sync endpoints
    18-Aug-2026: Added plans router (/api/plans) for subscription plan status and limits.
    24-Aug-2026: Added support router (/api/support) for contact-support tickets.
    07-Sep-2026: Added memory router (/api/memory).
    15-Sep-2026: Dropped the needs router; endpoints moved to /api/mytown/community and /api/mytown/{needid}/publish.
    30-Sep-2026: Added images router (/api/images) for image generation with an optional watermark.
    22-Oct-2026: Added invoices router (/api/invoices) for MDA subscription invoices.
"""
from fastapi import APIRouter

from app.api.v1.endpoints import chat, conversations, feeds, services, suggestions, embeddings, search, mytown, offers, testing, users, logs, groups, voice_calls, offerings, providers, dashboard, notifications, terms_and_conditions, whatsapp, invitation, otpverification, message, booking, events, instagram, facebook, devices, account_deletion, dataverse_sync, auth, plans, invoices, support, memory, images

api_router = APIRouter()

api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(voice_calls.router,prefix="/call", tags=["call"])
api_router.include_router(providers.router, prefix="/provider", tags=["providers"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(feeds.router, prefix="/feeds", tags=["feeds"])
api_router.include_router(services.router, prefix="/services", tags=["services"])
api_router.include_router(plans.router, prefix="/plans", tags=["plans"])
api_router.include_router(invoices.router, prefix="/invoices", tags=["invoices"])
api_router.include_router(support.router, prefix="/support", tags=["support"])
api_router.include_router(memory.router, prefix="/memory", tags=["memory"])
api_router.include_router(images.router, prefix="/images", tags=["images"])
api_router.include_router(suggestions.router, prefix="/suggestions", tags=["suggestions"])
api_router.include_router(embeddings.router, prefix="/embeddings", tags=["embeddings"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(mytown.router, prefix="/mytown", tags=["mytown"])
api_router.include_router(groups.router, prefix="/groups", tags=["groups"])
api_router.include_router(offers.router, prefix="/offers", tags=["offers"])
api_router.include_router(offerings.router, prefix="/offerings", tags=["offerings"])
api_router.include_router(testing.router, prefix="/testing", tags=["testing"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
api_router.include_router(terms_and_conditions.router, prefix="/info", tags=["info"])
api_router.include_router(invitation.router, prefix="/invitations", tags=["invitations"])
api_router.include_router(otpverification.router, prefix="/otp", tags=["otpverification"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(instagram.router, prefix="/instagram", tags=["instagram"])
api_router.include_router(facebook.router, prefix="/facebook", tags=["facebook"])
api_router.include_router(message.router, prefix="/message", tags=["message"])
api_router.include_router(booking.router, prefix="/book", tags=["book"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(devices.router, prefix="/devices", tags=["devices"])
api_router.include_router(account_deletion.router, prefix="/users", tags=["users"])
api_router.include_router(dataverse_sync.router, prefix="/dataverse-sync", tags=["dataverse_sync"])

from app.api.v1.endpoints import debug_perf
api_router.include_router(debug_perf.router, prefix="", tags=["debug"])
