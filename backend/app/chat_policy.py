"""Marketplace chat policy.

ALLOWED channels:
- listing: buyer asks details / makes offers (product-scoped only)
- order: post-purchase (order_id required when orders exist)
- dispute: order/listing conflicts (admin can see and reply)
- complaint: general app feedback (admin can see and reply)
- pl_business: PL <-> their business owner only
- business_admin: business owner <-> admin

Business listings: buyer messages go to the PL (assignee);
business owner is a viewer (can read/reply).

NEVER (not now, not later):
- Voice / video calls
- Group chats
- Admin sitting in every product listing chat
- Buyer <-> random sellers with no product context
"""

FORBIDDEN_FEATURES = (
    "voice",
    "video",
    "group_chat",
    "admin_in_listing_chat",
    "open_dm_without_product",
)

CONVERSATION_KINDS = (
    "listing",
    "order",
    "dispute",
    "complaint",
    "pl_business",
    "business_admin",
)

OFFER_STATUSES = ("pending", "accepted", "rejected", "withdrawn")
