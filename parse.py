import re

import re


def parse_universal_string(text: str):
    """
    Парсит строку для извлечения числа (звёзд), имени пользователя и количества (шт.).

    - Ищет число, за которым следует слово "звёзд".
    - Именем пользователя считается последнее слово в строке (может включать @).
    - Ищет количество в формате "N шт.", по умолчанию ставит 1.
    """
    cleaned_text = text.strip()

    number_match = re.search(r'(\d+)\s+звёзд', cleaned_text)
    if not number_match:
        return None, None, None

    number = int(number_match.group(1))

    words = cleaned_text.split()
    if not words:
        return None, None, None  # На случай пустой строки
    username = words[-1]

    amount_match = re.search(r'(\d+)\s+шт\.', cleaned_text)

    if amount_match:
        amount = int(amount_match.group(1))
    else:
        amount = 1

    return number, username, amount


