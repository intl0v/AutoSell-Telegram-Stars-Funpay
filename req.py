import subprocess
import json


def buy_stars(login: str, quantity: int, hide_sender: int = 0):
    """
    Отправляет запрос на сервер, исполняя curl команду в консоли.
    """
    api_url = "http://localhost:80/buy"

    payload = {
        "login": login,
        "quantity": quantity,
        "hide_sender": hide_sender
    }
    payload_str = json.dumps(payload)

    command = [
        "curl",
        "-X", "POST",
        api_url,
        "-H", "Content-Type: application/json",
        "-d", payload_str
    ]

    print(f"🚀 Выполняю команду в консоли: {' '.join(command)}")

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, shell=True, encoding='utf-8')

        print("✅ Ответ от сервера:")
        print(result.stdout)

    except subprocess.CalledProcessError as e:
        print(e.stdout)
        print("--- Вывод stderr ---")
        print(e.stderr)
    except FileNotFoundError:
        print("❌ ОШИБКА: Команда 'curl' не найдена. Убедись, что curl установлен и доступен в PATH Windows.")
    except Exception as e:
        print(f"❌ Произошла непредвиденная ошибка: {e}")


