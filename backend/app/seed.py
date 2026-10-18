from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category

# Main → mid → leaf (is_virtual_all = browse-only filters)
CATEGORY_TREE = [
    {
        "name": "Men",
        "slug": "men",
        "children": [
            {"name": "View all men", "slug": "all", "is_virtual_all": True},
            {
                "name": "Clothing",
                "slug": "clothing",
                "children": [
                    {"name": "Outerwear", "slug": "outerwear"},
                    {"name": "Suits & blazers", "slug": "suits-blazers"},
                    {"name": "Socks & underwear", "slug": "socks-underwear"},
                    {"name": "Swimwear", "slug": "swimwear"},
                    {"name": "Costumes & special outfits", "slug": "costumes-special"},
                    {"name": "Jeans", "slug": "jeans"},
                    {"name": "Tops & t-shirts", "slug": "tops-tshirts"},
                    {"name": "Jumpers & sweaters", "slug": "jumpers-sweaters"},
                    {"name": "Sleepwear", "slug": "sleepwear"},
                    {"name": "Activewear", "slug": "activewear"},
                    {"name": "Other clothing", "slug": "other-clothing"},
                    {"name": "Trousers", "slug": "trousers"},
                    {"name": "Shorts", "slug": "shorts"},
                    {"name": "Pants", "slug": "pants"},
                ],
            },
            {
                "name": "Shoes",
                "slug": "shoes",
                "children": [
                    {"name": "Boat shoes, loafers & moccasins", "slug": "boat-loafers-moccasins"},
                    {"name": "Boots", "slug": "boots"},
                    {"name": "Clogs & mules", "slug": "clogs-mules"},
                    {"name": "Espadrilles", "slug": "espadrilles"},
                    {"name": "Flip-flops & slides", "slug": "flipflops-slides"},
                    {"name": "Sandals", "slug": "sandals"},
                    {"name": "Slippers", "slug": "slippers"},
                    {"name": "Formal shoes", "slug": "formal-shoes"},
                    {"name": "Sports shoes", "slug": "sports-shoes"},
                    {"name": "Trainers", "slug": "trainers"},
                    {"name": "Other shoes", "slug": "other-shoes"},
                ],
            },
            {
                "name": "Accessories",
                "slug": "accessories",
                "children": [
                    {"name": "Belts", "slug": "belts"},
                    {"name": "Scarves & shawls", "slug": "scarves-shawls"},
                    {"name": "Bandanas & headscarves", "slug": "bandanas-headscarves"},
                    {"name": "Handkerchiefs", "slug": "handkerchiefs"},
                    {"name": "Sunglasses", "slug": "sunglasses"},
                    {"name": "Gloves", "slug": "gloves"},
                    {"name": "Hats & caps", "slug": "hats-caps"},
                    {"name": "Watches", "slug": "watches"},
                    {"name": "Jewellery", "slug": "jewellery"},
                    {"name": "Other accessories", "slug": "other-accessories"},
                    {"name": "Bags & backpacks", "slug": "bags-backpacks"},
                    {"name": "Wallets", "slug": "wallets"},
                ],
            },
        ],
    },
    {
        "name": "Women",
        "slug": "women",
        "children": [
            {"name": "View all women", "slug": "all", "is_virtual_all": True},
            {
                "name": "Clothing",
                "slug": "clothing",
                "children": [
                    {"name": "Jumpers & sweaters", "slug": "jumpers-sweaters"},
                    {"name": "Dresses", "slug": "dresses"},
                    {"name": "Skorts", "slug": "skorts"},
                    {"name": "Jeans", "slug": "jeans"},
                    {"name": "Shorts & cropped trousers", "slug": "shorts-cropped"},
                    {"name": "Swimwear", "slug": "swimwear"},
                    {"name": "Maternity clothes", "slug": "maternity"},
                    {"name": "Costumes & special outfits", "slug": "costumes-special"},
                    {"name": "Outerwear", "slug": "outerwear"},
                    {"name": "Suits & blazers", "slug": "suits-blazers"},
                    {"name": "Skirts", "slug": "skirts"},
                    {"name": "Tops & t-shirts", "slug": "tops-tshirts"},
                    {"name": "Jumpsuits & playsuits", "slug": "jumpsuits-playsuits"},
                    {"name": "Lingerie & nightwear", "slug": "lingerie-nightwear"},
                    {"name": "Activewear", "slug": "activewear"},
                    {"name": "Other clothing", "slug": "other-clothing"},
                    {"name": "Eastern", "slug": "eastern"},
                    {"name": "Trousers & leggings", "slug": "trousers-leggings"},
                    {"name": "Pants", "slug": "pants"},
                ],
            },
            {
                "name": "Shoes",
                "slug": "shoes",
                "children": [
                    {"name": "Ballerinas", "slug": "ballerinas"},
                    {"name": "Heels", "slug": "heels"},
                    {"name": "Lace-up shoes", "slug": "lace-up"},
                    {"name": "Mary Janes & T-bar shoes", "slug": "mary-janes-tbar"},
                    {"name": "Boat shoes, loafers & moccasins", "slug": "boat-loafers-moccasins"},
                    {"name": "Boots", "slug": "boots"},
                    {"name": "Clogs & mules", "slug": "clogs-mules"},
                    {"name": "Espadrilles", "slug": "espadrilles"},
                    {"name": "Flip-flops & slides", "slug": "flipflops-slides"},
                    {"name": "Sandals", "slug": "sandals"},
                    {"name": "Slippers", "slug": "slippers"},
                    {"name": "Sports shoes", "slug": "sports-shoes"},
                    {"name": "Trainers", "slug": "trainers"},
                    {"name": "Other shoes", "slug": "other-shoes"},
                ],
            },
            {
                "name": "Accessories",
                "slug": "accessories",
                "children": [
                    {"name": "Belts", "slug": "belts"},
                    {"name": "Scarves & shawls", "slug": "scarves-shawls"},
                    {"name": "Bandanas & headscarves", "slug": "bandanas-headscarves"},
                    {"name": "Handkerchiefs", "slug": "handkerchiefs"},
                    {"name": "Gloves", "slug": "gloves"},
                    {"name": "Hats & caps", "slug": "hats-caps"},
                    {"name": "Sunglasses", "slug": "sunglasses"},
                    {"name": "Watches", "slug": "watches"},
                    {"name": "Jewellery", "slug": "jewellery"},
                    {"name": "Other accessories", "slug": "other-accessories"},
                    {"name": "Bags & backpacks", "slug": "bags-backpacks"},
                    {"name": "Wallets", "slug": "wallets"},
                ],
            },
        ],
    },
    {
        "name": "Kids",
        "slug": "kids",
        "children": [
            {"name": "Girls clothing", "slug": "girls-clothing"},
            {"name": "Boys clothing", "slug": "boys-clothing"},
            {"name": "Other", "slug": "other-item"},
        ],
    },
    {
        "name": "Unisex",
        "slug": "unisex",
        "children": [
            {"name": "All unisex", "slug": "all", "is_virtual_all": True},
            {"name": "Clothing", "slug": "clothing"},
            {"name": "Other", "slug": "other-item"},
        ],
    },
    {
        "name": "Others",
        "slug": "others",
        "children": [
            {"name": "View all others", "slug": "all", "is_virtual_all": True},
            {"name": "Other item", "slug": "other-item"},
        ],
    },
]


async def _has_leaf_tier(db: AsyncSession) -> bool:
    """True if any category is a grandchild (parent also has a parent)."""
    row = (
        await db.execute(
            text(
                """
                SELECT 1
                FROM categories c
                JOIN categories p ON c.parent_id = p.id
                WHERE p.parent_id IS NOT NULL
                LIMIT 1
                """
            )
        )
    ).first()
    return row is not None


async def _wipe_catalog(db: AsyncSession) -> None:
    await db.execute(text("DELETE FROM product_images"))
    await db.execute(text("DELETE FROM products"))
    await db.execute(text("DELETE FROM categories"))
    await db.commit()


async def _get_or_create_category(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    parent_id: int | None,
    sort_order: int,
    is_virtual_all: bool = False,
) -> tuple[Category, bool]:
    stmt = select(Category).where(Category.slug == slug)
    if parent_id is None:
        stmt = stmt.where(Category.parent_id.is_(None))
    else:
        stmt = stmt.where(Category.parent_id == parent_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing, False
    cat = Category(
        name=name,
        slug=slug,
        sort_order=sort_order,
        parent_id=parent_id,
        is_virtual_all=is_virtual_all,
    )
    db.add(cat)
    await db.flush()
    return cat, True


async def _sync_category_tree(db: AsyncSession) -> int:
    """Insert any missing nodes from CATEGORY_TREE (safe for existing DBs)."""
    created = 0
    for i, main in enumerate(CATEGORY_TREE):
        parent, was_new = await _get_or_create_category(
            db,
            name=main["name"],
            slug=main["slug"],
            parent_id=None,
            sort_order=i,
        )
        if was_new:
            created += 1
        for j, child in enumerate(main["children"]):
            mid, was_new = await _get_or_create_category(
                db,
                name=child["name"],
                slug=child["slug"],
                parent_id=parent.id,
                sort_order=j,
                is_virtual_all=bool(child.get("is_virtual_all")),
            )
            if was_new:
                created += 1
            for k, leaf in enumerate(child.get("children") or []):
                _, was_new = await _get_or_create_category(
                    db,
                    name=leaf["name"],
                    slug=leaf["slug"],
                    parent_id=mid.id,
                    sort_order=k,
                    is_virtual_all=bool(leaf.get("is_virtual_all", False)),
                )
                if was_new:
                    created += 1
    await db.commit()
    return created


async def seed_categories(db: AsyncSession) -> None:
    existing = await db.execute(select(Category).where(Category.parent_id.is_(None)))
    has_mains = existing.scalars().first() is not None

    if has_mains and not await _has_leaf_tier(db):
        print("[db] upgrading category tree to 3 levels (wiping products/categories)")
        await _wipe_catalog(db)
        has_mains = False

    if not has_mains:
        created = await _sync_category_tree(db)
        print(f"[db] categories seeded ({created} rows)")
        return

    created = await _sync_category_tree(db)
    if created:
        print(f"[db] categories synced (+{created} new nodes, e.g. Others / Other leaves)")
