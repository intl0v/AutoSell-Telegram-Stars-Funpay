import asyncio
import base64
import json
import re
from typing import List, Dict, Any

import aiohttp
import tonutils.client
import tonutils.wallet
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from tonsdk.boc import Cell
import time
import os
from dotenv import load_dotenv
import ssl
import certifi

load_dotenv()

# --- Константы и конфигурация ---
FRAGMENT_HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}
fragment_lock = asyncio.Lock()

TONAPI_KEY = os.getenv("TONAPI_KEY", "")
MNEMONIC = os.getenv("MNEMONIC", "").split()
FRAGMENT_HASH = os.getenv("FRAGMENT_HASH", "")
try:
    FRAGMENT_COOKIES = json.loads(os.getenv("FRAGMENT_COOKIES", "{}"))
except Exception:
    FRAGMENT_COOKIES = {}

app = FastAPI()

async def get_event(event_id: str):
    async with aiohttp.ClientSession() as session:
        url = f"https://tonapi.io/v2/events/{event_id}"
        headers = {
            "accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.5"
        }
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                return None


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return text.strip()


def clean_and_filter(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: clean_and_filter(v) for k, v in obj.items() if not k.endswith("_html")}
    if isinstance(obj, list):
        return [clean_and_filter(v) for v in obj]
    if isinstance(obj, str):
        return strip_html_tags(obj)
    return obj


def decode_payload(payload: str) -> str:
    if not payload:
        return ""
    try:
        payload += "=" * (-len(payload) % 4)
        cell = Cell.one_from_boc(base64.b64decode(payload))
        sl = cell.begin_parse()
        return sl.read_string().strip()
    except:
        return ""


def decode_payload_b64_premium(payload: str) -> str:
    try:
        payload += "=" * (-len(payload) % 4)
        raw_bytes = base64.b64decode(payload)
        decoded = raw_bytes.decode('utf-8', errors='ignore')
        filtered = ''.join(ch for ch in decoded if 32 <= ord(ch) <= 126 or ch in '\r\n')
        filtered = re.sub(r'\r\n?', '\n', filtered)
        filtered = re.sub(r'[ ]*\n+', '\n\n', filtered).strip()
        idx = filtered.find("Telegram Premium")
        if idx != -1:
            filtered = filtered[idx:]
        return filtered
    except Exception as e:
        return f"decode_error: {e}"


class WalletManager:
    def __init__(self, api_key: str, mnemonic: List[str]):
        self.api_key = api_key
        self.mnemonic = mnemonic
        self.ton_client = None
        self.wallet = None

    async def __aenter__(self):
        await self.init_wallet()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def init_wallet(self):
        self.ton_client = tonutils.client.TonapiClient(api_key=self.api_key)
        self.wallet, _, _, _ = tonutils.wallet.WalletV4R2.from_mnemonic(self.ton_client, mnemonic=self.mnemonic)

    async def _get_wallet_address_str(self) -> str:
        addr_raw = str(self.wallet.address)
        return addr_raw.replace('Address<', '').replace('>', '')

    async def _fetch_seqno_toncenter_v3(self, address: str) -> int | None:
        url = f"https://toncenter.com/api/v3/wallet?address={address}"
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return int(data.get("seqno")) if data and "seqno" in data else None
        except Exception:
            return None

    async def _fetch_seqno_tonhub_v4(self, address: str) -> int | None:
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://mainnet-v4.tonhubapi.com/block/latest") as r1:
                    if r1.status != 200:
                        return None
                    latest = await r1.json()
                    mc = latest.get("last") or latest.get("seqno")
                    if isinstance(mc, dict):
                        seqno_block = mc.get("seqno")
                    else:
                        seqno_block = mc
                    if not seqno_block:
                        return None
                async with session.get(
                        f"https://mainnet-v4.tonhubapi.com/block/{seqno_block}/{address}/run/seqno"
                ) as r2:
                    if r2.status != 200:
                        return None
                    data = await r2.json()
                    if data and "result" in data and isinstance(data["result"], list) and data["result"]:
                        val = data["result"][0]
                        if isinstance(val, dict) and val.get("type") == "int":
                            return int(val.get("value"))
            return None
        except Exception:
            return None

    async def _get_seqno(self, address: str) -> int | None:
        seqno = await self._fetch_seqno_toncenter_v3(address)
        if seqno is not None:
            return seqno
        return await self._fetch_seqno_tonhub_v4(address)

    async def _wait_for_seqno_increase(self, address: str, previous_seqno: int, timeout_seconds: int = 60,
                                       poll_interval: float = 1.0) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            await asyncio.sleep(poll_interval)
            current = await self._get_seqno(address)
            if current is not None and current > previous_seqno:
                return True
        return False

    async def transfer(self, destination: str, amount_nano: int, payload: str,
                       ttl_seconds: int = 60, max_retries: int = 2) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "address": destination,
            "amount": amount_nano,
            "success": False,
            "tx_hash": None,
            "error": None,
            "attempts": 0,
        }
        try:
            if payload and ("Telegram Premium" in payload or re.fullmatch(r"[A-Za-z0-9+/=_-]+", payload) is None):
                body_obj: Any = payload
            else:
                body_obj: Any = decode_payload(payload)
        except Exception:
            body_obj = decode_payload(payload)

        try:
            wallet_address = await self._get_wallet_address_str()
            previous_seqno = await self._get_seqno(wallet_address) or 0

            attempts = 0
            last_error: str | None = None
            while attempts <= max_retries:
                attempts += 1
                result["attempts"] = attempts
                try:
                    valid_until = int(time.time()) + ttl_seconds
                    try:
                        tx_hash = await self.wallet.transfer(
                            destination=destination,
                            amount=amount_nano,
                            body=body_obj,
                            valid_until=valid_until,
                        )
                    except TypeError:
                        tx_hash = await self.wallet.transfer(
                            destination=destination,
                            amount=amount_nano,
                            body=body_obj,
                        )

                    result["tx_hash"] = tx_hash

                    confirmed = await self._wait_for_seqno_increase(
                        wallet_address, previous_seqno, timeout_seconds=ttl_seconds
                    )
                    if confirmed:
                        result["success"] = True
                        last_error = None
                        break
                    else:
                        last_error = "seqno_not_increased_within_ttl"
                        current_seqno = await self._get_seqno(wallet_address)
                        previous_seqno = current_seqno if current_seqno is not None else previous_seqno
                        continue

                except Exception as e:
                    last_error = str(e)
                    await asyncio.sleep(1.0)
                    continue

            if not result["success"] and last_error:
                result["error"] = last_error

        except Exception as e:
            result["error"] = str(e)

        return result

    async def close(self):
        if self.ton_client and hasattr(self.ton_client, "_session"):
            await self.ton_client._session.close()


async def check_transaction_periodically(event_id: str, tag: str, quantity: int, interval_seconds: int = 15,
                                         max_attempts: int = 240, max_send_attempts: int = 5) -> Dict[str, Any]:
    send_attempts = 0
    while send_attempts < max_send_attempts:
        attempt = 0
        while attempt < max_attempts:
            result = await get_event(event_id)
            if result is not None:
                if "actions" in result and len(result["actions"]) > 0:
                    action = result["actions"][0]
                    if action.get("status") == "ok":
                        return result
                    elif action.get("status") == "failed":
                        return result
                elif "error" in result and result["error"] == "entity not found":
                    send_attempts += 1
                    if send_attempts < max_send_attempts:
                        send_result = await buy_stars_logic_internal(tag, quantity)
                        if send_result.get("transfers") and send_result["transfers"][0].get("event_id"):
                            event_id = send_result["transfers"][0]["event_id"]
                            break
                        else:
                            return None
                    else:
                        return None

            attempt += 1
            await asyncio.sleep(interval_seconds)
        else:
            return None

    return None


async def buy_stars_logic_internal(login: str, quantity: int, hide_sender: int = 0) -> Dict[str, Any]:
    async with fragment_lock:
        async with WalletManager(TONAPI_KEY, MNEMONIC) as wm:
            results: Dict[str, Any] = {}
            async with aiohttp.ClientSession(cookies=FRAGMENT_COOKIES, headers=FRAGMENT_HEADERS) as session:
                init_data = {"mode": "new", "lv": "false", "dh": "1", "method": "updateStarsBuyState"}
                async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=init_data) as resp:
                    results["updateStarsBuyState"] = clean_and_filter(await resp.json())

                search_data = {"query": login, "quantity": str(quantity), "method": "searchStarsRecipient"}
                async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=search_data) as resp:
                    search_resp = await resp.json()
                results["searchStarsRecipient"] = clean_and_filter(search_resp)

                if "found" not in search_resp:
                    return results

                price_data = {"stars": "", "quantity": str(quantity), "method": "updateStarsPrices"}
                async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=price_data) as resp:
                    results["updateStarsPrices"] = clean_and_filter(await resp.json())

                recipient = search_resp["found"]["recipient"]
                buy_data = {"recipient": recipient, "quantity": str(quantity), "method": "initBuyStarsRequest"}
                async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=buy_data) as resp:
                    buy_resp = await resp.json()
                results["initBuyStarsRequest"] = clean_and_filter(buy_resp)

                if not buy_resp.get("req_id"):
                    return results

                req_id = buy_resp["req_id"]
                device = {"platform": "browser", "appName": "telegram-wallet", "appVersion": "1",
                          "maxProtocolVersion": 2, "features": ["SendTransaction",
                                                                {"name": "SendTransaction", "maxMessages": 4,
                                                                 "extraCurrencySupported": True}]}
                link_data = {"account": json.dumps(""), "device": json.dumps(device), "transaction": "1", "id": req_id,
                             "show_sender": str(hide_sender), "method": "getBuyStarsLink"}
                async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=link_data) as resp:
                    link_resp = await resp.json()
                results["getBuyStarsLink"] = clean_and_filter(link_resp)

                if not link_resp.get("ok") or "transaction" not in link_resp:
                    return results

                transfers = []
                for msg in link_resp["transaction"].get("messages", []):
                    addr = msg["address"]
                    amt = float(msg["amount"]) / 1e9
                    payload = msg.get("payload", "")
                    transfer_result = await wm.transfer(addr, amt, payload)
                    transfers.append(transfer_result)

                results["transfers"] = transfers
                total_nano = sum(t["amount"] for t in transfers if t.get("amount") is not None)
                results["total_ton"] = str(total_nano / 1e9)
                results["tx_hash"] = transfers[0].get("tx_hash") if transfers else None

            return results


async def buy_stars_logic(login: str, quantity: int, hide_sender: int = 0, interval_seconds: int = 10,
                          max_attempts: int = 360, max_send_attempts: int = 5) -> Dict[str, Any]:
    if quantity <= 5000:
        return await _send_stars_single_batch(login, quantity, hide_sender, interval_seconds, max_attempts,
                                              max_send_attempts)
    else:
        return await _send_stars_multiple_batches(login, quantity, hide_sender, interval_seconds, max_attempts,
                                                  max_send_attempts)


async def _send_stars_single_batch(login: str, quantity: int, hide_sender: int = 0, interval_seconds: int = 10,
                                   max_attempts: int = 360, max_send_attempts: int = 5) -> Dict[str, Any]:
    send_result = await buy_stars_logic_internal(login, quantity, hide_sender)
    results = send_result.copy()

    if not send_result.get("transfers"):
        return results

    event_id = send_result["tx_hash"]
    transaction_result = await check_transaction_periodically(event_id, login, quantity, interval_seconds, max_attempts,
                                                              max_send_attempts)

    results["transaction_status"] = transaction_result
    if transaction_result:
        results["status"] = transaction_result.get("actions", [{}])[0].get("status", "unknown")
    else:
        results["status"] = "failed"

    return results


async def _send_stars_multiple_batches(login: str, quantity: int, hide_sender: int = 0, interval_seconds: int = 10,
                                       max_attempts: int = 360, max_send_attempts: int = 5) -> Dict[str, Any]:
    BATCH_SIZE_1 = 5000
    BATCH_SIZE_2 = 5050
    batches = []
    remaining = quantity
    while remaining > 0:
        if remaining < 5050:
            batch_size = min(BATCH_SIZE_2, remaining)
        else:
            batch_size = min(BATCH_SIZE_1, remaining)
        batches.append(batch_size)
        remaining -= batch_size

    all_results = []
    all_transfers = []
    total_ton_sent = 0

    for i, batch_quantity in enumerate(batches):
        batch_num = i + 1
        total_batches = len(batches)

        batch_result = await buy_stars_logic_internal(login, batch_quantity, hide_sender)

        if batch_result.get("transfers"):
            all_transfers.extend(batch_result["transfers"])
            total_ton_sent += float(batch_result.get("total_ton", 0))

            if batch_result.get("tx_hash"):
                event_id = batch_result["tx_hash"]
                transaction_result = await check_transaction_periodically(
                    event_id, login, batch_quantity, interval_seconds, max_attempts, max_send_attempts
                )

                batch_result["transaction_status"] = transaction_result
                if transaction_result:
                    batch_result["status"] = transaction_result.get("actions", [{}])[0].get("status", "unknown")
                else:
                    batch_result["status"] = "failed"
            else:
                batch_result["status"] = "failed"
        else:
            batch_result["status"] = "failed"

        batch_result["batch_info"] = {
            "batch_number": batch_num,
            "total_batches": total_batches,
            "batch_quantity": batch_quantity
        }

        all_results.append(batch_result)

        if i < len(batches) - 1:
            await asyncio.sleep(2)

    final_result = {
        "status": "ok" if all(r.get("status") == "ok" for r in all_results) else "failed",
        "transfers": all_transfers,
        "total_ton": str(total_ton_sent),
        "batches": all_results,
        "total_quantity": quantity,
        "total_batches": len(batches),
        "batch_size": BATCH_SIZE_1
    }

    if all_results and all_results[0].get("tx_hash"):
        final_result["tx_hash"] = all_results[0]["tx_hash"]

    return final_result


async def check_transaction_simple(event_id: str, interval_seconds: int = 10, max_attempts: int = 360) -> Dict[str, Any] | None:
    attempt = 0
    while attempt < max_attempts:
        result = await get_event(event_id)
        if result is not None:
            if "actions" in result and len(result["actions"]) > 0:
                action = result["actions"][0]
                if action.get("status") in ("ok", "failed"):
                    return result
            elif "error" in result and result["error"] == "entity not found":
                # keep polling without resending
                pass
        attempt += 1
        await asyncio.sleep(interval_seconds)
    return None


async def buy_premium_logic(login: str, months: int, hide_sender: int = 0) -> Dict[str, Any]:
    if months not in (3, 6, 12):
        return {"error": "invalid_months", "allowed": [3, 6, 12]}
    async with fragment_lock:
        async with WalletManager(TONAPI_KEY, MNEMONIC) as wm:
            results: Dict[str, Any] = {}
            async with aiohttp.ClientSession(cookies=FRAGMENT_COOKIES, headers=FRAGMENT_HEADERS) as session:
                steps = [
                    ("updatePremiumState", {"mode": "new", "lv": "false", "dh": "1", "method": "updatePremiumState"}),
                    ("searchPremiumGiftRecipient", {"query": login, "method": "searchPremiumGiftRecipient"}),
                    ("initGiftPremiumRequest", {"recipient": None, "months": str(months), "method": "initGiftPremiumRequest"}),
                ]
                for name, data in steps:
                    if name == "initGiftPremiumRequest":
                        recipient = results.get("searchPremiumGiftRecipient", {}).get("found", {}).get("recipient")
                        if not recipient:
                            break
                        data["recipient"] = recipient
                    async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=data) as resp:
                        raw = await resp.json()
                    results[name] = clean_and_filter(raw)
                    if name == "searchPremiumGiftRecipient" and "found" not in raw:
                        return clean_and_filter(results)
                    if name == "initGiftPremiumRequest" and not raw.get("req_id"):
                        return clean_and_filter(results)

                req_id = results.get("initGiftPremiumRequest", {}).get("req_id")
                if not req_id:
                    return results

                device = {"platform": "browser", "appName": "telegram-wallet", "appVersion": "1",
                          "maxProtocolVersion": 2, "features": ["SendTransaction",
                                                                    {"name": "SendTransaction", "maxMessages": 4,
                                                                     "extraCurrencySupported": True}]}
                link_req = {"account": json.dumps(""), "device": json.dumps(device), "transaction": "1", "id": req_id,
                            "show_sender": str(hide_sender), "method": "getGiftPremiumLink"}
                async with session.post(f"https://fragment.com/api?hash={FRAGMENT_HASH}", data=link_req) as resp4:
                    link_resp = await resp4.json()
                results["getGiftPremiumLink"] = clean_and_filter(link_resp)

                if not link_resp.get("ok") or "transaction" not in link_resp:
                    return clean_and_filter(results)

                transfers = []
                for msg in link_resp["transaction"].get("messages", []):
                    addr = msg["address"]
                    amt = float(msg["amount"]) / 1e9
                    raw_payload = msg.get("payload", "")
                    decoded_comment = decode_payload_b64_premium(raw_payload)
                    transfer_result = await wm.transfer(addr, amt, decoded_comment)
                    transfer_result["decoded_payload_preview"] = decoded_comment[:200]
                    transfers.append(transfer_result)

                results["transfers"] = transfers
                total_nano = sum(t.get("amount", 0) for t in transfers)
                results["total_ton"] = str(total_nano / 1e9)
                results["tx_hash"] = transfers[0].get("tx_hash") if transfers else None

                if results.get("tx_hash"):
                    tx_result = await check_transaction_simple(results["tx_hash"])
                    results["transaction_status"] = tx_result
                    if tx_result:
                        results["status"] = tx_result.get("actions", [{}])[0].get("status", "unknown")
                    else:
                        results["status"] = "failed"

            return clean_and_filter(results)

class BuyRequest(BaseModel):
    login: str
    quantity: int
    hide_sender: int = 0


@app.post("/buy")
async def buy_stars_endpoint(req: BuyRequest):
    if not req.login or req.quantity <= 0:
        raise HTTPException(status_code=400, detail="invalid input")
    try:
        result = await buy_stars_logic(req.login, req.quantity, req.hide_sender)
        if not result:
            raise HTTPException(status_code=500, detail="transaction failed")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BuyPremiumRequest(BaseModel):
    login: str
    months: int
    hide_sender: int = 0


@app.post("/buy_premium")
async def buy_premium_endpoint(req: BuyPremiumRequest):
    if not req.login or req.months not in (3, 6, 12):
        raise HTTPException(status_code=400, detail="invalid input")
    try:
        result = await buy_premium_logic(req.login, req.months, req.hide_sender)
        if not result:
            raise HTTPException(status_code=500, detail="transaction failed")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "80"))
    uvicorn.run("api:app", host=host, port=port, reload=False)