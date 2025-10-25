import asyncio
import time
from funpay.funpay_func import funpay_gifter, start_funpay_gifter, unverif_orders


def show_menu():
    """Отображает главное меню."""
    print("\n╔══════════════════════════════════════╗")
    print("║              Главное меню            ║")
    print("╠══════════════════════════════════════╣")
    print("║ 1. Запустить основной скрипт         ║")
    print("║ 2. Ид заказов                        ║")
    print("║ 3. Выход                             ║")
    print("╚══════════════════════════════════════╝")

def main():
    """Основная функция для отображения меню и обработки выбора пользователя."""
    print("👋 Добро пожаловать!")
    print("Это скрипт для работы с FunPay by Par.crypto.")
    print("❓ Для помощи просто введите 'help' в меню.")

    while True:
        show_menu()
        choice = input("Выберите опцию (1-3): ")

        if choice == '1':
            try:
                asyncio.run(start_funpay_gifter())
            except KeyboardInterrupt:
                print("\n✅ Скрипт остановлен пользователем. Возврат в главное меню...")
            except Exception as e:
                print(f"\n❌ Произошла непредвиденная ошибка в скрипте: {e}")
                time.sleep(5)
        elif choice == '2':
            asyncio.run(unverif_orders())
        elif choice == '3':
            print("\n👋 До свидания!")
            break
        elif choice.lower() == 'help':
             print("\n--- Помощь ---")
             print("Это простое меню для управления скриптом.")
             print("1. Запустить основной скрипт: запускает процесс обработки заказов FunPay.")
             print("2. Ид заказов: выведет неподтверждённые заказы.")
             print("3. Выход: завершает программу.")
             print("ДЛЯ РАБОТЫ СКРИПТА НЕ ЗАБЫВАЕМ ЗАПУСТИТЬ ФАЙЛ API.PY")
             input("\nНажмите Enter, чтобы вернуться в меню...")
        else:
            print("\n❗️ Неверный выбор. Пожалуйста, выберите опцию от 1 до 3.")
            time.sleep(2)


if __name__ == "__main__":
    main()