from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    BTN_EXIT_TEST,
    BTN_SUBSCRIPTIONS,
    admin_main_menu,
    client_test_menu,
)
from app.db import AsyncSessionLocal
from app.models import Client, ClientSettings, Product, ProductSpec, Subscription

router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


@router.message(CommandStart())
async def admin_start(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if arg.startswith("client_"):
        slug = arg[len("client_"):]
        async with AsyncSessionLocal() as session:
            client = await session.scalar(select(Client).where(Client.slug == slug))
        if client is None:
            await message.answer(
                f"❌ Клієнт з slug <code>{slug}</code> не знайдений.",
                parse_mode="HTML",
            )
            return
        await state.update_data(
            selected_client_id=client.id,
            selected_client_slug=client.slug,
        )
        await message.answer(
            f"🔧 <b>Тест-режим</b>: {client.business_name}\n"
            f"Slug: <code>{client.slug}</code>\n\n"
            f"Натисни ⬅️ Выйти из тест-режима щоб повернутися до панелі адміна.",
            parse_mode="HTML",
            reply_markup=client_test_menu(),
        )
        return

    # Normal admin /start — clear any active test mode
    await state.clear()
    await message.answer(
        "Привет, админ платформы! 👋\nВыбери раздел:",
        reply_markup=admin_main_menu(),
    )


@router.message(F.text == BTN_EXIT_TEST)
async def exit_test_mode(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Вышел из тест-режима. Панель администратора:",
        reply_markup=admin_main_menu(),
    )


@router.message(F.text == BTN_SUBSCRIPTIONS)
async def list_subscriptions(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Subscription).order_by(Subscription.id)
        )
        subs = result.scalars().all()

    if not subs:
        await message.answer("🧾 Подписок пока нет.")
        return

    lines = ["🧾 <b>Подписки:</b>"]
    for s in subs:
        expires = s.expires_at.strftime("%Y-%m-%d") if s.expires_at else "—"
        lines.append(
            f"#{s.id} • client={s.client_id} • plan={s.plan_id} • "
            f"{s.status} • до {expires}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# /seed_demo_market — одноразова команда для запуску демо-клієнта з Railway
# ─────────────────────────────────────────────────────────────────────────────
_DEMO_SLUG = "demo-market"
_DEMO_PRODUCTS: list[dict] = [
    # Техніка
    dict(group_name="Побутова техніка", category="Техніка", brand="Bosch",
         name="Пральна машина Bosch WAN28163UA",
         description="Вузька пральна машина 6 кг, клас A, SpeedPerfect, захист від дітей.",
         specs="Завантаження: 6 кг\nКлас прання: A\nОберти: 1200 об/хв\nГлибина: 47 см\nГарантія: 2 роки",
         price=8999, old_price=10499, badge="Акція", is_available=True),
    dict(group_name="Побутова техніка", category="Техніка", brand="Samsung",
         name="Мікрохвильова піч Samsung MS23K3513AW",
         description="Соло мікрохвильова піч 23 л, 800 Вт, 5 рівнів потужності.",
         specs="Об'єм: 23 л\nПотужність: 800 Вт\nРівні: 5\nКолір: Білий",
         price=2499, old_price=None, badge="Топ продаж", is_available=True),
    dict(group_name="Побутова техніка", category="Техніка", brand="Philips",
         name="Електрочайник Philips HD9365/10",
         description="Скляний чайник 1.7 л, LED-підсвітка, фільтр від накипу.",
         specs="Об'єм: 1.7 л\nПотужність: 2200 Вт\nМатеріал: Скло + нержавіюча сталь",
         price=899, old_price=1199, badge="Акція", is_available=True),
    # Електроніка
    dict(group_name="Смартфони", category="Електроніка", brand="Samsung",
         name="Смартфон Samsung Galaxy A35 5G 128GB",
         description="6.6″ Super AMOLED, Exynos 1380, камера 50 Мп, АКБ 5000 мАг, IP67.",
         specs="Дисплей: 6.6″ Super AMOLED\nПроцесор: Exynos 1380\nОЗП: 6 GB\nПам'ять: 128 GB\nАКБ: 5000 мАг",
         price=14999, old_price=None, badge="Новинка", is_available=True),
    dict(group_name="Аудіо", category="Електроніка", brand="Xiaomi",
         name="Навушники Xiaomi Redmi Buds 5 Pro",
         description="TWS ANC до 52 дБ, Bluetooth 5.4, IP54, 38 год з кейсом.",
         specs="ANC: до 52 дБ\nЧас роботи: 9 год (38 год з кейсом)\nBluetooth: 5.4\nЗахист: IP54",
         price=1299, old_price=None, badge="Топ продаж", is_available=True),
    dict(group_name="Планшети", category="Електроніка", brand="Xiaomi",
         name="Планшет Xiaomi Pad 6 128GB Wi-Fi",
         description="11″ 2.8K 144 Гц, Snapdragon 870, АКБ 8840 мАг.",
         specs="Дисплей: 11″ 2880×1800 144 Гц\nПроцесор: Snapdragon 870\nОЗП: 6 GB\nПам'ять: 128 GB",
         price=12499, old_price=None, badge="Новинка", is_available=True),
    # Дім
    dict(group_name="Кухня", category="Дім", brand="Tefal",
         name="Сковорода Tefal Expertise 28 см",
         description="Titanova Prometal Pro (5 шарів), Thermo-Spot, індукція.",
         specs="Діаметр: 28 см\nПокриття: Prometal Pro\nІндикатор: Thermo-Spot\nСумісна з: індукція",
         price=799, old_price=999, badge="Акція", is_available=True),
    dict(group_name="Кухня", category="Дім", brand="Philips",
         name="Кавоварка Philips EP2220/10",
         description="Автоматична кавомашина, керамічний кавомолок, AquaClean, 5 рівнів.",
         specs="Тиск: 15 бар\nОб'єм бака: 1.8 л\nКавомолок: Керамічний\nПотужність: 1500 Вт",
         price=8499, old_price=None, badge="Топ продаж", is_available=True),
    dict(group_name="Кухня", category="Дім", brand="Bosch",
         name="Занурювальний блендер Bosch MSM2610B",
         description="600 Вт, 2 швидкості, ніжка з нержавіючої сталі.",
         specs="Потужність: 600 Вт\nШвидкості: 2\nМатеріал ніжки: Нержавіюча сталь",
         price=1299, old_price=None, badge="Новинка", is_available=True),
    # Зоотовари
    dict(group_name="Корм", category="Зоотовари", brand="Royal Canin",
         name="Сухий корм Royal Canin Adult 10 кг",
         description="Корм для котів 1–7 років, протеїн 30%, жири 14%.",
         specs="Вага: 10 кг\nДля: Кішок 1–7 років\nПротеїн: 30%\nЖири: 14%",
         price=1899, old_price=None, badge="Топ продаж", is_available=True),
    dict(group_name="Аксесуари", category="Зоотовари", brand="Ferplast",
         name="Закритий лоток Ferplast Challenger",
         description="58×45×46 см, вугільний фільтр, ABS-пластик.",
         specs="Розміри: 58 × 45 × 46 см\nФільтр: Вугільний\nМатеріал: ABS-пластик",
         price=899, old_price=1099, badge="Акція", is_available=True),
    dict(group_name="Ліки та догляд", category="Зоотовари", brand="Beaphar",
         name="Нашийник від бліх Beaphar 65 см",
         description="Захист від бліх, кліщів та вошей на 4 місяці. Для собак.",
         specs="Довжина: 65 см\nДія: 4 місяці\nДля: Собак\nРечовина: Диазинон",
         price=249, old_price=None, badge="Новинка", is_available=True),
]


@router.message(Command("seed_demo_market"))
async def cmd_seed_demo_market(message: Message) -> None:
    """Create or refresh the demo-market client, settings and demo products."""
    await message.answer("⏳ Запускаю seed demo-market…")
    log: list[str] = []

    async with AsyncSessionLocal() as session:
        # 1. Upsert client
        client = await session.scalar(
            select(Client).where(Client.slug == _DEMO_SLUG)
        )
        if client is None:
            client = Client(
                business_name="Demo Market",
                slug=_DEMO_SLUG,
                status="active",
                template_name="shop_bot",
            )
            session.add(client)
            await session.flush()
            log.append(f"✅ Клієнт <code>{_DEMO_SLUG}</code> створений (id={client.id})")
        else:
            client.status = "active"
            client.template_name = "shop_bot"
            log.append(f"♻️ Клієнт <code>{_DEMO_SLUG}</code> оновлений (id={client.id})")

        # 2. Upsert settings
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
            log.append("✅ Налаштування створені")
        else:
            cs.theme_name = "navy_teal"
            cs.shop_title = "Demo Market"
            cs.phone = "+38 (099) 000-00-00"
            cs.address = "Україна"
            log.append("♻️ Налаштування оновлені")

        # 3. Products — insert only if none exist
        existing_count: int = await session.scalar(
            select(func.count()).where(Product.client_id == client.id)
        )
        if existing_count == 0:
            for p in _DEMO_PRODUCTS:
                session.add(Product(client_id=client.id, **p))
            log.append(f"✅ Додано {len(_DEMO_PRODUCTS)} демо-товарів")
        else:
            log.append(
                f"ℹ️ Товари вже є ({existing_count} шт.), пропускаю"
            )

        await session.commit()

    text = "\n".join(log) + (
        f"\n\n🌐 https://{_DEMO_SLUG}.shopplatform.app"
        f"\n🌐 https://shopplatform.app/site/{_DEMO_SLUG}"
    )
    await message.answer(text, parse_mode="HTML")


# ─────────────────────────────────────────────────────────────────────────────
# /seed_demo_auto — demo-auto клієнт для шаблону auto_market
# ─────────────────────────────────────────────────────────────────────────────
_DEMO_AUTO_SLUG = "demo-auto"

_DEMO_AUTO_CARS: list[dict] = [
    dict(
        brand="Toyota", name="Camry 2.5 Hybrid", category="Седан",
        description="Надійний японський бізнес-седан з гібридним двигуном 2.5 л. Один власник, сервісна книга.",
        price=28500, old_price=31000, badge="Знижка", is_available=True,
        image_url="https://images.unsplash.com/photo-1621007947382-bb3c3994e3fb?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2021"),("Пробіг","42 000 км"),("Паливо","Гібрид"),("Коробка","Автомат"),("Двигун","2.5 л / 178 к.с."),("Місто","Київ")],
    ),
    dict(
        brand="BMW", name="X5 xDrive30d M Sport", category="SUV",
        description="Преміум позашляховик BMW X5 у комплектації M Sport. Панорамний дах, Harman Kardon, розмитнений.",
        price=65000, old_price=None, badge="Преміум", is_available=True,
        image_url="https://images.unsplash.com/photo-1555215695-3004980ad54e?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2022"),("Пробіг","38 500 км"),("Паливо","Дизель"),("Коробка","Автомат"),("Двигун","3.0 л / 286 к.с."),("Місто","Дніпро")],
    ),
    dict(
        brand="Audi", name="Q7 3.0 TDI quattro", category="SUV",
        description="7-місний преміум-SUV з дизельним двигуном 3.0 TDI і повним приводом quattro. Перший власник.",
        price=72000, old_price=76500, badge="Знижка", is_available=True,
        image_url="https://images.unsplash.com/photo-1606664515524-ed2f786a0bd6?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2020"),("Пробіг","61 000 км"),("Паливо","Дизель"),("Коробка","Автомат"),("Двигун","3.0 л / 249 к.с."),("Місто","Львів")],
    ),
    dict(
        brand="Volkswagen", name="Passat B8 2.0 TDI Variant", category="Університ",
        description="Практичний VW Passat B8 Variant. Великий багажник, DSG7, активний круїз-контроль.",
        price=19800, old_price=None, badge=None, is_available=True,
        image_url="https://images.unsplash.com/photo-1590362891991-f776e747a588?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2019"),("Пробіг","88 000 км"),("Паливо","Дизель"),("Коробка","Автомат"),("Двигун","2.0 л / 150 к.с."),("Місто","Харків")],
    ),
    dict(
        brand="Hyundai", name="Tucson 1.6 T-GDi 4WD", category="SUV",
        description="Стильний Hyundai Tucson нового покоління. Кругова камера, підігрів керма, CarPlay.",
        price=26500, old_price=28000, badge="Хіт", is_available=True,
        image_url="https://images.unsplash.com/photo-1617469767280-0cebb3967d73?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2022"),("Пробіг","29 000 км"),("Паливо","Бензин"),("Коробка","Автомат"),("Двигун","1.6 л / 180 к.с."),("Місто","Одеса")],
    ),
    dict(
        brand="Mercedes-Benz", name="E 220d AMG Line", category="Седан",
        description="Бізнес-седан Mercedes E-Class AMG Line. Пневматика, Burmester аудіо, проекційний дисплей.",
        price=47000, old_price=None, badge="Преміум", is_available=True,
        image_url="https://images.unsplash.com/photo-1618843479313-40f8afb4b4d8?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2021"),("Пробіг","54 000 км"),("Паливо","Дизель"),("Коробка","Автомат"),("Двигун","2.0 л / 194 к.с."),("Місто","Київ")],
    ),
    dict(
        brand="Nissan", name="Leaf e+ 62 kWh", category="Електро",
        description="Повністю електричний Nissan Leaf 62 кВт·год. Запас ходу 385 км, швидка зарядка CHAdeMO.",
        price=18500, old_price=21000, badge="Акція", is_available=True,
        image_url="https://images.unsplash.com/photo-1593941707882-a5bba14938c7?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2020"),("Пробіг","47 000 км"),("Паливо","Електро"),("Коробка","Автомат"),("Двигун","62 кВт·год / 217 к.с."),("Місто","Запоріжжя")],
    ),
    dict(
        brand="Skoda", name="Octavia A7 1.6 TDI Combi", category="Університ",
        description="Надійна Skoda Octavia A7 Combi. Велика навігація, підігрів сидінь, один власник, без аварій.",
        price=14900, old_price=None, badge=None, is_available=True,
        image_url="https://images.unsplash.com/photo-1596625618196-832a8077e24a?w=800&auto=format&fit=crop",
        spec_rows=[("Рік","2018"),("Пробіг","112 000 км"),("Паливо","Дизель"),("Коробка","Механіка"),("Двигун","1.6 л / 115 к.с."),("Місто","Вінниця")],
    ),
]


@router.message(Command("seed_demo_auto"))
async def cmd_seed_demo_auto(message: Message) -> None:
    """Create or refresh the demo-auto client with 8 cars for the auto_market template."""
    await message.answer("⏳ Запускаю seed demo-auto…")
    log: list[str] = []

    async with AsyncSessionLocal() as session:
        # 1. Upsert client
        client = await session.scalar(
            select(Client).where(Client.slug == _DEMO_AUTO_SLUG)
        )
        if client is None:
            client = Client(
                business_name="Auto Market Demo",
                slug=_DEMO_AUTO_SLUG,
                status="active",
                template_name="auto_market",
            )
            session.add(client)
            await session.flush()
            log.append(f"✅ Клієнт <code>{_DEMO_AUTO_SLUG}</code> створений (id={client.id})")
        else:
            client.status = "active"
            client.template_name = "auto_market"
            client.business_name = "Auto Market Demo"
            log.append(f"♻️ Клієнт <code>{_DEMO_AUTO_SLUG}</code> оновлений (id={client.id})")

        # 2. Upsert settings
        cs = await session.get(ClientSettings, client.id)
        if cs is None:
            cs = ClientSettings(
                client_id=client.id,
                language="uk",
                currency="USD",
                timezone="Europe/Kyiv",
                theme_name="auto_dark",
                shop_title="Auto Market Demo",
                phone="+38 (099) 111-22-33",
                address="Україна",
            )
            session.add(cs)
            log.append("✅ Налаштування створені")
        else:
            cs.theme_name = "auto_dark"
            cs.shop_title = "Auto Market Demo"
            cs.phone = "+38 (099) 111-22-33"
            cs.address = "Україна"
            log.append("♻️ Налаштування оновлені")

        # 3. Products + ProductSpec — insert only if none exist
        existing_count: int = await session.scalar(
            select(func.count()).where(Product.client_id == client.id)
        ) or 0

        if existing_count == 0:
            for car in _DEMO_AUTO_CARS:
                spec_rows = car.pop("spec_rows")
                product = Product(client_id=client.id, **car)
                session.add(product)
                await session.flush()
                for spec_name, spec_value in spec_rows:
                    session.add(ProductSpec(
                        product_id=product.id,
                        client_id=client.id,
                        name=spec_name,
                        value=spec_value,
                    ))
            log.append(f"✅ Додано {len(_DEMO_AUTO_CARS)} авто з характеристиками")
        else:
            log.append(f"ℹ️ Товари вже є ({existing_count} шт.), пропускаю")

        await session.commit()

    text = "\n".join(log) + (
        f"\n\n🌐 https://{_DEMO_AUTO_SLUG}.shopplatform.app"
        f"\n🌐 https://shopplatform.app/site/{_DEMO_AUTO_SLUG}"
    )
    await message.answer(text, parse_mode="HTML")
