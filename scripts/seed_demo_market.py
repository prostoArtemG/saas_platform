"""
Idempotent seed script — creates the demo-market client inside saas_platform.

Run from the project root:
    python3 scripts/seed_demo_market.py

The script is safe to run multiple times:
  - client is created once (matched by slug="demo-market")
  - settings are upserted
  - products are inserted only if none exist yet
"""

import asyncio
import os
import sys

# Allow running as plain script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.models import Client, ClientSettings, Product

# ─────────────────────────────────────────────────────────────────────────────
DEMO_SLUG = "demo-market"

DEMO_PRODUCTS: list[dict] = [
    # ── Техніка ──────────────────────────────────────────────────────────────
    dict(
        group_name="Побутова техніка",
        category="Техніка",
        brand="Bosch",
        name="Пральна машина Bosch WAN28163UA",
        description=(
            "Вузька пральна машина з завантаженням 6 кг, класом прання A, "
            "функцією SpeedPerfect та захистом від дітей. Ідеальна для невеликих квартир."
        ),
        specs=(
            "Завантаження: 6 кг\n"
            "Клас прання: A\n"
            "Оберти: 1200 об/хв\n"
            "Глибина: 47 см\n"
            "Колір: Білий\n"
            "Гарантія: 2 роки"
        ),
        price=8999,
        old_price=10499,
        badge="Акція",
        is_available=True,
    ),
    dict(
        group_name="Побутова техніка",
        category="Техніка",
        brand="Samsung",
        name="Мікрохвильова піч Samsung MS23K3513AW",
        description=(
            "Соло мікрохвильова піч об'ємом 23 л. Функція розморожування, "
            "таймер до 99 хвилин, зручне колесо керування."
        ),
        specs=(
            "Об'єм: 23 л\n"
            "Потужність: 800 Вт\n"
            "Рівні потужності: 5\n"
            "Колір: Білий\n"
            "Розміри: 48.9 × 27.5 × 37.5 см"
        ),
        price=2499,
        old_price=None,
        badge="Топ продаж",
        is_available=True,
    ),
    dict(
        group_name="Побутова техніка",
        category="Техніка",
        brand="Philips",
        name="Електрочайник Philips HD9365/10",
        description=(
            "Електрочайник 1.7 л зі скляним корпусом та LED-підсвіткою. "
            "Фільтр від накипу, 360° основа, функція Keep Warm."
        ),
        specs=(
            "Об'єм: 1.7 л\n"
            "Потужність: 2200 Вт\n"
            "Матеріал: Скло + нержавіюча сталь\n"
            "Підсвітка: LED\n"
            "Гарантія: 2 роки"
        ),
        price=899,
        old_price=1199,
        badge="Акція",
        is_available=True,
    ),
    # ── Електроніка ──────────────────────────────────────────────────────────
    dict(
        group_name="Смартфони",
        category="Електроніка",
        brand="Samsung",
        name="Смартфон Samsung Galaxy A35 5G 128GB",
        description=(
            "6.6″ Super AMOLED, процесор Exynos 1380, потрійна камера 50 Мп, "
            "акумулятор 5000 мАг та захист IP67."
        ),
        specs=(
            "Дисплей: 6.6″ Super AMOLED\n"
            "Процесор: Exynos 1380\n"
            "ОЗП: 6 GB\n"
            "Пам'ять: 128 GB\n"
            "Камера: 50 + 8 + 5 Мп\n"
            "АКБ: 5000 мАг\n"
            "Захист: IP67"
        ),
        price=14999,
        old_price=None,
        badge="Новинка",
        is_available=True,
    ),
    dict(
        group_name="Аудіо",
        category="Електроніка",
        brand="Xiaomi",
        name="Навушники Xiaomi Redmi Buds 5 Pro",
        description=(
            "TWS-навушники з активним шумоподавленням до 52 дБ, "
            "адаптивним ANC та часом роботи до 38 годин з кейсом."
        ),
        specs=(
            "ANC: до 52 дБ\n"
            "Час роботи: 9 год (38 год з кейсом)\n"
            "Bluetooth: 5.4\n"
            "Захист: IP54\n"
            "Зарядка: USB-C"
        ),
        price=1299,
        old_price=None,
        badge="Топ продаж",
        is_available=True,
    ),
    dict(
        group_name="Планшети",
        category="Електроніка",
        brand="Xiaomi",
        name="Планшет Xiaomi Pad 6 128GB Wi-Fi",
        description=(
            "11″ 2.8K 144 Гц екран, Snapdragon 870, акумулятор 8840 мАг. "
            "Ідеальний для роботи, навчання та відео."
        ),
        specs=(
            "Дисплей: 11″ 2880×1800 144 Гц\n"
            "Процесор: Snapdragon 870\n"
            "ОЗП: 6 GB\n"
            "Пам'ять: 128 GB\n"
            "АКБ: 8840 мАг\n"
            "Зарядка: 33 Вт"
        ),
        price=12499,
        old_price=None,
        badge="Новинка",
        is_available=True,
    ),
    # ── Дім ──────────────────────────────────────────────────────────────────
    dict(
        group_name="Кухня",
        category="Дім",
        brand="Tefal",
        name="Сковорода Tefal Expertise 28 см",
        description=(
            "Титанове антипригарне покриття Prometal Pro (5 шарів), "
            "індикатор температури Thermo-Spot, сумісна з індукцією."
        ),
        specs=(
            "Діаметр: 28 см\n"
            "Покриття: Prometal Pro (5 шарів)\n"
            "Індикатор: Thermo-Spot\n"
            "Сумісна з: Газ, електро, індукція"
        ),
        price=799,
        old_price=999,
        badge="Акція",
        is_available=True,
    ),
    dict(
        group_name="Кухня",
        category="Дім",
        brand="Philips",
        name="Кавоварка Philips EP2220/10",
        description=(
            "Повністю автоматична кавомашина з керамічним кавомолком, "
            "регулюванням міцності та кнопкою AquaClean."
        ),
        specs=(
            "Тиск: 15 бар\n"
            "Об'єм бака: 1.8 л\n"
            "Кавомолок: Керамічний\n"
            "Рівні міцності: 5\n"
            "Потужність: 1500 Вт"
        ),
        price=8499,
        old_price=None,
        badge="Топ продаж",
        is_available=True,
    ),
    dict(
        group_name="Кухня",
        category="Дім",
        brand="Bosch",
        name="Занурювальний блендер Bosch MSM2610B",
        description=(
            "Потужність 600 Вт, ергономічна рукоятка, насадка для подрібнення. "
            "Ніжка з нержавіючої сталі — легко мити."
        ),
        specs=(
            "Потужність: 600 Вт\n"
            "Швидкості: 2\n"
            "Матеріал ніжки: Нержавіюча сталь\n"
            "Колір: Чорний"
        ),
        price=1299,
        old_price=None,
        badge="Новинка",
        is_available=True,
    ),
    # ── Зоотовари ─────────────────────────────────────────────────────────────
    dict(
        group_name="Корм",
        category="Зоотовари",
        brand="Royal Canin",
        name="Сухий корм Royal Canin Adult 10 кг",
        description=(
            "Повноцінний збалансований корм для дорослих кішок від 1 до 7 років. "
            "Підтримує здоров'я шкіри, вовни та нирок."
        ),
        specs=(
            "Вага: 10 кг\n"
            "Для: Кішок 1–7 років\n"
            "Протеїн: 30%\n"
            "Жири: 14%\n"
            "Тривалість: ≈40 днів"
        ),
        price=1899,
        old_price=None,
        badge="Топ продаж",
        is_available=True,
    ),
    dict(
        group_name="Аксесуари",
        category="Зоотовари",
        brand="Ferplast",
        name="Закритий лоток Ferplast Challenger",
        description=(
            "Великий закритий лоток для кота з вентиляційними отворами "
            "та вугільним фільтром запаху. Легке очищення."
        ),
        specs=(
            "Розміри: 58 × 45 × 46 см\n"
            "Фільтр: Вугільний\n"
            "Матеріал: ABS-пластик\n"
            "Колір: Бежевий / Коричневий"
        ),
        price=899,
        old_price=1099,
        badge="Акція",
        is_available=True,
    ),
    dict(
        group_name="Ліки та догляд",
        category="Зоотовари",
        brand="Beaphar",
        name="Нашийник від бліх Beaphar 65 см",
        description=(
            "Захист собаки від бліх, кліщів та вошей на 4 місяці. "
            "Активна речовина — диазинон. Безпечний для дорослих собак."
        ),
        specs=(
            "Довжина: 65 см\n"
            "Дія: 4 місяці\n"
            "Для: Собак\n"
            "Речовина: Диазинон"
        ),
        price=249,
        old_price=None,
        badge="Новинка",
        is_available=True,
    ),
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # ── 1. Upsert client ────────────────────────────────────────────────
        client = await session.scalar(
            select(Client).where(Client.slug == DEMO_SLUG)
        )
        if client is None:
            client = Client(
                business_name="Demo Market",
                slug=DEMO_SLUG,
                status="active",
                template_name="shop_bot",
            )
            session.add(client)
            await session.flush()  # get client.id
            print(f"[+] Client created: slug={DEMO_SLUG} id={client.id}")
        else:
            client.status = "active"
            client.template_name = "shop_bot"
            print(f"[~] Client exists: slug={DEMO_SLUG} id={client.id}")

        # ── 2. Upsert settings ──────────────────────────────────────────────
        cs = await session.get(ClientSettings, client.id)
        if cs is None:
            cs = ClientSettings(
                client_id=client.id,
                language="uk",
                currency="UAH",
                timezone="Europe/Kyiv",
                theme_name="navy_teal",
                shop_title="Demo Market",
                phone="+38 (099) 000-00-00",
                address="Україна",
            )
            session.add(cs)
            print(f"[+] Settings created for client id={client.id}")
        else:
            cs.theme_name = "navy_teal"
            cs.shop_title = "Demo Market"
            cs.phone = "+38 (099) 000-00-00"
            cs.address = "Україна"
            print(f"[~] Settings updated for client id={client.id}")

        # ── 3. Insert products (only if none exist yet) ─────────────────────
        existing_count: int = await session.scalar(
            select(func.count()).where(Product.client_id == client.id)
        )
        if existing_count == 0:
            for p in DEMO_PRODUCTS:
                session.add(Product(client_id=client.id, **p))
            print(f"[+] Inserted {len(DEMO_PRODUCTS)} demo products")
        else:
            print(
                f"[~] Products already exist ({existing_count}), skipping. "
                "Delete them manually to re-seed."
            )

        await session.commit()

    print(f"\n[✓] Done. Demo site: /site/{DEMO_SLUG}")
    print(f"    Production URL: https://{DEMO_SLUG}.shopplatform.app")


if __name__ == "__main__":
    asyncio.run(seed())
