from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

GenderValue = Literal["male", "female", "other", "prefer_not_to_say"]
SellerPlanValue = Literal["basic", "smart", "pro"]
ZoneValue = Literal["europe", "pakistan"]
SellerTypeValue = Literal["private", "company"]


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    display_name: str | None = Field(default=None, alias="displayName")

    model_config = {"populate_by_name": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(alias="currentPassword", min_length=1)
    new_password: str = Field(alias="newPassword", min_length=6)

    model_config = {"populate_by_name": True}


class RefreshRequest(BaseModel):
    refresh_token: str = Field(alias="refreshToken")

    model_config = {"populate_by_name": True}


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, alias="refreshToken")

    model_config = {"populate_by_name": True}


class LocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName", max_length=255)
    bio: str | None = None
    phone: str | None = Field(default=None, max_length=32)
    gender: GenderValue | None = None
    birth_date: date | None = Field(default=None, alias="birthDate")
    province: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, alias="avatarUrl", max_length=512)

    model_config = {"populate_by_name": True}

    @field_validator("birth_date")
    @classmethod
    def birth_date_not_future(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("Birth date cannot be in the future")
        return value


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    bio: str | None = None
    phone: str | None = None
    gender: str | None = None
    birth_date: date | None = Field(default=None, serialization_alias="birthDate")
    avatar_url: str | None = Field(default=None, serialization_alias="avatarUrl")
    province: str | None = None
    city: str | None = None
    firebase_uid: str = Field(serialization_alias="firebaseUid")
    email_verified: bool = Field(default=False, serialization_alias="emailVerified")
    role: str = "user"
    business_id: int | None = Field(default=None, serialization_alias="businessId")
    latitude: float | None = None
    longitude: float | None = None
    seller_plan: str | None = Field(default=None, serialization_alias="sellerPlan")
    home_zone: str | None = Field(default=None, serialization_alias="homeZone")
    allowed_zones: list[str] = Field(default_factory=list, serialization_alias="allowedZones")
    seller_main_category_id: int | None = Field(
        default=None, serialization_alias="sellerMainCategoryId"
    )

    model_config = {"from_attributes": True, "populate_by_name": True}


class ActivateSellerPlanRequest(BaseModel):
    plan: SellerPlanValue
    home_zone: ZoneValue = Field(alias="homeZone")
    extra_zones: list[ZoneValue] = Field(default_factory=list, alias="extraZones")
    accepted_terms: bool = Field(alias="acceptedTerms")
    seller_type: SellerTypeValue = Field(default="private", alias="sellerType")

    model_config = {"populate_by_name": True}


class SellerPlanSummaryResponse(BaseModel):
    plan: str | None = None
    home_zone: str | None = Field(default=None, alias="homeZone")
    allowed_zones: list[str] = Field(default_factory=list, alias="allowedZones")
    seller_main_category_id: int | None = Field(
        default=None, alias="sellerMainCategoryId"
    )
    multi_category: bool = Field(default=False, alias="multiCategory")
    own_zone_used: int = Field(default=0, alias="ownZoneUsed")
    own_zone_limit: int = Field(default=0, alias="ownZoneLimit")
    other_zone_used: int = Field(default=0, alias="otherZoneUsed")
    other_zone_limit: int = Field(default=0, alias="otherZoneLimit")
    quota_reset: str = Field(default="never", alias="quotaReset")
    deleted_frees_slot: bool = Field(default=False, alias="deletedFreesSlot")
    zones: list[dict[str, Any]] = Field(default_factory=list)
    plans: dict[str, Any] = Field(default_factory=dict)
    quota_owner_user_id: int | None = Field(default=None, alias="quotaOwnerUserId")
    quota_owner_email: str | None = Field(default=None, alias="quotaOwnerEmail")

    model_config = {"populate_by_name": True}


class BusinessOut(BaseModel):
    id: int
    name: str
    owner_user_id: int = Field(serialization_alias="ownerUserId")
    owner_email: str | None = Field(default=None, serialization_alias="ownerEmail")
    owner_name: str | None = Field(default=None, serialization_alias="ownerName")
    province: str | None = None
    city: str | None = None
    phone: str | None = None
    is_active: bool = Field(default=True, serialization_alias="isActive")
    pl_count: int = Field(default=0, serialization_alias="plCount")

    model_config = {"populate_by_name": True}


class BusinessListResponse(BaseModel):
    businesses: list[BusinessOut]


class CreateBusinessRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    owner_email: EmailStr = Field(alias="ownerEmail")
    owner_password: str = Field(alias="ownerPassword", min_length=6)
    owner_display_name: str | None = Field(
        default=None, alias="ownerDisplayName", max_length=255
    )
    home_zone: ZoneValue = Field(default="pakistan", alias="homeZone")
    seller_plan: SellerPlanValue = Field(default="pro", alias="sellerPlan")
    province: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=32)

    model_config = {"populate_by_name": True}


class CreatePLRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    display_name: str | None = Field(default=None, alias="displayName", max_length=255)
    province: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)

    model_config = {"populate_by_name": True}


class PLOut(BaseModel):
    id: int
    email: str
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    province: str | None = None
    city: str | None = None
    phone: str | None = None
    business_id: int | None = Field(default=None, serialization_alias="businessId")
    role: str = "pl"

    model_config = {"populate_by_name": True}


class PLListResponse(BaseModel):
    pls: list[PLOut]


class AuthResponse(BaseModel):
    user: UserOut
    access_token: str = Field(serialization_alias="accessToken")
    refresh_token: str = Field(serialization_alias="refreshToken")

    model_config = {"populate_by_name": True}


class TokenPair(BaseModel):
    access_token: str = Field(serialization_alias="accessToken")
    refresh_token: str = Field(serialization_alias="refreshToken")

    model_config = {"populate_by_name": True}


class MeResponse(BaseModel):
    user: UserOut


class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str
    is_virtual_all: bool = Field(serialization_alias="isVirtualAll")
    children: list["CategoryOut"] = Field(default_factory=list)

    model_config = {"from_attributes": True, "populate_by_name": True}


class CategoryListResponse(BaseModel):
    categories: list[CategoryOut]


class ProductImageOut(BaseModel):
    id: int
    url: str
    sort_order: int = Field(serialization_alias="sortOrder")

    model_config = {"from_attributes": True, "populate_by_name": True}


class ProductOut(BaseModel):
    id: int
    seller_id: int = Field(serialization_alias="sellerId")
    seller_name: str | None = Field(default=None, serialization_alias="sellerName")
    listed_by_id: int | None = Field(default=None, serialization_alias="listedById")
    listed_by_name: str | None = Field(default=None, serialization_alias="listedByName")
    business_id: int | None = Field(default=None, serialization_alias="businessId")
    category_id: int = Field(serialization_alias="categoryId")
    category_name: str | None = Field(default=None, serialization_alias="categoryName")
    main_category_name: str | None = Field(default=None, serialization_alias="mainCategoryName")
    mid_category_name: str | None = Field(default=None, serialization_alias="midCategoryName")
    title: str
    description: str
    condition: str = "good"
    brand: str | None = None
    color: str | None = None
    parcel_size: str | None = Field(default=None, serialization_alias="parcelSize")
    custom_category: str | None = Field(default=None, serialization_alias="customCategory")
    price: Decimal
    currency: str
    zone: str = "pakistan"
    province: str
    city: str
    shipping_within_city: Decimal = Field(serialization_alias="shippingWithinCity")
    shipping_out_of_city: Decimal = Field(serialization_alias="shippingOutOfCity")
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float | None = Field(default=None, serialization_alias="distanceKm")
    images: list[ProductImageOut] = Field(default_factory=list)
    is_active: bool = Field(serialization_alias="isActive")

    model_config = {"populate_by_name": True}


class ProductListResponse(BaseModel):
    products: list[ProductOut]


class DeletedAccountOut(BaseModel):
    id: int
    user_id: int = Field(serialization_alias="userId")
    email: str
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    phone: str | None = None
    role: str = "user"
    province: str | None = None
    city: str | None = None
    home_zone: str | None = Field(default=None, serialization_alias="homeZone")
    seller_plan: str | None = Field(default=None, serialization_alias="sellerPlan")
    business_id: int | None = Field(default=None, serialization_alias="businessId")
    registered_at: datetime | None = Field(default=None, serialization_alias="registeredAt")
    deleted_at: datetime = Field(serialization_alias="deletedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class DeletedAccountListResponse(BaseModel):
    accounts: list[DeletedAccountOut]


class DeleteAccountResponse(BaseModel):
    ok: bool = True
    message: str = "Account deleted"
