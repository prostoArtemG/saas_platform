"""
Idempotent seed script — creates the demo-auto client for the auto_market template.

Run from the project root:
    python3 scripts/seed_demo_auto.py

The script is safe to run multiple times:
  - client is created once (matched by slug="demo-auto"), then updated on re-runs
  - settings are upserted
  - products + ProductSpec rows are inserted only if no products exist yet
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.models import Client, ClientSettings, Product, ProductSpec

# ─────────────────────────────────────────────────────────────────────────────
DEMO_SLUG = "demo-auto"

# Each car: dict with Product fields + "spec_rows" list of (name, value) tuples
# for ProductSpec table (used by sidebar filters).
DEMO_CARS: list[dict] = [
    # 1 ── Toyota Camry
    dict(
        brand="Toyota",
        name="Camry 2.5 Hybrid",
        category="Седан",
        description=(
            "Надійний японський бізнес-седан з гібридним двигуном 2.5 л. "
            "Комфортний салон, адаптивний круїз-контроль, LED-оптика. "
            "Один власник, сервісна книга. Ідеальний стан."
        ),
        price=28500,
        old_price=31000,
        badge="Знижка",
        is_available=True,
        image_url="https://images.unsplash.com/photo-1621007947382-bb3c3994e3fb?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2021"),
            ("Пробіг", "42 000 км"),
            ("Паливо", "Гібрид"),
            ("Коробка", "Автомат"),
            ("Двигун", "2.5 л / 178 к.с."),
            ("Місто",  "Київ"),
        ],
    ),
    # 2 ── BMW X5
    dict(
        brand="BMW",
        name="X5 xDrive30d M Sport",
        category="SUV",
        description=(
            "Преміум позашляховик BMW X5 у комплектації M Sport. "
            "Панорамний дах, підігрів всіх сидінь, Harman Kardon, "
            "адаптивна підвіска. Розмитнений, без ДТП."
        ),
        price=65000,
        old_price=None,
        badge="Преміум",
        is_available=True,
        image_url="https://images.unsplash.com/photo-1555215695-3004980ad54e?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2022"),
            ("Пробіг", "38 500 км"),
            ("Паливо", "Дизель"),
            ("Коробка", "Автомат"),
            ("Двигун", "3.0 л / 286 к.с."),
            ("Місто",  "Дніпро"),
        ],
    ),
    # 3 ── Audi Q7
    dict(
        brand="Audi",
        name="Q7 3.0 TDI quattro",
        category="SUV",
        description=(
            "7-місний преміум-SUV з дизельним двигуном 3.0 TDI і повним "
            "приводом quattro. Вентиляція сидінь, матричний LED, "
            "Bang & Olufsen. Перший власник."
        ),
        price=72000,
        old_price=76500,
        badge="Знижка",
        is_available=True,
        image_url="https://images.unsplash.com/photo-1606664515524-ed2f786a0bd6?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2020"),
            ("Пробіг", "61 000 км"),
            ("Паливо", "Дизель"),
            ("Коробка", "Автомат"),
            ("Двигун", "3.0 л / 249 к.с."),
            ("Місто",  "Львів"),
        ],
    ),
    # 4 ── Volkswagen Passat B8
    dict(
        brand="Volkswagen",
        name="Passat B8 2.0 TDI Variant",
        category="Універсал",
        description=(
            "Практичний VW Passat B8 у кузові Variant (універсал). "
            "Великий багажник, підігрів лобового скла, DSG7, "
            "активний круїз-контроль. Ідеальний для сім'ї."
        ),
        price=19800,
        old_price=None,
        badge=None,
        is_available=True,
        image_url="https://images.unsplash.com/photo-1590362891991-f776e747a588?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2019"),
            ("Пробіг", "88 000 км"),
            ("Паливо", "Дизель"),
            ("Коробка", "Автомат"),
            ("Двигун", "2.0 л / 150 к.с."),
            ("Місто",  "Харків"),
        ],
    ),
    # 5 ── Hyundai Tucson
    dict(
        brand="Hyundai",
        name="Tucson 1.6 T-GDi 4WD",
        category="SUV",
        description=(
            "Стильний Hyundai Tucson нового покоління з турбодвигуном. "
            "Безключовий доступ, кругова камера, підігрів керма, "
            "CarPlay / Android Auto. Без ДТП, сервіс у дилера."
        ),
        price=26500,
        old_price=28000,
        badge="Хіт",
        is_available=True,
        image_url="https://images.unsplash.com/photo-1617469767280-0cebb3967d73?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2022"),
            ("Пробіг", "29 000 км"),
            ("Паливо", "Бензин"),
            ("Коробка", "Автомат"),
            ("Двигун", "1.6 л / 180 к.с."),
            ("Місто",  "Одеса"),
        ],
    ),
    # 6 ── Mercedes-Benz E-Class
    dict(
        brand="Mercedes-Benz",
        name="E 220d AMG Line",
        category="Седан",
        description=(
            "Елегантний бізнес-седан Mercedes-Benz E-Class у пакеті AMG Line. "
            "Пневматична підвіска, Burmester аудіо, розпізнавання знаків, "
            "проекційний дисплей. Імпортований з Германії."
        ),
        price=47000,
        old_price=None,
        badge="Преміум",
        is_available=True,
        image_url="https://images.unsplash.com/photo-1618843479313-40f8afb4b4d8?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2021"),
            ("Пробіг", "54 000 км"),
            ("Паливо", "Дизель"),
            ("Коробка", "Автомат"),
            ("Двигун", "2.0 л / 194 к.с."),
            ("Місто",  "Київ"),
        ],
    ),
    # 7 ── Nissan Leaf
    dict(
        brand="Nissan",
        name="Leaf e+ 62 kWh",
        category="Електро",
        description=(
            "Повністю електричний Nissan Leaf з батареєю 62 кВт·год. "
            "Запас ходу до 385 км (WLTP), швидка зарядка CHAdeMO до 50 кВт, "
            "ProPilot — система автономного руху."
        ),
        price=18500,
        old_price=21000,
        badge="Акція",
        is_available=True,
        image_url="https://images.unsplash.com/photo-1593941707882-a5bba14938c7?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2020"),
            ("Пробіг", "47 000 км"),
            ("Паливо", "Електро"),
            ("Коробка", "Автомат"),
            ("Двигун", "62 кВт·год / 217 к.с."),
            ("Місто",  "Запоріжжя"),
        ],
    ),
    # 8 ── Skoda Octavia A7
    dict(
        brand="Skoda",
        name="Octavia A7 1.6 TDI Combi",
        category="Універсал",
        description=(
            "Надійна Skoda Octavia A7 у кузові Combi з економічним "
            "дизельним двигуном. Велика кольорова навігація, "
            "підігрів сидінь, парктроніки. Один власник, без аварій."
        ),
        price=14900,
        old_price=None,
        badge=None,
        is_available=True,
        image_url="https://images.unsplash.com/photo-1596625618196-832a8077e24a?w=800&auto=format&fit=crop",
        spec_rows=[
            ("Рік",   "2018"),
            ("Пробіг", "112 000 км"),
            ("Паливо", "Дизель"),
            ("Коробка", "Механіка"),
            ("Двигун", "1.6 л / 115 к.с."),
            ("Місто",  "Вінниця"),
        ],
    ),
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # ── 1. Upsert client ─────────────────────────────────────────────────
        client = await session.scalar(
            select(Client).where(Client.slug == DEMO_SLUG)
        )
        if client is None:
            client = Client(
                business_name="AutoMarket Demo",
                slug=DEMO_SLUG,
                status="active",
                template_name="auto_market",
            )
            session.add(client)
            await session.flush()
            print(f"[+] Client created: slug={DEMO_SLUG} id={client.id}")
        else:
            client.status = "active"
            client.template_name = "auto_market"
            client.business_name = "AutoMarket Demo"
            print(f"[~] Client exists: slug={DEMO_SLUG} id={client.id}")

        # ── 2. Upsert settings ───────────────────────────────────────────────
        cs = await session.get(ClientSettings, client.id)
        if cs is None:
            cs = ClientSettings(
                client_id=client.id,
                language="uk",
                currency="USD",
                timezone="Europe/Kyiv",
                theme_name="auto_dark",
                shop_title="AutoMarket Demo",
                phone="+38 (099) 111-22-33",
                address="Україна",
            )
            session.add(cs)
            print(f"[+] Settings created for client id={client.id}")
        else:
            cs.theme_name = "auto_dark"
            cs.shop_title = "AutoMarket Demo"
            cs.phone = "+38 (099) 111-22-33"
            cs.address = "Україна"
            print(f"[~] Settings updated for client id={client.id}")

        # ── 3. Insert products + specs (only if no products yet) ─────────────
        existing_count: int = await session.scalar(
            select(func.count()).where(Product.client_id == client.id)
        ) or 0

        if existing_count == 0:
            for car in DEMO_CARS:
                spec_rows = car.pop("spec_rows")
                product = Product(client_id=client.id, **car)
                session.add(product)
                await session.flush()  # get product.id

                for spec_name, spec_value in spec_rows:
                    session.add(ProductSpec(
                        product_id=product.id,
                        client_id=client.id,
                        name=spec_name,
                        value=spec_value,
                    ))

            print(f"[+] Inserted {len(DEMO_CARS)} demo cars with ProductSpec rows")
        else:
            print(
                f"[~] Products already exist ({existing_count}), skipping. "
                "Delete them manually to re-seed."
            )

        await session.commit()

    print(f"\n[✓] Done.")
    print(f"    Local:      /site/{DEMO_SLUG}")
    print(f"    Production: https://{DEMO_SLUG}.shopplatform.app")


if __name__ == "__main__":
    asyncio.run(seed())
