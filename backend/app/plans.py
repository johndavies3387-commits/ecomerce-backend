"""Vendor account plans (Basic / Smart / Pro) and zone listing rules.

Policy (confirmed):
- Free listing allowance never resets.
- Deleted / expired / inactive listings do NOT free a slot (lifetime usage).
"""

from __future__ import annotations

from typing import Any

# Geographic markets (launch: Europe + Pakistan; America reserved)
ZONES: tuple[str, ...] = ("europe", "pakistan")
ZONE_LABELS = {
    "europe": "Europe",
    "pakistan": "Pakistan",
}

PLANS: dict[str, dict[str, Any]] = {
    "basic": {
        "own_zone_listings": 10,
        "other_zone_listings": 0,
        "max_extra_zones": 0,
        "multi_category": False,
        "registration_fee": {
            "europe": {"amount": 0, "currency": "EUR"},
            "pakistan": {"amount": 1000, "currency": "PKR"},
        },
    },
    "smart": {
        "own_zone_listings": 25,
        "other_zone_listings": 5,
        "max_extra_zones": 1,
        "multi_category": True,
        "registration_fee": {
            "europe": {"amount": 2.99, "currency": "EUR"},
            "pakistan": {"amount": 3000, "currency": "PKR"},
        },
    },
    "pro": {
        "own_zone_listings": 50,
        "other_zone_listings": 15,
        "max_extra_zones": len(ZONES),  # may choose one, two, or all
        "multi_category": True,
        "registration_fee": {
            "europe": {"amount": 5, "currency": "EUR"},
            "pakistan": {"amount": 5000, "currency": "PKR"},
        },
    },
}

PLAN_ORDER = {"basic": 1, "smart": 2, "pro": 3}

# Confirmed product policy
QUOTA_RESET = "never"
DELETED_FREES_SLOT = False


def normalize_zone(zone: str | None) -> str | None:
    if not zone:
        return None
    z = zone.strip().lower()
    return z if z in ZONES else None


def plan_config(plan: str | None) -> dict[str, Any] | None:
    if not plan:
        return None
    return PLANS.get(plan.strip().lower())


def registration_fee(plan: str, home_zone: str) -> dict[str, Any]:
    cfg = plan_config(plan)
    if not cfg:
        raise ValueError("Unknown plan")
    fee = cfg["registration_fee"].get(home_zone)
    if not fee:
        raise ValueError("Unknown home zone")
    return fee


def parse_allowed_zones(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        z = normalize_zone(part)
        if z and z not in out:
            out.append(z)
    return out


def encode_allowed_zones(zones: list[str]) -> str:
    clean: list[str] = []
    for z in zones:
        nz = normalize_zone(z)
        if nz and nz not in clean:
            clean.append(nz)
    return ",".join(clean)
