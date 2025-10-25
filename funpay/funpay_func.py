import asyncio

from FunPayAPI.account import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewMessageEvent
from FunPayAPI.types import ChatShortcut
from data import FUNPAY_KEY, send_text
from parse import parse_universal_string
from models import SessionLocal, User
from req import buy_stars

# Инициализация аккаунта FunPay
account = Account(golden_key=FUNPAY_KEY)
account.get()
updater = Runner(account)

# Множества для отслеживания обработанных заказов и чатов
processed_orders = set()
responded_chats = set()


def get_db():
    """Возвращает новый сеанс базы данных."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def user_exists(db, username: str) -> bool:
    """Проверяет, есть ли пользователь уже в базе данных."""
    return db.query(User).filter(User.username == username).first() is not None


def add_user(db, username: str):
    """Добавляет нового пользователя в базу данных."""
    new_user = User(username=username)
    db.add(new_user)
    db.commit()


def blocking_events_handler():
    """
    Синхронный обработчик событий, который блокируется при ожидании новых событий.
    Должен выполняться в отдельном потоке.
    """
    for event in updater.listen():
        if isinstance(event, NewMessageEvent):
            if event.message.author_id != account.id:
                chat_id = event.message.chat_id

                # Проверяем, отвечали ли уже в этот чат
                if chat_id not in responded_chats:
                    print(f"Новое сообщение от {event.message.author}: {event.message.text}")
                    # Отправляем ответное сообщение
                    account.send_message(chat_id=chat_id, text=send_text)
                    print(f"Отправлен ответ в чат {chat_id}.")
                    # Добавляем чат в список уже отвеченных
                    responded_chats.add(chat_id)
                else:
                    print(f"Сообщение от {event.message.author} в чате {chat_id} проигнорировано (уже отвечали)")


async def events_handler():
    """
    Асинхронная обертка для запуска блокирующего обработчика событий в ThreadPoolExecutor.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, blocking_events_handler)


async def funpay_gifter():
    """
    Основная логика обработки оплаченных заказов.
    """
    while True:
        try:
            a = account.get()
            orders = a.get_sells(state='paid')
            db = next(get_db())

            if orders and orders[1]:
                for my_order in reversed(orders[1]):
                    id_sale = my_order.id

                    # Двойная защита от повторной обработки заказа
                    if id_sale in processed_orders or user_exists(db, id_sale):
                        print(f'Заказ {id_sale} уже обработан или находится в обработке.')
                        continue

                    # Добавляем заказ в обрабатываемые сразу
                    processed_orders.add(id_sale)

                    try:
                        print(f"Начинаю обработку заказа #{id_sale}")
                        print(my_order.description)
                        amount, buyer_name, count = parse_universal_string(my_order.description)
                        print(f"Извлечено: amount={amount}, buyer_name={buyer_name}, count={count}")
                        user_name = my_order.buyer_username
                        print(user_name)
                        chat_with_buyer: ChatShortcut = account.get_chat_by_name(name=str(user_name), make_request=True)
                        print(chat_with_buyer)

                        chat_id = chat_with_buyer.id
                        try:
                            amount = amount * count
                        except TypeError:
                            print(
                                f"Не удалось рассчитать сумму для заказа #{id_sale} (amount={amount}, count={count}).")
                            continue
                        if float(amount) >10000:
                            continue
                        print(f"Отправляю {amount} звёзд пользователю {buyer_name}")
                        a = buy_stars(login=buyer_name, quantity=amount)
                        await asyncio.sleep(45)
                        if a is not None:
                            print(f"✅ Успешно отправлены звёзды для заказа #{id_sale}")
                            print(a)
                        else:
                            print(f"❌ Возможно подарок не отправился для заказа #{id_sale}")

                        account.send_message(chat_id=chat_id,
                                             text='⭐️Звёзды уже на вашем аккаунте!⭐️\n\n ❗️Пожалуйста, подтвердите заказ.\n\n Так же будет очень приятно если оставите положительный отзыв за оперативность.')

                        print(f"✅ Заказ #{id_sale} успешно обработан")

                    except Exception as e:
                        print(f"❌ Произошла ошибка при обработке заказа #{id_sale}: {e}")
                    finally:
                        print(f"--- Завершаю работу с заказом #{id_sale}, добавляю в БД. ---")
                        add_user(db, id_sale)
            else:
                print("Новых заказов нет.")
        except Exception as e:
            print(f"Ошибка в основном цикле: {e}")
        await asyncio.sleep(60)


async def unverif_orders():
    a = account.get()
    orders = a.get_sells(state='paid')
    db = next(get_db())

    for my_order in reversed(orders[1]):
        ids = my_order.id
        print(ids)


async def start_funpay_gifter():
    """
    Запускает обе задачи: обработчик сообщений и обработчик заказов.
    """

    listener_task = asyncio.create_task(events_handler())
    gifter_task = asyncio.create_task(funpay_gifter())

    await asyncio.gather(listener_task, gifter_task)

