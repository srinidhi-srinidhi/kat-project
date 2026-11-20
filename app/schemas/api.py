"""API Schemas - Pydantic request and response models shared by endpoints, workers and agent tools.

Schema layer holding the main set of Pydantic models for request validation, response serialisation and OpenAPI docs across chat, feed, MyTown, search, bookings, plans, support and memory; it defines no functions, and message and voice-call models live in their own schema modules.

Features:
    - Request Validation: Field-level validation for every API endpoint's input model
    - Response Models: Structured, endpoint-specific response shapes for consistent output
    - Etown_-Prefixed Response Fields: PowerApps-era Etown_* names kept for app compatibility
    - Flexible Aliasing: AliasChoices/populate_by_name let edit models accept legacy aliases
    - Custom Validators: coordinate bounds, mobile format and mode-dependent required fields
    - Nested Models: feeds, offerings, bookings and social insights compose smaller models
    - Domain Coverage: chat, feed, MyTown, search, offers, onboarding, bookings, plans, support, needs, memory

Classes:
    ChaatieRequest(): Main agent chat request - prompt, identity, role, language/location, optional image
    ChaatieProcessingTextsRequest(): Prompt and context for the status lines shown while a turn processes
    ChaatieProcessingTextsResponse(): Status lines plus the activity they describe and their source
    HealthResponse(): Health check response (status, timestamp, version)
    ItemData(): Provider item data (id, name, category/subcategory)
    DataInsertionValidation(): Provider profile data-insertion payload with its items
    MobileLookupRequest(): Bulk mobile-number lookup, optionally in onboarding mode
    ProviderBusinessDetail(): A provider row matched by mobile number (onboarding=true)
    MobileLookupResponse(): Response for the bulk mobile-number lookup
    ConversationMessage(): A single conversation message
    ConversationSummary(): Conversation summary (message count, last message time)
    LogRequest(): Payload for inserting a conversation log entry
    TitleGenerationRequest(): Request for conversation title generation
    PromptSuggestionRequest(): Request for prompt suggestions
    ConversationListRequest(): Paginated conversation-list request
    ConversationTitleItem(): A single conversation title item
    ConversationListResponse(): Response for the conversation list
    FeedLikeDetails(): Compact "liked by X and N others" summary for a feed item
    EtownFeedItem(): A single feed item with all its properties
    FeedGroup(): A target group for a feed post (etown_feed.groups jsonb)
    FeedPhonebookContact(): A phonebook contact for a feed post (etown_feed.phonebook jsonb)
    CreateFeedRequest(): Request for creating a new feed item
    FeedCommentItem(): A single feed comment with commenter details
    FeedLikeUserItem(): A single user who liked a feed post
    FeedExecutionLogs(): Feed endpoint performance-metrics breakdown
    FullFeedResponse(): Complete feed response, feeds plus localized categories
    ServiceGenerationValidation(): Request for service generation
    ServiceItem(): Individual generated service or product item
    ServiceGenerationResponse(): Response for service generation
    LocationDetailsResponse(): Response for parsed location details
    GeocodingResponse(): Response for address geocoding
    PublicProviderDetailsGeneratorRequest(): Request to generate details from a Place ID
    PublicProviderItem(): A generated item/service
    PublicProviderDetailsData(): Wrapper list of generated PublicProviderItem entries
    PublicProviderDetailsResponse(): Response for generated public provider details
    GenerateTownDetailsResponse(): Response for generated town details
    CategoryGenerationRequest(): Request for generating business categories
    CategoryGenerationResponse(): Business type/subcategory/general category result
    ServiceDetailRequest(): One service entry when adding services to an existing provider
    OnboardServiceDetailRequest(): ServiceDetailRequest plus the service_type /provider/onboard stores as businesscategory
    GeneralCategoryGenerationRequest(): Request for generating general business categories
    GeneralCategoryGenerationResponse(): General category result
    BusinessDescriptionRequest(): Request for business description enhancement
    BusinessDescriptionResponse(): Response with the enhanced business description
    ProviderFeedDetails(): A provider's feed details
    ProviderRatingDetails(): Individual rating details from completed requests
    ProviderDetailItem(): Detailed provider information
    SubServiceDetailItem(): Sub-service details
    ProviderDetailsResponse(): Response for the provider details endpoint
    CollectionItem(): A user's collection
    CollectionResponse(): Response for /mytowncollections
    MyTownSearchResponse(): Response for /mytownsearch
    CitySuggestion(): City name and description
    AreaSuggestion(): A single area suggestion
    ProviderSuggestion(): A single provider suggestion (Etown_-prefixed)
    CategorySuggestion(): A provider category in a town or area (Etown_-prefixed)
    CategoryPinRequest(): Request payload to pin or unpin a category in a town and area or at a location
    CategoryPinResponse(): Response payload for a category pin or unpin
    TownImageItem(): Model for a town image
    CityItem(): A city in the available-cities list
    MyTownResponse(): Unified response for /location_based_suggestions and /mytown_suggestions
    VerifiedAreasResponse(): Response for /get_verified_areas
    TownPresenceResponse(): Response for the town presence check
    TownManagementResponse(): Response for town management operations
    UploadTownImageItem(): One image and optional caption inside an upload_townimage batch
    UploadTownImageRequest(): Request payload to upload one or more town images
    UploadedTownImage(): One stored town image returned by upload_townimage
    UploadTownImageResponse(): Response payload for uploading town images
    RequestTownImageRequest(): Request payload to submit one town image for review
    RequestTownImageResponse(): Response payload for a submitted town image review request
    VerifiedProvidersResponse(): Response for the verified-providers endpoint
    PopularProvidersResponse(): Response for the popular-providers-near-you endpoint: categories and All-tab providers
    AISearchRequest(): Request for the AI search endpoint
    SearchMemberResult(): A member search result
    SearchProviderResult(): A provider search result
    SearchResults(): Search results — members, providers, areas, and cities
    UniformPerformanceMetrics(): Uniform performance metrics shared across API endpoints
    AISearchResponse(): Response for the AI search endpoint
    OfferViewItem(): A single offer-viewer record
    OfferItem(): A single offer item
    ProviderOfferGroup(): A provider's offers grouped for WhatsApp story-style display
    ViewedOfferRequest(): Request to mark an offer as viewed
    OfferViewCountResponse(): Response with an offer's view count
    OfferContentItem(): Offer content fetched by offerunicid (incl. provider city/statecode)
    ValidPendingOfferItem(): A valid or pending offer
    NotificationData(): Call-session notification data payload
    NotificationPayload(): FCM notification title/body payload
    FCMMessage(): FCM message structure for call notifications
    PushNotificationRequest(): PowerApps-compatible FCM push notification request
    CallSessionResponse(): Call session response
    DeclineNotificationRequest(): Decline-notification request (PowerApps/FCM)
    DeclineNotificationResponse(): Decline-notification response
    TestAgentRequest(): Request for the automation testing agent
    UserRegistrationRequest(): Request for user registration in EtownDB
    ContactInfo(): Contact table response data
    UserprofileInfo(): Userprofile table response data
    UserRegistrationResponse(): Combined response for successful registration
    SwitchProfileRequest(): Request for the Switch Profile endpoint
    ContactInfoDetailed(): Detailed contact info for the user-existence response
    UserprofileInfoDetailed(): Detailed userprofile info for the user-existence response
    UserExistsDetailedResponse(): Response for the comprehensive user-existence check
    ServiceListRequest(): Request for the Service_list endpoint
    LastLoginRequest(): Request to update last-login status
    ConsentRequest(): Request to update user consent flags
    ConsentResponse(): Response for the consent update
    LocationDetailsRequest(): Request to add/edit/delete a user location record
    UpdateAddressRequest(): Request to update a contact's address fields
    GroupItem(): A user's group
    GroupResponse(): Response for /groups
    ProviderOnboardingServiceDetail(): One Service_Details entry for /provider/onboard, with three image slots
    ProviderOnboardingRequest(): Request for provider onboarding (Power Automate app-form)
    ProviderContactInfo(): Provider contact information
    ProviderUserProfileInfo(): Provider userprofile information
    ProviderBusinessInfo(): Provider business information
    ProviderServiceInfo(): Provider service information
    AIEnhancementInfo(): AI-enhancement information
    ProviderOnboardingResponse(): Response for successful provider onboarding
    OnboardServicesRequest(): Request for POST /provider/onboard_services
    OnboardServicesInfo(): Generated provider service details without ids
    OnboardServicesResponse(): Response for POST /provider/onboard_services
    ParsedOfferingItem(): One item within a parsed offering category (AI business parser)
    ParsedOfferingCategory(): A category grouping parsed items within an offering
    ParsedOfferingData(): A parsed offering group (product/service line) with its categories
    ParsedBusinessDetails(): Business-level details from the AI business parser
    AddServicesToProvidersRequest(): Add services to a provider (accepts two payload shapes)
    AddServicesToProvidersResponse(): Response for the add-services-to-provider workflow
    UnverifiedProviderServiceDetail(): ServiceDetailRequest minus serviceitems, for a field-captured service
    UnverifiedProviderOnboardingRequest(): Request model for POST /provider/unverified_provider
    UnverifiedProviderOnboardingResponse(): Response model for POST /provider/unverified_provider
    ProviderServiceRow(): One etown_providerservices row under a provider in the lookup response
    UnverifiedProviderItem(): One provider from GET /provider/unverified_provider_list
    UnverifiedProviderListResponse(): Response for GET /provider/unverified_provider_list
    ProviderApprovalRequest(): Request model for POST /provider/approve
    ProviderApprovalResponse(): Response for POST /provider/approve
    ProviderDeleteRequest(): Request to delete a provider by userprofileid + contactid
    ProviderDeleteResponse(): Response for provider deletion
    ProviderLifecycleRequest(): Request to deactivate or reactivate one provider business
    ProviderLifecycleResponse(): Response model for a per-provider lifecycle transition
    GroupMemberInput(): A single member to add to a group
    GroupManageRequest(): Request to manage a group (create, update, delete)
    AddToMyCircleProviderRequest(): Request to add a provider to the actor's MyCircle
    AddToMyCircleMemberRequest(): Request to add a member to the actor's MyCircle
    DeleteServiceRequest(): Request to delete a specific service from a provider
    DeleteServiceResponse(): Response for the delete-service workflow
    ItemOperation(): One add/remove/update operation on an item within a category
    CategoryOperation(): One add/remove/update operation on a category within an offering
    OfferingOperation(): One add/remove/update operation on an offering (etown_offerings tree)
    EditServiceRequest(): Request to edit a specific service of a provider
    EditServiceResponse(): Response for the edit-service workflow
    EditProviderProfileRequest(): Request to edit the top-level provider profile
    EditProviderProfileResponse(): Response for the edit-provider-profile workflow
    ProviderIdentifiersResponse(): The identity a providerid resolves to
    ProfileViewerItem(): A single user who viewed a provider's profile
    ProfileViewersResponse(): One page of profile viewers plus the total across every page
    EditMemberProfileRequest(): Request to edit a member/contact profile
    EditMemberProfileResponse(): Response for the edit-member-profile workflow
    UploadInfo(): File upload metadata
    OfferingField(): An extracted field with a per-value confidence score
    OfferingContactInfo(): Contact details extracted from a business image
    OfferingBusinessDetails(): Business details extracted/AI-generated from an image
    DetectedObject(): One object visually detected in an image
    MediaAnalysis(): Analysis metadata — what was detected and how
    OfferingItem(): An item within an offering category
    OfferingCategory(): A category grouping related items within an offering
    OfferingEntry(): One offering group (product/service line) extracted from an image
    OfferingServiceInput(): An edited offering group, ready to become a provider service
    ProcessingMetadata(): Processing and confidence metadata for the image analysis
    UnratedProviderItem(): A single unrated-provider item
    InstructionEntry(): A booking instruction with the date/time it was supplied
    ProviderBookingDetails(): Booking details for a single provider request
    ProviderServiceDetail(): An individual service line item within a booking
    ProviderBookingItem(): A booking entry with its details and service line items
    ProviderRequestListResponse(): Response for the provider_request_list endpoint
    ProviderCompletedBookingItem(): A completed booking with its rating and line items
    ProviderCompletedListResponse(): Response for the provider_completed_list endpoint
    AllRequestsListResponse(): Response for GET /book/request_list (paginated bookings)
    FrequentlyVisitedItem(): A frequently-visited provider-service entry
    AddRatingRequest(): Request to add a rating (PowerApps rating-flow payload)
    AddRatingResponse(): Response for the add-rating endpoint
    NotifyUnverifiedProviderRequest(): Member-triggered WhatsApp nudge to an unverified provider
    NotifyUnverifiedProviderResponse(): Response for the notify_unverified endpoint
    WhatsAppTemplateRequest(): Request to send an approved WhatsApp template message
    WhatsAppTextRequest(): Request for a freeform WhatsApp text message (24h session only)
    WhatsAppSendResponse(): Response for the WhatsApp send endpoints
    ImageUploadRequest(): Request for image upload with multipart/form-data
    ImageUploadResponse(): Response for POST /api/offerings/image
    OnboardProviderDetails(): Extracted provider/business details from an onboarding image
    OnboardServiceDetail(): A service or product extracted from an onboarding image
    OnboardItem(): Combined provider details + service list from one onboarding image
    OnboardDetailsResponse(): Response for the /onboard_details image extraction endpoint
    AIOnboardingRequest(): Request model for POST /provider/ai_onboarding
    AIOnboardingProviderDetails(): Onboarding details minus Phone_Number, plus mobiles, coordinates, city, statecode and category, for /ai_onboarding and /bubble
    AIOnboardingResponse(): Response model for POST /provider/ai_onboarding
    AIOnboardingBubbleDetails(): Conversation id, provider and service details shared by the /provider/bubble request and response
    AIOnboardingBubbleResponse(): Response model for POST /provider/bubble, the updated provider and service details
    AIOnboardingBubbleRequest(): Request model for POST /provider/bubble, provider and service details plus a prompt and/or image
    InstagramConnectURLResponse(): Instagram OAuth URL for a Business connection
    InstagramCallbackResponse(): Response after processing the Instagram OAuth callback
    InstagramStatusResponse(): Whether a user has an Instagram connection, with live profile data
    InstagramDisconnectResponse(): Response after disconnecting an Instagram account
    InstagramPostInsights(): Insights for one Instagram post published from an Etown feed
    InstagramAllPostsResponse(): All Instagram posts published from Etown feeds for a user
    FacebookConnectURLResponse(): Facebook OAuth URL for a Page connection
    FacebookCallbackResponse(): Response after processing the Facebook OAuth callback
    FacebookStatusResponse(): Whether a user has a Facebook connection, with live profile data
    SocialStatusResponse(): Combined Instagram + Facebook connection status
    FacebookDisconnectResponse(): Response after disconnecting a Facebook Page
    FacebookPostInsights(): Insights for one Facebook post published from an Etown feed
    FacebookAllPostsResponse(): All Facebook posts published from Etown feeds for a user
    BestTimeToPostItem(): A top-viewed post used to infer the best time to post
    SocialPostInsightResponse(): Combined Instagram + Facebook insights for one feed post
    SocialUserPostsInsightResponse(): All of a user's posts with combined social insights
    SaveDeviceTokenRequest(): Request for the save-device-token endpoint
    ContactDeviceRecord(): A single device row from etown_contactdevices
    SaveDeviceTokenResponse(): Response for the save-device-token endpoint
    CredsUpdateRequest(): Request for POST /devices/credsupdate
    CredsUpdateResponse(): Response for POST /devices/credsupdate
    AppUninstallRecord(): One device FCM has declared unreachable (app uninstall signal)
    AppUninstallListResponse(): Response returned by the uninstalls endpoint
    SharedUserProfileId(): One share recipient
    ShareProviderRequest(): Request for POST /provider/share
    ShareProviderResponse(): Response for the share-provider endpoint
    ShareCollectionRequest(): Request for POST /mytown/share_collection
    ShareCollectionResponse(): Response for the share-collection endpoint
    TownViewCountItem(): One item for a town view log
    TownViewCountResponse(): Response for fetching town view count tracking logs
    NearMeListRequest(): Request for the Near Me List endpoint
    NearMeListItem(): One item in the Near Me List response (Etown_-prefixed)
    PromoteProviderPlanRequest(): Request for POST /plans/promote
    DisableProviderRequest(): Body for POST /plans/disable - switch off one business
    DismissPlanAlertRequest(): Body for POST /plans/alert/dismiss - close the day's expiry notice
    UpdateSubscriptionPlanRequest(): Partial-update request for PATCH /plans/catalogue/{plan_code}
    PlanPaymentRequest(): Body for POST /plans/payment_request - a submitted plan payment record
    SupportContactRequest(): Body for POST /support/contact - one contact-support request
    SupportTicketItem(): One row of a caller's support history from GET /support/my_tickets
    SupportContactResponse(): Acknowledgement for POST /support/contact
    SupportTicketListResponse(): Page of a caller's own tickets for GET /support/my_tickets
    ProviderBusinessItem(): One live business owned by a mobile number
    ProviderBusinessListResponse(): Response for GET /users/business_list
    AlternateMobileCheckRequest(): Request body for POST /users/check_alternate_mobile
    AlternateMobileCheckResponse(): Whether an alternate mobile may be attached to the account
    StaffMemberUpsertRequest(): Request body for POST /users/staff_member (create, edit or find)
    StaffMemberItem(): One etown_staff_numbers row as written
    StaffMemberUpsertResponse(): Response for POST /users/staff_member
    NeedAttachmentInput(): One image or video, with an optional thumbnail, in the POST /mytown/community body
    NeedTownInput(): One {townname, townprofileid} in the POST /mytown/community town list
    PostNeedRequest(): Body for POST /mytown/community - one need posted to one or more towns or drafted
    NeedAttachment(): One file attached to a need, as stored on the row and returned by the feed
    NeedTown(): One town of a member's own post, with that town's postid
    NeedItem(): One need, as returned by POST /mytown/community and GET /mytown/community
    PostNeedResponse(): Acknowledgement for POST /mytown/community
    NeedListResponse(): One page of needs for GET /mytown/community, newest first
    CommunityPostDetailResponse(): One need, update or event for GET /mytown/community/post
    PublishNeedRequest(): Body for POST /mytown/{needid}/publish - publish one of your drafts
    PublishNeedResponse(): Result of POST /mytown/{needid}/publish
    CommunityLikeRequest(): Body for POST /mytown/community/like - like or dislike a post
    CommunityLikeResponse(): Result of POST /mytown/community/like, with the new like count
    CommunityLikeItem(): One liker of a community post, named and pictured by role
    CommunityLikeListResponse(): One page of a post's likers for GET /mytown/community/likes
    CommunityPostActionRequest(): Body for POST /mytown/community/action - edit or delete your own post
    CommunityPostActionResponse(): Result of POST /mytown/community/action, with the edited post
    NeedResponseRequest(): Body for POST /mytown/community/respond - message the poster of a need
    NeedResponseItem(): One stored need response with its Messages conversation and message ids
    NeedResponseResponse(): Result of POST /mytown/community/respond, with the stored response
    NeedResponderItem(): One responder's latest need response, with name and image by role
    NeedResponseListResponse(): One page of a need's latest-per-person responses, newest first
    CommunityCommentRequest(): Body for POST /mytown/community/comment - add, edit or delete a comment
    CommunityCommentItem(): One comment on a community post, with the commenter's name and image
    CommunityCommentResponse(): Result of POST /mytown/community/comment, with the new comment count
    CommunityCommentListResponse(): One page of a post's comments for GET /mytown/community/comments
    CommunityReviewRequest(): Body for POST /mytown/community/review - approve or reject an update or event
    CommunityReviewResponse(): Result of POST /mytown/community/review, with the decision received and the stored state
    CommunityViewPost(): One viewed item of a POST /mytown/community/view call, as type and id
    CommunityViewRequest(): Body for POST /mytown/community/view - record a user's views of community posts
    CommunityViewItem(): One viewed item's outcome and view count
    CommunityViewResponse(): Result of POST /mytown/community/view, with each item's outcome
    MemoryItem(): One durable fact Chaatie has stored about the caller
    MemoryListResponse(): Page of the caller's stored memories for GET /memory, newest first
    MemoryDeleteResponse(): Result of deleting one memory or all of them
    MemorySettingsRequest(): Body for PATCH /memory/settings
    MemorySettingsResponse(): Current state of the caller's memory switch
    ProviderReviewApprovalRequest(): Body for POST /dataverse-sync/provider_review_approval
    ProviderReviewApprovalResponse(): Resolved contact and per-table row counts
    CreateInvoiceRequest(): Body for POST /invoices - invoice number, paying mobile number, subscription period, PDF blob link and send_to_customer

Functions:
    No functions defined at module level (validators live on their model classes)

Dependencies:
    - pydantic: BaseModel, Field, validators, AliasChoices, ConfigDict and SkipJsonSchema
    - typing: Optional/List/Union/Dict/Any/Literal/ClassVar type hints
    - datetime: Timestamp and date fields
    - app.utils.ist_clock: IST timezone for naive datetime validators
    - app.utils.phone: is_valid_indian_mobile() and normalize_indian_mobile() for validators

Updates:
    23-Feb-2026: Initial creation
    24-Feb-2026: Added feature X
    27-Feb-2026: Completely updated the documentation with a detailed module description
    09-Mar-2026: Added user management models for EtownDB registration and existence checks
    12-Mar-2026: Migrated LogEntry to a UUID request_id primary key.
    14-Mar-2026: Log schemas moved to app.logs.schema.
    24-Mar-2026: Added IndividualRating and PopularProviderDetailsData.individual_ratings.
    25-Mar-2026: IndividualRating gains city and service fields.
    15-Jun-2026: Added the Onboard* models for the /onboard_details image extraction endpoint.
    02-Jul-2026: Added SaveDeviceTokenRequest, ContactDeviceRecord, SaveDeviceTokenResponse and SwitchProfileRequest.
    03-Jul-2026: Added Edit(Provider|Member)ProfileRequest/Response and AllRequestsListResponse.
    08-Jul-2026: Added email and website fields to ProviderOnboardingRequest
    10-Jul-2026: Added Etown_whatsapp; unverified nudge accepts provider mobile and name.
    29-Jul-2026: EditService descriptions updated for the offerings-tree mirror onto serviceitems.
    30-Jul-2026: Added FeedLikeDetails and EtownFeedItem.feedlikedetails for the /api/feeds summary.
    06-Aug-2026: Onboarding lookup mode; ProviderOnboardingRequest gains action_mode and providerid.
    07-Aug-2026: The /etown_lookup onboarding mode returns all providers with their publicsearchprovider.
    10-Aug-2026: AddToMyCircleProviderRequest may identify the provider by providerid alone.
    18-Aug-2026: ProviderSuggestion, ProviderBookingDetails and PopularProviderDetailsData gain the plan badge.
    19-Aug-2026: Added background_image fields and the provider completed-list models.
    20-Aug-2026: Etown_providertype on ProviderBusinessDetail, UserprofileInfoDetailed, OfferItem and ProviderOfferGroup.
    21-Aug-2026: Added plan promote/update models; InstructionEntry carries filename and thumbnail.
    24-Aug-2026: Added the SupportContact/SupportTicket models for the contact-support endpoints.
    25-Aug-2026: CityItem gains townprofileid for the MyTown city lists.
    26-Aug-2026: Added ProfileViewerItem for the provider profile-viewers list.
    01-Sep-2026: PlanPaymentRequest requires mobilenumber; PromoteProviderPlanRequest takes contact_id.
    02-Sep-2026: Added ProviderBusinessList, DismissPlanAlertRequest and the Post a Need models; PlanPaymentRequest drops name.
    03-Sep-2026: ProviderSuggestion carries Etown_background_imageUrl.
    07-Sep-2026: Three-state ProviderDetailItem.Etown_Mycircle; added AddToMyCircleMemberRequest.
    08-Sep-2026: Multi-image town uploads with TownImageItem and UploadedTownImage; CitySuggestion carries townprofileid.
    11-Sep-2026: Added optional per-image captions to town image uploads and responses.
    22-Sep-2026: Added ProviderReviewApprovalRequest/Response for the provider-approval flow.
    23-Sep-2026: UserExistsDetailedResponse adds Etown_FOS; UnverifiedProviderOnboardingRequest takes marketing_mobilenumber.
    25-Sep-2026: Added StaffMemberUpsertRequest, StaffMemberItem and StaffMemberUpsertResponse.
    28-Sep-2026: Added AIOnboardingRequest and AIOnboardingResponse with response; OnboardServiceDetail adds Service_Expected_Price.
    29-Sep-2026: Added AIOnboardingBubbleDetails, AIOnboardingBubbleRequest and AIOnboardingBubbleResponse (conversation id, business-name confirmation) for POST /provider/bubble; AIOnboardingRequest takes conversation_id, contact_id and user_profile_id; AIOnboardingResponse adds status, existing_providers, providerid and conflicting_business_name; AIOnboardingProviderDetails holds mobiles, coordinates and category for both AI onboarding APIs; OnboardServiceDetail adds Service_Image_Url; AIOnboardingResponse always carries the generated listing and adds confirmation_required and conflicting_address; alternatemobile notes the confirmation reply's mobile; AIOnboardingBubbleDetails drops business_image_url.
    30-Sep-2026: AIOnboardingResponse.response describes chatbot replies and the prepared-details message; confirmation_required covers every question; merged Service_Details capped at 5; UnverifiedProviderOnboardingRequest, AIOnboardingProviderDetails and ProviderReviewApprovalRequest take interested; AIOnboardingProviderDetails drops Phone_Number; confirmation_required covers a refused alternate mobile; OnboardProviderDetails gains Service_Type (Home Delivery, Store Delivery or Both).
    01-Oct-2026: UserExistsDetailedResponse adds unverified_onboarded_today; marketing_mobilenumber is stored.
    05-Oct-2026: PostNeedRequest and NeedItem add location name; post_type is need, update or event with its own id; budget is need-only; schedule, venue, organizer and entry info are event-only; NeedItem adds poster_role; PostNeedRequest fields default to empty strings, lat/long to 0; the date field is needed_at, with time still accepted; NeedItem adds like and comment counts; added community like and comment models, which take post_type; NeedItem adds review_status; NeedListResponse replaces count with all and per-type counts; needed_at is optional.
    06-Oct-2026: Added CommunityPostActionRequest and CommunityPostActionResponse for editing or deleting posts; NeedItem adds need_status, response_count and match_score; added need response models and the need response list; added CommunityReviewRequest and CommunityReviewResponse; NeedListResponse adds open and closed counts; added CommunityPostDetailResponse.
    07-Oct-2026: NeedAttachmentInput takes data and an optional thumbnail, no filename; NeedAttachment returns thumbnail_path and thumbnail_url; attachments are images or videos; NeedItem adds mycircle and invitationstatus; BusinessDescriptionRequest adds type: business_description, need, event or update.
    08-Oct-2026: NeedListResponse adds townimage, provider_count and view_count; added RequestTownImageRequest and RequestTownImageResponse; PromoteProviderPlanRequest adds route.
    09-Oct-2026: RequestTownImageRequest and RequestTownImageResponse add filename.
    12-Oct-2026: Added the community view models for POST /mytown/community/view; NeedItem adds view_count.
    14-Oct-2026: UserExistsDetailedResponse adds credsupdate; added CredsUpdateRequest and CredsUpdateResponse; PostNeedRequest takes a town list replacing townname and townprofileid; PostNeedResponse adds posts.
    16-Oct-2026: ProviderSuggestion gains Etown_category; added CategorySuggestion and MyTownResponse.categories; added CategoryPinRequest and CategoryPinResponse; CategorySuggestion gains Etown_pinned; CategoryPinRequest takes a pin string, "true" or "false", instead of an action; CitySuggestion gains new_need_count, new_update_count and new_event_count.
    20-Oct-2026: NeedItem adds event_status.
    21-Oct-2026: Added NeedTown; NeedItem adds towns; CommunityPostActionRequest takes a town list; event_status description covers start time and end time.
    22-Oct-2026: ProviderOnboardingRequest takes Service_Details, requiring a named service on create; added ProviderOnboardingServiceDetail with serviceimage1-3 and OnboardServiceDetailRequest; ProviderOnboardingResponse adds add_services_message; added OnboardServicesRequest, OnboardServicesInfo and OnboardServicesResponse; added CreateInvoiceRequest.
    23-Oct-2026: AIOnboardingProviderDetails adds city and statecode geocoded from Address; CreateInvoiceRequest counts months from period_start when sent.
    26-Oct-2026: PopularProvidersResponse returns categories and providers as ProviderSuggestion; removed its old provider, category, pagination and metadata models; CategoryPinRequest takes action_mode, latitude and longitude; CategoryPinResponse adds them; ProviderSuggestion and ProviderDetailItem add Etown_slideshow, the provider's image list.
    27-Oct-2026: NeedItem renames review_status to update_status, set on updates only; event_status carries an unapproved event's review state; added ChaatieProcessingTextsRequest and ChaatieProcessingTextsResponse.
"""

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator, ConfigDict
from pydantic.json_schema import SkipJsonSchema
from typing import ClassVar, Optional, List, Union, Dict, Any, Literal
from datetime import datetime, date, time as clock_time
from uuid import UUID

from app.utils.ist_clock import IST
from app.utils.phone import is_valid_indian_mobile, normalize_indian_mobile

class ChaatieRequest(BaseModel):
    prompt: str = None
    contact_id: str = None
    user_profile_id: str = None
    conversation_id: Optional[str] = None
    user_role: Literal[1, 2] = 1
    mobilenumber_accesstoken: Optional[str] = None
    first_name: Optional[str] = None
    language: str = None
    address: Optional[str] = None
    latitude: float = Field(..., description="User latitude (required; -90..90; not (0,0))")
    longitude: float = Field(..., description="User longitude (required; -180..180; not (0,0))")
    oauth_token: Optional[str] = None
    timestamp: Optional[Any] = Field(
        None,
        description=(
            "Accepted for backwards compatibility and ignored. The agent is anchored to the "
            "server's IST clock so a wrong or stale device clock cannot make a valid booking "
            "date look like it has already passed."
        ),
    )
    image_prompt: Optional[str] = Field(
        None,
        description=(
            "Optional image the member is asking about, base64 encoded (plain or data-URL), "
            "same encoding the messaging media fields accept. Stored in blob storage like any "
            "other uploaded image and shown to the model alongside the prompt."
        ),
    )

    @model_validator(mode="after")
    def _validate_coordinates(self) -> "ChaatieRequest":
        """Reject coordinates that are both zero, equal to each other, or outside valid latitude and longitude ranges."""
        lat, lon = self.latitude, self.longitude
        invalid = (
            (lat == 0.0 and lon == 0.0)
            or lat == lon
            or not (-90.0 <= lat <= 90.0)
            or not (-180.0 <= lon <= 180.0)
        )
        if invalid:
            raise ValueError(f"Invalid latitude/longitude: ({lat}, {lon})")
        return self


class ChaatieProcessingTextsRequest(BaseModel):
    """The message being sent to /chaatie, so the app can show matching status lines while it processes."""

    prompt: str = Field(..., min_length=1, max_length=2000, description="The same prompt sent to /api/chat/chaatie")
    user_role: Literal[1, 2] = Field(2, description="1 for a provider account, 2 for a member account")
    language: str = Field("en", description="Language code the status lines are written in, e.g. en, ta, hi")
    conversation_id: Optional[str] = Field(None, description="Conversation id, so a follow-up is read in context")
    has_image: bool = Field(False, description="True when the turn carries an image_prompt")


class ChaatieProcessingTextsResponse(BaseModel):
    """Status lines to cycle through, in order, while the Chaatie turn is processing."""

    texts: List[str] = Field(..., description="Three to five short status lines, each ending with an ellipsis")
    intent: Optional[str] = Field(None, description="The activity the lines describe, e.g. search_provider")
    source: Literal["llm", "template"] = Field(..., description="llm when written for this prompt, else template")


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    version: str


class ItemData(BaseModel):
    """Model for provider item data."""
    item_id: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    type: Optional[str] = None


class DataInsertionValidation(BaseModel):
    """Model for provider profile data insertion."""
    contact_id: str
    user_profile_id: str
    business_name: str
    address: Optional[str] = None
    additionaladdressinfo: Optional[str] = None
    email: Optional[str] = None
    mobilenumber: Optional[str] = None
    website: Optional[str] = None
    item: List[ItemData]
    availability: Optional[str] = None
    service_image: Optional[str] = None
    provider_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    years_in_business: Optional[int] = None


class MobileLookupRequest(BaseModel):
    """Request model for bulk mobile number lookup."""
    mobile_numbers: List[str] = Field(..., description="List of mobile numbers to look up.")
    onboarding: bool = Field(
        default=False,
        description=(
            "Optional, defaults to false. When false the lookup behaves exactly as before: "
            "the supplied numbers are checked against `etown_userprofile` and only the ones "
            "already registered in Etown are returned.\n"
            "Set it to true to additionally get the business details of **all** providers in "
            "the provider table (`etown_provider`) matching each supplied mobile number — "
            "verified and non-verified alike — so an onboarding flow can pre-fill the existing "
            "business profile. Each entry carries its own `publicsearchprovider` value."
        ),
    )


class ProviderBusinessDetail(BaseModel):
    """Business details of a provider row matched by mobile number (onboarding=true)."""
    mobile_number: Optional[str] = Field(
        default=None,
        description="The supplied mobile number this provider row was matched against.",
    )
    providerid: Optional[str] = None
    contactid: Optional[str] = None
    userprofileid: Optional[str] = None
    businessname: Optional[str] = None
    businesscategory: Optional[str] = None
    firstname: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    additionaladdressinfo: Optional[str] = None
    areaname: Optional[str] = None
    city: Optional[str] = None
    townname: Optional[str] = None
    statecode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    mobilenumber: Optional[str] = Field(
        default=None, description="Mobile number as stored on the provider record."
    )
    email: Optional[str] = None
    websiteurl: Optional[str] = None
    availability: Optional[str] = None
    servicetype: Optional[str] = None
    Etown_providertype: Optional[str] = None
    publicsearchprovider: Optional[str] = None
    serviceimageurl: Optional[str] = None
    yearsinbusiness: Optional[str] = None
    role: Optional[int] = None
    rolename: Optional[str] = None
    whatsapp: Optional[bool] = None
    creationdate: Optional[datetime] = None


class MobileLookupResponse(BaseModel):
    """Response model for bulk mobile number lookup."""
    etown_mobile_numbers: List[str]
    total_found: int
    total_searched: int
    onboarding: bool = Field(
        default=False, description="Echoes the onboarding flag the request was made with."
    )
    business_details: Optional[List[ProviderBusinessDetail]] = Field(
        default=None,
        description=(
            "Business details of the providers in `etown_provider` — verified and non-verified "
            "alike — one entry per matched provider row. Populated only when onboarding is "
            "true; null otherwise."
        ),
    )


class ConversationMessage(BaseModel):
    """Model for a conversation message."""
    id: int
    conversation_id: str
    user_id: str
    user_name: str
    role: str
    tools_used: List[str]
    response: str
    response_type: Optional[str] = None
    implicit_output: Optional[Any] = None
    created_at: str

class ConversationSummary(BaseModel):
    """Model for conversation summary."""
    conversation_id: str
    user_id: str
    user_name: str
    message_count: int
    last_message_at: str

class LogRequest(BaseModel):
    """Model for inserting conversation logs."""
    contact_id: str
    user_profile_id: str
    conversation_id: str
    conversation: str
    response_type: Optional[str] = None
    new: Optional[bool] = False
    audio: Optional[bool] = False
    audio_file: Optional[str] = None
    user_name: Optional[str] = "User"
    address: Optional[str] = ""
    language: Optional[str] = "en"
    role: Literal["user", "assistant", "system"] = "assistant"
    tools_used: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class TitleGenerationRequest(BaseModel):
    """Model for title generation request."""
    contact_id: str
    user_profile_id: str
    conversation_id: str


class PromptSuggestionRequest(BaseModel):
    """Model for prompt suggestion request."""
    contact_id: str
    user_profile_id: str
    conversation_id: str
    latest_user_message: Optional[str] = None
    name: Optional[str] = "User"
    address: Optional[str] = ""
    time: Optional[str] = None
    language: Optional[str] = "en"


class ConversationListRequest(BaseModel):
    """Request model for fetching conversation list with titles."""
    contact_id: str
    user_profile_id: str
    page: int = 1
    limit: int = 10

class ConversationTitleItem(BaseModel):
    """Model for a single conversation title item."""
    conversation_id: str
    title: str
    date: str

class ConversationListResponse(BaseModel):
    """Response model for conversation list."""
    titles: List[ConversationTitleItem]
    total_count: int
    page: int
    limit: int


class FeedLikeDetails(BaseModel):
    """Compact like summary for a feed item ("liked by X and N others").

    The featured liker is the requester's My Circle member/provider who liked
    most recently, falling back to the most recent liker overall. The requester's
    own like ranks last, surfacing only when nobody else liked the post. count is
    the post's total likes minus the featured one. likedbyimage is the featured
    liker's own picture (null if they have none); likedbyimage2 is the next liker
    in priority order that has one, never the requester.
    """
    likedby: Optional[str] = None
    count: int = 0
    likedbyimage: Optional[str] = None
    likedbyimage2: Optional[str] = None

    @field_validator('likedby')
    @classmethod
    def truncate_likedby(cls, v: Optional[str]) -> Optional[str]:
        """Truncate likedby to 12 characters followed by an ellipsis when longer."""
        if v and len(v) > 12:
            return v[:12] + "..."
        return v


class EtownFeedItem(BaseModel):
    """Model representing a single feed item with all its properties"""
    Etown_feedid: Optional[str] = None
    Etown_feedfilelocationurl: Optional[str] = None
    Etown_feedfilelocationurl2: Optional[str] = None
    Etown_feedfilelocationurl3: Optional[str] = None
    Etown_feedfilelocationurl4: Optional[str] = None
    Etown_feedfilelocationurl5: Optional[str] = None
    Etown_feedvideofilelocation: Optional[str] = None
    Etown_thumbnailfileurl: Optional[str] = None
    Etown_feedtext: Optional[str] = None
    Etown_servicename: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_role: Optional[int] = None
    Etown_profileimage: Optional[str] = None
    Etown_ets_serviceimageurl: Optional[str] = None
    Etown_commentscount: Optional[str] = ""
    Etown_feedlikecount: Optional[str] = ""
    Etown_feedlikecontactperson: Optional[bool] = None
    Etown_likedPerson: Optional[str] = ""
    Etown_createdon: Optional[str] = None
    Etown_name: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_mycircle: Optional[str] = ""
    Etown_mycirclerole: Optional[str] = ""
    Etown_invitation: Optional[str] = ""
    Etown_feedcategory: Optional[str] = None
    Etown_businesscategory: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_is_liked_by_circle: Optional[bool] = None
    Etown_distance_km: Optional[float] = None
    Etown_distance_meters: Optional[int] = None
    Etown_latitude: Optional[float] = None
    Etown_longitude: Optional[float] = None
    Etown_is_trending: Optional[bool] = None
    Etown_engagement_score: Optional[float] = None
    Etown_relevance_score: Optional[float] = None
    Etown_orientation: Optional[str] = None
    feedlikedetails: Optional[FeedLikeDetails] = None

class FeedGroup(BaseModel):
    """A target group for a feed post (stored in etown_feed.groups jsonb)."""
    groupid: str


class FeedPhonebookContact(BaseModel):
    """A phonebook contact for a feed post (stored in etown_feed.phonebook jsonb)."""
    mobilenumber: str


class CreateFeedRequest(BaseModel):
    """
    Model for creating a new feed item.
    """
    contact_id: str
    userprofile_id: str
    firstname: str
    role: int
    feed_text: Optional[str] = None
    servicename: Optional[str] = None
    audience: Optional[str] = Field(
        None,
        description=(
            'Who the post is for: "mycircle" (the author\'s circle only) or "public" (the '
            'public feed). Stored as audiencename, with its choice value in audience, and it '
            'is what sets the row\'s mycircle flag. Omit to leave the audience unset.'
        ),
        examples=["mycircle", "public"],
    )
    Oauth: Optional[str] = None
    tags: Optional[str] = None
    service_imagefile: Optional[str] = None
    feed_file: Optional[str] = None
    thumbnail_file: Optional[str] = None
    feedfile2: Optional[str] = None
    feedfile3: Optional[str] = None
    feedfile4: Optional[str] = None
    feedfile5: Optional[str] = None
    videofile: Optional[str] = None
    orientation: Optional[str] = None
    social_media: Optional[List[str]] = None
    sharetooffer: Optional[bool] = False
    groups: Optional[List[FeedGroup]] = None
    phonebook: Optional[List[FeedPhonebookContact]] = None

    @field_validator("audience", mode="before")
    @classmethod
    def _audience_to_text(cls, value):
        """Convert an integer audience from older clients to its string form."""
        return str(value) if isinstance(value, int) else value

class FeedCommentItem(BaseModel):
    """Model representing a single feed comment with commenter details."""
    Etown_feedcommentsid: Optional[str] = None
    Etown_servicename: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_name: Optional[str] = None
    Etown_textcomment: Optional[str] = None
    Etown_role: Optional[int] = None
    Etown_providertype: Optional[str] = None
    Etown_commentcreateddate: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_imageurl: Optional[str] = None
    Etown_deleted: Optional[str] = None
    Etown_deactivated: Optional[str] = None


class FeedLikeUserItem(BaseModel):
    """Model representing a single user who liked a feed post."""
    Etown_name: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_profileimage: Optional[str] = None
    Etown_role: Optional[int] = None
    Etown_providertype: Optional[str] = None
    Etown_isconnected: Optional[bool] = None
    Etown_invitation: Optional[str] = None
    Etown_likedon: Optional[str] = None
    Etown_deleted: Optional[str] = None
    Etown_deactivated: Optional[str] = None


class FeedExecutionLogs(BaseModel):
    """Model for tracking feed endpoint performance metrics"""
    total_execution_time_ms: float
    main_feed_query_time_ms: float
    trending_check_time_ms: float
    offers_check_time_ms: float
    parallel_tasks_time_ms: float
    category_processing_time_ms: float
    localization_time_ms: float
    database_total_time_ms: float
    timestamp: str
    user_profile_id: str
    mode: str
    feeds_count: int
    database_queries_count: int = 3
    performance_breakdown: dict


class FullFeedResponse(BaseModel):
    """Complete response model for feed endpoint including feeds and localized categories"""
    feeds: List[EtownFeedItem]
    feed_categorize: List[str]
    feed_categorize_English: List[str]
    feed_categorize_Hindi: List[str]
    feed_categorize_Tamil: List[str]
    feed_categorize_Malayalam: List[str]
    feed_categorize_Telugu: List[str]
    logs: FeedExecutionLogs


class ServiceGenerationValidation(BaseModel):
    """Request model for service generation."""
    business_description: str


class ServiceItem(BaseModel):
    """Individual service or product item."""
    type: str
    category: str
    name: str
    description: str
    generalcategory: str


class ServiceGenerationResponse(BaseModel):
    """Response model for service generation."""
    status: str
    business_description: str
    analysis: str
    recommended_services: List[ServiceItem]
    thought_process: str
    total_recommendations: int

class LocationDetailsResponse(BaseModel):
    """Response model for location details."""
    parsed_components: Dict[str, str]
    raw: Dict[str, Any]

class GeocodingResponse(BaseModel):
    """Response model for address geocoding."""
    latitude: float
    longitude: float
    formatted_address: str

class PublicProviderDetailsGeneratorRequest(BaseModel):
    """Request model for generating details from a place ID."""
    place_id: str = Field(..., description="The Google Maps Place ID.")

class PublicProviderItem(BaseModel):
    """Model for a generated item/service."""
    type: str
    category: str
    generalcategory: str
    name: str
    description: str

class PublicProviderDetailsData(BaseModel):
    items: List[PublicProviderItem]

class PublicProviderDetailsResponse(BaseModel):
    """Response model for generated public provider details."""
    data: PublicProviderDetailsData

class GenerateTownDetailsResponse(BaseModel):
    """Response Model for generated town details"""
    townname: str
    cityname: str
    description: str
    latitude: float
    longitude: float
    image: str


class CategoryGenerationRequest(BaseModel):
    """Request model for generating business categories."""
    description: str = Field(
        ...,
        max_length=500,
        description="The business description to be categorized."
    )

class CategoryGenerationResponse(BaseModel):
    """Final response model containing the business type, subcategory, and general category."""
    business_type: str
    subcategory: str
    generalcategory: Optional[str] = None

class ServiceDetailRequest(BaseModel):
    """A single service entry submitted when adding services to an existing provider."""
    service_name: str = Field(..., description="Name of the service being added")
    serviceimage1: Optional[str] = Field(None, description="Base64-encoded service image 1 (or null/empty)")
    serviceimage2: Optional[str] = Field(None, description="Base64-encoded service image 2 (or null/empty)")
    serviceimage3: Optional[str] = Field(None, description="Base64-encoded service image 3 (or null/empty)")
    pricerange: Optional[str] = Field(None, description="Price range for the service")
    description: Optional[str] = Field(None, description="Service description (used for category classification)")
    quantity: Optional[str] = Field(None, description="Quantity/unit information for the service")
    serviceitems: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Offering categories/items (from /api/offerings/image) preserved verbatim as JSON on the service row",
    )

class OnboardServiceDetailRequest(ServiceDetailRequest):
    """A Service_Details entry from POST /provider/onboard, carrying its type for the service row's businesscategory."""
    service_type: Optional[str] = Field(
        None,
        description="'product' or 'service', stored as the service row's businesscategory instead of the AI's",
    )

class GeneralCategoryGenerationRequest(BaseModel):
    """Request model for generating general business categories."""
    description: str = Field(
        ...,
        max_length=650,
        description="The business description to be categorized into general categories."
    )

class GeneralCategoryGenerationResponse(BaseModel):
    """Final response model containing the general category."""
    generalcategory: str


class BusinessDescriptionRequest(BaseModel):
    """Request model for business description enhancement"""
    description: str = Field(
        ..., 
        min_length=5, 
        max_length=500, 
        description="Original business description to enhance (5-500 characters)"
    )
    business_name: Optional[str] = Field(
        None,
        max_length=200,
        description="Trading name, so the rewrite can name the trade the business works in"
    )
    place_types: Optional[List[str]] = Field(
        None,
        description="Google place types for the business, umbrella entries already removed"
    )
    reviews: Optional[List[str]] = Field(
        None,
        description="Customer review texts, used as evidence for what the business offers"
    )
    type: Literal["business_description", "need", "event", "update"] = Field(
        "business_description",
        description="What the text describes: business_description (default), or a community need, "
                    "event or update post; blank is read as business_description"
    )

    @field_validator('type', mode='before')
    @classmethod
    def normalize_type(cls, v: Optional[str]) -> str:
        """Lower-case and trim the type, reading a blank or missing value as business_description."""
        if v is None or not str(v).strip():
            return "business_description"
        return str(v).strip().lower()

    @field_validator('description')
    @classmethod
    def validate_description(cls, v: str) -> str:
        """Collapse whitespace in the description and reject it when empty or under 5 characters."""
        import re
        if not v or not v.strip():
            raise ValueError("Description cannot be empty")
        cleaned = re.sub(r'\s+', ' ', v.strip())
        if len(cleaned) < 5:
            raise ValueError("Description must be at least 5 characters after cleaning")
        return cleaned

class BusinessDescriptionResponse(BaseModel):
    """Response model for enhanced business description"""
    original_description: str
    enhanced_description: str
    word_count: int
    enhancement_applied: bool
    message: Optional[str] = None


class ProviderFeedDetails(BaseModel):
    """Model for provider's feed details."""
    Etown_feedid: Optional[str] = None
    Etown_feedfilelocationurl: Optional[str] = None
    Etown_feedfilelocationurl2: Optional[str] = None
    Etown_feedfilelocationurl3: Optional[str] = None
    Etown_feedfilelocationurl4: Optional[str] = None
    Etown_feedfilelocationurl5: Optional[str] = None
    Etown_feedvideofilelocation: Optional[str] = None
    Etown_thumbnailfileurl: Optional[str] = None
    Etown_feedtext: Optional[str] = None
    Etown_commentscount: Optional[str] = None
    Etown_feedlikecount: Optional[str] = None
    Etown_feedlikecontactperson: Optional[str] = None
    Etown_createdon: Optional[str] = None
    Etown_modifiedon: Optional[str] = None

class ProviderRatingDetails(BaseModel):
    """Model for individual rating details from completed requests."""
    Etown_requestorname: Optional[str] = None
    Etown_ratingvalue: Optional[str] = None
    Etown_ratingdate: Optional[str] = None
    Etown_profileimage: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_servicebooked: Optional[str] = None
    Etown_servicedescription: Optional[str] = None
    Etown_source: Optional[str] = None

class ProviderDetailItem(BaseModel):
    """Model for detailed provider information."""
    Etown_ProviderId: Optional[str] = None
    Etown_businessname: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_fetchaddress: Optional[str] = None
    Etown_description: Optional[str] = None
    Etown_availability: Optional[str] = None
    Etown_latitude: Optional[str] = None
    Etown_longitude: Optional[str] = None
    Etown_serviceimageurl: Optional[str] = None
    Etown_background_imageUrl: Optional[str] = None
    Etown_slideshow: List[str] = []
    Etown_city: Optional[str] = None
    Etown_areaname: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_publicsearchprovider: Optional[str] = None
    Etown_unverified_msg: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_mobile: Optional[str] = None
    Etown_alternatemobile: Optional[str] = None
    Etown_whatsapp: bool = True
    Etown_createdon: Optional[str] = None
    Etown_overallrating: Optional[str] = None
    Etown_ratingcount: Optional[str] = None
    Etown_completedservices: Optional[str] = None
    Etown_Mycircle: Optional[str] = ""
    Etown_MyCircleCount: Optional[str] = "0"
    Etown_emailaddress: Optional[str] = None
    Etown_favouritecheck: Optional[str] = "false"
    Etown_CollectionId: Optional[str] = ""
    Etown_servicetype: Optional[str] = None

class SubServiceDetailItem(BaseModel):
    """Model for sub-service details."""
    Etown_providerserviceid: Optional[str] = None
    Etown_servicename: Optional[str] = None
    Etown_businesscategory: Optional[str] = None
    Etown_servicecategory: Optional[str] = None
    Etown_providerid: Optional[str] = None
    Etown_serviceimage1: Optional[str] = None
    Etown_serviceimage2: Optional[str] = None
    Etown_serviceimage3: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_additionaladdressinfo: Optional[str] = None
    Etown_pricerange: Optional[str] = None
    Etown_servicecreatedon: Optional[str] = None
    Etown_servicedescription: Optional[str] = None
    Etown_quantity: Optional[str] = None
    Etown_serviceitems: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Offering categories/items stored with this service (from /api/offerings/image); null when none were captured",
    )

class ProviderDetailsResponse(BaseModel):
    """Response model for provider details endpoint."""
    Providerdetails: List[ProviderDetailItem]
    Subservicedetails: List[SubServiceDetailItem]
    Feeddetails: List[ProviderFeedDetails]
    Ratingdetails: List[ProviderRatingDetails]
    offering_data: List["ParsedOfferingData"] = Field(
        default_factory=list,
        description="Full offering→category→item tree for the provider (from etown_offerings.items); [] when none captured. Images are Azure Blob URLs.",
    )
    plan_code: Optional[str] = Field(
        None,
        description="Subscription plan the provider is on (free/silver/gold/platinum); null when it could not be resolved.",
    )
    badge_label: Optional[str] = Field(
        None,
        description="Badge text to show against the business, e.g. 'Verified' or 'Silver Member'.",
    )
    verified_badge: bool = Field(
        False,
        description="Whether the provider's plan grants the verified business badge.",
    )


class CollectionItem(BaseModel):
    """Model for a user's collection."""
    Etown_collectionid: str
    Etown_collectionname: str
    Etown_collectiontype: str
    Etown_isMytown: Optional[str] = None
    Etown_createdon: Optional[str] = None
    Etown_collectioncount: Optional[int] = 0
    items: List[Dict[str, Any]] = [] 
    
class CollectionResponse(BaseModel):
    """Response model for /mytowncollections."""
    collections: List[CollectionItem]

class MyTownSearchResponse(BaseModel):
    """Response model for /mytownsearch."""
    providers: List['ProviderSuggestion']
    areas: List['AreaSuggestion']
    cities: List['CityItem']
    keywords: List[str] = []

class TownSearchResponse(BaseModel):
    """Response model for partial town-name searches."""
    towns: List['CityItem']
    page: int = 1
    limit: int = 20
    total: int = 0
    total_pages: int = 0
    similar_results: bool = False

class CitySuggestion(BaseModel):
    """Model for city name and description."""
    name: str
    townprofileid: Optional[str] = None
    description: str
    image: str = ""
    apihitcount: int = 0
    viewcount: int = 0
    providecount: int = 0
    verified_count: int = 0
    unverified_count: int = 0
    isfavourite: bool = False
    favouritetownid: Optional[str] = None
    new_need_count: Optional[int] = None
    new_update_count: Optional[int] = None
    new_event_count: Optional[int] = None

class AreaSuggestion(BaseModel):
    """Model for a single area suggestion."""
    area_name: str
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    distance_km: Optional[float] = None
    city_name: Optional[str] = None
    state_code: Optional[str] = None
    provider_count: Optional[int] = None
    verified_count: Optional[int] = None
    unverified_count: Optional[int] = None

class ProviderSuggestion(BaseModel):
    """Model for a single provider suggestion, using Etown_ prefixes."""
    Etown_providerid: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_role: Optional[int] = 1
    Etown_rolename: Optional[str] = "Provider"
    Etown_description: Optional[str] = None
    Etown_firstname: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_isconnected: bool = False
    Etown_mobilenumber: Optional[str] = None
    Etown_email: Optional[str] = None
    Etown_profileimage: Optional[str] = None
    Etown_serviceimageurl: Optional[str] = None
    Etown_background_imageUrl: Optional[str] = None
    Etown_slideshow: List[str] = []
    Etown_offerimageurl: Optional[str] = None
    Etown_createdon: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_areaname: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_latitude: Optional[str] = None
    Etown_longitude: Optional[str] = None
    Etown_publicsearchprovider: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_memberbookingcount: int = 0
    Etown_ratingcount: int = 0
    Etown_overallrating: float = 0.0
    Etown_total_members: Optional[int] = 0
    Etown_distance_km: Optional[float] = None
    Etown_resultType: int = 1
    Etown_isfavourite: Optional[str] = None
    Etown_collectionid: Optional[str] = None
    Etown_collectionitemsid: Optional[str] = None
    Etown_item_createdon: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_badge_label: Optional[str] = None
    Etown_category: Optional[str] = None

class CategorySuggestion(BaseModel):
    """Model for a provider category offered in a town or area, using Etown_ prefixes."""
    Etown_categoryid: Optional[str] = None
    Etown_categoryname: str
    Etown_displayname: Optional[str] = None
    Etown_categoryimage: Optional[str] = None
    Etown_providercount: int = 0
    Etown_pinned: bool = False

class CategoryPinRequest(BaseModel):
    """Request payload to pin or unpin a category for a user in a town and area, or at a popular-providers location."""
    contact_id: str = Field(..., min_length=1, description="The contact ID of the user")
    userprofile_id: str = Field(..., min_length=1, description="The user profile ID of the user")
    action_mode: Literal["town", "popular_provider"] = Field("town", description="\"town\" pins in cityname and areaname; \"popular_provider\" pins at latitude and longitude")
    cityname: Optional[str] = Field(None, description="The town the pin applies to; required for action_mode town")
    areaname: Optional[str] = Field(None, description="The area the pin applies to; omit for the whole town")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="The popular-providers latitude; required for action_mode popular_provider")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="The popular-providers longitude; required for action_mode popular_provider")
    category_id: UUID = Field(..., description="The etown_category id")
    pin: Literal["true", "false"] = Field(..., description="\"true\" pins the category; \"false\" unpins it")

    @field_validator("pin", mode="before")
    @classmethod
    def _normalize_pin(cls, value):
        """Lowercase and trim a string pin so "True" and " false " are accepted."""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("action_mode", mode="before")
    @classmethod
    def _normalize_action_mode(cls, value):
        """Lowercase a string action_mode, joining words with underscores, and default a blank one to town."""
        if value is None:
            return "town"
        if isinstance(value, str):
            return "_".join(value.lower().replace("-", " ").split()) or "town"
        return value

class CategoryPinResponse(BaseModel):
    """Response payload for a category pin or unpin."""
    success: bool = Field(True, description="True when the pin state was saved")
    message: str = Field(..., description="Outcome message")
    action_mode: Literal["town", "popular_provider"] = Field("town", description="Where the pin was stored: town or popular_provider")
    cityname: Optional[str] = Field(None, description="The town, trimmed and lowercased as stored; null for action_mode popular_provider")
    areaname: Optional[str] = Field(None, description="The area, trimmed and lowercased as stored; null for the whole town")
    latitude: Optional[float] = Field(None, description="The latitude as stored, to 4 decimals; null for action_mode town")
    longitude: Optional[float] = Field(None, description="The longitude as stored, to 4 decimals; null for action_mode town")
    categorypinid: Optional[str] = Field(None, description="The ID of the etown_category_pin row, or the etown_popular_category_pin row for popular_provider")
    category_id: str = Field(..., description="The etown_category id")
    categoryname: Optional[str] = Field(None, description="The category's name in etown_category")
    displayname: Optional[str] = Field(None, description="The category's display name")
    ispinned: bool = Field(..., description="The pin state after this call")

class TownImageItem(BaseModel):
    """Model for a town image."""
    imageurl: str
    name: str
    caption: Optional[str] = None
    createdon: Optional[str] = None
    townprofileid: Optional[str] = None
    verification: Optional[str] = None
    is_own_upload: bool = False

class CityItem(BaseModel):
    """Model for a city in the available cities list."""
    name: str = ""
    latitude: Optional[str] = None
    cityname: Optional[str] = None
    longitude: Optional[str] = None
    image: str = ""
    distance_km: Optional[float] = None
    provider_count: int = 0
    verified_count: int = 0
    unverified_count: int = 0
    townprofileid: Optional[str] = None
    favouritetownid: Optional[str] = None
    createdon: Optional[str] = None
    description: str = ""
    favourite_count: int = 0
    view_count: int = 0
    api_hit_count: int = 0
    isfavourite: bool = False
    town_images: List[TownImageItem] = []

class MyTownResponse(BaseModel):
    """
    Unified response model for /location_based_suggestions
    and /mytown_suggestions.
    """
    city: Optional[CitySuggestion] = None
    nearby_areas: Optional[List[AreaSuggestion]] = None
    verified_providers: Optional[List[ProviderSuggestion]] = None
    categories: Optional[List[CategorySuggestion]] = None
    available_cities: List[CityItem]
    favourite_cities: Optional[List[CityItem]] = None
    favourite_collection: Optional[List[CollectionItem]] = None
    saved_providers: Optional[List[ProviderSuggestion]] = None

class VerifiedAreasResponse(BaseModel):
    """
    Specific response model for /get_verified_areas.
    """
    verified_areas: List[AreaSuggestion]
    available_cities: List[CityItem]

class TownPresenceResponse(BaseModel):
    """Response model for town presence check."""
    townnamepresent: bool = False
    administartivelevel3present: bool = False
    administartivelevel3value: Optional[str] = None
    townprofileid: Optional[str] = None
    townname: Optional[str] = None
    cityname: Optional[str] = None
    townstatus: Optional[str] = None

class TownManagementResponse(BaseModel):
    """Response model for town management operations."""
    areaname: Optional[str] = None
    cityname: Optional[str] = None
    townname: Optional[str] = None
    statecode: Optional[str] = None

class UploadTownImageItem(BaseModel):
    """One image and optional caption inside an upload_townimage batch."""
    image: str = Field(..., description="The base64 encoded image string")
    caption: Optional[str] = Field(None, description="Optional caption for the image")

class UploadTownImageRequest(BaseModel):
    """Request payload to upload one or more town images."""
    userprofileid: str = Field(..., description="The user profile ID making the request")
    contactid: str = Field(..., description="The contact ID of the user")
    townprofileid: str = Field(..., description="The ID of the town profile")
    townimage: Union[List[UploadTownImageItem], List[str], str] = Field(
        ...,
        description=(
            'One or more base64 encoded images: an array of {"image": "<base64>", "caption": "..."} objects, '
            'an array of base64 strings, or a single base64 string. Max 10 per request. '
            'Whatever the shape, the field is normalised to a list of base64 strings.'
        ),
    )

    @field_validator("townimage", mode="before")
    @classmethod
    def normalize_townimage(cls, v: Any) -> List[UploadTownImageItem]:
        """Coerce strings, dicts or items into 1 to 10 UploadTownImageItem entries with non-empty images."""
        images: List[UploadTownImageItem] = []
        for item in (v if isinstance(v, list) else [v]):
            if isinstance(item, dict):
                image = item.get("image") or item.get("townimage")
                caption = item.get("caption")
            elif isinstance(item, UploadTownImageItem):
                image = item.image
                caption = item.caption
            else:
                image = item
                caption = None
            if not isinstance(image, str) or not image.strip():
                raise ValueError("Each town image must be a non-empty base64 string")
            images.append(UploadTownImageItem(image=image, caption=caption))
        if not images:
            raise ValueError("At least one town image is required")
        if len(images) > 10:
            raise ValueError("At most 10 town images can be uploaded in one request")
        return images

class UploadedTownImage(BaseModel):
    """One stored town image returned by upload_townimage."""
    townimageid: str = Field(..., description="The ID of the stored town image record")
    townimageurl: str = Field(..., description="The URL of the uploaded image")
    caption: Optional[str] = Field(None, description="The caption saved for the image")

class RequestTownImageRequest(BaseModel):
    """Request payload to submit one town image for review."""
    townprofileid: str = Field(..., min_length=1, description="The ID of the town profile the image is for")
    contactid: str = Field(..., min_length=1, description="The contact ID of the requester")
    userprofileid: str = Field(..., min_length=1, description="The user profile ID of the requester")
    image: str = Field(..., min_length=1, description="The base64 encoded image, with or without a data-URL prefix")
    filename: str = Field(
        ..., min_length=1, max_length=255,
        description="The image's file name, stored as-is and used as the last segment of the blob path; no slashes",
    )
    description: Optional[str] = Field(None, description="Optional description of the image")

class RequestTownImageResponse(BaseModel):
    """Response payload for a submitted town image review request."""
    success: bool = Field(True, description="True when the image was stored and the request recorded")
    message: str = Field(..., description="Success message")
    townreviewid: str = Field(..., description="The ID of the etown_townreview record")
    townprofileid: str = Field(..., description="The ID of the town profile")
    townname: Optional[str] = Field(None, description="The town's name from etown_townprofile")
    contactid: str = Field(..., description="The contact ID of the requester")
    contactidname: Optional[str] = Field(None, description="The requester's first name from etown_contact")
    userprofileid: str = Field(..., description="The user profile ID of the requester")
    imageurl: str = Field(..., description="The relative blob path /{container}/townreview/{townreviewid}/{filename}")
    filename: str = Field(..., description="The exact filename the image was requested under")
    description: Optional[str] = Field(None, description="The description saved with the image")
    status: str = Field("pending", description="Review status, pending on create")

class UploadTownImageResponse(BaseModel):
    """Response payload for uploading town images."""
    success: bool = Field(True, description="True if at least one image was successfully uploaded")
    message: str = Field(..., description="Success message")
    townimageurl: str = Field(..., description="The URL of the first uploaded image")
    townimages: List[UploadedTownImage] = Field(
        default_factory=list, description="Every image stored by this request, with its record ID"
    )

class VerifiedProvidersResponse(BaseModel):
    """Response model for verified providers endpoint."""
    providers: List[ProviderSuggestion]


class PopularProvidersResponse(BaseModel):
    """Response model for popular providers near you: the categories list and the All tab's providers, shaped as /mytown_suggestions providers."""
    categories: List[CategorySuggestion] = Field(default_factory=list)
    providers: List[ProviderSuggestion] = Field(default_factory=list)

class AISearchRequest(BaseModel):
    """Request model for AI search endpoint."""
    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    contact_id: str = Field(..., description="Current user's contact ID")
    user_profile_id: str = Field(..., description="Current user's profile ID")
    mycircle: bool = Field(False, description="Filter results to only show users in My Circle")
    limit: int = Field(10, description="Maximum number of results", ge=1, le=100)
    offset: int = Field(0, description="Number of results to skip", ge=0)
    latitude: Optional[float] = Field(None, description="User's latitude for distance-based provider sorting", ge=-90, le=90)
    longitude: Optional[float] = Field(None, description="User's longitude for distance-based provider sorting", ge=-180, le=180)

class SearchMemberResult(BaseModel):
    """Model for a member search result."""
    Etown_userprofileid: Optional[str] = None
    Etown_role: Optional[int] = None
    Etown_rolename: Optional[str] = None
    Etown_firstname: Optional[str] = None
    Etown_lastname: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_email: Optional[str] = None
    Etown_mobilenumber: Optional[str] = None
    Etown_profileimage: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_areaname: Optional[str] = None
    Etown_createdon: Optional[Union[str, datetime]] = None
    Etown_similarity: Optional[float] = None
    Etown_isconnected: Optional[bool] = None
    Etown_invitation: Optional[str] = None
    Etown_status: Optional[str] = None
    Etown_mycircle_count: Optional[int] = 0
    providercurrent: Optional[List[Dict[str, Any]]] = None
    mutualconnections: Optional[List[Dict[str, Any]]] = None

class SearchProviderResult(BaseModel):
    """Model for a provider search result."""
    Etown_userProfileId: Optional[str] = None
    Etown_role: Optional[int] = None
    Etown_rolename: Optional[str] = None
    Etown_description: Optional[str] = None
    Etown_businessName: Optional[str] = None
    Etown_businessCategory: Optional[str] = None
    Etown_services: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_firstname: Optional[str] = None
    Etown_ownername: Optional[str] = None
    Etown_email: Optional[str] = None
    Etown_mobilenumber: Optional[str] = None
    Etown_profileimage: Optional[str] = None
    Etown_serviceimageurl: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_areaname: Optional[str] = None
    Etown_latitude: Optional[str] = None
    Etown_longitude: Optional[str] = None
    Etown_publicSearchProvider: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_availability: Optional[str] = None
    Etown_createdon: Optional[Union[str, datetime]] = None
    Etown_servicedescription: Optional[str] = None
    Etown_similarity: Optional[float] = None
    Etown_distance_km: Optional[float] = None
    Etown_distance_meters: Optional[int] = None
    Etown_memberbookingcount: Optional[int] = None
    Etown_ratingcount: Optional[int] = None
    Etown_overallrating: Optional[float] = None
    Etown_status: Optional[str] = None
    Etown_invitation: Optional[str] = None
    Etown_isconnected: Optional[bool] = None
    Etown_mycircle_count: Optional[int] = 0
    Etown_favourite_count: Optional[int] = 0

class SearchResults(BaseModel):
    """Model for search results containing members, providers, areas, and cities."""
    member: List[SearchMemberResult] = []
    provider: List[SearchProviderResult] = []
    areas: List[AreaSuggestion] = []
    cities: List[CityItem] = []
    keywords: List[str] = []

class UniformPerformanceMetrics(BaseModel):
    """Enhanced uniform performance metrics for all API endpoints.
    
    Features:
        - Comprehensive timing: Total response time with granular breakdowns
        - Cache tracking: Hit/miss status across endpoints
        - External monitoring: Database and API operation times
        - Request metadata: Endpoint name and timestamp for tracking
    """
    response_time_seconds: float
    database_time_ms: float = 0.0
    external_api_time_ms: float = 0.0
    cache_hit: bool = False
    timestamp: str = ""
    endpoint_name: str = ""

class AISearchResponse(BaseModel):
    """Response model for AI search endpoint."""
    status: str
    query: str
    original_query: str
    translation_applied: bool = False
    detected_language: str = "en"
    correction_applied: bool = False
    correction_confidence: float = 0.0
    results: SearchResults


class OfferViewItem(BaseModel):
    """Model for a single offer viewer record."""
    Etown_name: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_watchedtime: Optional[str] = None

class OfferItem(BaseModel):
    """Model for a single offer item."""
    Etown_businessname: Optional[str] = None
    Etown_businessprofileimageurl: Optional[str] = None
    Etown_colorcode: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_contactidname: Optional[str] = None
    Etown_createdon: Optional[str] = None
    Etown_createdtime: Optional[str] = None
    Etown_description: Optional[str] = None
    Etown_fontfamily: Optional[str] = None
    Etown_fontsize: Optional[str] = None
    Etown_fontstyle: Optional[str] = None
    Etown_latitude: Optional[float] = None
    Etown_longitude: Optional[float] = None
    Etown_statecode: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_offerfile1: Optional[str] = None
    Etown_offerfile2: Optional[str] = None
    Etown_offerfile3: Optional[str] = None
    Etown_offerfile4: Optional[str] = None
    Etown_offerfile5: Optional[str] = None
    Etown_offersid: Optional[str] = None
    Etown_offertype: Optional[str] = None
    Etown_offertypename: Optional[str] = None
    Etown_offerunicid: Optional[str] = None
    Etown_offervideofile: Optional[str] = None
    Etown_title: Optional[str] = None
    Etown_scheduledendtime: Optional[str] = None
    Etown_scheduledtime: Optional[str] = None
    Etown_tags: Optional[str] = None
    Etown_thumbnailfileurl: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_userprofileidname: Optional[str] = None
    Etown_validity: Optional[str] = None
    Etown_vieweduserprofileid: Optional[str] = None
    Etown_viewcount: int
    Etown_watchedstatus: bool
    Etown_is_expired: Optional[bool] = None
    Etown_providertype: Optional[str] = None
    Etown_views: List["OfferViewItem"] = []

class ProviderOfferGroup(BaseModel):
    """Provider group containing all their offers for WhatsApp story-style display."""
    Etown_userprofileid: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_businessname: Optional[str] = None
    Etown_businessprofileimageurl: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_has_unviewed: bool
    Etown_offers: List[OfferItem]

class ViewedOfferRequest(BaseModel):
    """Request model for marking an offer as viewed."""
    offersid: str
    userprofileid: str

class OfferViewCountResponse(BaseModel):
    """Response model for offer view count."""
    offersid: str
    viewcount: int

class OfferContentItem(BaseModel):
    """Response model for offer content fetched by offerunicid, including provider city and statecode."""
    Etown_offersid: Optional[str] = None
    Etown_businessname: Optional[str] = None
    Etown_businessprofileimageurl: Optional[str] = None
    Etown_colorcode: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_contactidname: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_userprofileidname: Optional[str] = None
    Etown_offerunicid: Optional[str] = None
    Etown_offertype: Optional[str] = None
    Etown_offertypename: Optional[str] = None
    Etown_fontfamily: Optional[str] = None
    Etown_description: Optional[str] = None
    Etown_thumbnailfileurl: Optional[str] = None
    Etown_offerfile1: Optional[str] = None
    Etown_offerfile2: Optional[str] = None
    Etown_offerfile3: Optional[str] = None
    Etown_offerfile4: Optional[str] = None
    Etown_offerfile5: Optional[str] = None
    Etown_offervideofile: Optional[str] = None
    Etown_validity: Optional[str] = None
    Etown_createdtime: Optional[str] = None
    Etown_scheduledtime: Optional[str] = None
    Etown_scheduledendtime: Optional[str] = None
    Etown_latitude: Optional[float] = None
    Etown_longitude: Optional[float] = None
    Etown_createdon: Optional[str] = None
    Etown_tags: Optional[str] = None
    Etown_vieweduserprofileid: Optional[str] = None
    Etown_isactive: Optional[str] = None
    Etown_languagecode: Optional[str] = None
    Etown_title: Optional[str] = None
    Etown_fontsize: Optional[str] = None
    Etown_fontstyle: Optional[str] = None
    Etown_prov_city: Optional[str] = None
    Etown_prov_statecode: Optional[str] = None

class ValidPendingOfferItem(BaseModel):
    """Response model for a valid or pending offer."""
    Etown_offersid: Optional[str] = None
    Etown_businessname: Optional[str] = None
    Etown_offerdescription: Optional[str] = None
    Etown_createdtime: Optional[str] = None
    Etown_scheduledtime: Optional[str] = None
    Etown_scheduleendtime: Optional[str] = None
    Etown_offertype: Optional[str] = None
    Etown_validity: Optional[str] = None


class NotificationData(BaseModel):
    """Notification data payload model."""
    type: str
    route: str
    isVideo: str
    userprofile: str
    opponentuserprofile: str
    call_url: str
    expires_at: str
    initiator_participant_id: str
    initiator_token: str
    room_id: str
    room_name: str
    session_id: str
    status: str
    target_participant_id: str
    target_token: str
    target_role: Optional[str] = None
    inviter_profile_img: Optional[str] = None

class NotificationPayload(BaseModel):
    """FCM notification payload model."""
    title: str
    body: str

class FCMMessage(BaseModel):
    """FCM message structure model."""
    token: str
    notification: NotificationPayload
    data: NotificationData

class PushNotificationRequest(BaseModel):
    """Push notification request model for PowerApps/FCM integration."""
    oauth_token: str = Field(..., description="OAuth token for FCM authentication")
    title: str = Field(..., description="Notification title")
    body: str = Field(..., description="Notification body text")
    is_video: str = Field(..., description="Video call flag ('true'/'false')")
    initiator_userprofile_id: str = Field(..., description="User profile ID of call initiator")
    target_userprofile_id: str = Field(..., description="User profile ID of call target")
    room_id: Optional[str] = Field(None, description="Existing room ID if joining a pre-created room")
    room_name: Optional[str] = Field(None, description="Existing room name if joining a pre-created room")
    session_id: Optional[str] = Field(None, description="Existing session ID if joining a pre-created session")
    initiator_token: Optional[str] = Field(None, description="Existing initiator JWT token if joining a pre-created room")
    target_token: Optional[str] = Field(None, description="Existing target JWT token if joining a pre-created room")
    initiator_participant_id: Optional[str] = Field(None, description="Existing initiator participant ID if joining a pre-created room")
    target_participant_id: Optional[str] = Field(None, description="Existing target participant ID if joining a pre-created room")
    target_role: Optional[str] = Field(None, description="Target role for call notification")
    inviter_profile_img: Optional[str] = Field(None, description="Profile image URL/path of the inviter from request body")

class CallSessionResponse(BaseModel):
    """Call session response model."""
    call_url: str
    expires_at: str
    initiator_participant_id: str
    initiator_token: str
    room_id: str
    room_name: str
    session_id: str
    status: str
    target_participant_id: str
    target_token: str
    target_role: Optional[str] = None
    inviter_profile_img: Optional[str] = None

class DeclineNotificationRequest(BaseModel):
    """Decline notification request model for PowerApps/FCM integration."""
    oauth_token: str = Field(..., description="OAuth token for FCM authentication")
    title: str = Field(..., description="Notification title (e.g., 'John declined your call')")
    body: str = Field(..., description="Notification body text (e.g., 'Your call was declined')")
    is_video: str = Field(..., description="Video call flag ('true'/'false')")
    userprofile_id: str = Field(..., description="User profile ID of the person declining the call")
    opponent_userprofile_id: str = Field(..., description="User profile ID of the person to notify (original caller)")
    room_id: Optional[str] = Field(None, description="Room ID from the original call session")
    room_name: Optional[str] = Field(None, description="Room name from the original call session")
    session_id: Optional[str] = Field(None, description="Session ID from the original call session")
    initiator_token: Optional[str] = Field(None, description="Existing initiator JWT token if declining in a pre-created room")
    target_token: Optional[str] = Field(None, description="Existing target JWT token if declining in a pre-created room")
    initiator_participant_id: Optional[str] = Field(None, description="Existing initiator participant ID if declining in a pre-created room")
    target_participant_id: Optional[str] = Field(None, description="Existing target participant ID if declining in a pre-created room")

class DeclineNotificationResponse(BaseModel):
    """Decline notification response model."""
    success: bool = Field(..., description="Whether the notification was sent successfully")
    message: str = Field(..., description="Response message")
    data: Dict[str, Any] = Field(..., description="Notification data that was sent")


class TestAgentRequest(BaseModel):
    """Request model for the automation testing agent."""
    user_query: str = Field(..., description="Natural language query from the tester (e.g., 'Test login flow')")
    flutter_message: Dict[str, Any] = Field(default_factory=dict, description="JSON object representing the current UI state from Flutter")


class UserRegistrationRequest(BaseModel):
    """Request model for user registration in EtownDB."""
    firstname: str = Field(..., description="User's first name")
    mobilenumber: int = Field(..., description="Mobile number (numeric)")
    role: int = Field(..., description="User role (1=Provider, 2=Member)")
    user_login: str = Field(..., description="Login type (e.g., 'mobile')")
    MobilenumberAccesstoken: str = Field(..., description="Mobile number access token")
    membersignup_status: str = Field(..., description="Member signup status ('true'/'false')")
    lastsignup_status: int = Field(..., description="Last signup status code")
    country_code: str = Field("+91", description="Country code for mobile number")
    language: str = Field("en", description="Preferred language code for notifications (e.g. 'en', 'ta')")

    lastname: Optional[str] = Field(None, description="Last name (no DB column, accepted for compatibility)")
    email: Optional[str] = Field(None, description="Email address")
    dateofbirth: Optional[str] = Field(None, description="Date of birth in DD/MM/YYYY format")
    gender: Optional[str] = Field(None, description="Gender (no DB column, accepted for compatibility)")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address information")
    termsandcondition: Optional[str] = Field(None, description="Terms and conditions (no DB column, accepted for compatibility)")
    businessnumber: Optional[int] = Field(None, description="Business number (no DB column, accepted for compatibility)")
    City: Optional[str] = Field(None, description="City name")
    country: Optional[str] = Field(None, description="Country (no DB column, accepted for compatibility)")
    country_region: Optional[str] = Field(None, description="Country region (no DB column, accepted for compatibility)")
    zip_portal_code: Optional[str] = Field(None, description="Postal/ZIP code (no DB column, accepted for compatibility)")
    latitude: Optional[float] = Field(None, description="Address latitude")
    longitude: Optional[float] = Field(None, description="Address longitude (PowerApps sent this as 'langitude')")
    companyname: Optional[str] = Field(None, description="Company name (stored in etown_provider.businessname for role=1)")
    companydescription: Optional[str] = Field(None, description="Company description (stored in etown_provider.description for role=1)")
    services: Optional[str] = Field(None, description="Services offered (no DB column, accepted for compatibility)")
    address: Optional[str] = Field(None, description="Composite address string")
    profileImage: Optional[str] = Field(None, description="Profile image as base64 string for Azure Blob upload")
    profileImageURL: Optional[str] = Field(None, description="Profile image as direct URL (used when no base64 is provided)")
    preferredLanguage: Optional[int] = Field(None, description="Preferred language integer (no DB column, accepted for compatibility)")
    Connectycube_id: Optional[str] = Field(None, description="ConnectyCube identifier")
    userregistration_type: Optional[str] = Field(None, description="User registration type")
    state_code: Optional[str] = Field(None, description="State code")
    invite_code: Optional[str] = Field(None, description="Invitation code")
    from_socialmedia: str = Field("false", description="Whether the signup originated from social media ('true'/'false'); defaults to 'false'")

    @field_validator('from_socialmedia')
    @classmethod
    def normalize_from_socialmedia(cls, v: str) -> str:
        """Return from_socialmedia as a trimmed lower-case string."""
        return str(v).strip().lower()

    @field_validator('mobilenumber')
    @classmethod
    def validate_mobile_number(cls, v: int) -> int:
        """Reject a mobile number that is not 8 to 15 digits long."""
        mobile_str = str(v)
        if not (8 <= len(mobile_str) <= 15):
            raise ValueError("Mobile number must be between 8-15 digits")
        return v

    @field_validator('role')
    @classmethod
    def validate_role(cls, v: int) -> int:
        """Reject a role other than 1 (provider) or 2 (member)."""
        if v not in [1, 2]:
            raise ValueError("Role must be 1 (Provider) or 2 (Member)")
        return v

class ContactInfo(BaseModel):
    """Model for contact table response data."""
    mobilephone: str = Field(..., description="Mobile phone number")
    ets_mobilenumberaccesstoken: str = Field(..., description="Mobile number access token")
    fullname: str = Field(..., description="Full name of the contact")
    contactid: str = Field(..., description="Unique contact identifier (UUID)")
    createdon: str = Field(..., description="Contact creation timestamp (ISO format)")
    ets_userlogin: str = Field(..., description="User login type")
    firstname: str = Field(..., description="First name of the contact")
    emailaddress1: Optional[str] = Field(None, description="Email address")
    address1_city: Optional[str] = Field(None, description="City")
    ets_statecode: Optional[str] = Field(None, description="State code")
    address1_latitude: Optional[float] = Field(None, description="Address latitude")
    address1_longitude: Optional[float] = Field(None, description="Address longitude")
    ets_profileimage: Optional[str] = Field(None, description="Profile image URL")
    ets_is_active: Optional[str] = Field(None, description="Active state")
    ets_verified: Optional[str] = Field(None, description="Verified status")

class UserprofileInfo(BaseModel):
    """Model for userprofile table response data."""
    ets_contact_value: str = Field(..., description="Contact ID reference")
    ets_role: int = Field(..., description="User role (1=Provider, 2=Member)")
    ets_membersignup: str = Field(..., description="Member signup status")
    ets_lastsignupstatus: int = Field(..., description="Last signup status code")
    ets_userprofileid: str = Field(..., description="Unique userprofile identifier (UUID)")
    contact_firstname: str = Field(..., description="First name from contact table", alias="contact.firstname")
    ets_email: Optional[str] = Field(None, description="Email address")
    ets_mobilenumber: Optional[str] = Field(None, description="Mobile number")

class UserRegistrationResponse(BaseModel):
    """Response model for successful user registration."""
    contact_info: List[ContactInfo] = Field(..., description="Contact information array", alias="ContactInfo")
    userprofile_info: List[UserprofileInfo] = Field(..., description="User profile information array", alias="UserprofileInfo")
    access_token: Optional[str] = Field(default=None, description="Session access JWT, so a new user lands signed in")
    refresh_token: Optional[str] = Field(default=None, description="Opaque rotating refresh token")
    token_type: Optional[str] = Field(default=None, description="Always 'Bearer' when a session was issued")
    expires_in: Optional[int] = Field(default=None, description="Access token lifetime in seconds")

class SwitchProfileRequest(BaseModel):
    """Request model for the Switch Profile endpoint."""
    firstname: str = Field(..., description="Contact's first name")
    contact_id: str = Field(..., description="Existing contact UUID")
    role: int = Field(..., description="Target role to create: 1=Provider, 2=Member")
    email: str = Field(..., description="Contact's email address")
    mobilenumber: str = Field(..., description="Contact's mobile number")

    @field_validator('role')
    @classmethod
    def validate_role(cls, v: int) -> int:
        """Reject a role other than 1 (provider) or 2 (member)."""
        if v not in [1, 2]:
            raise ValueError("Role must be 1 (Provider) or 2 (Member)")
        return v

class ContactInfoDetailed(BaseModel):
    """Model for detailed contact information in user existence response."""
    ets_additionaladdressinfo: Optional[str] = Field(None, description="Additional address info")
    mobilephone: Optional[str] = Field(None, description="Mobile phone number")
    ets_alternatemobile: Optional[str] = Field(None, description="Alternate mobile number from etown_contact.alternatemobile; null when none is stored")
    ets_pushnotificationcheck: Optional[str] = Field(None, description="Push notification check")
    ets_is_active: Optional[str] = Field(None, description="Active status")
    address1_latitude: Optional[float] = Field(None, description="Address latitude")
    address1_city: Optional[str] = Field(None, description="Address city")
    ets_mobilenumberaccesstoken: Optional[str] = Field(None, description="Mobile number access token")
    address1_composite: Optional[str] = Field(None, description="Address composite")
    fullname: Optional[str] = Field(None, description="Full name")
    contactid: Optional[str] = Field(None, description="Contact ID")
    address1_longitude: Optional[float] = Field(None, description="Address longitude")
    createdon: Optional[str] = Field(None, description="Creation date")
    ets_userlogin: Optional[str] = Field(None, description="User login type")
    firstname: Optional[str] = Field(None, description="First name")
    email: Optional[str] = Field(None, description="Email")
    ets_statecode: Optional[str] = Field(None, description="State code")
    address1_primarycontactname: Optional[str] = Field(None, description="Primary contact name")
    ets_verified: Optional[str] = Field(None, description="Verified status from contact")
    dateofbirth: Optional[str] = Field(None, description="Date of birth in DD/MM/YYYY format")
    ets_profileimage: Optional[str] = Field(None, description="Profile image URL from contact")
    ets_deactivated: Optional[str] = Field(
        None,
        description=(
            "'true' when the account is currently deactivated - deactivated and not since "
            "reactivated - otherwise 'false'. Same value as the top-level Deactivated."
        ),
    )

class UserprofileInfoDetailed(BaseModel):
    """Model for detailed user profile information in user existence response."""
    ets_contact_value: Optional[str] = Field(None, description="Contact ID reference", alias="_ets_contact_value")
    ets_role: Optional[int] = Field(None, description="User role")
    ets_rolename: Optional[str] = Field(None, description="User role name")
    ets_mobilenumber: Optional[str] = Field(None, description="Mobile number")
    ets_membersignup: Optional[str] = Field(None, description="Member signup status")
    ets_lastsignupstatus: Optional[int] = Field(None, description="Last signup status")
    ets_lastsignupstatusname: Optional[str] = Field(None, description="Last signup status name")
    ets_userprofileid: Optional[str] = Field(None, description="User profile ID")
    ets_verified: Optional[str] = Field(None, description="Verified status from userprofile")
    contact_firstname: Optional[str] = Field(None, description="Contact first name", alias="contact.firstname")
    Etown_providertype: Optional[str] = Field(None, description="etown_provider.providertype of the provider behind this profile; null for member profiles and for a provider profile with no provider row")

class UserExistsDetailedResponse(BaseModel):
    """Response model for comprehensive user existence check."""
    ContactInfo: List[ContactInfoDetailed] = Field([], description="Detailed contact information")
    UserprofileInfo: List[UserprofileInfoDetailed] = Field([], description="Detailed user profile information")
    MyTownInfo: str = Field("true", description="MyTown information status")
    Userverified: Optional[bool] = Field(None, description="Verified status from contact")
    AisearchConsent: Optional[str] = Field(None, description="AI search consent value from contact")
    ChattieConsent: Optional[str] = Field(None, description="Chattie consent value from contact")
    Deactivated: Optional[str] = Field(
        None,
        description=(
            "'true' when the account is currently deactivated, meaning etown_contact has "
            "deactivated = 'true' and has not since been reactivated; 'false' otherwise, "
            "including an account that was deactivated and then reactivated. Text, not a "
            "boolean, matching the column type and the AisearchConsent / ChattieConsent "
            "fields above. Exactly the condition the per-party deactivated flags on the "
            "bookings, messages and feed reads report. A deactivated account still reports "
            "as existing, so the client can offer reactivation rather than routing the "
            "user to registration. Null only when the lookup found no user."
        ),
    )
    Etown_staff: bool = Field(
        False,
        description=(
            "True when the mobile number has an active role='staff' or role='csr' row in the "
            "etown_staff_numbers table, read live on every request. Matched on the last ten "
            "digits, so the format the number is sent in does not matter. Reported on the "
            "not-found path too, since a staff number need not belong to a registered user. An "
            "email-only lookup reports on the number of the contact it resolved to, and false "
            "when it resolved to none."
        ),
    )
    Etown_FOS: bool = Field(
        False,
        description=(
            "True when the mobile number has an active role='fos' row in etown_staff_numbers. "
            "Resolved exactly like Etown_staff; a number holding both a staff-side role and "
            "'fos' reports both true, and a normal user reports both false."
        ),
    )
    unverified_onboarded: bool = Field(
        False,
        description=(
            "True when a provider captured in the field on this number is still awaiting "
            "approval — a live etown_provider row with source 'FOS', 'CSR' or 'STAFF' whose approve flag is "
            "null (undecided) or false (rejected). Reported on the not-found path as well, "
            "because a field capture alone does not make the number an existing user: it is "
            "how the client tells 'brand new number' from 'we already have a listing for you, "
            "pending approval'. A capture with an etown_provider_review row whose review "
            "is not approved does not count. Goes false as soon as "
            "POST /provider/approve approves it, which also verifies the listing."
        ),
    )
    unverified_onboarded_today: int = Field(
        0,
        description=(
            "For a number with Etown_staff or Etown_FOS true: how many live providers it captured "
            "today (IST) through POST /provider/unverified_provider, matched on the provider's "
            "stored marketing_mobilenumber and source 'FOS', 'CSR' or 'STAFF'. Every capture counts, "
            "whether or not it has since been approved; edit-mode submissions create no provider "
            "and do not count. 0 for everyone else, and on a failed lookup."
        ),
    )
    credsupdate: Optional[bool] = Field(
        None,
        description=(
            "etown_contactdevices.credsupdate for the devicetoken sent, on the contact the "
            "mobile number or email resolved to; false when any of that contact's rows for the "
            "token is false. Null when no devicetoken was sent, the user was not found, the "
            "token is not registered to that contact, or the lookup failed."
        ),
    )

class ServiceListRequest(BaseModel):
    """Request model for the Service_list endpoint."""
    getcontact_id: Optional[str] = Field(None, description="Contact ID of the user whose services to fetch")
    contact_id: Optional[str] = Field(None, description="Contact ID of the requesting user")
    userprofile_id: Optional[str] = Field(None, description="User profile ID")
    is_sameuser: Optional[str] = Field(None, description="Flag indicating if the requesting user is the same as the target user")
    mobilenumber: Optional[str] = Field(None, description="Mobile number of the user")

class LastLoginRequest(BaseModel):
    """Request model for updating last login status."""
    contact_id: Optional[str] = Field(None, description="Contact unique identifier")
    user_profile_id: str = Field(..., description="User profile unique identifier")
    lastsignup_status: Optional[int] = Field(None, description="Last signup status code")
    membersignup_status: Optional[str] = Field(None, description="Member signup status")

class ConsentRequest(BaseModel):
    """Request model for updating user consent flags."""
    contactid: str = Field(..., description="Contact unique identifier")
    aisearch_consent: Optional[str] = Field(None, description="AI search consent value (e.g. 'true' or 'false')")
    chattie_consent: Optional[str] = Field(None, description="Chattie consent value (e.g. 'true' or 'false')")

class ConsentResponse(BaseModel):
    """Response model for consent update."""
    contactid: str
    aisearch_consent: Optional[str] = None
    chattie_consent: Optional[str] = None

class LocationDetailsRequest(BaseModel):
    """Request model for adding, editing, or deleting a user location record."""
    contact_id: Optional[str] = Field(None, description="Contact unique identifier")
    latitude: Optional[str] = Field(None, description="Latitude coordinate")
    longitude: Optional[str] = Field(None, description="Longitude coordinate")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address information")
    type: Optional[str] = Field(None, description="Location type (e.g. home, work)")
    homeaddress: Optional[str] = Field(None, description="Home address")
    workaddress: Optional[str] = Field(None, description="Work address")
    city: Optional[str] = Field(None, description="City name")
    statecode: Optional[str] = Field(None, description="State code")
    landmark: Optional[str] = Field(None, description="Landmark near the location")
    requiredaction: str = Field(..., description="Action to perform: add, edit, or delete")
    editlocationrow_id: Optional[str] = Field(None, description="Location row ID to edit")
    deletelocationrow_id: Optional[str] = Field(None, description="Location row ID to delete")

class UpdateAddressRequest(BaseModel):
    """Request model for updating a contact's address fields in etown_contact.

    Every field is optional. Only the fields supplied are updated. When ``address``
    is provided, both the ``address`` and ``addresscomp`` columns are set to its value.
    ``contact_id`` identifies the row to update.
    """
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address information")
    address: Optional[str] = Field(None, description="Street address (stored in both address and addresscomp)")
    contact_id: Optional[str] = Field(None, description="Contact unique identifier")
    latitude: Optional[str] = Field(None, description="Latitude coordinate")
    longitude: Optional[str] = Field(None, description="Longitude coordinate")
    statecode: Optional[str] = Field(None, description="State code")
    city: Optional[str] = Field(None, description="City name")

class GroupItem(BaseModel):
    """Model for a user's group."""
    Etown_groupid: str
    Etown_groupname: Optional[str] = None
    Etown_groupimageurl: Optional[str] = None
    Etown_createdon: Optional[str] = None
    Etown_membercount: Optional[int] = 0
    members: List[Dict[str, Any]] = []

class GroupResponse(BaseModel):
    """Response model for /groups."""
    groups: List[GroupItem]


class ProviderOnboardingServiceDetail(BaseModel):
    """A Service_Details entry for POST /provider/onboard, with three image slots."""
    Service_Name: Optional[str] = Field(None, description="Name of the service or product")
    Service_Description: Optional[str] = Field(None, description="Description of the service or product")
    Service_Type: Optional[str] = Field(None, description="'Product' or 'Service', stored as the service row's businesscategory")
    Service_Quantity: Optional[str] = Field(None, description="Unit or quantity information (e.g. 'per kg', 'per unit')")
    Service_Price: Optional[str] = Field(None, description="Price, stored as pricerange")
    Service_Expected_Price: Optional[str] = Field(None, description="Estimated price, stored as pricerange when Service_Price is empty")
    serviceimage1: Optional[str] = Field(None, description="Service image 1: base64 to upload, or an existing URL or blob path kept as is")
    serviceimage2: Optional[str] = Field(None, description="Service image 2: base64 to upload, or an existing URL or blob path kept as is")
    serviceimage3: Optional[str] = Field(None, description="Service image 3: base64 to upload, or an existing URL or blob path kept as is")


class ProviderOnboardingRequest(BaseModel):
    """Request model for provider onboarding (aligned with Power Automate app-form workflow).

    Serves both creation and editing via ``action_mode``:
      - ``create`` (default): registers a brand-new provider. ``mobilenumber`` and
        ``businessname`` are mandatory.
      - ``edit``: updates the provider named by ``providerid``, which is mandatory. Every
        other field is optional — only the fields supplied are written; omitted/null fields
        keep their stored value. In this mode ``contact_id`` means the contact the listing
        should belong to, so passing a different one moves the business onto it.
    """
    action_mode: str = Field(
        "create",
        description="'create' (default) to onboard a new provider, 'edit' to update an existing one by providerid",
    )
    providerid: Optional[str] = Field(
        None,
        description="Provider ID to update — mandatory when action_mode is 'edit'",
    )
    mobilenumber: Optional[str] = Field(None, description="Business mobile number (required when action_mode is 'create')")
    alternatemobile: Optional[str] = Field(
        None,
        description=(
            "Second mobile number for the business. Stored on the contact and the provider, shown "
            "alongside the primary, and signs in to the same account. Must be a valid Indian "
            "mobile, must differ from mobilenumber, and must not already belong to another account."
        ),
    )
    businessname: Optional[str] = Field(None, description="Business name (required when action_mode is 'create')")
    description: Optional[str] = Field(None, description="Business description")
    types: Optional[str] = Field(None, description="Business type/category label from form")
    formattedAddress: Optional[str] = Field(None, description="Pre-formatted address from client")
    latitude: Optional[str] = Field(None, description="Business location latitude (string)")
    longitude: Optional[str] = Field(None, description="Business location longitude (string)")
    businesslogo: Optional[str] = Field(None, description="Base64-encoded business logo image")
    business_category: Optional[str] = Field(None, description="Pre-provided business category")
    service_category: Optional[str] = Field(None, description="Pre-provided service category")
    contact_id: Optional[str] = Field(None, description="Existing contact ID (if contact already exists)")
    userprofileid: Optional[str] = Field(None, description="Existing user profile ID (informational)")
    city: Optional[str] = Field(None, description="Business city")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address details (flat, building, landmark)")
    services: Optional[str] = Field(
        None,
        description=(
            "Services text for the provider's first service row; on create it defaults to the "
            "first Service_Details name. No services are generated in the background."
        ),
    )
    Service_Details: List[ProviderOnboardingServiceDetail] = Field(
        default_factory=list,
        description=(
            "Services to add, one etown_providerservices and _copy row each, matched by "
            "Service_Name so an existing name is updated; category, content_vector and the "
            "Dataverse sync follow in the background. Create requires at least one named service."
        ),
    )
    area_location: Optional[str] = Field(None, description="Area/locality name")
    servicetype: Optional[str] = Field(None, description="Service type for the provider (e.g. 'Home Service', 'Shop')")
    state: Optional[str] = Field(None, description="State name or code")
    language: str = Field("en", description="Preferred language code for notifications (e.g. 'en', 'ta')")
    email: Optional[str] = Field(None, description="Business email address")
    website: Optional[str] = Field(None, description="Business website URL")
    availability: Optional[str] = Field(None, description="Business availability text (e.g. 'Mon-Sat 9am-6pm')")

    @field_validator('action_mode')
    @classmethod
    def validate_action_mode(cls, v: Optional[str]) -> str:
        """Return action_mode lower-cased, defaulting to 'create', and reject anything but 'create' or 'edit'."""
        mode = (v or 'create').strip().lower()
        if mode not in ('create', 'edit'):
            raise ValueError("action_mode must be either 'create' or 'edit'")
        return mode

    @field_validator('mobilenumber')
    @classmethod
    def validate_mobilenumber(cls, v: Optional[str]) -> Optional[str]:
        """Allow None, and reject a blank mobile number or one without 8 to 15 digits."""
        if v is None:
            return v
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Mobile number cannot be blank")
        digits_only = ''.join(filter(str.isdigit, v))
        if not (8 <= len(digits_only) <= 15):
            raise ValueError("Mobile number must be between 8-15 digits")
        return v

    @field_validator('alternatemobile')
    @classmethod
    def validate_alternatemobile(cls, v: Optional[str]) -> Optional[str]:
        """Normalize alternatemobile to 10 Indian national digits, treating blank as None and rejecting invalid numbers."""
        if v is None or not str(v).strip():
            return None
        national = normalize_indian_mobile(v)
        if national is None:
            raise ValueError(
                "alternatemobile must be a valid Indian mobile number "
                "(10 digits starting 6-9, optionally +91-prefixed)"
            )
        return national

    @field_validator('businessname')
    @classmethod
    def validate_businessname(cls, v: Optional[str]) -> Optional[str]:
        """Allow None, reject a blank business name, and return it stripped."""
        if v is None:
            return v
        if not v.strip():
            raise ValueError("Business name cannot be blank")
        return v.strip()

    @model_validator(mode="after")
    def _validate_action_mode_requirements(self) -> "ProviderOnboardingRequest":
        """Require providerid for edit, mobile, business name and a service for create, and an alternate differing from the mobile."""
        if self.action_mode == 'edit':
            if not self.providerid or not self.providerid.strip():
                raise ValueError("providerid is required when action_mode is 'edit'")
        else:
            if not self.mobilenumber:
                raise ValueError("Mobile number is required")
            if not self.businessname:
                raise ValueError("Business name is required")
            if not any((service.Service_Name or '').strip() for service in self.Service_Details):
                raise ValueError("At least one service with a Service_Name is required in Service_Details")
        if self.alternatemobile and self.mobilenumber:
            if self.alternatemobile == normalize_indian_mobile(self.mobilenumber):
                raise ValueError("alternatemobile must differ from mobilenumber")
        return self

class ProviderContactInfo(BaseModel):
    """Model for provider contact information."""
    contactid: str = Field(..., description="Unique contact identifier (UUID)")
    firstname: Optional[str] = Field(None, description="Contact first name")
    mobilephone: Optional[str] = Field(None, description="Mobile phone number")
    alternatemobile: Optional[str] = Field(None, description="Second mobile number on the contact, if one is stored")
    email: Optional[str] = Field(None, description="Contact email address")
    createdon: Optional[str] = Field(None, description="Contact creation timestamp (ISO format)")
    userlogin: Optional[str] = Field(None, description="User login type")
    city: Optional[str] = Field(None, description="Business city")

class ProviderUserProfileInfo(BaseModel):
    """Model for provider user profile information."""
    userprofileid: str = Field(..., description="Unique userprofile identifier (UUID)")
    contactid: str = Field(..., description="Contact ID reference")
    role: int = Field(..., description="User role (2=Provider)")
    name: str = Field(..., description="User name")

class ProviderBusinessInfo(BaseModel):
    """Model for provider business information."""
    providerid: str = Field(..., description="Unique provider identifier (UUID)")
    businessname: str = Field(..., description="Business name")
    businesscategory: Optional[str] = Field(None, description="Business category")
    contactid: str = Field(..., description="Contact ID reference")
    userprofileid: str = Field(..., description="User profile ID reference")
    city: Optional[str] = Field(None, description="Business city")
    description: Optional[str] = Field(None, description="Business description")
    publicsearchprovider: str = Field(..., description="Public search provider status")
    serviceimageurl: Optional[str] = Field(None, description="Business logo/profile image URL from Azure Blob")

class ProviderServiceInfo(BaseModel):
    """Model for provider service information."""
    providerservicesid: str = Field(..., description="Unique provider service identifier (UUID)")
    providerid: str = Field(..., description="Provider ID reference")
    businessname: str = Field(..., description="Business name")
    servicedescription: str = Field(..., description="Service description")
    services: str = Field(..., description="Generated services list")
    businesscategory: str = Field(..., description="Business category")
    servicecategory: str = Field(..., description="Service category")
    availability: Optional[str] = Field(None, description="Business availability text (e.g. 'Mon-Sat 9am-6pm')")

class AIEnhancementInfo(BaseModel):
    """Model for AI enhancement information."""
    original_description: str = Field(..., description="Original business description")
    enhanced_description: str = Field(..., description="AI-enhanced description")
    generated_services: List[str] = Field(..., description="AI-generated service suggestions")
    business_category: str = Field(..., description="AI-classified business category")
    service_category: str = Field(..., description="AI-classified service category")
    general_category: str = Field(..., description="AI-classified general category")

class ProviderOnboardingResponse(BaseModel):
    """Response model for successful provider onboarding."""
    contact_info: ProviderContactInfo = Field(..., description="Contact information")
    userprofile_info: ProviderUserProfileInfo = Field(..., description="User profile information")
    provider_info: ProviderBusinessInfo = Field(..., description="Provider business information")
    providerservice_info: ProviderServiceInfo = Field(..., description="Provider service information")
    ai_enhancements: AIEnhancementInfo = Field(..., description="AI enhancement details")
    add_services_message: Optional[str] = Field(
        None,
        description=(
            "Outcome of the Service_Details block ('Services added successfully', 'No services "
            "were added', or the failure reason), null when the request carried none"
        ),
    )

class OnboardServicesRequest(BaseModel):
    """Request model for POST /provider/onboard_services, the inputs /onboard's AI service generation reads."""
    businessname: str = Field(..., description="Business name, the strongest signal for the generated services")
    description: Optional[str] = Field(None, description="Business description")
    business_category: Optional[str] = Field(None, description="Pre-provided business category, used when the AI returns none")
    service_category: Optional[str] = Field(None, description="Pre-provided service category, preferred over the AI's")

    @field_validator('businessname')
    @classmethod
    def validate_businessname(cls, v: str) -> str:
        """Reject a blank business name and return it stripped."""
        if not v or not v.strip():
            raise ValueError("Business name cannot be blank")
        return v.strip()

class OnboardServicesInfo(BaseModel):
    """Generated provider service details, shaped as ProviderServiceInfo without ids."""
    businessname: str = Field(..., description="Business name")
    servicedescription: str = Field(..., description="Service description")
    services: str = Field(..., description="Generated services list")
    businesscategory: str = Field(..., description="Business category")
    servicecategory: str = Field(..., description="Service category")

class OnboardServicesResponse(BaseModel):
    """Response model for POST /provider/onboard_services; nothing is stored."""
    providerservice_info: OnboardServicesInfo = Field(..., description="Generated provider service information")
    ai_enhancements: AIEnhancementInfo = Field(..., description="AI enhancement details")

class ParsedOfferingItem(BaseModel):
    """A single item within a parsed offering category (from the AI business parser)."""
    item_id: Optional[str] = Field(None, description="Stable UUID assigned on first save; used for precise add/remove/update via category_operations")
    item_name: Optional[str] = Field(None, description="Item name (original language/script)")
    item_type: Optional[str] = Field(None, description="Raw item type label (e.g. 'Product' / 'Service')")
    item_image: Optional[str] = Field(None, description="Base64 image in; replaced with a blob URL before storage")
    item_description: Optional[str] = Field(None, description="Item description as supplied/extracted")
    item_description_ai_generated: Optional[str] = Field(None, description="AI-written English item description")
    item_price: Optional[float] = Field(0, description="Item price as a number")
    item_currency: Optional[str] = Field("INR", description="Currency code/symbol")
    item_confidence: Optional[float] = Field(0.0, description="Confidence score for this item (0.0–1.0)")
    source_type: Optional[str] = Field(None, description="How this item was sourced (e.g. 'ocr', 'manual')")

class ParsedOfferingCategory(BaseModel):
    """A category grouping parsed items within an offering."""
    category_id: Optional[str] = Field(None, description="Stable UUID assigned on first save; used for precise add/remove/update via category_operations")
    category_name: Optional[str] = Field(None, description="Category name (original language/script)")
    category_image: Optional[str] = Field(None, description="Base64 image in; replaced with a blob URL before storage")
    category_description: Optional[str] = Field(None, description="Category description as supplied/extracted")
    category_description_ai_generated: Optional[str] = Field(None, description="AI-written English category description")
    category_confidence: Optional[float] = Field(0.0, description="Confidence score for this category (0.0–1.0)")
    source_type: Optional[str] = Field(None, description="How this category was sourced")
    items: List[ParsedOfferingItem] = Field(default_factory=list, description="Items within this category")

class ParsedOfferingData(BaseModel):
    """A single parsed offering group (product/service line) with its categories."""
    offering_id: Optional[str] = Field(None, description="Stable UUID assigned on first save; exposed in provider_details offering_data for use with edit_service offering_operations")
    offering_name: Optional[str] = Field(None, description="Offering group name (e.g. 'Beverages')")
    offering_type: Optional[str] = Field(None, description="Raw offering type label (e.g. 'Product' / 'Service')")
    offering_image: Optional[str] = Field(None, description="Base64 image in; replaced with a blob URL before storage")
    offering_description: Optional[str] = Field(None, description="Offering description as supplied/extracted")
    offering_description_ai_generated: Optional[str] = Field(None, description="AI-written English offering description")
    offering_confidence: Optional[float] = Field(0.0, description="Confidence score for this offering (0.0–1.0)")
    source_type: Optional[str] = Field(None, description="How this offering was sourced")
    providerservices_id: Optional[str] = Field(None, description="providerservicesid of the etown_providerservices row that corresponds to this offering")
    categories: List[ParsedOfferingCategory] = Field(default_factory=list, description="Category groups under this offering")

class ParsedBusinessDetails(BaseModel):
    """Business-level details from the AI business parser (confirmed/edited by the user)."""
    business_name: Optional[str] = Field(None, description="Business name")
    business_description: Optional[str] = Field(None, description="Business description as supplied/extracted")
    business_description_ai_generated: Optional[str] = Field(None, description="AI-written English business description")
    business_image: Optional[str] = Field(None, description="Base64 image in; replaced with a blob URL before storage")
    business_category: Optional[str] = Field(None, description="Business category")
    business_category_confidence: Optional[float] = Field(0.0, description="Confidence for the business category (0.0–1.0)")
    phone_number: Optional[str] = Field(None, description="Business phone number")
    providerid: str = Field(..., description="Provider ID the offerings belong to (required)")
    userprofileid: str = Field(..., description="UserProfile ID of the provider (required)")
    contactid: str = Field(..., description="Contact ID of the provider (required)")
    email: Optional[str] = Field(None, description="Business email")
    website: Optional[str] = Field(None, description="Business website URL")
    address: Optional[str] = Field(None, description="Business address")
    confidence_score: Optional[float] = Field(0.0, description="Overall parse confidence (0.0–1.0)")

class AddServicesToProvidersRequest(BaseModel):
    """Request model for adding services to a provider — accepts TWO payload shapes.

    Nested (AI business parser): the frontend submits the confirmed/edited output of
    the parser — a ``business_details`` block (which carries the provider/userprofile/
    contact IDs) plus an ``offering_data`` tree (offering → category → item). All
    ``*_image`` fields are base64 strings uploaded to Azure Blob and replaced with URLs
    before storage.

    Flat (manual add-service form): the mobile client submits the provider keys
    (``contact_id`` + ``userprofile_id`` + ``provider_id``) and a ``servicedetails``
    list directly, with no business/offering tree.

    The handler branches on whether ``business_details`` is present. Exactly one of the
    two shapes must be supplied; ``_validate_payload_shape`` enforces this.
    """
    business_details: Optional[ParsedBusinessDetails] = Field(
        None, description="Parsed business details + keys (nested payload)"
    )
    offering_data: List[ParsedOfferingData] = Field(
        default_factory=list,
        description="Parsed offering groups (offering → category → item).",
    )
    contact_id: Optional[str] = Field(None, description="Contact ID of the provider (flat payload)")
    userprofile_id: Optional[str] = Field(None, description="UserProfile ID of the provider (flat payload)")
    provider_id: Optional[str] = Field(None, description="Provider ID to add services to (flat payload)")
    servicedetails: List[ServiceDetailRequest] = Field(
        default_factory=list,
        description="Services to add (flat payload).",
    )

    @model_validator(mode="after")
    def _validate_payload_shape(self) -> "AddServicesToProvidersRequest":
        """Accept the nested business_details payload, else require the flat ids plus servicedetails."""
        if self.business_details is not None:
            return self
        missing = [
            f for f in ("contact_id", "userprofile_id", "provider_id")
            if not getattr(self, f)
        ]
        if missing:
            raise ValueError(
                "Provide either 'business_details' (nested payload) or "
                f"{', '.join(missing)} + 'servicedetails' (flat payload)."
            )
        if not self.servicedetails:
            raise ValueError("'servicedetails' is required for the flat payload.")
        return self

class AddServicesToProvidersResponse(BaseModel):
    """Response for the add-services-to-provider workflow."""
    message: str = Field(..., description="Outcome message (e.g. 'Services added successfully' or 'User Not Found')")

class UnverifiedProviderServiceDetail(BaseModel):
    """ServiceDetailRequest minus ``serviceitems``, for a service captured in the field.

    POST /provider/unverified_provider takes a plain service — a name, images, a price range,
    a description and a quantity. An offering tree belongs to the offerings endpoints, which
    run against a provider that already exists, so it is not accepted here; the insert reads
    serviceitems through getattr() and writes NULL when the field is absent.
    """
    service_name: str = Field(..., description="Name of the service being added")
    serviceimage1: Optional[str] = Field(None, description="Base64-encoded service image 1 (or null/empty)")
    serviceimage2: Optional[str] = Field(None, description="Base64-encoded service image 2 (or null/empty)")
    serviceimage3: Optional[str] = Field(None, description="Base64-encoded service image 3 (or null/empty)")
    pricerange: Optional[str] = Field(None, description="Price range for the service")
    description: Optional[str] = Field(None, description="Service description (used for category classification)")
    quantity: Optional[str] = Field(None, description="Quantity/unit information for the service")

class UnverifiedProviderOnboardingRequest(BaseModel):
    """Request model for POST /provider/unverified_provider.

    Carries ProviderOnboardingRequest's create-mode fields — but none of its ids — plus an
    optional ``servicedetails`` list.

    A create onboards the business and stocks its services in one call, so every entity key it
    works with is minted here: no providerid, userprofileid or business contactid is accepted.
    The only id a create reads is ``marketing_contactid``, which names the field agent capturing
    the listing rather than anything this call creates. Supplying no ``servicedetails`` at all
    just onboards the provider.

    ``action_mode='edit'`` reuses the same body to PROPOSE a correction: ``mobilenumber`` names
    the listing the edit is about, optionally narrowed by ``edit_providerid`` when the number
    carries more than one, and the whole body is stored in etown_provider_review flagged
    ``edit_providerid`` is the one entity id this endpoint accepts, and only there:
    it selects an existing listing, it never writes one. No live row is
    written; a back-office pass over that queue decides what reaches the listing.

    Both modes may carry ``FOSlatitude``/``FOSlongitude``, the capturing device's own
    position. They are optional by default and are never stored. They become required, and
    are measured against ``latitude``/``longitude``, only for a capture whose
    ``marketing_mobilenumber`` is an active ``role = 'fos'`` row in etown_staff_numbers, and
    only while env FOS_ONBOARD_PROXIMITY_CHECK_ENABLED is true. So the requirement follows
    the agent rather than the deployment: a rostered field agent has to prove they are at the
    shop, and a capture from anyone else is accepted wherever it was taken.
    """
    marketing_contactid: Optional[str] = Field(
        None,
        description=(
            "Contact ID of the field agent capturing this listing. Their firstname is stored "
            "as the provider's marketing_person_name. The business gets its own contact, "
            "minted by this call - this is never used as the provider's contactid. It must "
            "not be the contact that already holds 'mobilenumber': an agent capturing their "
            "own business is refused with 400, in create and edit mode alike, and registers "
            "it through POST /provider/onboard instead."
        ),
    )
    marketing_mobilenumber: Optional[str] = Field(
        None,
        description=(
            "Mobile number of the field agent capturing this listing, any format - only its "
            "last ten digits are compared. A create stores those ten digits as the provider's "
            "marketing_mobilenumber, which GET /users/exists counts as "
            "unverified_onboarded_today. It is looked up in "
            "etown_staff_numbers, and an active row pairing it with role = 'fos' is what "
            "makes FOSlatitude/FOSlongitude required for this request (and only then, and "
            "only while env FOS_ONBOARD_PROXIMITY_CHECK_ENABLED is true). Omit it, or send a "
            "number nobody has rostered as a field agent, and both coordinates stay optional "
            "and the capture is accepted wherever it was taken. The roster is read live, so "
            "adding or deactivating an agent applies on the next request without a deploy."
        ),
    )
    action_mode: str = Field(
        "create",
        description=(
            "'create' (default) onboards a new non-verified provider, and additionally stores "
            "this body in etown_provider_review when a live provider already "
            "holds the mobilenumber; 'edit' PROPOSES a change to the listing on this "
            "mobilenumber - the body is stored in etown_provider_review and no live "
            "row is written"
        ),
    )
    mobilenumber: Optional[str] = Field(None, description="Business mobile number (required; the provider's key in edit mode). Must not be a number the marketing_contactid agent already holds - capturing your own business here is a 400")
    alternatemobile: Optional[str] = Field(
        None,
        description=(
            "Second mobile number captured for the business. Stored unproven: a field agent "
            "reading a number off a shopfront has not shown that its owner consents to it "
            "opening this account, so it is a displayed detail until the listing is claimed and "
            "the number answers an OTP. Must be a valid Indian mobile and differ from "
            "mobilenumber."
        ),
    )
    edit_providerid: Optional[str] = Field(
        None,
        description=(
            "action_mode='edit' only. Names WHICH listing on this mobilenumber the edit is "
            "about, for a number carrying more than one. The provider must hold the "
            "mobilenumber sent as well, so the two identify one row together. Omit it and the "
            "newest live provider on the number is used, as before. Ignored on a create."
        ),
    )
    businessname: Optional[str] = Field(None, description="Business name (required in create mode)")
    description: Optional[str] = Field(None, description="Business description")
    types: Optional[str] = Field(None, description="Business type/category label from form")
    formattedAddress: Optional[str] = Field(None, description="Pre-formatted address from client")
    latitude: Optional[str] = Field(None, description="Business location latitude (string; required only for a capture whose marketing_mobilenumber is a rostered role='fos' agent, since FOSlatitude/FOSlongitude are measured against it)")
    longitude: Optional[str] = Field(None, description="Business location longitude (string; required only for a capture whose marketing_mobilenumber is a rostered role='fos' agent, since FOSlatitude/FOSlongitude are measured against it)")
    FOSlatitude: Optional[str] = Field(
        None,
        description=(
            "Latitude the field agent's own device reports at capture time (string, optional "
            "by default). It is never stored: it exists only to prove the agent is standing "
            "at the business. It is REQUIRED, in both modes, when 'marketing_mobilenumber' is "
            "an active role='fos' number in etown_staff_numbers and env "
            "FOS_ONBOARD_PROXIMITY_CHECK_ENABLED is true - a capture by such an agent that is "
            "missing it, or taken beyond FOS_ONBOARD_MAX_DISTANCE_KM (1 km by default) from "
            "latitude/longitude, is refused with 400 and nothing at all is written. For every "
            "other caller it is optional and ignored."
        ),
    )
    FOSlongitude: Optional[str] = Field(
        None,
        description=(
            "Longitude the field agent's own device reports at capture time (string, optional "
            "by default). Not stored; see FOSlatitude for the proximity rule and the two "
            "things that have to be true before it applies."
        ),
    )
    businesslogo: Optional[str] = Field(None, description="Base64-encoded business logo image")
    business_category: Optional[str] = Field(None, description="Pre-provided business category")
    service_category: Optional[str] = Field(None, description="Pre-provided service category")
    city: Optional[str] = Field(None, description="Business city")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address details (flat, building, landmark)")
    services: Optional[str] = Field(None, description="Pre-provided services list from form")
    area_location: Optional[str] = Field(None, description="Area/locality name")
    servicetype: Optional[str] = Field(None, description="Service type for the provider (e.g. 'Home Service', 'Shop')")
    state: Optional[str] = Field(None, description="State name or code")
    language: str = Field("en", description="Preferred language code for notifications (e.g. 'en', 'ta')")
    email: Optional[str] = Field(None, description="Business email address")
    interested: Optional[str] = Field(
        None,
        description=(
            "Whether the business is interested: 'true' or 'false' (JSON true/false, 'yes'/'no' "
            "and '1'/'0' are accepted and stored as 'true'/'false'; blank means unset). Stored on "
            "the provider a create writes and on any etown_provider_review row either mode writes, "
            "and mirrored to Dataverse ets_interested as Yes/No"
        ),
    )
    servicedetails: List[UnverifiedProviderServiceDetail] = Field(
        default_factory=list,
        description="Services to add to the provider this call creates",
    )

    @field_validator('action_mode')
    @classmethod
    def validate_action_mode(cls, v: Optional[str]) -> str:
        """Return action_mode lower-cased, defaulting to 'create', and reject anything but 'create' or 'edit'."""
        mode = (v or 'create').strip().lower()
        if mode not in ('create', 'edit'):
            raise ValueError("action_mode must be either 'create' or 'edit'")
        return mode

    @field_validator('mobilenumber')
    @classmethod
    def validate_mobilenumber(cls, v: Optional[str]) -> Optional[str]:
        """Allow None, and reject a blank mobile number or one without 8 to 15 digits."""
        if v is None:
            return v
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Mobile number cannot be blank")
        digits_only = ''.join(filter(str.isdigit, v))
        if not (8 <= len(digits_only) <= 15):
            raise ValueError("Mobile number must be between 8-15 digits")
        return v

    @field_validator('alternatemobile')
    @classmethod
    def validate_alternatemobile(cls, v: Optional[str]) -> Optional[str]:
        """Normalize alternatemobile to 10 Indian national digits, treating blank as None and rejecting invalid numbers."""
        if v is None or not str(v).strip():
            return None
        national = normalize_indian_mobile(v)
        if national is None:
            raise ValueError(
                "alternatemobile must be a valid Indian mobile number "
                "(10 digits starting 6-9, optionally +91-prefixed)"
            )
        return national

    @field_validator('businessname')
    @classmethod
    def validate_businessname(cls, v: Optional[str]) -> Optional[str]:
        """Allow None, reject a blank business name, and return it stripped."""
        if v is None:
            return v
        if not v.strip():
            raise ValueError("Business name cannot be blank")
        return v.strip()

    @field_validator('interested', mode='before')
    @classmethod
    def validate_interested(cls, v: Any) -> Optional[str]:
        """Normalise interested to 'true' or 'false', treating blank as None and rejecting anything else."""
        if v is None:
            return None
        text = str(v).strip().lower()
        if not text:
            return None
        if text in ("true", "yes", "1"):
            return "true"
        if text in ("false", "no", "0"):
            return "false"
        raise ValueError("interested must be 'true' or 'false'")

    @model_validator(mode="after")
    def _validate_mode_requirements(self) -> "UnverifiedProviderOnboardingRequest":
        """Require a mobile number always, a business name on create, and an alternate differing from the mobile."""
        if not self.mobilenumber:
            raise ValueError("Mobile number is required")
        if self.action_mode == "create" and not self.businessname:
            raise ValueError("Business name is required")
        if self.alternatemobile and self.alternatemobile == normalize_indian_mobile(self.mobilenumber):
            raise ValueError("alternatemobile must differ from mobilenumber")
        return self

class UnverifiedProviderOnboardingResponse(ProviderOnboardingResponse):
    """Response model for POST /provider/unverified_provider.

    The provider is committed before the services run, so a services failure does not undo the
    onboard: the ids are still returned and ``add_services_message`` says what happened, leaving
    the caller able to retry just the services against POST /provider/add_services.
    """
    add_services_message: Optional[str] = Field(
        None,
        description=(
            "action_mode='create': outcome of the services block ('Services added successfully', "
            "'No services were added', 'Provider not found', or the failure reason), null when "
            "the request carried no services. action_mode='edit': always set, naming the "
            "etown_provider_review row the edit was stored as and stating that the live listing "
            "was not changed."
        ),
    )

class ProviderServiceRow(BaseModel):
    """One etown_providerservices row hanging off a provider in the lookup response."""
    providerservicesid: str = Field(..., description="Unique service identifier (UUID)")
    providerid: Optional[str] = Field(None, description="Provider this service belongs to")
    services: Optional[str] = Field(None, description="Service name")
    servicedescription: Optional[str] = Field(None, description="Service description")
    businesscategory: Optional[str] = Field(None, description="Business category on the service row")
    servicecategory: Optional[str] = Field(None, description="Service category")
    generalcategory: Optional[str] = Field(None, description="General category")
    pricerange: Optional[str] = Field(None, description="Price range")
    quantity: Optional[str] = Field(None, description="Quantity/unit information")
    availability: Optional[str] = Field(None, description="Availability text for this service")
    yearsinbusiness: Optional[str] = Field(None, description="Years in business, as stored")
    serviceimageurl: Optional[str] = Field(None, description="Primary service image URL")
    serviceimageurl1: Optional[str] = Field(None, description="Service image 1 URL")
    serviceimageurl2: Optional[str] = Field(None, description="Service image 2 URL")
    serviceimageurl3: Optional[str] = Field(None, description="Service image 3 URL")
    serviceitems: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Offering categories/items stored on the service row; empty when it has none",
    )
    providertype: Optional[str] = Field(None, description="Provider type on the service row")
    publicsearchprovider: Optional[str] = Field(None, description="Public search status on the service row")
    source: Optional[str] = Field(None, description="How the service row was created, e.g. 'FOS' or 'Onboard'")
    createdon: Optional[str] = Field(None, description="Creation timestamp (ISO 8601)")

class UnverifiedProviderItem(BaseModel):
    """One provider returned by GET /provider/unverified_provider_list, with its services."""
    providerid: str = Field(..., description="Unique provider identifier (UUID)")
    businessname: Optional[str] = Field(None, description="Business name")
    businesscategory: Optional[str] = Field(None, description="Business category")
    contactid: Optional[str] = Field(None, description="Contact ID that owns the listing")
    userprofileid: Optional[str] = Field(None, description="UserProfile ID that owns the listing")
    contactidname: Optional[str] = Field(None, description="Display name of the owning contact")
    userprofileidname: Optional[str] = Field(None, description="Display name of the owning userprofile")
    firstname: Optional[str] = Field(None, description="Contact first name stored on the provider")
    mobilenumber: Optional[str] = Field(None, description="Mobile number as stored on the provider row")
    alternatemobile: Optional[str] = Field(
        None,
        description=(
            "Second mobile number stored on the provider row, or null when none was captured. "
            "Send it back through POST /provider/approve or POST /provider/onboard "
            "(action_mode='edit') to change it."
        ),
    )
    email: Optional[str] = Field(None, description="Business email")
    description: Optional[str] = Field(None, description="Business description")
    city: Optional[str] = Field(None, description="Business city")
    areaname: Optional[str] = Field(None, description="Area/locality name")
    townname: Optional[str] = Field(None, description="Town name")
    statecode: Optional[str] = Field(None, description="Two-letter state code")
    address: Optional[str] = Field(None, description="Formatted address")
    additionaladdressinfo: Optional[str] = Field(None, description="Flat/building/landmark details")
    latitude: Optional[str] = Field(None, description="Latitude, as stored")
    longitude: Optional[str] = Field(None, description="Longitude, as stored")
    servicetype: Optional[str] = Field(None, description="Service type (e.g. 'Home Service', 'Shop')")
    websiteurl: Optional[str] = Field(None, description="Business website URL")
    availability: Optional[str] = Field(None, description="Business availability text")
    serviceimageurl: Optional[str] = Field(None, description="Business logo/profile image URL")
    providertype: Optional[str] = Field(None, description="Provider type, e.g. 'etown_nonverified'")
    publicsearchprovider: Optional[str] = Field(None, description="Public search status, e.g. 'non-verified'")
    source: Optional[str] = Field(None, description="How the listing was created, e.g. 'FOS' or 'Public Insert'")
    approve: Optional[bool] = Field(
        None,
        description=(
            "etown_provider.approve — null when nobody has ruled on the listing, true approved, "
            "false rejected. Only POST /provider/approve writes it."
        ),
    )
    creationdate: Optional[str] = Field(None, description="Creation timestamp (ISO 8601)")
    is_verified: bool = Field(
        False,
        description=(
            "Whether the listing counts as verified under the platform's is_provider_verified "
            "rule, which keys off providertype alone."
        ),
    )
    status: str = Field(
        ...,
        description=(
            "'verified_provider' when the listing is already verified, 'added_by_fos' when it is "
            "not but was captured in the field (source = 'FOS'), 'unverified' otherwise. Verified "
            "wins over FOS: a field capture that has since been verified reports as verified."
        ),
    )
    message: Optional[str] = Field(
        None,
        description=(
            "Why a field agent cannot add this business, or null when nothing stands in the way. "
            "Set for 'verified_provider' and 'added_by_fos' only."
        ),
    )
    services: List[ProviderServiceRow] = Field(
        default_factory=list,
        description="The provider's live etown_providerservices rows, oldest first",
    )

class UnverifiedProviderListResponse(BaseModel):
    """Response for GET /provider/unverified_provider_list."""
    mobilenumber: str = Field(..., description="The ten digits the lookup actually matched on")
    count: int = Field(..., description="Number of live providers found on the number")
    status: str = Field(
        ...,
        description=(
            "The number's overall state, most significant first: 'verified_provider' when any "
            "listing on it is verified, else 'added_by_fos' when any was captured in the field, "
            "else 'unverified' when providers exist but neither applies, else 'available' when "
            "the number carries no provider at all."
        ),
    )
    message: Optional[str] = Field(
        None,
        description=(
            "The message to show for `status` — 'This is already a verified provider on Etown' "
            "or 'This provider is already added by another FOS'. Null for 'unverified' and "
            "'available', where nothing stands in the way of capturing the business."
        ),
    )
    providers: List[UnverifiedProviderItem] = Field(
        default_factory=list,
        description=(
            "Every live provider on the number, newest first, each with its own status, message "
            "and service rows. Empty when none match."
        ),
    )

class ProviderApprovalRequest(BaseModel):
    """Request model for POST /provider/approve.

    ``providerid`` + ``approve`` carry the decision, which is taken on one listing: a
    contact can hold several businesses and each is approved or rejected on its own
    merits. Everything after them is the correction the approver is signing off with the
    decision — the same optional field set ``ProviderOnboardingRequest`` takes in
    action_mode='edit', plus a ``servicedetails`` list, so a back-office pass over
    etown_provider_review can apply what it reviewed in the same call that approves it.
    They are written only when ``approve`` is true.
    """
    providerid: str = Field(
        ...,
        description="The provider being decided. Everything this call writes goes to this one listing",
    )
    approve: bool = Field(
        ...,
        description="true approves the provider, false rejects it",
    )
    businessname: Optional[str] = Field(None, description="Business name")
    description: Optional[str] = Field(None, description="Business description")
    types: Optional[str] = Field(None, description="Business type/category label from form (accepted for payload parity; not written)")
    formattedAddress: Optional[str] = Field(None, description="Pre-formatted address from client")
    latitude: Optional[str] = Field(None, description="Business location latitude (string)")
    longitude: Optional[str] = Field(None, description="Business location longitude (string)")
    businesslogo: Optional[str] = Field(None, description="Base64-encoded business logo image")
    business_category: Optional[str] = Field(None, description="Pre-provided business category")
    service_category: Optional[str] = Field(None, description="Pre-provided service category")
    city: Optional[str] = Field(None, description="Business city")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address details (flat, building, landmark)")
    services: Optional[str] = Field(None, description="Pre-provided services list from form")
    area_location: Optional[str] = Field(None, description="Area/locality name")
    servicetype: Optional[str] = Field(None, description="Service type for the provider (e.g. 'Home Service', 'Shop')")
    state: Optional[str] = Field(None, description="State name or code")
    language: str = Field("en", description="Preferred language code for notifications (accepted for payload parity; not written)")
    email: Optional[str] = Field(None, description="Business email address")
    alternatemobile: Optional[str] = Field(
        None,
        description=(
            "Second mobile number to write onto the approved listing. Approving does not prove "
            "it: it is stored unproven and opens no login until its owner answers an OTP on it."
        ),
    )
    servicedetails: List[UnverifiedProviderServiceDetail] = Field(
        default_factory=list,
        description=(
            "Services to write onto the approved listing. A service_name matching a row the "
            "provider already has updates that row; a new name adds one, counted against the "
            "plan's max_listings."
        ),
    )

    PROVIDER_UPDATE_FIELDS: ClassVar[tuple] = (
        "businessname", "description", "formattedAddress", "latitude", "longitude",
        "businesslogo", "business_category", "service_category", "city",
        "additionaladdressinfo", "services", "area_location", "servicetype", "state",
        "email", "alternatemobile",
    )

    @field_validator('providerid')
    @classmethod
    def validate_providerid(cls, v: str) -> str:
        """Reject a blank providerid and return it stripped."""
        if not v or not v.strip():
            raise ValueError("providerid cannot be blank")
        return v.strip()

    @field_validator('alternatemobile')
    @classmethod
    def validate_alternatemobile(cls, v: Optional[str]) -> Optional[str]:
        """Normalize alternatemobile to 10 Indian national digits, treating blank as None and rejecting invalid numbers."""
        if v is None or not str(v).strip():
            return None
        national = normalize_indian_mobile(v)
        if national is None:
            raise ValueError(
                "alternatemobile must be a valid Indian mobile number "
                "(10 digits starting 6-9, optionally +91-prefixed)"
            )
        return national

    def has_provider_updates(self) -> bool:
        """Return True when any field in PROVIDER_UPDATE_FIELDS is set."""
        return any(getattr(self, name) is not None for name in self.PROVIDER_UPDATE_FIELDS)

class ProviderApprovalResponse(BaseModel):
    """Response for POST /provider/approve."""
    providerid: str = Field(..., description="The provider the decision was written to")
    approve: bool = Field(..., description="The value written to etown_provider.approve")
    verified: bool = Field(
        False,
        description=(
            "Whether this call also promoted the provider to verified — true only when it "
            "was still non-verified and approve was true. False when approve is false — a "
            "rejection demotes an already-verified listing to non-verified instead, and "
            "leaves an already non-verified one alone — when the provider was already "
            "verified, or when the cascade failed."
        ),
    )
    update_message: Optional[str] = Field(
        None,
        description=(
            "Outcome of those field updates — 'Provider updated successfully', the reason "
            "they failed, or, on approve = false, a note that the body's fields and "
            "servicedetails were not written. Null when the body carried none."
        ),
    )
    add_services_message: Optional[str] = Field(
        None,
        description=(
            "Outcome of the 'servicedetails' block ('Services added successfully', 'No "
            "services were added', 'Provider not found', the plan-cap refusal, or the failure "
            "reason). Null when the body carried no servicedetails."
        ),
    )


class ProviderDeleteRequest(BaseModel):
    """Request model for deleting a provider by userprofileid + contactid."""
    userprofileid: str = Field(..., description="UserProfile ID of the provider to delete")
    contactid: str = Field(..., description="Contact ID of the provider to delete")

class ProviderDeleteResponse(BaseModel):
    """Response model for provider deletion."""
    deleted: bool = Field(..., description="Whether the provider was found and deleted")
    contactid: str = Field(..., description="Contact ID that was deleted")
    userprofileid: str = Field(..., description="UserProfile ID that was deleted")
    providerid: Optional[str] = Field(None, description="Provider ID that was deleted")
    business_name: Optional[str] = Field(None, description="Business name of the deleted provider")


class ProviderLifecycleRequest(BaseModel):
    """Request model for deactivating or reactivating one provider (business)."""
    providerid: str = Field(..., description="Provider ID of the business to deactivate or reactivate")
    action_mode: str = Field(
        "deactivate",
        description=(
            "'deactivate' hides the business from search, feed and booking and drops it to the "
            "free tier while its owner keeps using Etown; 'reactivate' undoes that. "
            "Case-insensitive."
        ),
        examples=["deactivate", "reactivate"],
    )

class ProviderLifecycleResponse(BaseModel):
    """Response model for a per-provider lifecycle transition."""
    status: str = Field(..., description="'deactivated' or 'reactivated'")
    providerid: str = Field(..., description="Provider ID that was flagged")
    userprofileid: Optional[str] = Field(None, description="UserProfile ID of that business")
    contactid: Optional[str] = Field(None, description="Contact ID that owns the business")
    business_name: Optional[str] = Field(None, description="Business name of the flagged provider")
    rows_flagged: int = Field(..., description="Rows stamped across the six provider-owned tables")


class GroupMemberInput(BaseModel):
    """A single member to add to a group (one entry of the request's ``groupitems``)."""
    userprofile_id: str = Field(..., description="UserProfile ID of the member to add")
    name: str = Field(..., description="Display name of the member (stored as the group item username)")


class GroupManageRequest(BaseModel):
    """Request model for managing a group (create, update, delete).
    """
    actionmode: Literal["create", "update", "delete"] = Field(..., description="Operation to perform")
    user_contactid: Optional[str] = Field(None, description="Contact ID of the group owner (required for create/update)")
    user_userprofileid: Optional[str] = Field(None, description="UserProfile ID of the group owner (required for create/update)")
    groupname: Optional[str] = Field(None, description="Group name (create / group-detail updates)")
    groupimageurl: Optional[str] = Field(None, description="Base64/data-URL image, an existing blob path/URL, or null")
    groupitems: List[GroupMemberInput] = Field(default_factory=list, description="Members (create / item updates)")
    groupupdatecondition: Optional[Literal["groupupdate", "groupitemsupdate", "completegroupupdate"]] = Field(
        None, description="Update scope (required when actionmode=update)"
    )
    groupid: Optional[str] = Field(None, description="Group ID (required for update/delete)")

    @model_validator(mode="after")
    def _validate_by_action(self) -> "GroupManageRequest":
        """Require user ids for create and update, groupid and condition for update, and groupid for delete."""
        if self.actionmode in ("create", "update"):
            missing = [f for f in ("user_contactid", "user_userprofileid") if not getattr(self, f)]
            if missing:
                raise ValueError(f"{', '.join(missing)} required for actionmode='{self.actionmode}'")
        if self.actionmode == "update":
            if not self.groupid:
                raise ValueError("groupid is required for actionmode='update'")
            if not self.groupupdatecondition:
                raise ValueError("groupupdatecondition is required for actionmode='update'")
        if self.actionmode == "delete" and not self.groupid:
            raise ValueError("groupid is required for actionmode='delete'")
        return self


class AddToMyCircleProviderRequest(BaseModel):
    """Request body for adding a provider to the actor's MyCircle.

    Field names mirror the original PowerApps ``AddtoMycircleProvider`` HTTP trigger so
    the mobile client only needs to swap the endpoint URL. ``from_*`` is the actor doing
    the adding; ``provider_*`` / ``providerid`` is the provider being added.

    ``provider_contactid`` / ``provider_userprofileid`` may be omitted: whichever is
    absent is resolved from the ``etown_provider`` row identified by ``providerid``.
    Supplied values always win, so a caller sending all three is unaffected.
    """
    model_config = ConfigDict(populate_by_name=True)

    from_contactid: str = Field(..., description="Contact ID of the actor adding the provider")
    from_userprofileid: str = Field(..., description="UserProfile ID of the actor")
    provider_contactid: Optional[str] = Field(
        None, description="Contact ID of the provider being added; resolved from providerid when omitted",
    )
    provider_userprofileid: Optional[str] = Field(
        None, description="UserProfile ID of the provider being added; resolved from providerid when omitted",
    )
    providerid: str = Field(..., description="ets_provider record ID of the provider being added")
    language_code: Optional[str] = Field(None, description="Language for the unverified-provider SMS ('EN' | 'TA')")
    role: Optional[str] = Field(None, description="Actor's role code, stored as the invitation type (adx_type)")
    action_mode: Optional[str] = Field(
        "add",
        description="'add' (default) adds the provider to MyCircle; 'remove' removes them from MyCircle",
    )
    oauth: Optional[str] = Field(
        None, alias="Oauth",
        description="Client FCM OAuth bearer token; when omitted the push path mints one server-side",
    )


class AddToMyCircleMemberRequest(BaseModel):
    """Request body for adding a member to the actor's MyCircle.

    The member mirror of ``AddToMyCircleProviderRequest``: the counterparty is a member
    (an ``etown_member`` row / role-2 profile) rather than a provider. ``from_*`` is the
    actor doing the adding; ``member_*`` / ``memberid`` is the member being added.

    ``member_contactid`` + ``member_userprofileid`` is the expected pair, both of which
    GET /invitations/mycircle already hands the client. ``memberid`` is the fallback for
    a caller holding only one of them: supply it and the missing id is resolved from the
    ``etown_member`` row. Supplied values always win, so a caller sending the pair is
    unaffected and pays no lookup.
    """
    model_config = ConfigDict(populate_by_name=True)

    from_contactid: str = Field(..., description="Contact ID of the actor adding the member")
    from_userprofileid: str = Field(..., description="UserProfile ID of the actor")
    member_contactid: Optional[str] = Field(
        None, description="Contact ID of the member being added; resolved from memberid when omitted",
    )
    member_userprofileid: Optional[str] = Field(
        None, description="UserProfile ID of the member being added; resolved from memberid when omitted",
    )
    memberid: Optional[str] = Field(
        None,
        description="etown_member record ID; only read to resolve an omitted member_contactid / member_userprofileid",
    )
    role: Optional[str] = Field(None, description="Actor's role code, stored as the invitation type (adx_type)")
    action_mode: Optional[str] = Field(
        "add",
        description="'add' (default) adds the member to MyCircle; 'remove' removes them from MyCircle",
    )
    oauth: Optional[str] = Field(
        None, alias="Oauth",
        description="Client FCM OAuth bearer token; when omitted the push path mints one server-side",
    )


class DeleteServiceRequest(BaseModel):
    """Request body for deleting a specific service from a provider."""
    contact_id: str = Field(..., description="Contact ID of the requesting user (must be the provider owner)")
    provider_id: str = Field(..., description="Provider ID that owns the service")
    providerservices_id: str = Field(..., description="ID of the provider service row to delete")


class DeleteServiceResponse(BaseModel):
    """Response for the delete-service workflow."""
    message: str = Field(..., description="Outcome message (e.g. 'Service deleted successfully' or 'Service not found')")


class ItemOperation(BaseModel):
    """A single add/remove/update operation on an item within a category."""
    op: Literal["add", "remove", "update"] = Field(..., description="Operation type")
    item_id: Optional[str] = Field(None, description="item_id of the target item (required for remove/update; omit for add)")
    data: Optional[ParsedOfferingItem] = Field(None, description="Item data (required for add; for update, only supplied fields are merged)")


class CategoryOperation(BaseModel):
    """A single add/remove/update operation on a category within a service's offering."""
    op: Literal["add", "remove", "update"] = Field(..., description="Operation type")
    category_id: Optional[str] = Field(None, description="category_id of the target category (required for remove/update; omit for add)")
    category_name: Optional[str] = Field(None, description="Human-readable name hint (used for add when category_id is absent)")
    data: Optional[ParsedOfferingCategory] = Field(None, description="Category data (required for add; for update, only supplied non-None fields are merged)")
    item_operations: Optional[List[ItemOperation]] = Field(None, description="Nested item-level operations within this category (only used with op=update)")


class OfferingOperation(BaseModel):
    """A single add/remove/update operation on an offering within the etown_offerings tree."""
    op: Literal["add", "remove", "update"] = Field(..., description="Operation type")
    offering_id: Optional[str] = Field(None, description="offering_id of the target offering (required for remove/update; omit for add)")
    offering_name: Optional[str] = Field(None, description="Offering name (required for add; optional label for update)")
    offering_type: Optional[str] = Field(None, description="Offering type label (e.g. 'Product' / 'Service')")
    offering_image: Optional[str] = Field(None, description="Base64 image for the offering (uploaded to Azure Blob before storage)")
    offering_description: Optional[str] = Field(None, description="Offering description")
    category_operations: Optional[List[CategoryOperation]] = Field(None, description="Nested category-level operations within this offering (applied after offering scalars are merged)")


class EditServiceRequest(BaseModel):
    """
    Request body for editing a specific service of a provider.
    """
    model_config = ConfigDict(populate_by_name=True)

    contact_id: str = Field(..., description="Contact ID of the requesting user (must own the provider)")
    providerservices_id: str = Field(
        ...,
        validation_alias=AliasChoices("providerservices_id", "providerserviceid"),
        description="ID of the provider service row to update",
    )
    provider_id: Optional[str] = Field(None, description="Provider ID that owns the service; when supplied it must match the stored row")
    userprofile_id: Optional[str] = Field(None, description="Provider's userprofileid; when supplied it must match the stored row")
    service_name: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("service_name", "servicename"),
        description="New service name (maps to 'services')",
    )
    description: Optional[str] = Field(None, description="New description (maps to 'servicedescription')")
    is_description: Optional[bool] = Field(
        None,
        description=(
            "True when the description changed, which regenerates businesscategory / servicecategory / "
            "generalcategory from it before the write. False or omitted keeps the stored categories."
        ),
    )
    pricerange: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("pricerange", "price_range"),
        description="New price range",
    )
    quantity: Optional[str] = Field(None, description="New quantity/unit info")
    serviceitems: Optional[List[Dict[str, Any]]] = Field(None, description="Full replacement of offering categories/items JSON (use offering_operations for granular edits)")
    serviceimage1: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("serviceimage1", "image1"),
        description="Base64 image 1, or 'Null' to keep / 'Deleted' to clear the stored image",
    )
    serviceimage2: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("serviceimage2", "image2"),
        description="Base64 image 2, or 'Null' to keep / 'Deleted' to clear the stored image",
    )
    serviceimage3: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("serviceimage3", "image3"),
        description="Base64 image 3, or 'Null' to keep / 'Deleted' to clear the stored image",
    )
    offering_operations: Optional[List[OfferingOperation]] = Field(
        None,
        description=(
            "Granular add/remove/update operations on offerings (and their nested categories/items). "
            "Use offering_id / category_id / item_id from provider_details offering_data. "
            "Only syncs etown_offerings.items — etown_providerservices.serviceitems is unchanged."
        ),
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> "EditServiceRequest":
        """Require at least one editable service field to be set."""
        editable = {
            "service_name", "description", "pricerange", "quantity", "serviceitems",
            "serviceimage1", "serviceimage2", "serviceimage3", "offering_operations",
        }
        if not (self.model_fields_set & editable):
            raise ValueError("Provide at least one field to update")
        return self


class EditServiceResponse(BaseModel):
    """Response for the edit-service workflow."""
    message: str = Field(..., description="Outcome message (e.g. 'Service updated successfully' or 'Service not found')")
    serviceitems: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Updated serviceitems (categories + items with IDs) — returned only when serviceitems was replaced directly; not returned for offering_operations (which only patches etown_offerings)",
    )


class EditProviderProfileRequest(BaseModel):
    """Request body for editing the top-level provider profile.

    Updates etown_provider and all associated service records (etown_providerservices,
    etown_providerservices_copy). Pass None or the literal string 'Null' for
    service_image_url / availability to retain the existing value.
    """
    model_config = ConfigDict(populate_by_name=True)

    contact_id: str = Field(..., description="Contact ID of the provider owner")
    userprofile_id: str = Field(
        ...,
        validation_alias=AliasChoices("userprofile_id", "provider_userprofile"),
        description="User profile ID identifying the specific provider profile",
    )
    business_name: Optional[str] = Field(None, description="Updated business name")
    address: Optional[str] = Field(None, description="Updated address")
    description: Optional[str] = Field(None, description="Updated business description")
    availability: Optional[str] = Field(None, description="Availability text; pass 'Null' to retain the existing value")
    email: Optional[str] = Field(None, description="Business email address")
    service_image_url: Optional[str] = Field(None, description="Base64-encoded service image; pass 'Null' to retain the existing image. An already-stored blob URL is kept as-is")
    background_image: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("background_image", "Etown_background_imageUrl"),
        description="Base64-encoded profile background image; pass 'Null' to retain the existing image, 'Deleted' to clear it. An already-stored blob URL is kept as-is",
    )
    mobilenumber: Optional[str] = Field(None, description="Business mobile number")
    business_category: Optional[str] = Field(None, description="Business category — normalised to 'product', 'service' or 'product,service'; anything else becomes 'service'")
    latitude: Optional[str] = Field(None, description="Latitude as a string, -90 to 90")
    longitude: Optional[str] = Field(None, description="Longitude as a string, -180 to 180")
    city: Optional[str] = Field(None, description="City name")
    statecode: Optional[str] = Field(None, description="State code")
    area_location: Optional[str] = Field(None, description="Area or locality name")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address information")
    servicetype: Optional[str] = Field(None, description="Service type (e.g. 'Home Service', 'Shop')")
    addresschange: Optional[bool] = Field(
        False,
        description=(
            "True when the address moved the provider to a different town/area, which re-runs "
            "town assignment (etown_townprofile + etown_etownprovider link) in the background."
        ),
    )

    @field_validator("latitude", "longitude")
    @classmethod
    def _valid_coordinate(cls, v: Optional[str], info) -> Optional[str]:
        """Treat a blank coordinate as None and reject non-numeric or out-of-range values."""
        if v is None or str(v).strip() == "":
            return None
        try:
            parsed = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{info.field_name} must be a number")
        limit = 90 if info.field_name == "latitude" else 180
        if not -limit <= parsed <= limit:
            raise ValueError(f"{info.field_name} must be between -{limit} and {limit}")
        return v

    @model_validator(mode="after")
    def _at_least_one(self) -> "EditProviderProfileRequest":
        """Require at least one editable provider profile field to be set."""
        editable = {
            "business_name", "address", "description", "availability",
            "email", "service_image_url", "background_image", "mobilenumber",
            "business_category", "latitude", "longitude", "city", "statecode",
            "area_location", "additionaladdressinfo", "servicetype",
        }
        if not (self.model_fields_set & editable):
            raise ValueError("Provide at least one field to update")
        return self


class EditProviderProfileResponse(BaseModel):
    """Response for the edit-provider-profile workflow."""
    message: str = Field(..., description="Outcome message (e.g. 'Profile updated successfully' or 'Provider not found')")


class ProviderIdentifiersResponse(BaseModel):
    """The identity a providerid resolves to (GET /provider/identifiers)."""
    providerid: str = Field(..., description="Provider ID that was looked up")
    contactid: Optional[str] = Field(None, description="Contact ID owning the provider listing")
    userprofileid: Optional[str] = Field(None, description="Provider's role=1 user profile ID")
    businessname: Optional[str] = Field(None, description="Business name of the listing, for caller-side sanity checks")
    source: str = Field(..., description="Table the identifiers came from: 'etown_provider' or 'etown_providerservices'")


class ProfileViewerItem(BaseModel):
    """A single user who viewed a provider's profile (GET /provider/profile_viewers).

    Mirrors FeedLikeUserItem field for field, with Etown_vieweddate in place of
    Etown_likedon. Every identity field is null on a viewer whose etown_userprofile
    row has not mirrored down from the CRM -- the view is still real, so the row is
    returned rather than dropped.
    """
    Etown_name: Optional[str] = Field(None, description="Business name for a role=1 viewer, contact first name for role=2")
    Etown_contactid: Optional[str] = Field(None, description="Viewer's contact ID")
    Etown_userprofileid: Optional[str] = Field(None, description="Viewer's user profile ID")
    Etown_profileimage: Optional[str] = Field(None, description="Service image for a role=1 viewer, contact profile image for role=2")
    Etown_role: Optional[int] = Field(None, description="Viewer's role: 1 provider, 2 member")
    Etown_providertype: Optional[str] = Field(None, description="etown_provider.providertype when the viewer is a provider")
    Etown_isconnected: Optional[bool] = Field(None, description="Whether the viewer shares a member or provider relation with the requesting provider")
    Etown_invitation: Optional[str] = Field(None, description="Invitation code shared between the requesting provider and the viewer, if any")
    Etown_vieweddate: Optional[str] = Field(None, description="First view timestamp in IST, MM/DD/YYYY hh:mm AM/PM")
    Etown_deleted: Optional[str] = Field(None, description="'true'/'false' - viewer account deleted")
    Etown_deactivated: Optional[str] = Field(None, description="'true'/'false' - viewer account currently deactivated")


class ProfileViewersResponse(BaseModel):
    """One page of profile viewers plus the total across every page.

    The endpoint used to return a bare list, which PerformanceMiddleware wrapped as
    {"data": [...], "performance": {...}}; returning this model keeps that exact outer
    shape -- the middleware merges "performance" into a dict response rather than
    re-wrapping it -- and only adds total_views beside it, so a client already reading
    "data" is unaffected.
    """
    total_views: int = Field(..., description="Distinct viewers across all pages, matching get_provider_view_count(); the provider's own visits, anonymous views and deleted accounts are excluded")
    data: List[ProfileViewerItem] = Field(default_factory=list, description="This page of viewers, newest first; shorter than the page size on the last page")


class EditMemberProfileRequest(BaseModel):
    """Request body for editing a member/contact profile.

    Updates etown_contact and propagates name, email, and mobilenumber changes
    to etown_userprofile and etown_member. Only the fields supplied are changed.
    For profile_image: pass 'no image' to clear the existing image, omit or
    pass null to retain the existing image, or pass a base64 string to upload a new one.
    """
    contact_id: str = Field(..., description="Contact ID of the member to update")
    firstname: Optional[str] = Field(None, description="Updated first name")
    email: Optional[str] = Field(None, description="Updated email address")
    mobilenumber: Optional[int] = Field(None, description="Updated mobile number")
    additionaladdressinfo: Optional[str] = Field(None, description="Additional address information")
    address: Optional[str] = Field(None, description="Updated street address")
    latitude: Optional[float] = Field(None, description="Latitude coordinate")
    longitude: Optional[float] = Field(None, description="Longitude coordinate")
    statecode: Optional[str] = Field(None, description="State code")
    city: Optional[str] = Field(None, description="City name")
    connectycube_id: Optional[str] = Field(None, description="ConnectyCube user identifier")
    birthdate: Optional[str] = Field(None, description="Date of birth in DD/MM/YYYY or YYYY-MM-DD format")
    profile_image: Optional[str] = Field(None, description="Base64-encoded profile image; pass 'no image' to clear the existing image; omit or pass null to retain the existing image")

    @model_validator(mode="after")
    def _at_least_one(self) -> "EditMemberProfileRequest":
        """Require at least one editable member profile field to be set."""
        editable = {
            "firstname", "email", "mobilenumber", "additionaladdressinfo",
            "address", "latitude", "longitude", "statecode", "city",
            "connectycube_id", "birthdate", "profile_image",
        }
        if not (self.model_fields_set & editable):
            raise ValueError("Provide at least one field to update")
        return self


class EditMemberProfileResponse(BaseModel):
    """Response for the edit-member-profile workflow."""
    message: str = Field(..., description="Outcome message (e.g. 'Profile updated successfully' or 'Contact not found')")
    status: Optional[str] = Field(None, description="Status of the operation")
    firstname: Optional[str] = None
    emailaddress1: Optional[str] = None
    address1_composite: Optional[str] = None
    address1_line1: Optional[str] = None
    ets_additionaladdressinfo: Optional[str] = None
    ets_profileimage: Optional[str] = None


class UploadInfo(BaseModel):
    """File upload metadata."""
    filename: Optional[str] = Field(None, description="Original filename of the uploaded image")
    file_size_bytes: Optional[int] = Field(None, description="File size in bytes")
    file_size_formatted: Optional[str] = Field(None, description="Human-readable file size (e.g. '2.5 MB')")
    file_type: Optional[str] = Field(None, description="MIME type of the uploaded file")

class OfferingField(BaseModel):
    """A single extracted field with a per-value confidence score."""
    value: Optional[str] = Field(None, description="Extracted value, or null if not visible")
    confidence: Optional[float] = Field(None, description="Extraction confidence (0.0–1.0), or null if unknown")

class OfferingContactInfo(BaseModel):
    """Contact details extracted from the business image."""
    phone_number: Optional[OfferingField] = Field(None, description="Business phone number")
    email: Optional[OfferingField] = Field(None, description="Business email address")
    website: Optional[OfferingField] = Field(None, description="Business website URL")
    address: Optional[OfferingField] = Field(None, description="Physical business address")

class OfferingBusinessDetails(BaseModel):
    """Business details extracted and/or AI-generated from the image."""
    business_name: Optional[OfferingField] = Field(None, description="Business name as visible in the image")
    business_description_extracted: Optional[OfferingField] = Field(None, description="Raw visible text describing the business (e.g. slogans, taglines)")
    business_description_ai_generated: Optional[OfferingField] = Field(None, description="AI-written English description of the business as a named entity")
    business_category: Optional[OfferingField] = Field(None, description="Business category (e.g. 'Convenience Store', 'Restaurant')")
    business_confidence: Optional[float] = Field(None, description="Overall confidence score for business identification (0.0–1.0)")
    contact_info: Optional[OfferingContactInfo] = Field(None, description="Contact information extracted from the image")

class DetectedObject(BaseModel):
    """A single object visually detected in the image."""
    object_name: str = Field(..., description="Name of the detected object (e.g. 'Pepsi Bottle')")
    object_type: str = Field(..., description="Object type (e.g. 'Product', 'Brand', 'Signage')")
    confidence_score: float = Field(..., description="Detection confidence (0.0–1.0)")

class MediaAnalysis(BaseModel):
    """Analysis metadata: what was detected and how."""
    primary_analysis_method: Optional[str] = Field(None, description="Method used: 'ocr', 'object_detection', or 'hybrid'")
    ocr_extracted_text: List[str] = Field(default_factory=list, description="Distinct text strings extracted via OCR")
    detected_objects: List[DetectedObject] = Field(default_factory=list, description="Objects visually detected in the image")

class OfferingItem(BaseModel):
    """A single item within an offering category."""
    item_name: Optional[str] = Field(None, description="Item name (original language/script)")
    item_price: Optional[float] = Field(None, description="Item price as a number, or null if not visible")
    item_currency: Optional[str] = Field(None, description="Currency symbol/code as visible in image, or null")
    item_description_extracted: Optional[str] = Field(None, description="Raw text description of the item from the image")
    item_description_ai_generated: Optional[str] = Field(None, description="AI-written English description of the item")
    source: Optional[str] = Field(None, description="How this item was detected: 'ocr', 'object_detection', or 'hybrid'")
    confidence_score: Optional[float] = Field(None, description="Confidence score for this item (0.0–1.0)")

class OfferingCategory(BaseModel):
    """A category grouping related items within an offering."""
    category_name: Optional[str] = Field(None, description="Category name (original language/script)")
    items: List[OfferingItem] = Field(default_factory=list, description="Items within this category")

class OfferingEntry(BaseModel):
    """A single offering group (product/service line) extracted from the image."""
    offering_name: Optional[str] = Field(None, description="Offering group name (e.g. 'Beverages', 'Fresh Produce')")
    offering_description: Optional[str] = Field(None, description="English description of this offering group")
    offering_type: Optional[str] = Field(None, description="'product' or 'service'")
    suggestion_source: Optional[str] = Field(None, description="How this offering was detected: 'ocr', 'object_detection', or 'hybrid'")
    confidence_score: Optional[float] = Field(None, description="Confidence score for this offering (0.0–1.0)")
    categories: List[OfferingCategory] = Field(default_factory=list, description="Category groups with items under this offering")

class OfferingServiceInput(BaseModel):
    """An edited offering group from /api/offerings/image, ready to become one provider service.

    Mirrors the editable fields of ``OfferingEntry`` and adds the flat service fields the
    frontend supplies/re-attaches (price range, quantity, base64 images). The backend
    flattens each instance into a single ``ServiceDetailRequest`` (one service row).
    """
    offering_name: Optional[str] = Field(None, description="Maps to service_name")
    offering_description: Optional[str] = Field(None, description="Maps to service description; if omitted, derived from item names")
    offering_type: Optional[str] = Field(None, description="'product' or 'service' (informational)")
    pricerange: Optional[str] = Field(None, description="User-edited price range; if omitted, derived from item prices")
    quantity: Optional[str] = Field(None, description="User-supplied quantity/unit (offerings have none)")
    serviceimage1: Optional[str] = Field(None, description="Base64 image re-attached by frontend")
    serviceimage2: Optional[str] = Field(None, description="Base64 image re-attached by frontend")
    serviceimage3: Optional[str] = Field(None, description="Base64 image re-attached by frontend")
    categories: List[OfferingCategory] = Field(default_factory=list, description="Optional; used only to derive price/description fallbacks")

AddServicesToProvidersRequest.model_rebuild()
ProviderDetailsResponse.model_rebuild()

class ProcessingMetadata(BaseModel):
    """Processing and confidence metadata for the image analysis."""
    overall_confidence: Optional[float] = Field(None, description="Overall extraction confidence (0.0–1.0)")
    requires_user_confirmation: bool = Field(True, description="Whether user should review/confirm the extracted data")
    schema_version: str = Field("1.1", description="Response schema version")

class UnratedProviderItem(BaseModel):
    """Single unrated provider item in the unrated provider list response."""
    Etown_providercontactid: Optional[str] = Field(None, description="Provider's contact ID")
    Etown_provideruserprofileid: Optional[str] = Field(None, description="Provider's user profile ID")
    Etown_businessname: Optional[str] = Field(None, description="Provider's business name")
    Etown_serviceimageurl: Optional[str] = Field(None, description="URL of the provider's service image")
    Etown_providertype: Optional[str] = Field(None, description="etown_provider.providertype of the provider")
    Etown_servicecreatedon: Optional[str] = Field(None, description="ISO timestamp when the service was created")
    Etown_bookingid: Optional[str] = Field(None, description="Booking ID (etown_allrequests.allrequestid) of the unrated booking")
    Etown_bookingtrackid: Optional[str] = Field(None, description="Booking track ID (etown_allrequests.requestid), the same pairing the booking lists use")
    Etown_providerdeleted: Optional[str] = Field(None, description="'true'/'false' - provider account deleted")
    Etown_providerdeactivated: Optional[str] = Field(None, description="'true'/'false' - provider account currently deactivated")


class InstructionEntry(BaseModel):
    """A single booking instruction with the date/time it was supplied.

    Only `userprofileid`/`contactid` are persisted against the instruction; `role`
    (the sender's rolename), `name` and `profileimage` are resolved from the profile
    tables when the list is read, so they always reflect the sender's current details.

    Media entries carry what the plain URL cannot say: a document's original `filename`,
    and a video's `thumbnail` for rendering the clip's poster frame. Both are None on
    every entry that has neither.
    """
    instruction: Optional[str] = None
    type: Optional[str] = None
    date: Optional[str] = None
    userprofileid: Optional[str] = None
    contactid: Optional[str] = None
    role: Optional[str] = None
    name: Optional[str] = None
    profileimage: Optional[str] = None
    filename: Optional[str] = None
    thumbnail: Optional[str] = None


class ProviderBookingDetails(BaseModel):
    """Booking details for a single provider request."""
    Etown_BookingId: Optional[str] = None
    Etown_BookingTrackId: Optional[str] = None
    Etown_requestorContactId: Optional[str] = None
    Etown_requestorUserProfileId: Optional[str] = None
    Etown_requestorname: Optional[str] = None
    Etown_requestorrole: Optional[Any] = None
    Etown_requestorrolename: Optional[str] = None
    Etown_requestoraddress: Optional[str] = None
    Etown_requestorlatitude: Optional[str] = None
    Etown_requestorlongitude: Optional[str] = None
    Etown_requestorProfileImageUrl: Optional[Any] = None
    Etown_requeststatus: Optional[str] = None
    Etown_requestedservicedatetime: Optional[str] = None
    Etown_otp: Optional[str] = None
    Etown_instructions: Optional[List[InstructionEntry]] = None
    Etown_bookingcreatedon: Optional[Any] = None
    Etown_updatedcompletedtime: Optional[Any] = None
    Etown_updatedacceptedtime: Optional[Any] = None
    Etown_cancelledtime: Optional[Any] = None
    Etown_ownprofileimageurl: Optional[Any] = None
    Etown_cancelreason: Optional[Any] = None
    Etown_publicsearchprovider: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_servicetype: Optional[str] = None
    Etown_businesscreatedon: Optional[str] = None
    Etown_providerrole: Optional[Any] = None
    Etown_providerrolename: Optional[Any] = None
    Etown_mobilenumber: Optional[Any] = None
    Etown_providercontactid: Optional[str] = None
    Etown_serviceproviderUserProfileId: Optional[str] = None
    Etown_ServiceProviderBusinessname: Optional[str] = None
    Etown_serviceprovideraddress: Optional[str] = None
    Etown_serviceproviderlatitude: Optional[str] = None
    Etown_serviceproviderlongitude: Optional[str] = None
    Etown_serviceprovidercurrentaddress: Optional[str] = None
    Etown_serviceprovidercurrentaddresslatitude: Optional[str] = None
    Etown_serviceprovidercurrentaddresslongitude: Optional[str] = None
    Etown_requestordeleted: Optional[str] = None
    Etown_requestordeactivated: Optional[str] = None
    Etown_providerdeleted: Optional[str] = None
    Etown_providerdeactivated: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_disabled: Optional[bool] = Field(
        None,
        description=(
            "etown_provider.disabled of the booking's service provider; null when the "
            "booking has no provider row behind it"
        ),
    )
    Etown_badge_label: Optional[str] = None


class ProviderServiceDetail(BaseModel):
    """Individual service line item within a booking."""
    Etown_servicedescription: Optional[str] = None
    Etown_quantity: Optional[Any] = None
    Etown_price: Optional[str] = None
    Etown_serviceimageurl: Optional[str] = None
    Etown_bookingId: Optional[str] = None


class ProviderBookingItem(BaseModel):
    """Single booking entry containing booking details and its service line items."""
    BookingDetails: ProviderBookingDetails
    ServiceDetails: List[ProviderServiceDetail]


class ProviderRequestListResponse(BaseModel):
    """Response model for the provider_request_list endpoint."""
    Bookings: List[ProviderBookingItem]


class ProviderCompletedBookingItem(BaseModel):
    """Completed booking entry: booking details, the rating left for it, and its service line items."""
    BookingDetails: ProviderBookingDetails
    Rating: Optional[int] = None
    ServiceDetails: List[ProviderServiceDetail]


class ProviderCompletedListResponse(BaseModel):
    """Response model for the provider_completed_list endpoint."""
    Bookings: List[ProviderCompletedBookingItem]


class AllRequestsListResponse(BaseModel):
    """Response model for GET /book/request_list — paginated booking list for members and providers."""
    RequestDetails: List[ProviderBookingItem]
    TotalCount: int


class FrequentlyVisitedItem(BaseModel):
    """Single frequently-visited provider-service entry for GET /book/frequently_visited.

    Mirrors the PowerApps FrequentlyVisitViewAPI Select projection (exact Etown_* keys),
    with an added Etown_visitcount for the number of requests the member made to that
    provider-service.
    """
    Etown_contactlatitude: Optional[str] = None
    Etown_contactlongitude: Optional[str] = None
    Etown_connectycubeid: Optional[str] = None
    Etown_contactaddress: Optional[str] = None
    Etown_contactid: Optional[str] = None
    Etown_firstname: Optional[str] = None
    Etown_contactcreatedon: Optional[str] = None
    Etown_mobilenumber: Optional[str] = None
    Etown_businessname: Optional[str] = None
    Etown_provideraddress: Optional[str] = None
    Etown_providerlatitude: Optional[str] = None
    Etown_providerlongitude: Optional[str] = None
    Etown_providername: Optional[str] = None
    Etown_providercontact: Optional[str] = None
    Etown_providerid: Optional[str] = None
    Etown_rolename: Optional[str] = None
    Etown_role: Optional[Any] = None
    Etown_providercreatedon: Optional[str] = None
    Etown_yearinbusiness: Optional[str] = None
    Etown_provideremail: Optional[str] = None
    Etown_userprofileid: Optional[str] = None
    Etown_imageurl: Optional[str] = None
    Etown_publicsearchprovider: Optional[str] = None
    Etown_providertype: Optional[str] = None
    Etown_address: Optional[str] = None
    Etown_city: Optional[str] = None
    Etown_statecode: Optional[str] = None
    Etown_visitcount: int = 0


class AddRatingRequest(BaseModel):
    """Request body for adding a rating, matching the PowerApps rating flow payload."""
    booking_id: str = Field(..., description="allrequestid of the booking to rate")
    rating_value: int = Field(..., ge=1, le=5, description="Star rating (1–5)")
    israting: Optional[str] = Field("True", description="Rating flag; defaults to 'True'")
    request_Status: Optional[str] = Field(None, description="Accepted but not stored")
    service_provider_comments: Optional[str] = Field(None, description="Accepted but not stored")
    service_provider_id: Optional[str] = Field(None, description="Accepted but not stored")
    service_provideruserprofileid: Optional[str] = Field(None, description="Accepted but not stored")


class AddRatingResponse(BaseModel):
    """Response body for the add-rating endpoint, matching etown_ratings columns."""
    ratingid: Optional[str] = None
    overallrating: Optional[float] = None
    ratingcount: Optional[int] = None
    provideruserprofileid: Optional[str] = None
    providercontactid: Optional[str] = None
    providerbusinessname: Optional[str] = None
    memberbookingcount: Optional[int] = None
    createdon: Optional[str] = None


class NotifyUnverifiedProviderRequest(BaseModel):
    """Request body for the member-triggered WhatsApp nudge to an unverified provider.

    The provider is identified one of two ways:
      * Both provider_userprofileid and provider_contactid -> the existing DB flow
        (the business name is read from the provider record).
      * Otherwise provider_mobilenumber and provider_name are both required, and the
        number must be a valid Indian mobile number (not a landline).
    """
    customer_userprofileid: str = Field(..., description="UserProfile ID of the customer triggering the nudge")
    customer_contactid: str = Field(..., description="Contact ID of the customer triggering the nudge")
    provider_userprofileid: Optional[str] = Field(None, description="UserProfile ID of the provider (with provider_contactid, uses the DB flow)")
    provider_contactid: Optional[str] = Field(None, description="Contact ID of the provider (with provider_userprofileid, uses the DB flow)")
    provider_mobilenumber: Optional[str] = Field(
        None,
        description="Indian mobile number of the provider; required (with provider_name) when the IDs are not both supplied",
    )
    provider_name: Optional[str] = Field(
        None,
        description="Provider/business name shown in the WhatsApp message; required when identifying by phone number",
    )

    @model_validator(mode="after")
    def _validate_provider_identifier(self) -> "NotifyUnverifiedProviderRequest":
        """Require both provider ids, or a provider name plus a valid Indian mobile number."""
        has_both_ids = bool((self.provider_userprofileid or "").strip()) and bool(
            (self.provider_contactid or "").strip()
        )
        if has_both_ids:
            return self
        phone = (self.provider_mobilenumber or "").strip()
        name = (self.provider_name or "").strip()
        if not phone or not name:
            raise ValueError(
                "Provide both provider_userprofileid and provider_contactid, "
                "or provider_mobilenumber and provider_name"
            )
        if not is_valid_indian_mobile(phone):
            raise ValueError(
                "provider_mobilenumber must be a valid Indian mobile number "
                "(10 digits starting 6-9, optional +91), not a landline"
            )
        return self


class NotifyUnverifiedProviderResponse(BaseModel):
    """Response body for the notify_unverified endpoint."""
    sent: bool = Field(..., description="Whether the WhatsApp notification was dispatched")
    reason: Optional[str] = Field(None, description="Reason when sent=False (e.g. already_notified, no_mobile, template_disabled, invalid_number, send_failed)")
    businessname: Optional[str] = Field(None, description="Provider business name resolved during the call")


class WhatsAppTemplateRequest(BaseModel):
    """Request model for sending an approved WhatsApp template message."""

    mobile_number: str = Field(
        ...,
        description="Recipient phone number in E.164 form (e.g. +919876543210). Non-digit characters are stripped.",
        examples=["+919876543210"],
    )
    template_name: str = Field(
        ...,
        description="Name of the approved WhatsApp template in the Meta Business Manager.",
        examples=["offer_notification"],
    )
    language_code: Optional[str] = Field(
        None,
        description="BCP-47 language code for the template (e.g. 'en', 'en_US', 'ta'). Defaults to WHATSAPP_DEFAULT_LANGUAGE.",
        examples=["en"],
    )
    body_parameters: Optional[List[str]] = Field(
        None,
        description="Body variables in template definition order (substituted for {{1}}, {{2}}, ...). Use for POSITIONAL templates only.",
        examples=[["Acme Cafe", "Buy one get one free"]],
    )
    body_named_parameters: Optional[Dict[str, str]] = Field(
        None,
        description="Named body variables for NAMED templates (e.g. {'businessname': 'Acme Cafe'} substitutes {{businessname}}). Mutually exclusive with body_parameters.",
        examples=[{"businessname": "Acme Cafe"}],
    )
    header_parameters: Optional[List[str]] = Field(
        None,
        description="Text header variables, if the template has a text header with variables.",
    )


class WhatsAppTextRequest(BaseModel):
    """Request model for a freeform WhatsApp text message (24h session only)."""

    mobile_number: str = Field(..., description="Recipient phone in E.164 form.")
    body: str = Field(..., min_length=1, max_length=4096, description="Message body.")
    preview_url: bool = Field(
        False, description="Whether WhatsApp should render URL previews."
    )


class WhatsAppSendResponse(BaseModel):
    """Response model for WhatsApp send endpoints."""

    success: bool = Field(..., description="True if the message was accepted by Meta.")
    message_id: Optional[str] = Field(
        None, description="WhatsApp message wamid returned by the Cloud API."
    )
    recipient: Optional[str] = Field(
        None, description="Normalized recipient phone (digits-only)."
    )
    raw: Optional[Dict[str, Any]] = Field(
        None, description="Raw response payload from the WhatsApp Cloud API."
    )


class ImageUploadRequest(BaseModel):
    """Request model for image upload with multipart/form-data."""
    user_profile_id: str = Field(..., description="User profile ID for the image upload", examples=[""])

class ImageUploadResponse(BaseModel):
    """Response model for POST /api/offerings/image (schema v1.1)."""
    upload_info: Optional[UploadInfo] = Field(None, description="Uploaded file metadata")
    business_details: Optional[OfferingBusinessDetails] = Field(None, description="Extracted and AI-generated business information")
    media_analysis: Optional[MediaAnalysis] = Field(None, description="OCR text, detected objects, and analysis method used")
    offering_data: List[OfferingEntry] = Field(default_factory=list, description="Explicitly confirmed offerings (OCR-sourced, e.g. menus with prices)")
    offering_suggestions: List[OfferingEntry] = Field(default_factory=list, description="AI-inferred offerings (visually detected products without explicit price list)")
    selected_offerings: List[Any] = Field(default_factory=list, description="User-confirmed offerings (populated post-review)")
    processing_metadata: Optional[ProcessingMetadata] = Field(None, description="Overall confidence and schema version")


class OnboardProviderDetails(BaseModel):
    """Extracted provider/business details from an onboarding image."""
    Business_Name: Optional[str] = Field(None, description="Business or company name")
    Description: Optional[str] = Field(None, description="Business description or tagline")
    Phone_Number: Optional[str] = Field(None, description="Contact phone number")
    Email: Optional[str] = Field(None, description="Contact email address")
    Website: Optional[str] = Field(None, description="Business website URL")
    Category: Optional[str] = Field(None, description="Business category or industry")
    Name: Optional[str] = Field(None, description="Contact person or owner name")
    Address: Optional[str] = Field(None, description="Physical business address")
    Service_Type: Optional[str] = Field(
        None,
        description=(
            "How the business serves customers: 'Home Delivery', 'Store Delivery' or 'Both', only when "
            "the prompt or image says so (POST /provider/ai_onboarding and /provider/bubble only; null elsewhere)"
        ),
    )

    @field_validator('Service_Type', mode='before')
    @classmethod
    def coerce_service_type(cls, v: Any) -> Optional[str]:
        """Map any-case 'home delivery', 'store delivery' or 'both' onto its label, and any other value onto None."""
        key = " ".join(str(v or "").replace("_", " ").split()).lower()
        return {"home delivery": "Home Delivery", "store delivery": "Store Delivery", "both": "Both"}.get(key)


class OnboardServiceDetail(BaseModel):
    """A single service or product extracted from an onboarding image."""
    Service_Name: Optional[str] = Field(None, description="Name of the service or product")
    Service_Description: Optional[str] = Field(None, description="One-line description of the service or product")
    Service_Type: Optional[str] = Field(None, description="'Product' or 'Service'")
    Service_Quantity: Optional[str] = Field(None, description="Unit or quantity information if visible (e.g. 'per kg', 'per unit')")
    Service_Price: Optional[str] = Field(None, description="Price as visible in the image")
    Service_Expected_Price: Optional[str] = Field(
        None,
        description="AI-estimated usual local price in rupees (POST /provider/ai_onboarding only; null elsewhere)",
    )
    Service_Image_Url: Optional[str] = Field(
        None,
        description=(
            "Relative Azure Blob path of this service's photo: the business_image the prompt links to "
            "it (or that best matches it), or the existing service's image on a merge "
            "(POST /provider/ai_onboarding and /provider/bubble only; null elsewhere)"
        ),
    )


class OnboardItem(BaseModel):
    """Combined provider details and service list extracted from a single onboarding image."""
    Provider_Details: OnboardProviderDetails
    Service_Details: List[OnboardServiceDetail]


class OnboardDetailsResponse(BaseModel):
    """Response model for the /onboard_details image extraction endpoint."""
    Onboard_data: List[OnboardItem]


class AIOnboardingRequest(BaseModel):
    """Request model for POST /provider/ai_onboarding.

    Carries a photo of a business - shopfront board, visiting card, flyer or menu - as base64
    text, a free-text prompt the field agent typed about it, or both; at least one is required.
    Nothing is onboarded: the input is read so the capture form can be pre-filled before
    POST /provider/unverified_provider is called. ``contact_id`` and ``user_profile_id`` name
    the field agent making the call and are stored with the call, as is ``conversation_id``.
    """
    conversation_id: Optional[str] = Field(
        None,
        max_length=100,
        description=(
            "Conversation id the call belongs to, stored with the call; needed for a prompt that "
            "picks an existing provider by providerid"
        ),
    )
    contact_id: Optional[str] = Field(
        None,
        max_length=100,
        description="etown_contact.contactid of the field agent making the call, stored with the call",
    )
    user_profile_id: Optional[str] = Field(
        None,
        max_length=100,
        description="etown_userprofile.userprofileid of the field agent making the call, stored with the call",
    )
    business_image: Optional[str] = Field(
        None,
        description="Base64 (or data-URL) JPEG, PNG or WebP of the business, at most 10 MB",
    )
    prompt: Optional[str] = Field(
        None,
        max_length=4000,
        description=(
            "What the field agent knows about the business, in their own words - e.g. 'Sri "
            "Ganesh Mobiles, 9876543210, Anna Nagar Chennai, sells phones and does repairs'. "
            "Facts in it win over the image when the two disagree"
        ),
    )

    @model_validator(mode="after")
    def _require_image_or_prompt(self) -> "AIOnboardingRequest":
        """Require a business_image, a prompt, or both."""
        if not (self.business_image or "").strip() and not (self.prompt or "").strip():
            raise ValueError("Send a business_image, a prompt, or both")
        return self


class AIOnboardingProviderDetails(OnboardProviderDetails):
    """Provider details for POST /provider/ai_onboarding and /bubble: the onboarding fields without Phone_Number, plus mobiles, coordinates, city, statecode and business category."""
    Phone_Number: SkipJsonSchema[Optional[str]] = Field(None, exclude=True)
    mobilenumber: Optional[str] = Field(
        None,
        description=(
            "First valid Indian mobile in the prompt or image, as 10 national digits (on a merge or "
            "an existing match, the matched provider's mobile), or null"
        ),
    )
    alternatemobile: Optional[str] = Field(
        None,
        description=(
            "Second distinct valid Indian mobile in the prompt or image, as 10 national digits (on a "
            "merge, a mobile given in the confirmation reply first, then the provider's stored one), or null"
        ),
    )
    latitude: Optional[float] = Field(
        None,
        description=(
            "Latitude geocoded from Address (on a merge, the existing provider's stored latitude "
            "when its address is kept); null when there is no address or it could not be found"
        ),
    )
    longitude: Optional[float] = Field(
        None,
        description=(
            "Longitude geocoded from Address (on a merge, the existing provider's stored longitude "
            "when its address is kept); null when there is no address or it could not be found"
        ),
    )
    city: Optional[str] = Field(
        None,
        description=(
            "City or town geocoded from Address (on a merge, the existing provider's stored city "
            "when its address is kept); null when there is no address or Google gives none"
        ),
    )
    statecode: Optional[str] = Field(
        None,
        description=(
            "Two-letter state code geocoded from Address, e.g. 'TN' (on a merge, the existing "
            "provider's stored statecode when its address is kept); null when there is no address "
            "or Google gives none"
        ),
    )
    business_category: Optional[str] = Field(
        None,
        description="'product', 'service' or 'product,service', from the services' types, or null",
    )
    interested: Optional[str] = Field(
        None,
        description=(
            "'true' or 'false', for POST /provider/unverified_provider's interested; null from "
            "/ai_onboarding, and kept by /bubble unless the prompt changes it"
        ),
    )

    @field_validator('interested', mode='before')
    @classmethod
    def coerce_interested(cls, v: Any) -> Optional[str]:
        """Turn a JSON boolean or any-case 'true'/'false' into lower-case 'true'/'false', passing other values through."""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str) and v.strip().lower() in ("true", "false"):
            return v.strip().lower()
        return v


class AIOnboardingResponse(BaseModel):
    """Response model for POST /provider/ai_onboarding.

    ``Provider_Details`` mixes two kinds of value: the business name, mobiles, email,
    website, address and owner name are taken from the prompt and image and are null when
    neither gives them, while the description is generated. ``Service_Details`` holds 3-4
    services, the ones the prompt names or the image shows first, topped up with generated ones.
    When the mobile already has providers, ``existing_providers`` carries them, alongside the
    generated listing. When a later prompt in the same conversation names one of them by
    providerid, that saved listing (or one generated from the earlier input) is merged into
    that provider - its values win, empty ones and dissimilar services (up to 5 in all) come from the listing -
    and ``providerid`` is set. When the business names or addresses differ, nothing is merged until
    the next prompt confirms which to use, and ``confirmation_required`` is true meanwhile; a
    confirmed name rewrites the business and service descriptions to fit it.
    """
    providerid: Optional[str] = Field(
        None,
        description=(
            "The existing provider the details were merged into, or wait to be merged into while "
            "confirmation_required is true; null otherwise"
        ),
    )
    conflicting_business_name: Optional[str] = Field(
        None,
        description=(
            "While confirmation_required is true, the generated business name that does not match "
            "the picked provider's name; null otherwise"
        ),
    )
    conflicting_address: Optional[str] = Field(
        None,
        description=(
            "While confirmation_required is true, the address from the input when it is neither worded "
            "like nor within 2 km of the picked provider's address; null otherwise"
        ),
    )
    confirmation_required: bool = Field(
        False,
        description=(
            "True when response asks the agent to confirm or provide something: a business name or "
            "address that differs from the picked provider's (nothing is merged until the next prompt "
            "names the ones to use, or says yes for the generated ones, no for the existing ones), "
            "existing providers to pick from, a missing business name or mobile, an invalid mobile or "
            "name, a refused alternate mobile, a photo whose service is unclear, input with no business details, the caller's own "
            "provider, or a provider not shown or gone; false when nothing is asked"
        ),
    )
    status: Optional[str] = Field(
        None,
        description=(
            "The mobile's state as in GET /provider/unverified_provider_list: 'verified_provider', "
            "'added_by_fos' or 'unverified' when it already has providers, 'available' when it has "
            "none, null when no valid mobile was found"
        ),
    )
    existing_providers: List[UnverifiedProviderItem] = Field(
        default_factory=list,
        description=(
            "Providers already on the mobile, as in GET /provider/unverified_provider_list, shown "
            "for the field agent to pick one by providerid. Empty otherwise"
        ),
    )
    business_image_url: Optional[str] = Field(
        None,
        description=(
            "Relative Azure Blob path the image was stored at; null when no image was sent or "
            "its upload failed (the image is still analysed)"
        ),
    )
    Provider_Details: AIOnboardingProviderDetails = Field(
        default_factory=AIOnboardingProviderDetails,
        description=(
            "Business facts taken from the prompt and image, the generated description, mobiles, "
            "coordinates and business_category; on a merge, the existing provider's values with gaps "
            "filled; the unmerged generated details while confirmation_required is true"
        ),
    )
    Service_Details: List[OnboardServiceDetail] = Field(
        default_factory=list,
        description=(
            "3-4 services, each with name, description, type, quantity (a number for a product, "
            "'--' for a service), price (null unless given) and AI-estimated expected price; fewer only when the kind of business "
            "could not be told from the input; on a merge, the existing services plus generated "
            "ones not similar to them, added only while there are fewer than 5 in all; the unmerged generated services while confirmation_required is true"
        ),
    )
    response: Optional[str] = Field(
        None,
        description=(
            "Message to the field agent: that the mobile is already registered when "
            "existing_providers is filled; on a merge, that it was merged or, on a name conflict, "
            "both business names and/or addresses and a request to confirm; for a greeting, question or "
            "gibberish, a chatbot reply; else what was prepared, then a request for the business name "
            "and/or a valid mobile number when either is missing, or an invitation to review"
        ),
    )


class AIOnboardingBubbleDetails(BaseModel):
    """Fields shared by the POST /provider/bubble request and response: conversation id, provider and service details."""
    conversation_id: Optional[str] = Field(
        None,
        max_length=100,
        description=(
            "Bubble conversation id: the one sent in the request, or a new UUID when none was sent; "
            "send it back on every follow-up so the turns are kept together in etown_ai_onboarding_bubble"
        ),
    )
    Provider_Details: AIOnboardingProviderDetails = Field(
        ...,
        description=(
            "Provider_Details from /ai_onboarding or the previous /bubble response, as sent except where "
            "the prompt changed them; mobilenumber and alternatemobile are re-validated, business_category "
            "is recomputed, and latitude/longitude are kept while Address is unchanged and geocoded again "
            "when it changes or none were sent"
        ),
    )
    Service_Details: List[OnboardServiceDetail] = Field(
        default_factory=list,
        description="Services as sent except where the prompt added, removed or changed them",
    )


class AIOnboardingBubbleResponse(AIOnboardingBubbleDetails):
    """Response model for POST /provider/bubble: the provider and service details with the follow-up applied."""
    response: Optional[str] = Field(
        None,
        description=(
            "Message to the field agent saying what was changed and, for anything the prompt asked "
            "for that was not updated, which field and why"
        ),
    )
    confirmation_required: bool = Field(
        False,
        description=(
            "True when response asks the agent to confirm or provide something: a business name that "
            "does not fit the description and services (not changed yet, see pending_business_name), "
            "an unclear prompt, an invalid mobile number, an address that cannot be found, a photo "
            "whose service is unclear or missing, or a detail that was not given"
        ),
    )
    pending_business_name: Optional[str] = Field(
        None,
        description=(
            "The business name waiting for confirmation when confirmation_required is true, else null; "
            "a 'yes' in the next prompt of the same conversation applies it"
        ),
    )


class AIOnboardingBubbleRequest(AIOnboardingBubbleDetails):
    """Request model for POST /provider/bubble.

    Carries the Provider_Details and Service_Details from POST /provider/ai_onboarding (or the
    previous /bubble response) plus the field agent's follow-up ``prompt``, a new
    ``business_image``, or both; at least one is required. ``conversation_id`` ties the turns
    together; omit it on the first turn to get a new one. ``contact_id`` and ``user_profile_id``
    name the field agent making the call and are stored with each turn.
    """
    contact_id: Optional[str] = Field(
        None,
        max_length=100,
        description="etown_contact.contactid of the field agent making the call, stored with the turn",
    )
    user_profile_id: Optional[str] = Field(
        None,
        max_length=100,
        description="etown_userprofile.userprofileid of the field agent making the call, stored with the turn",
    )
    business_image: Optional[str] = Field(
        None,
        description=(
            "Base64 (or data-URL) JPEG, PNG or WebP of the business, at most 10 MB - e.g. a menu, "
            "price list or visiting card whose facts and services should be applied to the details"
        ),
    )
    prompt: Optional[str] = Field(
        None,
        max_length=4000,
        description=(
            "The field agent's follow-up, e.g. 'change the second service price to 300', "
            "'add AC gas refilling', 'add the items from this menu' or 'make the description shorter'"
        ),
    )

    @model_validator(mode="after")
    def _require_image_or_prompt(self) -> "AIOnboardingBubbleRequest":
        """Require a business_image, a prompt, or both."""
        if not (self.business_image or "").strip() and not (self.prompt or "").strip():
            raise ValueError("Send a business_image, a prompt, or both")
        return self


class InstagramConnectURLResponse(BaseModel):
    """Response containing the Instagram OAuth URL for Instagram Business connection."""
    oauth_url: str = Field(..., description="Instagram OAuth URL — redirect the user's browser to this URL")


class InstagramCallbackResponse(BaseModel):
    """Response after successfully processing the OAuth callback and saving the connection."""
    message: str = Field(..., description="Human-readable status message")
    instagram_business_id: str = Field(..., description="Instagram Business Account ID")
    username: Optional[str] = Field(None, description="Instagram username / handle")
    name: Optional[str] = Field(None, description="Instagram display name")
    account_type: Optional[str] = Field(None, description="Instagram account type: BUSINESS or CREATOR")
    followers_count: Optional[int] = Field(None, description="Number of followers")
    media_count: Optional[int] = Field(None, description="Number of media posts")
    biography: Optional[str] = Field(None, description="Instagram bio")
    website: Optional[str] = Field(None, description="Website linked to the Instagram account")
    profile_picture_url: Optional[str] = Field(None, description="Profile picture URL")
    token_expires_at: Optional[str] = Field(None, description="ISO 8601 timestamp when the access token expires")


class InstagramStatusResponse(BaseModel):
    """Response indicating whether a user has an active Instagram Business connection, including live profile data."""
    connected: bool = Field(..., description="True if an active Instagram connection exists")
    instagram_business_id: Optional[str] = Field(None, description="Instagram Business Account ID if connected")
    username: Optional[str] = Field(None, description="Instagram username if connected")
    instagram_name: Optional[str] = Field(None, description="Instagram display name if connected")
    account_type: Optional[str] = Field(None, description="Instagram account type: BUSINESS or CREATOR")
    biography: Optional[str] = Field(None, description="Instagram bio")
    followers_count: Optional[int] = Field(None, description="Number of followers")
    media_count: Optional[int] = Field(None, description="Number of media posts")
    website: Optional[str] = Field(None, description="Website URL linked to the Instagram account")
    profile_picture_url: Optional[str] = Field(None, description="Profile picture URL")
    token_expires_at: Optional[str] = Field(None, description="ISO 8601 token expiry timestamp if connected")
    connected_at: Optional[str] = Field(None, description="ISO 8601 timestamp when the account was connected")


class InstagramDisconnectResponse(BaseModel):
    """Response after disconnecting an Instagram Business account."""
    message: str = Field(..., description="Outcome message")
    disconnected: bool = Field(..., description="True if the connection was successfully deactivated")


class InstagramPostInsights(BaseModel):
    """Insights for a single Instagram post published from an Etown feed."""
    feed_id: str = Field(..., description="Etown feed post ID")
    instagram_media_id: str = Field(..., description="Instagram media ID")
    media_type: str = Field(..., description="Media type: PHOTO, CAROUSEL_ALBUM, or VIDEO")
    published_at: Optional[str] = Field(None, description="ISO 8601 publish timestamp")
    feed_url: Optional[str] = Field(None, description="URL of the Etown feed media stored when the post was created")
    thumbnail_url: Optional[str] = Field(None, description="Thumbnail/poster image URL of the feed (mainly for video posts); null when none stored")
    impressions: Optional[int] = Field(None, description="Total times the post was seen")
    reach: Optional[int] = Field(None, description="Unique accounts that saw the post")
    likes: Optional[int] = Field(None, description="Number of likes")
    comments: Optional[int] = Field(None, description="Number of comments")
    shares: Optional[int] = Field(None, description="Number of shares")
    saved: Optional[int] = Field(None, description="Number of saves")
    total_interactions: Optional[int] = Field(None, description="Total interactions")
    plays: Optional[int] = Field(None, description="Video/Reel play count (videos only)")
    caption_link_clicks: Optional[int] = Field(None, description="Times the tracking URL in the caption was tapped (tracked server-side)")
    error: Optional[str] = Field(None, description="Error message if insights fetch failed")


class InstagramAllPostsResponse(BaseModel):
    """List of all Instagram posts published from Etown feeds for a user."""
    posts: List[InstagramPostInsights] = Field(..., description="List of posts with insights")
    total_posts: int = Field(..., description="Total number of published posts")


class FacebookConnectURLResponse(BaseModel):
    """Response containing the Facebook OAuth URL for Facebook Page connection."""
    oauth_url: str = Field(..., description="Facebook OAuth URL — redirect the user's browser to this URL")


class FacebookCallbackResponse(BaseModel):
    """Response after successfully processing the OAuth callback and saving the connection."""
    message: str = Field(..., description="Human-readable status message")
    fb_page_id: str = Field(..., description="Connected Facebook Page ID")
    fb_page_name: Optional[str] = Field(None, description="Facebook Page name")
    fb_user_id: Optional[str] = Field(None, description="Facebook User ID that owns the Page")
    category: Optional[str] = Field(None, description="Facebook Page category")
    about: Optional[str] = Field(None, description="Facebook Page about/description")
    fan_count: Optional[int] = Field(None, description="Number of people who like the Page")
    followers_count: Optional[int] = Field(None, description="Number of Page followers")
    link: Optional[str] = Field(None, description="Public URL of the Facebook Page")
    profile_picture_url: Optional[str] = Field(None, description="Page profile picture URL")
    token_expires_at: Optional[str] = Field(None, description="ISO 8601 timestamp when the user access token expires")


class FacebookStatusResponse(BaseModel):
    """Response indicating whether a user has an active Facebook Page connection, including live profile data."""
    connected: bool = Field(..., description="True if an active Facebook connection exists")
    fb_page_id: Optional[str] = Field(None, description="Facebook Page ID if connected")
    fb_page_name: Optional[str] = Field(None, description="Facebook Page name if connected")
    fb_user_id: Optional[str] = Field(None, description="Facebook User ID if connected")
    category: Optional[str] = Field(None, description="Facebook Page category")
    about: Optional[str] = Field(None, description="Facebook Page about/description")
    fan_count: Optional[int] = Field(None, description="Number of people who like the Page")
    followers_count: Optional[int] = Field(None, description="Number of Page followers")
    link: Optional[str] = Field(None, description="Public URL of the Facebook Page")
    profile_picture_url: Optional[str] = Field(None, description="Page profile picture URL")
    token_expires_at: Optional[str] = Field(None, description="ISO 8601 token expiry timestamp if connected")
    connected_at: Optional[str] = Field(None, description="ISO 8601 timestamp when the Page was connected")


class SocialStatusResponse(BaseModel):
    """Combined social connection status — Instagram and Facebook in a single response."""
    instagram: InstagramStatusResponse = Field(..., description="Instagram Business account connection status")
    facebook: FacebookStatusResponse = Field(..., description="Facebook Page connection status")


class FacebookDisconnectResponse(BaseModel):
    """Response after disconnecting a Facebook Page."""
    message: str = Field(..., description="Outcome message")
    disconnected: bool = Field(..., description="True if the connection was successfully deactivated")


class FacebookPostInsights(BaseModel):
    """Insights for a single Facebook post published from an Etown feed."""
    feed_id: str = Field(..., description="Etown feed post ID")
    facebook_post_id: str = Field(..., description="Facebook post ID (pageid_postid)")
    media_type: str = Field(..., description="Media type: STATUS, PHOTO, ALBUM, or VIDEO")
    published_at: Optional[str] = Field(None, description="ISO 8601 publish timestamp")
    feed_url: Optional[str] = Field(None, description="URL of the Etown feed media stored when the post was created")
    thumbnail_url: Optional[str] = Field(None, description="Thumbnail/poster image URL of the feed (mainly for video posts); null when none stored")
    impressions: Optional[int] = Field(None, description="Total times the post was seen")
    reach: Optional[int] = Field(None, description="Unique accounts that saw the post")
    reactions: Optional[int] = Field(None, description="Total reactions (likes, loves, etc.)")
    likes: Optional[int] = Field(None, description="Number of likes/reactions")
    comments: Optional[int] = Field(None, description="Number of comments")
    shares: Optional[int] = Field(None, description="Number of shares")
    clicks: Optional[int] = Field(None, description="Number of post clicks")
    video_views: Optional[int] = Field(None, description="Video view count (videos only)")
    caption_link_clicks: Optional[int] = Field(None, description="Times the tracking URL in the post was tapped (tracked server-side)")
    insights_note: Optional[str] = Field(None, description="Note when insights are delayed (e.g. post too new for Facebook to process)")
    error: Optional[str] = Field(None, description="Error message if insights fetch failed")


class FacebookAllPostsResponse(BaseModel):
    """List of all Facebook posts published from Etown feeds for a user."""
    posts: List[FacebookPostInsights] = Field(..., description="List of posts with insights")
    total_posts: int = Field(..., description="Total number of published posts")


class BestTimeToPostItem(BaseModel):
    """A single top-viewed post used to infer the best time to post."""
    rank: int = Field(..., description="1 = highest views, 2 = second highest, …")
    feed_id: str = Field(..., description="Etown feed post ID")
    platform: str = Field(..., description="Platform the post belongs to: 'instagram' or 'facebook'")
    published_at: Optional[str] = Field(None, description="ISO 8601 publish timestamp of the post")
    post_time: Optional[str] = Field(None, description="Publish time-of-day in IST, formatted for display (e.g. '1 AM', '1:30 PM')")
    feed_url: Optional[str] = Field(None, description="URL of the Etown feed media stored when the post was created")
    views: int = Field(0, description="View count for the post (Instagram views/plays or Facebook video views)")


class SocialPostInsightResponse(BaseModel):
    """Combined Instagram + Facebook insights for a single feed post."""
    feed_id: str = Field(..., description="Etown feed post ID")
    instagram: Optional[InstagramPostInsights] = Field(None, description="Instagram insights for this post, or null if not published to Instagram")
    facebook: Optional[FacebookPostInsights] = Field(None, description="Facebook insights for this post, or null if not published to Facebook")
    EngagementRate: float = Field(
        0.0,
        description="Engagement rate as a percentage = (likes + comments + shares) / views * 100, combined across Instagram + Facebook; 0 when views is 0",
    )


class SocialUserPostsInsightResponse(BaseModel):
    """All of a user's posts (by contactid / userprofileid) with combined insights."""
    posts: List[SocialPostInsightResponse] = Field(
        default_factory=list,
        description="One entry per feed post, each with its Instagram + Facebook insights and engagement rate",
    )
    total_posts: int = Field(0, description="Number of feed posts returned")
    besttimetopost: List[BestTimeToPostItem] = Field(
        default_factory=list,
        description="Top posts by views (1st and 2nd maximum across both platforms) with their publish times, to infer the best time to post",
    )


class SaveDeviceTokenRequest(BaseModel):
    """Request body for the save-device-token endpoint (migrated from PowerApps SaveDeviceToken flow)."""
    contact_id: str = Field(..., description="Dynamics contact GUID")
    userprofile_id: str = Field(..., description="Etown user profile GUID")
    devicetoken: Optional[str] = Field(None, description="FCM/APNs push device token; omit or null to leave unchanged")
    cc_user_id: Optional[str] = Field(None, description="Cloud Communications user ID; omit or null to leave unchanged")
    voip_device_token: Optional[str] = Field(None, description="VoIP APNs device token; omit or null to leave unchanged")
    device_id: Optional[str] = Field(
        None,
        description=(
            "Stable per-installation device id (e.g. Android ID / iOS identifierForVendor). "
            "Optional but recommended: when sent, it identifies the device across token "
            "refreshes so the same handset updates its row instead of adding a new one."
        ),
    )


class ContactDeviceRecord(BaseModel):
    """Single device row from etown_contactdevices."""
    contactdevicesid: Optional[str] = Field(None)
    deviceguid: Optional[str] = Field(None)
    ccuserid: Optional[str] = Field(None)
    contactid: Optional[str] = Field(None)
    contactidname: Optional[str] = Field(None)
    userprofileid: Optional[str] = Field(None)
    userprofileidname: Optional[str] = Field(None)
    createdon: Optional[datetime] = Field(None)
    device_token: Optional[str] = Field(None)
    device_token_type: Optional[str] = Field(None)
    token_expires_at: Optional[datetime] = Field(None)
    last_used_at: Optional[datetime] = Field(None)
    voip_device_token: Optional[str] = Field(None)
    device_id: Optional[str] = Field(None)
    updatedon: Optional[datetime] = Field(None)


class SaveDeviceTokenResponse(BaseModel):
    """Response returned by the save-device-token endpoint."""
    data: List[ContactDeviceRecord] = Field(
        ...,
        description="Every device registered to the contact/userprofile pair after the upsert, newest device first",
    )


class CredsUpdateRequest(BaseModel):
    """Request body for POST /devices/credsupdate."""
    action_mode: Optional[str] = Field(
        "frontend",
        description="'frontend' updates one device token of a contact; 'powerapps' updates every etown_contactdevices row",
    )
    credsupdate: Optional[str] = Field(None, description="'true' or 'false'; required in both modes")
    devicetoken: Optional[str] = Field(None, description="Push device token to update; required when action_mode is 'frontend'")
    contactid: Optional[str] = Field(None, description="Contact owning the token; frontend mode needs this or mobilenumber")
    mobilenumber: Optional[str] = Field(None, description="Mobile number of the contact owning the token; matched on its last ten digits")


class CredsUpdateResponse(BaseModel):
    """Response returned by POST /devices/credsupdate."""
    action_mode: str = Field(..., description="Mode the update ran in")
    credsupdate: bool = Field(..., description="Value written to etown_contactdevices.credsupdate")
    updated_count: int = Field(..., description="Rows whose credsupdate was changed")


class AppUninstallRecord(BaseModel):
    """One device FCM has declared unreachable — the app uninstall signal, per device."""
    uninstall_id: Optional[str] = Field(None)
    contactdevicesid: Optional[str] = Field(None)
    contactid: Optional[str] = Field(None)
    userprofileid: Optional[str] = Field(None)
    contactidname: Optional[str] = Field(None)
    device_id: Optional[str] = Field(None)
    app_instance_id: Optional[str] = Field(None, description="FCM app instance; survives token rotation")
    device_token_prefix: Optional[str] = Field(None, description="First 12 chars only; the token is a credential")
    platform: Optional[str] = Field(None, description="android | ios | unknown (derived from device_id)")
    detected_at: Optional[datetime] = Field(None)
    detection_source: Optional[str] = Field(None, description="probe | live_push")
    fcm_status_code: Optional[int] = Field(None)
    last_seen_at: Optional[datetime] = Field(None, description="Device's last activity before it went unreachable")
    reinstalled_at: Optional[datetime] = Field(None, description="Set when the same device registered again")


class AppUninstallListResponse(BaseModel):
    """Response returned by the uninstalls endpoint."""
    count: int = Field(..., description="Number of records in this page")
    data: List[AppUninstallRecord] = Field(..., description="Uninstall records, newest detection first")


class SharedUserProfileId(BaseModel):
    """One share recipient (mirrors a PowerApps shared_userprofileids array item)."""
    userprofileid: str = Field(..., description="Recipient's Etown user profile GUID")


class ShareProviderRequest(BaseModel):
    """Request body for POST /provider/share (migrated from the PowerApps ShareProviders flow)."""
    providerid: str = Field(..., description="Provider (userprofile) GUID being shared")
    shared_userprofileids: List[SharedUserProfileId] = Field(..., description="Recipients to notify")
    sender_contactid: str = Field(..., description="Contact GUID of the user sharing the provider")
    Oauth: str = Field(..., description="Google FCM OAuth bearer token (client-supplied)")


class ShareProviderResponse(BaseModel):
    """Response returned by the share-provider endpoint."""
    success: bool = Field(..., description="Whether the share push was dispatched")
    recipients: int = Field(..., description="Number of recipient user profiles targeted")
    delivered: bool = Field(..., description="True if at least one push was delivered")


class ShareCollectionRequest(BaseModel):
    """Request body for POST /mytown/share_collection (migrated from the PowerApps ShareCollection flow)."""
    collectionid: str = Field(..., description="Collection GUID being shared")
    shared_userprofileids: List[SharedUserProfileId] = Field(..., description="Recipients to notify")
    Oauth: Optional[str] = Field(None, description="Google FCM OAuth bearer token (client-supplied; server mints one if omitted)")


class ShareCollectionResponse(BaseModel):
    """Response returned by the share-collection endpoint."""
    success: bool = Field(..., description="Whether the share push was dispatched")
    recipients: int = Field(..., description="Number of recipient user profiles targeted")
    delivered: bool = Field(..., description="True if at least one push was delivered")


class TownViewCountItem(BaseModel):
    """Single item for a town view log."""
    town_name: Optional[str] = Field(None, description="Name of the town")
    viewer_name: Optional[str] = Field(None, description="Name of the user who viewed the town")
    view_time: Optional[str] = Field(None, description="ISO timestamp of when the view occurred")

class TownViewCountResponse(BaseModel):
    """Response model for fetching town view count tracking logs."""
    total_count: int = Field(..., description="Total number of views across the given filter")
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description="Number of items per page")
    data: List[TownViewCountItem] = Field(default_factory=list, description="List of view records")


class NearMeListRequest(BaseModel):
    """Request model for the Near Me List endpoint.

    Migrated from the PowerApps NearMeListAPI flow. Finds nearby providers
    from the requesting user's social circle using a geographic bounding box.
    """
    inviter_latitude: str = Field(..., description="Requesting user's current latitude as a string")
    inviter_longitude: str = Field(..., description="Requesting user's current longitude as a string")
    inviter_id: str = Field(..., description="Contact ID of the requesting user")
    inviteruserprofile_id: str = Field(..., description="User profile ID of the requesting user")


class NearMeListItem(BaseModel):
    """Single item in the Near Me List response.

    Uses Etown_ prefixed field names to maintain mobile app compatibility
    with the original PowerApps flow response contract.
    """
    Etown_relationshiptypename: Optional[str] = Field(None, description="Human-readable relationship type label")
    Etown_relationshiptype: Optional[int] = Field(None, description="Relationship type code")
    Etown_contactlatitude: Optional[float] = Field(None, description="Contact's latitude")
    Etown_contactlongitude: Optional[float] = Field(None, description="Contact's longitude")
    Etown_connectycubeid: Optional[str] = Field(None, description="ConnectyCube user ID")
    Etown_contactid: Optional[str] = Field(None, description="Contact ID of the circle member")
    Etown_firstname: Optional[str] = Field(None, description="First name of the circle member")
    Etown_mobilenumber: Optional[str] = Field(None, description="Mobile number of the circle member")
    Etown_businessname: Optional[str] = Field(None, description="Provider's business name")
    Etown_providerlatitude: Optional[float] = Field(None, description="Provider's business latitude")
    Etown_providerlongitude: Optional[float] = Field(None, description="Provider's business longitude")
    Etown_providercontact: Optional[str] = Field(None, description="Provider's linked contact ID")
    Etown_providerid: Optional[str] = Field(None, description="Provider ID")
    Etown_rolename: Optional[str] = Field(None, description="Human-readable role label")
    Etown_role: Optional[int] = Field(None, description="Role code (1=Provider, 2=Member)")
    Etown_userprofileid: Optional[str] = Field(None, description="User profile ID of the circle member")
    Etown_imageurl: Optional[str] = Field(None, description="Profile or service image URL")
    Etown_publicsearchprovider: Optional[str] = Field(None, description="Public search visibility flag")
    Etown_contactcreatedon: Optional[str] = Field(None, description="ISO 8601 creation timestamp")
    Etown_address: Optional[str] = Field(None, description="Address of the circle member or business")
    Etown_city: Optional[str] = Field(None, description="City of the circle member or business")
    Etown_statecode: Optional[str] = Field(None, description="State code")
    Etown_memberdistance: Optional[float] = Field(None, description="Haversine distance (km) to the contact's location")
    Etown_providerdistance: Optional[float] = Field(None, description="Haversine distance (km) to the provider's business location")


class PromoteProviderPlanRequest(BaseModel):
    """Request to move a contact's providers onto a subscription plan (POST /plans/promote).

    Identifies the subject by ``contact_id``, not by one of its businesses: the
    subscription belongs to the person, so every live provider the contact owns moves
    together and the contact is the only identifier that names all of them.
    """
    model_config = ConfigDict(extra="forbid")

    contact_id: Optional[str] = Field(
        None,
        description=(
            "contactid whose providers should be promoted. Required unless the legacy "
            "provider_id is sent instead."
        ),
    )
    provider_id: Optional[str] = Field(
        None,
        json_schema_extra={"deprecated": True},
        description=(
            "Legacy alias for contact_id: providerid of any one business the contact "
            "owns, resolved to its contactid server-side. Send contact_id instead."
        ),
    )
    provider_name: Optional[str] = Field(
        None,
        json_schema_extra={"deprecated": True},
        description=(
            "Legacy label, accepted and ignored -- the businessname does not identify "
            "the subject and is not matched against the resolved provider."
        ),
    )
    plan_code: str = Field(..., description="Target plan_code; must exist in etown_subscription_plan")
    status: str = Field(
        "active",
        description="Subscription status to set: active, cancelled, expired, or suspended",
    )
    start_date: Optional[date] = Field(
        None,
        description="Plan start date; defaults to today when moving onto a paid plan with none currently held",
    )
    end_date: Optional[date] = Field(
        None, description="Plan expiry date; omit or null for a plan that never lapses"
    )
    changed_by: Optional[str] = Field(None, description="Operator identifier recorded on the change")
    reason: Optional[str] = Field(None, description="Free-text reason recorded on the change")
    route: Optional[str] = Field(
        None, description="App route the promotion push opens on tap; defaults to '/'"
    )

    @field_validator("contact_id", "provider_id", "route")
    @classmethod
    def validate_identifier(cls, v: Optional[str]) -> Optional[str]:
        """Return the identifier stripped, or None when blank."""
        return v.strip() if v and v.strip() else None

    @model_validator(mode="after")
    def require_an_identifier(self) -> "PromoteProviderPlanRequest":
        """Require contact_id or its legacy alias provider_id."""
        if not self.contact_id and not self.provider_id:
            raise ValueError("contact_id is required (or provider_id, its legacy alias)")
        return self

    @field_validator("plan_code")
    @classmethod
    def validate_plan_code(cls, v: str) -> str:
        """Reject a blank plan_code and return it stripped and lower-cased."""
        if not v or not v.strip():
            raise ValueError("plan_code cannot be blank")
        return v.strip().lower()

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Return status lower-cased, defaulting to active, and reject values outside the four plan statuses."""
        mode = (v or "active").strip().lower()
        if mode not in ("active", "cancelled", "expired", "suspended"):
            raise ValueError("status must be one of active, cancelled, expired, suspended")
        return mode


class DisableProviderRequest(BaseModel):
    """Body for POST /plans/disable -- switch off one business.

    Both ids are required and both are matched, because either one alone names the wrong
    thing. A contact can own several businesses, so contactid alone would switch off every
    one of them; userprofileid identifies a single provider row but says nothing about who
    owns it, so on its own it would let a caller holding one profile id disable a business
    belonging to a contact it never named. Requiring the pair means the request only lands
    when the caller has both halves and they agree, and a mismatched pair matches no row and
    404s rather than quietly disabling something adjacent.

    Extra fields are rejected rather than silently dropped, so a typo'd field name (e.g.
    ``userProfileId``) surfaces as a 422 instead of leaving the request looking successful
    while naming nothing.
    """
    model_config = ConfigDict(extra="forbid")

    contactid: str = Field(..., description="contactid that owns the provider being disabled")
    userprofileid: str = Field(..., description="userprofileid of the provider row to disable")

    @field_validator("contactid", "userprofileid")
    @classmethod
    def not_blank(cls, v: str, info) -> str:
        """Reject a blank value and return it stripped."""
        v = (v or "").strip()
        if not v:
            raise ValueError(f"{info.field_name} cannot be blank")
        return v


class DismissPlanAlertRequest(BaseModel):
    """Body for POST /plans/alert/dismiss -- close the day's plan-expiry notice.

    ``contactid`` alone, and deliberately so: unlike /plans/disable, which needs a
    userprofileid because it switches off one business, the subscription and its expiry date
    belong to the contact. Every business the contact owns shares that date, so a notice
    dismissed on one profile is the same notice on the others, and taking a userprofileid here
    would leave the owner closing it once per business.

    Extra fields are rejected rather than silently dropped, so a typo'd field name (e.g.
    ``contactId``) surfaces as a 422 instead of leaving the request looking successful while
    naming nothing.
    """
    model_config = ConfigDict(extra="forbid")

    contactid: str = Field(
        ..., description="contactid whose plan-expiry alert is dismissed for the day")

    @field_validator("contactid")
    @classmethod
    def not_blank(cls, v: str, info) -> str:
        """Reject a blank value and return it stripped."""
        v = (v or "").strip()
        if not v:
            raise ValueError(f"{info.field_name} cannot be blank")
        return v


class UpdateSubscriptionPlanRequest(BaseModel):
    """Update-or-create body for one row of ``public.etown_subscription_plan`` (PATCH
    /plans/catalogue/{plan_code}).

    ``plan_code`` itself is the path parameter, not a body field, so a row can never be
    renamed onto a different key by accident. When ``plan_code`` names an existing plan,
    only fields present in the request body are written -- an omitted field keeps its
    stored value, while a field sent explicitly as ``null`` clears it (e.g.
    ``max_listings: null`` makes that cap unlimited, matching the table's own
    NULL-means-unlimited convention). When ``plan_code`` does not exist yet, the row is
    created instead: ``plan_name`` is then required (the one column with no default),
    and every other omitted field takes the table's own default rather than "unchanged".
    Extra fields are rejected rather than silently dropped, so a typo'd column name
    surfaces as a 422 instead of quietly doing nothing -- the allowlist check inside
    update_subscription_plan() only ever sees an unknown key from a non-HTTP caller.
    """
    model_config = ConfigDict(extra="forbid")

    plan_name: Optional[str] = Field(
        None,
        description="Display name of the plan; required when plan_code doesn't exist yet",
    )
    badge_label: Optional[str] = Field(None, description="Badge text shown for a verified provider on this plan")
    display_order: Optional[int] = Field(None, description="Sort rank among plans, used when the best held plan must be picked")
    is_active: Optional[bool] = Field(None, description="Whether the plan can still be assigned or offered")
    max_gallery_photos: Optional[int] = Field(None, ge=0, description="Per-post gallery photo cap; null = unlimited")
    max_listings: Optional[int] = Field(None, ge=0, description="Live listing cap; null = unlimited")
    max_towns: Optional[int] = Field(None, ge=0, description="Distinct town-coverage cap; null = unlimited")
    max_active_offers: Optional[int] = Field(None, ge=0, description="Live active-offer cap; null = unlimited")
    max_offers_per_month: Optional[int] = Field(None, ge=0, description="Monthly offer-creation cap; null = unlimited")
    max_chaatie_generations_month: Optional[int] = Field(None, ge=0, description="Monthly Chaatie-generation cap; null = unlimited")
    max_posts_per_month: Optional[int] = Field(None, ge=0, description="Monthly feed-post cap; null = unlimited")
    whatsapp_quota_month: Optional[int] = Field(None, ge=0, description="Monthly WhatsApp send cap; 0 disables the feature, null = unlimited")
    verified_badge: Optional[bool] = Field(None, description="Whether this plan grants the verified badge")
    full_business_profile: Optional[bool] = Field(None, description="Whether this plan unlocks the full business profile")
    post_audience_targeting: Optional[bool] = Field(None, description="Whether this plan can target post audiences")
    booking_insights: Optional[bool] = Field(None, description="Whether this plan includes booking insights")
    profile_view_insights: Optional[str] = Field(None, description="One of none, weekly, monthly, weekly_monthly, daily")
    support_channel: Optional[str] = Field(None, description="Support channel offered, e.g. email or priority")
    support_response_hours: Optional[int] = Field(None, ge=0, description="Support SLA in hours; null = no SLA")
    price_amount: Optional[float] = Field(None, ge=0, description="Plan price")
    price_currency: Optional[str] = Field(None, description="ISO currency code for price_amount")
    billing_period_months: Optional[int] = Field(None, ge=0, description="Billing cycle length in months")
    provider_onboard: Optional[int] = Field(None, ge=0, description="How many providers one contact may onboard on this plan; null = unlimited")
    chattie_conversation: Optional[int] = Field(None, ge=0, description="Daily Chaatie-message cap; null = unlimited")
    post_public_feed: Optional[bool] = Field(None, description="Whether posts on this plan reach the public feed")
    changed_by: Optional[str] = Field(None, description="Operator identifier recorded in the log line for this change; not written to the table")

    @field_validator("profile_view_insights")
    @classmethod
    def validate_profile_view_insights(cls, v: Optional[str]) -> Optional[str]:
        """Allow None and reject values outside the supported profile view insight cadences."""
        if v is None:
            return v
        allowed = {"none", "weekly", "monthly", "weekly_monthly", "daily"}
        if v not in allowed:
            raise ValueError(f"profile_view_insights must be one of {sorted(allowed)}")
        return v

    @field_validator("plan_name")
    @classmethod
    def validate_plan_name(cls, v: Optional[str]) -> Optional[str]:
        """Allow None but reject a blank plan_name."""
        if v is not None and not v.strip():
            raise ValueError("plan_name cannot be blank")
        return v


class PlanPaymentRequest(BaseModel):
    """Body for POST /plans/payment_request -- a caller-submitted plan payment record.

    Only ``mobilenumber`` is required; the client may hold nothing else but the payment
    screenshot at the moment it submits. Blank strings on the optional fields are normalised
    to None so an empty form field reads the same as an omitted one downstream. Extra fields
    are rejected rather than silently dropped, so a typo'd field name surfaces as a 422
    instead of quietly doing nothing.

    There is deliberately no ``contactid`` field. The server resolves the payer's contact
    from ``mobilenumber`` with the same verified-first ownership lookup the OTP path uses,
    which is a better answer than the client's: a number can carry several contacts, and
    the one that owns it is not necessarily the id the app happens to be holding. A client
    that still sends contactid gets a 422 from ``extra="forbid"`` rather than having it
    quietly ignored.

    That is what makes ``mobilenumber`` required, and validated rather than merely present:
    it is the only identifier on the submission and the sole input the payer's contact is
    resolved from, so a blank or landline number would produce a payment nobody can match to
    an account and nobody can ring back. The rule is the one NotifyUnverifiedProviderRequest
    already applies -- 10 digits starting 6-9, an optional +91 -- because a number that
    cannot be an Indian mobile cannot own an Etown account either.

    ``screenshot`` is base64 image data, which the endpoint uploads to Azure Blob before
    emailing support the link. There is no length cap on it here: a payment screenshot from
    a modern phone is routinely several megabytes of base64, and a limit tight enough to be
    worth stating would reject exactly the submissions this endpoint exists to carry.

    ``paymentid`` is not the ``reference`` the endpoint returns. The reference is Etown's
    own handle on the submission; paymentid is the payer's handle on the payment at their
    provider, which is what support quotes when reconciling against a bank statement. It
    stays optional because a payer who only has the screenshot must still be able to submit.

    There is no ``name`` field either, for the same reason there is no contactid: the payer's
    name is read off the contact the number resolves to (``etown_contact.firstname``, returned
    as ``contactidname``), which is the name support will find when they look the payer up,
    rather than whatever the client had in a form field. A submission whose name and account disagree is one support cannot
    reconcile, so only one of them is worth putting in the email.
    """
    model_config = ConfigDict(extra="forbid")

    mobilenumber: str = Field(
        ...,
        description=(
            "Payer's Indian mobile number, with or without +91. Required: it is the only "
            "identifier on the submission and what the payer's contactid is resolved from."
        ),
    )
    screenshot: Optional[str] = Field(
        None,
        description=(
            "Payment screenshot as base64, plain or data-URL prefixed; uploaded to blob "
            "storage and linked in the email. An already-stored blob URL is passed through."
        ),
    )
    paymentid: Optional[str] = Field(
        None,
        description=(
            "The payment's own reference from whichever app the payer used -- a UPI "
            "transaction id, an order id, a bank reference. Optional and unvalidated: it "
            "comes from outside Etown in whatever shape that provider issues it, so it is "
            "carried to support as submitted rather than pattern-matched."
        ),
    )

    @field_validator("screenshot", "paymentid")
    @classmethod
    def blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        """Return the value stripped, or None when blank."""
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("mobilenumber")
    @classmethod
    def validate_mobilenumber(cls, v: str) -> str:
        """Reject a mobile number that is not a valid Indian mobile and return it stripped."""
        v = (v or "").strip()
        if not is_valid_indian_mobile(v):
            raise ValueError(
                "mobilenumber must be a valid Indian mobile number "
                "(10 digits starting 6-9, optional +91), not a landline"
            )
        return v


class SupportContactRequest(BaseModel):
    """Body for POST /support/contact -- one contact-support request from any tier.

    Support is not a paid feature: a caller on the free tier may file a ticket exactly like
    a Platinum one, and the tier only decides the SLA reported back. Identity is not derived
    from the bearer token (there is one shared token), so the caller states who they are with
    contact_id and/or userprofile_id; at least one is required, because a ticket nobody can
    be matched back to is one support cannot answer or the requester ever list again.
    userprofile_id is what the tier is resolved from -- omitting it on a provider's ticket
    silently downgrades them to the free tier's terms.
    """
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(..., min_length=1, max_length=200, description="One-line summary of the request")
    message: str = Field(..., min_length=1, max_length=5000, description="The request in the user's own words")
    contact_id: Optional[str] = Field(None, description="Requester's contactid; required unless userprofile_id is given")
    userprofile_id: Optional[str] = Field(None, description="Requester's userprofileid; the role and the tier are resolved from this")
    category: str = Field("general", description="One of general, account, booking, payment, subscription, provider, technical, feedback, other")

    @field_validator("subject", "message")
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        """Reject a blank value and return it stripped."""
        if not (v or "").strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        """Return category lower-cased, defaulting to general, and reject unsupported categories."""
        allowed = {
            "general", "account", "booking", "payment", "subscription",
            "provider", "technical", "feedback", "other",
        }
        mode = (v or "general").strip().lower()
        if mode not in allowed:
            raise ValueError(f"category must be one of {sorted(allowed)}")
        return mode

    @model_validator(mode="after")
    def validate_identity(self) -> "SupportContactRequest":
        """Require contact_id or userprofile_id."""
        if not (self.contact_id or self.userprofile_id):
            raise ValueError("contact_id or userprofile_id is required")
        return self


class SupportTicketItem(BaseModel):
    """One row of a caller's support history, as returned by GET /support/my_tickets.

    plan_code, support_channel and response_hours are the tier terms captured when the
    ticket was filed, not the caller's tier now, so an old ticket keeps reporting the SLA it
    was actually promised. due_at is null whenever response_hours is -- the free tier gets
    email support with no deadline attached.
    """
    ticketid: str = Field(..., description="Ticket's primary key")
    reference: str = Field(..., description="Human-quotable reference, e.g. ETS-3F9A2C71")
    category: str = Field(..., description="Ticket category")
    subject: str = Field(..., description="One-line summary as submitted")
    message: str = Field(..., description="Full request text as submitted")
    status: str = Field(..., description="open, in_progress, waiting_on_user, resolved or closed")
    plan_code: Optional[str] = Field(None, description="Tier in force when the ticket was filed")
    support_channel: Optional[str] = Field(None, description="Support channel that tier grants")
    response_hours: Optional[int] = Field(None, description="Promised SLA in hours; null = no SLA on that tier")
    due_at: Optional[datetime] = Field(None, description="When the SLA expires; null when there is no SLA")
    source: str = Field(..., description="api for the endpoint, chaatie for the agent tool")
    email_sent: bool = Field(..., description="Whether the routing email to support actually left")
    resolved_at: Optional[datetime] = Field(None, description="When support closed the ticket")
    created_at: Optional[datetime] = Field(None, description="When the ticket was filed")
    updated_at: Optional[datetime] = Field(None, description="Last change to the ticket")


class SupportContactResponse(BaseModel):
    """Acknowledgement for POST /support/contact.

    Returned as soon as the ticket row is committed; the two emails are dispatched in the
    background, so a 200 here means "recorded", not "already in the support inbox".
    ``support_email`` is echoed so the client can offer a direct mailto: fallback with the
    reference already in hand.
    """
    success: bool = Field(True, description="True when the ticket was recorded")
    reference: str = Field(..., description="Quote this in any follow-up")
    ticketid: str = Field(..., description="Ticket's primary key")
    status: str = Field(..., description="Always open for a newly filed ticket")
    plan_code: Optional[str] = Field(None, description="Tier the SLA was taken from")
    support_channel: Optional[str] = Field(None, description="Support channel that tier grants")
    response_hours: Optional[int] = Field(None, description="Promised SLA in hours; null = no SLA on this tier")
    due_at: Optional[datetime] = Field(None, description="When the SLA expires; null when there is no SLA")
    support_email: str = Field(..., description="The inbox this request was routed to")
    message: str = Field(..., description="Human-readable confirmation for display")


class SupportTicketListResponse(BaseModel):
    """Page of a caller's own tickets for GET /support/my_tickets, newest first."""
    success: bool = Field(True, description="True when the read succeeded")
    count: int = Field(..., description="Tickets in this page, not the total on file")
    support_email: str = Field(..., description="The inbox support requests are routed to")
    tickets: List[SupportTicketItem] = Field(default_factory=list, description="The page of tickets")


class ProviderBusinessItem(BaseModel):
    """One live business registered against the requested mobile number."""
    businessname: Optional[str] = Field(None, description="Business name on the provider row")
    serviceimageurl: Optional[str] = Field(None, description="Provider's service/business image URL")
    disabled: Optional[bool] = Field(None, description="Operator switch set by POST /plans/disable; distinct from the owner's deactivation")


class ProviderBusinessListResponse(BaseModel):
    """Response for GET /users/business_list — the number's live businesses."""
    success: bool = Field(True, description="True when the lookup succeeded")
    count: int = Field(..., description="Number of businesses returned")
    businesses: List[ProviderBusinessItem] = Field(default_factory=list, description="Live businesses on the number, business name ascending")


class AlternateMobileCheckRequest(BaseModel):
    """Request body for POST /users/check_alternate_mobile."""
    mobilenumber: str = Field(..., min_length=1, description="The account's original (primary) mobile number")
    alternatemobile: str = Field(..., min_length=1, description="The alternate mobile number to check")


class AlternateMobileCheckResponse(BaseModel):
    """Whether an alternate mobile number may be attached to the original number's account."""
    success: bool = Field(True, description="True when the check ran")
    status: Literal["no_record", "separate_account", "linked_to_another", "same_as_original"] = Field(
        ...,
        description=(
            "no_record: no conflicting record - nobody holds the number, or it is already "
            "the alternate of this same number. "
            "separate_account: the number is some account's primary mobilephone. "
            "linked_to_another: the number is the alternate of a different primary number. "
            "same_as_original: both numbers are the same."
        ),
    )
    message: str = Field(..., description="Human-readable outcome to show the user")

StaffRole = Literal["staff", "csr", "fos"]


class StaffMemberUpsertRequest(BaseModel):
    """Body for POST /users/staff_member - create, edit or find etown_staff_numbers rows.

    action_mode 'create' inserts a new row; 'edit' (the default) changes an existing one,
    found by mobile number; 'find' returns the number's rows without writing. Every other
    field is optional: on edit only what is sent is changed, on find what is sent filters.
    ``name`` is not accepted - it is always the firstname of the contact holding this number.
    """
    model_config = ConfigDict(extra="forbid")

    mobilenumber: str = Field(..., min_length=1, description="Staff member's mobile number, any format; stored as its 10-digit national form")
    action_mode: Literal["create", "edit", "find"] = Field(
        "edit",
        description=(
            "create: insert a new row (role required). edit: change the number's existing row. "
            "find: return the number's rows, filtered by role / is_active when sent"
        ),
    )
    role: Optional[StaffRole] = Field(
        None,
        description=(
            "create: the new row's role (required). edit: picks the number's row with this "
            "role; when the number has only one row, that row is edited and its role set to this. "
            "find: only rows with this role."
        ),
    )
    is_active: Optional[bool] = Field(None, description="Active flag; a new row defaults to true. find: only rows with this flag")

    @field_validator("mobilenumber")
    @classmethod
    def validate_mobilenumber(cls, v: str) -> str:
        """Normalize the mobile number to 10 national digits, rejecting invalid numbers."""
        national = normalize_indian_mobile(v)
        if not national:
            raise ValueError("must be a valid 10-digit mobile number")
        return national

    @field_validator("action_mode", "role", mode="before")
    @classmethod
    def lowercase(cls, v: Optional[str]) -> Optional[str]:
        """Return a string value stripped and lower-cased, or None when blank; pass other values through."""
        if isinstance(v, str):
            return v.strip().lower() or None
        return v

    @model_validator(mode="after")
    def validate_create_has_role(self) -> "StaffMemberUpsertRequest":
        """Require a role when action_mode is create."""
        if self.action_mode == "create" and not self.role:
            raise ValueError("role is required when action_mode is create")
        return self


class StaffMemberItem(BaseModel):
    """One etown_staff_numbers row as written."""
    id: str = Field(..., description="Row id (uuid)")
    mobilenumber: str = Field(..., description="10-digit mobile number")
    role: StaffRole = Field(..., description="Roster role")
    name: Optional[str] = Field(None, description="Firstname of the contact holding this number")
    is_active: bool = Field(..., description="Whether the role is currently granted")
    createdon: datetime = Field(..., description="When the row was created")
    modifiedon: datetime = Field(..., description="When the row was last written")


class StaffMemberUpsertResponse(BaseModel):
    """Response for POST /users/staff_member."""
    success: bool = Field(True, description="True when the request succeeded")
    action: Literal["created", "updated", "found"] = Field(..., description="What was done: a row inserted, a row edited, or rows read")
    message: str = Field(..., description="Human-readable outcome")
    count: int = Field(..., description="Number of rows in staff")
    staff: List[StaffMemberItem] = Field(
        default_factory=list,
        description="create/edit: the one row after the write. find: the matching rows, oldest first (empty when none)",
    )


class NeedAttachmentInput(BaseModel):
    """One image or video being attached to a post, as sent in the POST /mytown/community JSON body.

    ``data`` takes the same encoding every other media field on this API accepts: a plain
    base64 string or a full ``data:<mime>;base64,...`` URL. The type is read from the data URL
    or, failing that, from the file's own bytes, so no filename is needed; a ``filename`` key
    sent by an older client is ignored.
    """
    model_config = ConfigDict(extra="ignore")

    data: str = Field(..., min_length=1, description="Base64 or data-URL encoded image or video")
    thumbnail: Optional[str] = Field(
        None,
        description="Optional preview image for this file, typically a video's poster frame: "
                    "base64 or data-URL JPEG, PNG or WebP, with no size limit",
    )


class NeedTownInput(BaseModel):
    """One town a POST /mytown/community post is made in; each becomes its own stored post."""
    model_config = ConfigDict(extra="forbid")

    townname: str = Field("", description="Town the post is made in; required")
    townprofileid: str = Field("", description="etown_townprofile id of that town; required")

    @field_validator("townname", "townprofileid")
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        """Reject a blank required value and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()


class PostNeedRequest(BaseModel):
    """Body for POST /mytown/community -- one requirement posted against one or more towns, or saved as a draft.

    ``town`` lists every town to post in; one record is stored per town, with every other
    field and the attachments shared. A town repeated by townprofileid is stored once.

    Coordinates and the wanted-by date accept either name: ``lat``/``latitude``,
    ``long``/``longitude`` and ``needed_at``/``time``, so a client written against the
    response shape and one written against the original form fields both work.

    Every field defaults to an empty string, lat/long to 0 and status to posted, so the body
    can be sent with any field left blank. Defaults are validated too: a blank required field
    is a 422, a blank optional one is stored as null, and blank post_type is read as need.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_default=True)

    town: List[NeedTownInput] = Field(
        default_factory=list,
        description="Towns to post in, each {townname, townprofileid}; at least one is required "
                    "and one record is stored per town",
    )
    userprofileid: str = Field("", description="Poster's userprofileid; required")
    contactid: str = Field("", description="Poster's contactid; required")
    title: str = Field("", max_length=200, description="One-line title; required")
    description: str = Field("", max_length=300, description="Post details; required")
    lat: float = Field(
        0, ge=-90.0, le=90.0,
        validation_alias=AliasChoices("lat", "latitude"),
        description="Latitude of the post's location",
    )
    long: float = Field(
        0, ge=-180.0, le=180.0,
        validation_alias=AliasChoices("long", "longitude"),
        description="Longitude of the post's location",
    )
    needed_at: Optional[str] = Field(
        "",
        validation_alias=AliasChoices("needed_at", "time"),
        description="Optional. When it is needed: ISO date or datetime; blank is stored as null. A "
                    "value with no offset is read as IST, which is the timezone the app's users "
                    "pick times in. The older name time is still accepted",
    )
    category: Optional[str] = Field("", max_length=100, description="Optional service category")
    budget: Optional[str] = Field(
        "", max_length=100,
        description="Needs only; optional budget, free text, e.g. '5000' or '2000-3000'",
    )
    post_type: str = Field(
        "",
        description="need, update or event; picks the table the post is stored in and the id it "
                    "gets. Blank is read as need",
    )
    start_date: Optional[str] = Field("", description="Events only; optional start date, YYYY-MM-DD")
    start_time: Optional[str] = Field("", description="Events only; optional start time, HH:MM (24-hour)")
    end_date: Optional[str] = Field(
        "", description="Events only; optional end date, YYYY-MM-DD; not before start_date"
    )
    end_time: Optional[str] = Field("", description="Events only; optional end time, HH:MM (24-hour)")
    location_name: Optional[str] = Field(
        "", max_length=200, description="Optional place name for lat/long, e.g. 'Pakyong'"
    )
    venue: Optional[str] = Field(
        "", max_length=200, description="Events only; optional venue, e.g. 'Pakyong Community Hall'"
    )
    organizer_name: Optional[str] = Field("", max_length=100, description="Events only; optional organizer name")
    organizer_number: Optional[str] = Field(
        "", max_length=20, pattern=r"^\+?[0-9 ()-]{6,20}$",
        description="Events only; optional organizer phone number",
    )
    entry_info: Optional[str] = Field(
        "", max_length=300, description="Events only; optional entry details, e.g. 'Free entry'"
    )
    status: str = Field(
        "posted",
        description="posted to publish and notify providers, draft to save quietly",
    )
    attachments: List[NeedAttachmentInput] = Field(
        default_factory=list,
        description="Up to 5 images (JPEG, PNG, WebP or GIF, no size limit) or "
                    "videos (MP4, MOV, WebM or 3GP, 15MB each), each with an optional thumbnail. The "
                    "count is enforced in the handler, not here, so exceeding it returns "
                    "the same 400 as an oversized or unsupported file rather than a 422",
    )

    @field_validator("userprofileid", "contactid", "title", "description")
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        """Reject a blank required value and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()

    @field_validator("town")
    @classmethod
    def validate_towns(cls, v: List[NeedTownInput]) -> List[NeedTownInput]:
        """Require at least one town and return the list with repeated townprofileids dropped."""
        if not v:
            raise ValueError("at least one town is required")
        unique: Dict[str, NeedTownInput] = {}
        for t in v:
            unique.setdefault(t.townprofileid, t)
        return list(unique.values())

    @field_validator(
        "category", "budget", "start_date", "start_time", "end_date", "end_time",
        "location_name", "venue", "organizer_name", "organizer_number", "entry_info",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, v: Any) -> Any:
        """Return an optional text value stripped, or None when it is blank."""
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("post_type")
    @classmethod
    def normalise_post_type(cls, v: str) -> str:
        """Return post_type lower-cased, reading blank as need, and reject anything but need, update or event."""
        kind = (v or "").strip().lower() or "need"
        if kind not in ("need", "update", "event"):
            raise ValueError("post_type must be one of ['need', 'update', 'event']")
        return kind

    @field_validator("needed_at")
    @classmethod
    def parse_needed_at(cls, v: Optional[str]) -> Optional[datetime]:
        """Parse an ISO date or datetime, attaching IST when it has no offset, and return None when blank."""
        text = (v or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError("must be an ISO date or datetime, e.g. 2025-07-12 or 2025-07-12T10:00")
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed

    @field_validator("start_date", "end_date")
    @classmethod
    def parse_date(cls, v: Optional[str]) -> Optional[date]:
        """Parse a YYYY-MM-DD string into a date, passing None through."""
        if v is None:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise ValueError("must be a date in YYYY-MM-DD form")

    @field_validator("start_time", "end_time")
    @classmethod
    def parse_time(cls, v: Optional[str]) -> Optional[clock_time]:
        """Parse an HH:MM or HH:MM:SS string into a time, passing None through."""
        if v is None:
            return None
        try:
            return clock_time.fromisoformat(v)
        except ValueError:
            raise ValueError("must be a time in HH:MM (24-hour) form")

    @model_validator(mode="after")
    def validate_schedule(self) -> "PostNeedRequest":
        """Reject fields sent for the wrong post_type, an end date before the start date, or an end time before the start time on one day."""
        type_only = {
            "need": ("budget",),
            "event": (
                "start_date", "start_time", "end_date", "end_time",
                "venue", "organizer_name", "organizer_number", "entry_info",
            ),
        }
        for owner, fields in type_only.items():
            sent = [f for f in fields if getattr(self, f) is not None]
            if sent and self.post_type != owner:
                raise ValueError(f"only allowed when post_type is {owner}: {', '.join(sent)}")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        same_day = (self.end_date or self.start_date) == self.start_date
        if same_day and self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time must not be before start_time on the same day")
        return self

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Return status lower-cased, defaulting to posted, and reject anything but draft, posted or closed."""
        mode = (v or "posted").strip().lower()
        if mode not in ("draft", "posted", "closed"):
            raise ValueError("status must be one of ['draft', 'posted', 'closed']")
        return mode


class NeedAttachment(BaseModel):
    """One file attached to a need, as stored on the row and returned by the feed.

    ``path`` is the relative blob path that was written ('/container/blob'); ``url`` is that
    path resolved against the storage account. Storing the relative form and building the
    URL at response time is what lets the storage account change without rewriting every
    historic row.
    """
    path: str = Field(..., description="Relative blob path, e.g. /etown/<upid>/needs/<file>")
    filename: Optional[str] = Field(None, description="Original filename; only on attachments stored before 07-Oct-2026")
    content_type: str = Field(..., description="MIME type the file was stored with")
    size_bytes: int = Field(..., description="File size in bytes")
    url: Optional[str] = Field(None, description="Full public URL, resolved from path")
    thumbnail_path: Optional[str] = Field(None, description="Relative blob path of the thumbnail, when one was sent")
    thumbnail_url: Optional[str] = Field(None, description="Full public thumbnail URL, resolved from thumbnail_path")


class NeedTown(BaseModel):
    """One town a member's own post was made in, with that town's copy of the post."""
    townname: Optional[str] = Field(None, description="Town name")
    townprofileid: Optional[str] = Field(None, description="etown_townprofile id of the town")
    postid: str = Field(..., description="needid, updateid or eventid of the post's copy in this town")


class NeedItem(BaseModel):
    """One community post - a need, update or event - as returned by POST and GET /mytown/community.

    Each post type lives in its own table keyed by its own id, so exactly one of ``needid``,
    ``updateid`` and ``eventid`` is set, matching ``post_type``. ``budget`` exists only on
    needs, and the schedule, venue, organizer and entry fields only on events; each is null
    on the other types.

    ``poster_name`` and ``poster_image`` are joined at read time by the poster's userprofile
    role: a provider (role 1) shows the provider's businessname and serviceimageurl, a member
    (role 2) the contact's firstname and profileimage. Nothing is snapshotted, so a rename is
    never stale on old posts. All three poster fields are null on the POST response - the
    endpoint returns what it wrote, and the poster is the caller.
    """
    post_type: str = Field(..., description="need, update or event")
    needid: Optional[str] = Field(None, description="etown_need primary key; set only on needs")
    updateid: Optional[str] = Field(None, description="etown_update primary key; set only on updates")
    eventid: Optional[str] = Field(None, description="etown_event primary key; set only on events")
    townname: str = Field(..., description="Town the need was posted in")
    townprofileid: str = Field(..., description="etown_townprofile id the feed reads by")
    userprofileid: str = Field(..., description="Poster's userprofileid")
    contactid: str = Field(..., description="Poster's contactid")
    title: str = Field(..., description="What the member needs, one line")
    description: str = Field(..., description="The requirement in the member's own words")
    category: Optional[str] = Field(None, description="Optional service category")
    budget: Optional[str] = Field(None, description="Budget, free text; needs only")
    need_status: Optional[str] = Field(
        None, description="open or closed; needs only, open when posted; null on updates and events"
    )
    response_count: Optional[int] = Field(
        None,
        description="Unique people who responded to the need; set only on GET /community with "
                    "is_sameuser=true, null otherwise and on updates and events",
    )
    start_date: Optional[date] = Field(None, description="Start date; events only")
    start_time: Optional[clock_time] = Field(None, description="Start time; events only")
    end_date: Optional[date] = Field(None, description="End date; events only")
    end_time: Optional[clock_time] = Field(None, description="End time; events only")
    location_name: Optional[str] = Field(None, description="Place name for latitude/longitude")
    venue: Optional[str] = Field(None, description="Venue; events only")
    organizer_name: Optional[str] = Field(None, description="Organizer name; events only")
    organizer_number: Optional[str] = Field(None, description="Organizer phone number; events only")
    entry_info: Optional[str] = Field(None, description="Entry details; events only")
    event_status: Optional[str] = Field(
        None,
        description="Events only: pending or rejected while the review is not approved; once approved, "
                    "upcoming, live or completed in IST, null when the event has no dates; upcoming until start_date at start_time, live until end_date (or start_date) "
                    "at end_time or the end of end_date, else EVENT_OPEN_ENDED_HOURS (default 48) after the start",
    )
    latitude: float = Field(..., description="Need's latitude")
    longitude: float = Field(..., description="Need's longitude")
    needed_at: Optional[datetime] = Field(None, description="When the member needs it, in IST (+05:30); null when not given")
    attachments: List[NeedAttachment] = Field(default_factory=list, description="Up to 5 attached files")
    status: str = Field(..., description="draft, posted or closed")
    created_at: datetime = Field(..., description="When the need was posted, in IST (+05:30)")
    poster_role: Optional[int] = Field(None, description="Poster's userprofile role: 1 provider, 2 member; reads only")
    poster_name: Optional[str] = Field(
        None, description="Provider businessname for role 1, contact firstname for role 2; reads only"
    )
    poster_image: Optional[str] = Field(
        None, description="Provider serviceimageurl for role 1, contact profileimage for role 2; reads only"
    )
    like_count: int = Field(0, description="Current likes on the post; reads only")
    comment_count: int = Field(0, description="Live comments on the post; reads only")
    view_count: int = Field(
        0, description="Views on the post from POST /community/view: every onclick, and public once per viewer per IST day; reads only"
    )
    is_liked: bool = Field(False, description="True when the userprofileid passed to the read has liked it")
    mycircle: bool = Field(
        False,
        description="True when the poster is in the MyCircle of the userprofileid passed to the read, "
                    "via a member or provider relation in either direction",
    )
    invitationstatus: Optional[str] = Field(
        None,
        description="Connected when mycircle is true, Requested when a pending invitation links the viewer "
                    "and the poster either way, otherwise null",
    )
    update_status: Optional[str] = Field(
        None,
        description="Updates only: pending, approved or rejected, from etown_update.review set in "
                    "Dataverse; null for needs and events. The town feed only ever returns approved ones",
    )
    match_score: Optional[float] = Field(
        None, description="Search relevance, higher is better; set only by GET /community/search"
    )
    towns: Optional[List[NeedTown]] = Field(
        None,
        description="Every town the post was made in, with each town's postid, when the reading userprofileid "
                    "posted it (is_sameuser true or false, /community/post) and on publish and edit; only this "
                    "item's own town on other members' posts",
    )


class PostNeedResponse(BaseModel):
    """Acknowledgement for POST /mytown/community.

    A 200 means every town's post is committed, not that any provider has been reached: the
    fan-out runs as a background task after the response, so no notified count is returned
    here - it is reported in the logs instead. The id fields and ``need`` describe the first
    town's post; ``posts`` holds one stored post per town, in request order.
    """
    success: bool = Field(True, description="True when the posts were recorded")
    post_type: str = Field(..., description="need, update or event")
    needid: Optional[str] = Field(None, description="First town's new need id; set only for needs")
    updateid: Optional[str] = Field(None, description="First town's new update id; set only for updates")
    eventid: Optional[str] = Field(None, description="First town's new event id; set only for events")
    status: str = Field(..., description="draft or posted, as submitted")
    need: NeedItem = Field(..., description="The first town's stored post")
    posts: List[NeedItem] = Field(default_factory=list, description="Every stored post, one per town, in request order")
    message: str = Field(..., description="Human-readable confirmation for display")


class NeedListResponse(BaseModel):
    """One page of needs for GET /mytown/community, newest first.

    Paged the way the rest of the API pages: a 1-based ``page`` plus ``limit``, never an
    offset. ``total`` is every row matching the filter, so a client paginates on
    ``total_pages`` rather than by asking for the next page until it gets an empty one.

    ``all``, ``need``, ``event`` and ``update`` are the tab counts. They apply the same town,
    status and approval rules as the list but ignore the ``post_type`` filter, so each tab's
    number is right whichever tab is open; ``all`` is the sum of the three.
    """
    success: bool = Field(True, description="True when the read succeeded")
    all: int = Field(..., description="Posts of every type matching the town and status, ignoring post_type")
    need: int = Field(..., description="Needs matching the town and status, ignoring post_type")
    event: int = Field(..., description="Approved events matching the town and status, ignoring post_type")
    update: int = Field(..., description="Approved updates matching the town and status, ignoring post_type")
    open: Optional[int] = Field(
        None, description="With is_sameuser=true, the caller's needs whose need_status is not closed; null otherwise"
    )
    closed: Optional[int] = Field(
        None, description="With is_sameuser=true, the caller's needs whose need_status is closed; null otherwise"
    )
    total: int = Field(..., description="Posts matching every filter including post_type, across all pages")
    page: int = Field(..., description="The 1-based page number that was returned")
    limit: int = Field(..., description="Rows per page that was applied")
    total_pages: int = Field(..., description="Number of pages at this limit; 0 when there are no matches")
    townimage: Optional[str] = Field(
        None, description="The town's card image, as /mytown_suggestions returns it; null when no townprofileid was given"
    )
    provider_count: Optional[int] = Field(
        None, description="Active providers in the town, counted as /mytown_suggestions counts them; null when no townprofileid was given"
    )
    view_count: Optional[int] = Field(
        None, description="The town's view count from get_town_view_count(); null when no townprofileid was given"
    )
    needs: List[NeedItem] = Field(default_factory=list, description="The page of needs")


class CommunityPostDetailResponse(BaseModel):
    """One need, update or event for GET /mytown/community/post, in the same shape as a GET /mytown/community item."""
    success: bool = Field(True, description="True when the post was found")
    post_type: str = Field(..., description="need, update or event")
    postid: str = Field(..., description="The needid, updateid or eventid that was read")
    need: NeedItem = Field(..., description="The post, with the same fields as GET /community returns")


class PublishNeedRequest(BaseModel):
    """Body for POST /mytown/{needid}/publish - turn one of your own drafts into a posted need.

    userprofileid is the ownership check, not a convenience: the bearer token is shared, so
    without it any caller could publish anyone's draft. needed_at is here because a draft
    saved weeks ago may have an original date that has since passed, and the member has to
    be able to correct it at the moment they post rather than being stuck with a stale one.
    """
    model_config = ConfigDict(extra="forbid")

    userprofileid: str = Field(..., min_length=1, description="Caller's userprofileid; must own the draft")
    needed_at: Optional[datetime] = Field(
        None,
        description="Optional replacement for 'when do you need it?'; omit to keep the stored "
                    "date. A value with no offset is read as IST",
    )

    @field_validator("needed_at")
    @classmethod
    def assume_ist(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Attach IST to a naive needed_at and return None or aware datetimes unchanged."""
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=IST)
        return v


class PublishNeedResponse(BaseModel):
    """Result of POST /mytown/{needid}/publish.

    ``published`` separates "this call posted it" from "it was already posted": a retried or
    double-tapped publish returns 200 with published=false and fires no second provider
    fan-out, so the client can treat both as success without double-notifying anyone.
    """
    success: bool = Field(True, description="True when the post is posted")
    post_type: str = Field(..., description="need, update or event")
    needid: Optional[str] = Field(None, description="Need's primary key; set only for needs")
    updateid: Optional[str] = Field(None, description="Update's primary key; set only for updates")
    eventid: Optional[str] = Field(None, description="Event's primary key; set only for events")
    status: str = Field(..., description="Always posted on a successful call")
    published: bool = Field(..., description="True if this call posted it; false if it already was")
    need: NeedItem = Field(..., description="The posted post")
    message: str = Field(..., description="Human-readable confirmation for display")


class CommunityPostActionRequest(BaseModel):
    """Body for POST /mytown/community/action - edit or delete one of your own needs, updates or events.

    action_mode=edit overwrites only the fields present in the body on every town copy of the
    post: a blank optional field clears it, while title and description cannot be blanked.
    attachments adds new files and remove_attachments drops stored ones by path; town, when
    sent, is the full town list to keep. action_mode=delete soft-deletes every town copy.
    userprofileid is the ownership check, since the bearer token is shared.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action_mode: str = Field("", description="edit (update is accepted too) or delete; required")
    post_type: str = Field("", description="need, update or event; required")
    postid: str = Field("", description="needid, updateid or eventid of the post; required")
    userprofileid: str = Field("", description="Caller's userprofileid; must own the post; required")
    title: Optional[str] = Field(None, max_length=200, description="New one-line title")
    description: Optional[str] = Field(None, max_length=300, description="New post details")
    category: Optional[str] = Field(None, max_length=100, description="New service category; blank clears it")
    budget: Optional[str] = Field(None, max_length=100, description="Needs only; new budget; blank clears it")
    need_status: Optional[str] = Field(None, description="Needs only; open or closed")
    lat: Optional[float] = Field(
        None, ge=-90.0, le=90.0,
        validation_alias=AliasChoices("lat", "latitude"),
        description="New latitude",
    )
    long: Optional[float] = Field(
        None, ge=-180.0, le=180.0,
        validation_alias=AliasChoices("long", "longitude"),
        description="New longitude",
    )
    needed_at: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("needed_at", "time"),
        description="New ISO date or datetime, read as IST without an offset; blank clears it",
    )
    location_name: Optional[str] = Field(None, max_length=200, description="New place name; blank clears it")
    start_date: Optional[str] = Field(None, description="Events only; new start date, YYYY-MM-DD")
    start_time: Optional[str] = Field(None, description="Events only; new start time, HH:MM (24-hour)")
    end_date: Optional[str] = Field(None, description="Events only; new end date, YYYY-MM-DD")
    end_time: Optional[str] = Field(None, description="Events only; new end time, HH:MM (24-hour)")
    venue: Optional[str] = Field(None, max_length=200, description="Events only; new venue")
    organizer_name: Optional[str] = Field(None, max_length=100, description="Events only; new organizer name")
    organizer_number: Optional[str] = Field(
        None, max_length=20, pattern=r"^\+?[0-9 ()-]{6,20}$",
        description="Events only; new organizer phone number",
    )
    entry_info: Optional[str] = Field(None, max_length=300, description="Events only; new entry details")
    attachments: List[NeedAttachmentInput] = Field(
        default_factory=list,
        description="Files to add, base64 or data URL; the post keeps at most 5 in all",
    )
    remove_attachments: List[str] = Field(
        default_factory=list, description="Stored attachment paths to drop, as returned in attachments[].path"
    )
    town: Optional[List[NeedTownInput]] = Field(
        None,
        description="Edit only; the full list of towns to keep the post in: matching towns stay, a town left out "
                    "is moved to a new town in the list or else deleted, and remaining new towns get a copy; "
                    "omit to keep the towns as they are",
    )

    @field_validator("town")
    @classmethod
    def validate_towns(cls, v: Optional[List[NeedTownInput]]) -> Optional[List[NeedTownInput]]:
        """Pass None through, require at least one town otherwise and drop repeated townprofileids."""
        if v is None:
            return None
        if not v:
            raise ValueError("at least one town is required")
        unique: Dict[str, NeedTownInput] = {}
        for t in v:
            unique.setdefault(t.townprofileid, t)
        return list(unique.values())

    @field_validator("action_mode", "post_type", "postid", "userprofileid", "title", "description")
    @classmethod
    def strip_text(cls, v: Optional[str]) -> str:
        """Return a text value stripped of surrounding whitespace, reading None as blank."""
        return (v or "").strip()

    @field_validator("need_status")
    @classmethod
    def validate_need_status(cls, v: Optional[str]) -> str:
        """Return need_status lower-cased and reject anything but open or closed."""
        mode = (v or "").strip().lower()
        if mode not in ("open", "closed"):
            raise ValueError("need_status must be one of ['open', 'closed']")
        return mode

    @field_validator(
        "category", "budget", "needed_at", "start_date", "start_time", "end_date", "end_time",
        "location_name", "venue", "organizer_name", "organizer_number", "entry_info",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, v: Any) -> Any:
        """Return an optional text value stripped, or None when it is blank."""
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("needed_at")
    @classmethod
    def parse_needed_at(cls, v: Optional[str]) -> Optional[datetime]:
        """Parse an ISO date or datetime, attaching IST when it has no offset, and pass None through."""
        if v is None:
            return None
        try:
            parsed = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("must be an ISO date or datetime, e.g. 2025-07-12 or 2025-07-12T10:00")
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed

    @field_validator("start_date", "end_date")
    @classmethod
    def parse_date(cls, v: Optional[str]) -> Optional[date]:
        """Parse a YYYY-MM-DD string into a date, passing None through."""
        if v is None:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise ValueError("must be a date in YYYY-MM-DD form")

    @field_validator("start_time", "end_time")
    @classmethod
    def parse_time(cls, v: Optional[str]) -> Optional[clock_time]:
        """Parse an HH:MM or HH:MM:SS string into a time, passing None through."""
        if v is None:
            return None
        try:
            return clock_time.fromisoformat(v)
        except ValueError:
            raise ValueError("must be a time in HH:MM (24-hour) form")

    @model_validator(mode="after")
    def validate_action(self) -> "CommunityPostActionRequest":
        """Normalise action_mode and post_type, and reject missing ids, blanked required fields, wrong-type fields or an empty edit."""
        mode = self.action_mode.lower()
        self.action_mode = "edit" if mode == "update" else mode
        self.post_type = self.post_type.lower()
        if self.action_mode not in ("edit", "delete"):
            raise ValueError("action_mode must be one of ['edit', 'delete']")
        if self.post_type not in ("need", "update", "event"):
            raise ValueError("post_type must be one of ['need', 'update', 'event']")
        missing = [f for f in ("postid", "userprofileid") if not getattr(self, f)]
        if missing:
            raise ValueError(f"{', '.join(missing)} required")
        if self.action_mode == "delete":
            return self
        sent = self.model_fields_set - {"action_mode", "post_type", "postid", "userprofileid"}
        if not sent:
            raise ValueError("send at least one field to change when action_mode is edit")
        blanked = [
            f for f in ("title", "description", "lat", "long")
            if f in sent and not (getattr(self, f) is not None and str(getattr(self, f)).strip())
        ]
        if blanked:
            raise ValueError(f"cannot be blank: {', '.join(blanked)}")
        type_only = {
            "need": ("budget", "need_status"),
            "event": (
                "start_date", "start_time", "end_date", "end_time",
                "venue", "organizer_name", "organizer_number", "entry_info",
            ),
        }
        for owner, fields in type_only.items():
            wrong = [f for f in fields if f in sent]
            if wrong and self.post_type != owner:
                raise ValueError(f"only allowed when post_type is {owner}: {', '.join(wrong)}")
        return self


class CommunityPostActionResponse(BaseModel):
    """Result of POST /mytown/community/action, carrying the edited post, or null after a delete."""
    success: bool = Field(True, description="True when the change was saved")
    action_mode: str = Field(..., description="edit or delete")
    post_type: str = Field(..., description="need, update or event")
    postid: str = Field(..., description="The post that was edited or deleted")
    need: Optional[NeedItem] = Field(None, description="The post after the edit; null after a delete")
    message: str = Field(..., description="Human-readable confirmation for display")


class NeedResponseRequest(BaseModel):
    """Body for POST /mytown/community/respond - send a message to the poster of a posted, open need.

    The message is stored on the need and delivered in Messages as two messages from the
    responder to the poster: the need's title, then the message itself.
    """
    model_config = ConfigDict(extra="forbid")

    needid: str = Field("", description="The need being responded to; required")
    userprofileid: str = Field("", description="Responder's userprofileid; must not be the need's poster; required")
    contactid: str = Field("", description="Responder's contactid; optional")
    message: str = Field("", max_length=1000, description="Response text; required")
    oauth_token: Optional[str] = Field(None, description="Google OAuth token so the poster is pushed the message")

    @field_validator("needid", "userprofileid", "message")
    @classmethod
    def validate_required(cls, v: str) -> str:
        """Reject a blank required value and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()

    @field_validator("contactid")
    @classmethod
    def strip_contactid(cls, v: str) -> str:
        """Return contactid stripped of surrounding whitespace."""
        return (v or "").strip()


class NeedResponseItem(BaseModel):
    """One stored response to a need, with the Messages conversation and message ids it was delivered as."""
    needresponseid: str = Field(..., description="Response row's primary key")
    needid: str = Field(..., description="The need responded to")
    userprofileid: str = Field(..., description="Responder's userprofileid")
    contactid: Optional[str] = Field(None, description="Responder's contactid")
    message: str = Field(..., description="Response text")
    conversation_id: Optional[str] = Field(None, description="Messages conversation between responder and poster")
    title_message_id: Optional[str] = Field(None, description="Message carrying the need's title")
    message_id: Optional[str] = Field(None, description="Message carrying the response text")
    created_at: datetime = Field(..., description="When the response was sent, in IST (+05:30)")


class NeedResponderItem(NeedResponseItem):
    """One responder's latest response to a need, with the responder named and pictured by userprofile role."""
    role: Optional[int] = Field(None, description="1 provider, 2 member")
    name: Optional[str] = Field(None, description="Provider businessname for role 1, contact firstname for role 2")
    image: Optional[str] = Field(None, description="Provider serviceimageurl for role 1, contact profileimage for role 2")


class NeedResponseListResponse(BaseModel):
    """One page of a need's responses for GET /mytown/community/responses: each person's latest, newest first."""
    success: bool = Field(True, description="True when the read succeeded")
    needid: str = Field(..., description="The need whose responses are listed")
    count: int = Field(..., description="Responders in this page")
    total: int = Field(..., description="Unique people who responded to the need")
    page: int = Field(..., description="The 1-based page number that was returned")
    limit: int = Field(..., description="Rows per page that was applied")
    total_pages: int = Field(..., description="Number of pages at this limit; 0 when there are none")
    responses: List[NeedResponderItem] = Field(default_factory=list, description="The page of responses")


class NeedResponseResponse(BaseModel):
    """Result of POST /mytown/community/respond."""
    success: bool = Field(True, description="True when the response was stored and sent")
    response: NeedResponseItem = Field(..., description="The stored response")
    message: str = Field(..., description="Human-readable confirmation for display")


class CommunityLikeRequest(BaseModel):
    """Body for POST /mytown/community/like - like (1) or dislike (2) a posted need, update or event.

    One row is kept per post and userprofile, so liking twice is harmless and a dislike flips
    that row rather than deleting it. likemode 1 is stored with likemodename Like, 2 with Dislike.
    """
    model_config = ConfigDict(extra="forbid", validate_default=True)

    post_type: str = Field("", description="need, update or event; the post's type, stored on the like; required")
    postid: str = Field("", description="needid, updateid or eventid of the post; required")
    userprofileid: str = Field("", description="Liker's userprofileid; required")
    contactid: str = Field("", description="Liker's contactid; resolves a member's name and image")
    like_mode: int = Field(1, description="1 to like (Like), 2 to dislike (Dislike)")

    @field_validator("postid", "userprofileid")
    @classmethod
    def validate_required(cls, v: str) -> str:
        """Reject a blank required id and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()

    @field_validator("post_type")
    @classmethod
    def validate_post_type(cls, v: str) -> str:
        """Return post_type lower-cased and reject anything but need, update or event."""
        kind = (v or "").strip().lower()
        if kind not in ("need", "update", "event"):
            raise ValueError("post_type must be one of ['need', 'update', 'event']")
        return kind

    @field_validator("like_mode")
    @classmethod
    def validate_like_mode(cls, v: int) -> int:
        """Reject any like_mode other than 1 (like) or 2 (dislike)."""
        if v not in (1, 2):
            raise ValueError("like_mode must be 1 (like) or 2 (dislike)")
        return v


class CommunityLikeResponse(BaseModel):
    """Result of POST /mytown/community/like, carrying the post's like count after the change."""
    success: bool = Field(True, description="True when the like was recorded")
    postid: str = Field(..., description="The post that was liked or disliked")
    post_type: str = Field(..., description="need, update or event")
    like_mode: int = Field(..., description="1 liked, 2 disliked")
    likemodename: str = Field(..., description="Like for like_mode 1, Dislike for like_mode 2, as stored")
    is_liked: bool = Field(..., description="True when the caller now likes the post")
    like_count: int = Field(..., description="Current likes on the post")
    message: str = Field(..., description="Human-readable confirmation for display")


class CommunityLikeItem(BaseModel):
    """One liker of a community post, named and pictured by userprofile role like the post's poster."""
    likeid: str = Field(..., description="Like row's primary key")
    userprofileid: str = Field(..., description="Liker's userprofileid")
    contactid: Optional[str] = Field(None, description="Liker's contactid")
    role: Optional[int] = Field(None, description="1 provider, 2 member")
    name: Optional[str] = Field(None, description="Provider businessname for role 1, contact firstname for role 2")
    image: Optional[str] = Field(None, description="Provider serviceimageurl for role 1, contact profileimage for role 2")
    liked_at: datetime = Field(..., description="When the post was last liked, in IST (+05:30)")


class CommunityLikeListResponse(BaseModel):
    """One page of a post's current likers for GET /mytown/community/likes, most recent first."""
    success: bool = Field(True, description="True when the read succeeded")
    postid: str = Field(..., description="The post whose likers are listed")
    count: int = Field(..., description="Likers in this page")
    total: int = Field(..., description="Total likes on the post")
    page: int = Field(..., description="The 1-based page number that was returned")
    limit: int = Field(..., description="Rows per page that was applied")
    total_pages: int = Field(..., description="Number of pages at this limit; 0 when there are none")
    likes: List[CommunityLikeItem] = Field(default_factory=list, description="The page of likers")


class CommunityCommentRequest(BaseModel):
    """Body for POST /mytown/community/comment - add, edit or delete a comment on a posted need, update or event.

    action_mode=add needs post_type, postid and comment; update needs commentid and comment;
    delete needs commentid. post_type is stored on the comment; update and delete ignore it,
    since the comment already records it. Only the commenter may update or delete, and a
    delete is a soft delete.
    """
    model_config = ConfigDict(extra="forbid", validate_default=True)

    action_mode: str = Field("add", description="add, update or delete")
    post_type: str = Field("", description="need, update or event; required for add, ignored otherwise")
    postid: str = Field("", description="needid, updateid or eventid; required for add")
    commentid: str = Field("", description="Comment to change; required for update and delete")
    userprofileid: str = Field("", description="Commenter's userprofileid; required")
    contactid: str = Field("", description="Commenter's contactid; resolves a member's name and image")
    comment: str = Field("", max_length=1000, description="Comment text; required for add and update")

    @field_validator("action_mode", "post_type", "postid", "commentid", "userprofileid", "contactid", "comment")
    @classmethod
    def strip_text(cls, v: str) -> str:
        """Return a text value stripped of surrounding whitespace."""
        return (v or "").strip()

    @model_validator(mode="after")
    def validate_action(self) -> "CommunityCommentRequest":
        """Lower-case action_mode and post_type, and reject a missing or invalid field the chosen action needs."""
        self.action_mode = self.action_mode.lower() or "add"
        self.post_type = self.post_type.lower()
        needs = {
            "add": ("userprofileid", "post_type", "postid", "comment"),
            "update": ("userprofileid", "commentid", "comment"),
            "delete": ("userprofileid", "commentid"),
        }
        if self.action_mode not in needs:
            raise ValueError("action_mode must be one of ['add', 'update', 'delete']")
        missing = [f for f in needs[self.action_mode] if not getattr(self, f)]
        if missing:
            raise ValueError(f"{', '.join(missing)} required when action_mode is {self.action_mode}")
        if self.action_mode == "add" and self.post_type not in ("need", "update", "event"):
            raise ValueError("post_type must be one of ['need', 'update', 'event']")
        return self


class CommunityCommentItem(BaseModel):
    """One comment on a community post, with the commenter named and pictured by userprofile role."""
    commentid: str = Field(..., description="Comment's primary key")
    postid: str = Field(..., description="needid, updateid or eventid of the post")
    post_type: str = Field(..., description="need, update or event")
    userprofileid: str = Field(..., description="Commenter's userprofileid")
    contactid: Optional[str] = Field(None, description="Commenter's contactid")
    comment: str = Field(..., description="Comment text")
    role: Optional[int] = Field(None, description="1 provider, 2 member")
    name: Optional[str] = Field(None, description="Provider businessname for role 1, contact firstname for role 2")
    image: Optional[str] = Field(None, description="Provider serviceimageurl for role 1, contact profileimage for role 2")
    created_at: datetime = Field(..., description="When the comment was added, in IST (+05:30)")
    updated_at: datetime = Field(..., description="When the comment was last changed, in IST (+05:30)")


class CommunityCommentResponse(BaseModel):
    """Result of POST /mytown/community/comment, carrying the post's live comment count after the change."""
    success: bool = Field(True, description="True when the change was saved")
    action_mode: str = Field(..., description="add, update or delete")
    commentid: str = Field(..., description="The comment that was added, updated or deleted")
    postid: str = Field(..., description="The post the comment is on")
    comment_count: int = Field(..., description="Live comments on the post")
    comment: Optional[CommunityCommentItem] = Field(None, description="The saved comment; null after a delete")
    message: str = Field(..., description="Human-readable confirmation for display")


class CommunityCommentListResponse(BaseModel):
    """One page of a post's live comments for GET /mytown/community/comments, newest first."""
    success: bool = Field(True, description="True when the read succeeded")
    postid: str = Field(..., description="The post whose comments are listed")
    count: int = Field(..., description="Comments in this page")
    total: int = Field(..., description="Total live comments on the post")
    page: int = Field(..., description="The 1-based page number that was returned")
    limit: int = Field(..., description="Rows per page that was applied")
    total_pages: int = Field(..., description="Number of pages at this limit; 0 when there are none")
    comments: List[CommunityCommentItem] = Field(default_factory=list, description="The page of comments")


class CommunityReviewRequest(BaseModel):
    """Body for POST /mytown/community/review - receive an admin's approve or reject decision on an update or event.

    status is free text; only a value equal to approved, ignoring case, triggers the pushes.
    post_type is required and picks the table postid is looked up in.
    """
    model_config = ConfigDict(extra="forbid")

    postid: str = Field(..., description="updateid or eventid of the post")
    status: str = Field(..., description="Review status text; approved (any case) sends the pushes")
    post_type: str = Field(..., description="update or event; required")

    @field_validator("postid", "status")
    @classmethod
    def validate_required_text(cls, v: str) -> str:
        """Reject a blank postid or status and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()

    @field_validator("post_type")
    @classmethod
    def validate_post_type(cls, v: str) -> str:
        """Return post_type lower-cased and reject anything but update or event."""
        kind = (v or "").strip().lower()
        if kind not in ("update", "event"):
            raise ValueError("post_type must be 'update' or 'event'; needs are not reviewed")
        return kind


class CommunityReviewResponse(BaseModel):
    """Result of POST /mytown/community/review, echoing the decision received beside the post's stored review state."""
    success: bool = Field(True, description="True when the decision was received")
    postid: str = Field(..., description="The update or event that was reviewed")
    post_type: str = Field(..., description="update or event")
    review_status: str = Field(..., description="The status text as received in this call")
    stored_review_status: str = Field(
        ..., description="pending, approved or rejected, as currently stored on the post; this call does not change it"
    )
    visible_in_feed: bool = Field(
        ..., description="True when the stored review is approved and the post is posted and not deleted"
    )
    poster_notified: bool = Field(
        False, description="True when an approved update's or event's poster push was queued; at most one is sent per post"
    )
    town_notified: bool = Field(
        False,
        description=(
            "True when the push to the town's providers and members within about 10 km was queued; "
            "only for a posted, approved update or event, at most once per person per post"
        ),
    )
    message: str = Field(..., description="Human-readable confirmation")


class CommunityViewPost(BaseModel):
    """One item viewed in a POST /mytown/community/view call, as its type and id."""
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="need, update or event are counted; any other type is accepted and skipped")
    id: str = Field(..., description="needid, updateid or eventid of the post")

    @field_validator("type", "id")
    @classmethod
    def validate_required(cls, v: str) -> str:
        """Reject a blank type or id and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()

    @field_validator("type")
    @classmethod
    def lower_type(cls, v: str) -> str:
        """Return type lower-cased."""
        return v.lower()


class CommunityViewRequest(BaseModel):
    """Body for POST /mytown/community/view - record that a user viewed one or more community posts.

    view_type says how the post was seen. onclick, when it was opened, adds one view on every
    call. public (the default), when it was shown in the feed, adds at most one view per
    userprofileid per IST day, so the client can report every impression without deduplicating.
    Only need, update and event items are counted; other types and ids with no post are skipped.
    """
    model_config = ConfigDict(extra="forbid")

    userprofileid: str = Field(..., description="Viewer's userprofileid; required")
    post_type: List[CommunityViewPost] = Field(
        ..., min_length=1, max_length=100, description="The posts viewed, 1 to 100 items of type and id"
    )
    view_type: str = Field("public", description="onclick or public; public when omitted")

    @field_validator("userprofileid")
    @classmethod
    def validate_userprofileid(cls, v: str) -> str:
        """Reject a blank userprofileid and return it stripped."""
        if not (v or "").strip():
            raise ValueError("is required")
        return v.strip()

    @field_validator("view_type", mode="before")
    @classmethod
    def validate_view_type(cls, v: Any) -> str:
        """Return view_type lower-cased, reading blank as public, and reject anything but onclick or public."""
        mode = (v or "").strip().lower() or "public"
        if mode not in ("onclick", "public"):
            raise ValueError("view_type must be one of ['onclick', 'public']")
        return mode


class CommunityViewItem(BaseModel):
    """One item of a POST /mytown/community/view call, with whether this call counted it and its view count."""
    type: str = Field(..., description="The type as sent, lower-cased")
    id: str = Field(..., description="The id as sent")
    counted: bool = Field(..., description="True when this call added the viewer's view for today")
    view_count: Optional[int] = Field(
        None, description="The post's view_count after this call; null for a skipped type or unknown post"
    )


class CommunityViewResponse(BaseModel):
    """Result of POST /mytown/community/view, with each item's outcome and view count."""
    success: bool = Field(True, description="True when the views were recorded")
    userprofileid: str = Field(..., description="The viewer")
    view_type: str = Field(..., description="onclick or public, as applied")
    view_date: date = Field(..., description="The IST day the views were counted against")
    counted: int = Field(..., description="Items this call counted; 0 when every one was already counted today or skipped")
    posts: List[CommunityViewItem] = Field(default_factory=list, description="One entry per item sent, in order")
    message: str = Field(..., description="Human-readable confirmation")


class MemoryItem(BaseModel):
    """One durable fact Chaatie has stored about the caller."""
    memory_id: str = Field(..., description="Identifier to pass to DELETE /memory/{memory_id}")
    memory: str = Field(..., description="The fact as stored, in English")
    created_at: Optional[datetime] = Field(None, description="When the fact was first stored")


class MemoryListResponse(BaseModel):
    """Page of the caller's stored memories for GET /memory, newest first."""
    success: bool = Field(True, description="True when the read succeeded")
    enabled: bool = Field(..., description="False when the caller has switched memory off; stored facts are kept but unused")
    count: int = Field(..., description="Memories in this page")
    total: int = Field(..., description="Total memories on file for the caller")
    memories: List[MemoryItem] = Field(default_factory=list, description="The page of memories")


class MemoryDeleteResponse(BaseModel):
    """Result of deleting one memory or all of them."""
    success: bool = Field(..., description="True when at least one memory was removed")
    deleted: int = Field(..., description="How many memories were removed")
    message: str = Field(..., description="Human-readable outcome")


class MemorySettingsRequest(BaseModel):
    """Body for PATCH /memory/settings."""
    contact_id: Optional[str] = Field(None, description="Caller's contact id; one of the two ids is required")
    user_profile_id: Optional[str] = Field(None, description="Caller's user-profile id; memories are keyed on this")
    enabled: bool = Field(..., description="True to let Chaatie remember new facts, False to stop reading and writing them")


class MemorySettingsResponse(BaseModel):
    """Current state of the caller's memory switch."""
    success: bool = Field(True, description="True when the read or write succeeded")
    enabled: bool = Field(..., description="Whether long-term memory is on for the caller")
    total: int = Field(..., description="Memories currently on file, kept even while disabled")


class ProviderReviewApprovalRequest(BaseModel):
    """Request body for the provider_review_approval endpoint.

    Every value field except review and rejectedreason reads a blank string as "not supplied"
    and normalises it to None, so it keeps its stored value. The capture sends "" for anything
    it never collected, and writing that through would clear the live value on five tables and
    in Dataverse; a blank alternate mobile would also land on the empty key of the partial
    unique index ux_contact_alternatemobile_live and abort the whole approval. review and
    rejectedreason keep their blanks, so an approval can clear a stale rejection reason.
    """
    reviewid: str = Field(..., min_length=1, description="etown_provider_review.reviewid of the worked row")
    providerid: str = Field(..., min_length=1, description="Provider GUID the review names")
    review: Optional[str] = Field(None, description="The approver's decision, written verbatim")
    rejectedreason: Optional[str] = Field(None, description="The approver's reason, written verbatim")
    action_mode: Optional[str] = Field(None, description="'create' or 'edit', as the capture recorded it")
    mobilenumber: Optional[str] = Field(None, description="Provider mobile number")
    alternatemobile: Optional[str] = Field(None, description="Secondary mobile number")
    businessname: Optional[str] = Field(None, description="Business name")
    businesscategory: Optional[str] = Field(None, description="Business category")
    servicecategory: Optional[str] = Field(None, description="Service category; the two service tables only")
    servicetype: Optional[str] = Field(None, description="Service type")
    description: Optional[str] = Field(None, description="Business description; servicedescription on the service tables")
    services: Optional[str] = Field(None, description="Services blob; the two service tables only")
    servicedetails: Optional[str] = Field(None, description="Service details blob; the review row only")
    types: Optional[str] = Field(None, description="Place types; the review row only")
    language: Optional[str] = Field(None, description="Language; the review row only")
    marketing_person_name: Optional[str] = Field(None, description="Field agent who captured the listing")
    businesslogo: Optional[str] = Field(None, description="Logo URL; also written to serviceimageurl and serviceimageurl1")
    email: Optional[str] = Field(None, description="Email address")
    formattedaddress: Optional[str] = Field(None, description="Street address; address on the provider tables")
    additionaladdressinfo: Optional[str] = Field(None, description="Second address line")
    arealocation: Optional[str] = Field(None, description="Area or locality; areaname on the provider tables")
    city: Optional[str] = Field(None, description="City name; also written to etown_provider.townname")
    state: Optional[str] = Field(None, description="State; statecode on the provider tables")
    latitude: Optional[float] = Field(None, description="Latitude in degrees, -90 to 90")
    longitude: Optional[float] = Field(None, description="Longitude in degrees, -180 to 180")
    interested: Optional[str] = Field(
        None,
        description=(
            "Whether the business is interested: true/false, Yes/No or 1/0 (the ets_interested "
            "Yes/No value), stored as 'true'/'false' on the review row and etown_provider and "
            "mirrored to ets_providers.ets_interested"
        ),
    )

    @field_validator("interested", mode="before")
    @classmethod
    def _normalise_interested(cls, value):
        """Normalise interested to 'true' or 'false', treating blank as None and rejecting anything else."""
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if text in ("true", "yes", "1"):
            return "true"
        if text in ("false", "no", "0"):
            return "false"
        raise ValueError("interested must be true/false, Yes/No or 1/0")

    @field_validator(
        "action_mode", "mobilenumber", "alternatemobile", "businessname", "businesscategory",
        "servicecategory", "servicetype", "description", "services", "servicedetails", "types",
        "language", "marketing_person_name", "businesslogo", "email", "formattedaddress",
        "additionaladdressinfo", "arealocation", "city", "state",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value):
        """Convert a blank string to None and pass other values through."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def _coerce_coordinate(cls, value):
        """Convert a coordinate to float, treating blank as None and rejecting non-numeric values."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid coordinate value: {value!r}")

    @model_validator(mode="after")
    def _check_coordinate_range(self):
        """Reject a latitude outside -90..90 or a longitude outside -180..180."""
        if self.latitude is not None and not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"latitude {self.latitude} is outside -90..90")
        if self.longitude is not None and not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"longitude {self.longitude} is outside -180..180")
        return self


class ProviderReviewApprovalResponse(BaseModel):
    """Result of applying a worked review row across the provider tables."""
    reviewid: str = Field(..., description="Review row that was applied")
    providerid: str = Field(..., description="Provider GUID that was updated")
    contactid: Optional[str] = Field(None, description="Contact read off the provider row, when present")
    review_updated: int = Field(..., description="etown_provider_review rows updated")
    provider_updated: int = Field(..., description="etown_provider rows updated")
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(..., description="etown_providerservices_copy rows updated")
    contact_updated: int = Field(..., description="etown_contact rows updated")
    embeddings: str = Field(
        "scheduled",
        description="'scheduled' — content_vector is rebuilt as a background task after the response",
    )


class CreateInvoiceRequest(BaseModel):
    """Body for POST /invoices -- issue a subscription invoice for MDA to render.

    The invoice number, the mobile number, the period and the blob link of the invoice PDF
    are taken; the number and link are stored on the invoice row as given. The period is
    either ``months`` (1 to 12) counted from period_start, or from today when period_start is
    not sent, as GET /invoices/preview computes it, or period_start and period_end; with months
    the end is calculated and any period_end is ignored, and sending neither is a 422. invoice_number
    follows the GST rule for invoice serials: at most 16 characters of letters, digits,
    '-' and '/'. The customer id, business name,
    email, address and state are read off the business registered on the number, and the
    amounts come from the stored price for the period's length, so MDA cannot send a
    figure that disagrees with what the database says. Payment details are not part of the
    invoice.

    Extra fields are rejected rather than silently dropped, so a typo'd field name surfaces
    as a 422 instead of being ignored.
    """
    model_config = ConfigDict(extra="forbid")

    invoice_number: str = Field(
        ..., pattern=r"^[A-Za-z0-9/-]{1,16}$", description="Invoice number printed on the PDF, e.g. INV-2026-008"
    )
    mobile_number: str = Field(..., description="Mobile number of the paying business; +91 optional")
    months: Optional[int] = Field(
        None, ge=1, le=12,
        description=(
            "Subscription length 1 to 12; the period runs from period_start, or today when it is "
            "not sent, and period_end is calculated. Overrides period_end."
        ),
    )
    period_start: Optional[date] = Field(
        None,
        description="First day of the subscription, YYYY-MM-DD; with months defaults to today, else required",
    )
    period_end: Optional[date] = Field(
        None,
        description=(
            "Last day of the subscription, YYYY-MM-DD, the day before the same date N months on; "
            "required when months is not sent"
        ),
    )
    invoice_url: str = Field(..., description="Blob link of the invoice PDF generated by MDA")
    send_to_customer: str = Field(
        "false",
        description=(
            "'true' sends the invoice to the customer: the WhatsApp template with the PDF when the "
            "business is on WhatsApp, and an email with the PDF attached when it has an email. "
            "Stored on the invoice either way."
        ),
    )

    @field_validator("send_to_customer")
    @classmethod
    def true_or_false(cls, v: str) -> str:
        """Accept 'true' or 'false' in any case and return it lower-cased."""
        v = (v or "").strip().lower()
        if v not in ("true", "false"):
            raise ValueError("send_to_customer must be 'true' or 'false'")
        return v

    @field_validator("invoice_url")
    @classmethod
    def url_not_blank(cls, v: str) -> str:
        """Reject a blank link and return it stripped."""
        v = (v or "").strip()
        if not v:
            raise ValueError("invoice_url cannot be blank")
        return v

    @field_validator("mobile_number")
    @classmethod
    def valid_mobile(cls, v: str) -> str:
        """Reject a number that is not an Indian mobile and return its 10-digit form."""
        national = normalize_indian_mobile(v)
        if not national:
            raise ValueError("mobile_number must be a 10-digit Indian mobile number starting 6-9")
        return national

    @model_validator(mode="after")
    def months_or_dates(self) -> "CreateInvoiceRequest":
        """Require months, or both period_start and period_end."""
        if self.months is None and (self.period_start is None or self.period_end is None):
            raise ValueError("Send months (1 to 12), or both period_start and period_end")
        return self
