import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import pytz
from requests import Session

from config import BASE_URL, MAIN_INFO_URL, USER_AGENT, USER_MNDP_URL

LOG = logging.getLogger(__name__)


def get_now() -> datetime:
    korea_tz = pytz.timezone("Asia/Seoul")
    return datetime.now(pytz.utc).astimezone(korea_tz)


def get_sale_status() -> Tuple[bool, str, Optional[str]]:
    now = get_now()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    if weekday == 5 and hour >= 20:
        if hour == 20 and minute < 50:
            return False, "drawing", "추첨이 진행 중입니다. 추첨 완료 후 결과를 확인해주세요."
        return False, "closed", "금주 판매가 마감되었습니다. 다음 회차 판매는 일요일 06:00부터 시작됩니다."

    if weekday == 6 and hour < 6:
        return False, "closed", "금주 판매가 마감되었습니다. 다음 회차 판매는 06:00부터 시작됩니다."

    return True, "available", None


def get_result_check_status() -> Tuple[bool, str, Optional[str]]:
    now = get_now()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    if weekday == 5:
        if hour < 21:
            return False, "before_draw", "아직 추첨 전입니다. 추첨은 토요일 오후 8시 45분경 진행됩니다."
        if hour == 20 and minute < 50:
            return False, "drawing", "추첨이 진행 중입니다. 잠시 후 결과를 확인해주세요."
        if hour >= 21 and hour < 22:
            return False, "processing", "당첨 결과를 집계 중입니다. 22시 이후에 확인해주세요."

    return True, "available", None


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_session_from_context(
    context,
    proxy_address: Optional[str] = None,
    proxy_user: Optional[str] = None,
    proxy_pw: Optional[str] = None,
) -> Session:
    session = Session()
    for cookie in context.cookies():
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])

    if proxy_address:
        auth = ""
        if proxy_user and proxy_pw:
            auth = f"{quote(proxy_user, safe='')}:{quote(proxy_pw, safe='')}@"
        proxy_url = f"http://{auth}{proxy_address}"
        session.proxies.update({"http": proxy_url, "https": proxy_url})

    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL,
        "Origin": BASE_URL,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def fetch_user_balance(session: Session) -> Tuple[int, Dict[str, Any]]:
    res = session.get(USER_MNDP_URL, timeout=20)
    res.raise_for_status()
    payload = res.json()
    user_mndp = (payload.get("data") or {}).get("userMndp") or {}
    total_amt = user_mndp.get("totalAmt")
    if total_amt is None:
        pnt_dpst = as_int(user_mndp.get("pntDpstAmt"))
        pnt_tkmny = as_int(user_mndp.get("pntTkmnyAmt"))
        ncsbl_dpst = as_int(user_mndp.get("ncsblDpstAmt"))
        ncsbl_tkmny = as_int(user_mndp.get("ncsblTkmnyAmt"))
        csbl_dpst = as_int(user_mndp.get("csblDpstAmt"))
        csbl_tkmny = as_int(user_mndp.get("csblTkmnyAmt"))
        total_amt = (pnt_dpst - pnt_tkmny) + (ncsbl_dpst - ncsbl_tkmny) + (
            csbl_dpst - csbl_tkmny
        )
    return as_int(total_amt), user_mndp


def extract_entries(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or payload
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("list", "buyList", "resultList", "lottoList", "myLottoList"):
        if isinstance(data.get(key), list):
            return [entry for entry in data[key] if isinstance(entry, dict)]
    return []


def entry_matches_date(entry: Dict[str, Any], date_str: str) -> bool:
    date_formats = {
        date_str,
        f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}",
    }
    for value in entry.values():
        if isinstance(value, str) and any(fmt in value for fmt in date_formats):
            return True
    return False


def extract_numbers_from_text(text: str) -> List[List[str]]:
    nums = [as_int(n) for n in re.findall(r"\b\d{1,2}\b", text)]
    nums = [n for n in nums if 1 <= n <= 45]
    if len(nums) < 6:
        return []
    groups: List[List[str]] = []
    for idx in range(0, len(nums), 6):
        group = nums[idx : idx + 6]
        if len(group) == 6:
            groups.append([str(n) for n in group])
    return groups


def extract_numbers_from_entry(entry: Dict[str, Any]) -> List[List[str]]:
    numbers: List[List[str]] = []
    for key, value in entry.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (int, str)):
                    numbers.extend(extract_numbers_from_text(str(item)))
                elif isinstance(item, dict):
                    numbers.extend(extract_numbers_from_entry(item))
            continue
        if isinstance(value, dict):
            numbers.extend(extract_numbers_from_entry(value))
            continue
        if isinstance(value, str):
            if any(token in key.lower() for token in ("wnno", "lotto", "number", "num")):
                numbers.extend(extract_numbers_from_text(value))
    return numbers


def write_debug_payload(
    debug_dir: str,
    label: str,
    payload: Dict[str, Any],
) -> None:
    os.makedirs(debug_dir, exist_ok=True)
    path = os.path.join(debug_dir, f"{label}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def fetch_json(
    session: Session,
    url: str,
    params: Dict[str, Any],
    method: str,
    debug_dir: Optional[str],
    label: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "method": method,
        "url": url,
        "params": params,
        "status_code": None,
        "error": None,
    }
    text: Optional[str] = None
    try:
        if method == "GET":
            res = session.get(url, params=params, timeout=20)
        else:
            res = session.post(url, data=params, timeout=20)
        meta["status_code"] = res.status_code
        text = res.text
        if debug_dir:
            write_debug_payload(
                debug_dir,
                label,
                {
                    "meta": meta,
                    "response_text": text[:20000],
                },
            )
        res.raise_for_status()
        return res.json(), meta
    except Exception as exc:
        meta["error"] = str(exc)
        if debug_dir:
            write_debug_payload(
                debug_dir,
                label,
                {
                    "meta": meta,
                    "response_text": text[:20000] if text else None,
                },
            )
        return None, meta


def is_drawing_in_progress(entry: Dict[str, Any]) -> bool:
    drawing_keywords = [
        "추첨중", "추첨 중", "진행중", "진행 중", "대기",
        "확인불가", "확인 불가", "열람불가", "열람 불가"
    ]
    for value in entry.values():
        if isinstance(value, str):
            if any(kw in value for kw in drawing_keywords):
                return True
    return False


def _fetch_ticket_detail(
    session: Session,
    ntsl_ordr_no: str,
    start_date: str,
    end_date: str,
    barcd: str,
    debug_dir: Optional[str] = None,
) -> List[List[str]]:
    """Fetch actual lotto numbers from ticket detail API.
    
    This matches the new site structure (Jan 2026+) where numbers
    are retrieved via a separate detail endpoint.
    """
    from urllib.parse import urlencode
    
    params = {
        "ntslOrdrNo": ntsl_ordr_no,
        "srchStrDt": start_date,
        "srchEndDt": end_date,
        "barcd": barcd,
    }
    query = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"https://www.dhlottery.co.kr/mypage/lotto645TicketDetail.do?{query}"
    
    payload, _ = fetch_json(
        session,
        url,
        {},
        "GET",
        debug_dir,
        label="ticket_detail",
    )
    
    if not payload:
        return []
    
    data = payload.get("data") or {}
    ticket = data.get("ticket") or {}
    games = ticket.get("game_dtl") or []
    
    numbers: List[List[str]] = []
    for game in games:
        nums = game.get("num") or []
        if len(nums) == 6:
            numbers.append([str(n).zfill(2) for n in nums])
    
    return numbers


def fetch_today_purchase_numbers(
    session: Session,
    date_str: str,
    debug_dir: Optional[str] = None,
) -> Tuple[List[List[str]], Dict[str, Any]]:
    """Fetch purchased lotto numbers for a given date.
    
    Uses the new site API structure (Jan 2026+):
    1. First fetches the ledger list to get purchase metadata
    2. Then fetches ticket details to get actual numbers
    """
    attempts: List[Dict[str, Any]] = []
    
    # New primary endpoint (matches check_result.py)
    ledger_url = (
        f"https://www.dhlottery.co.kr/mypage/selectMyLotteryledger.do"
        f"?srchStrDt={date_str}&srchEndDt={date_str}"
        f"&sort=&ltGdsCd=LO40&winResult=&pageNum=1&recordCountPerPage=10"
    )
    
    payload, meta = fetch_json(
        session,
        ledger_url,
        {},
        "GET",
        debug_dir,
        label="ledger_new_api",
    )
    attempts.append(meta)
    
    if payload:
        data = payload.get("data") or {}
        items = data.get("list") or []
        
        if items:
            LOG.info(f"새 API에서 {len(items)}개 구매 항목 발견")
            all_numbers: List[List[str]] = []
            
            for item in items:
                if item.get("ltGdsCd") != "LO40":
                    continue
                
                ntsl_ordr_no = item.get("ntslOrdrNo")
                gm_info = item.get("gmInfo")
                
                if ntsl_ordr_no and gm_info:
                    ticket_numbers = _fetch_ticket_detail(
                        session,
                        ntsl_ordr_no,
                        date_str,
                        date_str,
                        gm_info,
                        debug_dir,
                    )
                    if ticket_numbers:
                        all_numbers.extend(ticket_numbers)
                        LOG.info(f"티켓 상세에서 {len(ticket_numbers)}개 번호 그룹 추출")
            
            if all_numbers:
                seen = set()
                unique_numbers = []
                for group in all_numbers:
                    key = tuple(group)
                    if key not in seen:
                        seen.add(key)
                        unique_numbers.append(group)
                return unique_numbers, {"attempts": attempts}
    
    # Fallback to legacy endpoints for compatibility
    legacy_params = {"srchStrDt": date_str, "srchEndDt": date_str}
    legacy_endpoints = [
        "/mypage/selectMyLottoLedgerList.do",
        "/mypage/selectMylotteryledgerList.do",
        "/mypage/selectMyLottoBuyList.do",
        "/mypage/selectMyLottoList.do",
    ]
    
    entries: List[Dict[str, Any]] = []
    for endpoint in legacy_endpoints:
        url = f"{BASE_URL}{endpoint}"
        payload, meta = fetch_json(
            session,
            url,
            legacy_params,
            "GET",
            debug_dir,
            label=f"ledger_{endpoint.strip('/').replace('/', '_')}",
        )
        attempts.append(meta)
        if not payload:
            payload, meta = fetch_json(
                session,
                url,
                legacy_params,
                "POST",
                debug_dir,
                label=f"ledger_{endpoint.strip('/').replace('/', '_')}_post",
            )
            attempts.append(meta)
        if payload:
            entries = extract_entries(payload)
        if entries:
            break

    if entries:
        dated_entries = [e for e in entries if entry_matches_date(e, date_str)]
        if dated_entries:
            entries = dated_entries

        drawing_entries = [e for e in entries if is_drawing_in_progress(e)]
        if drawing_entries and len(drawing_entries) == len(entries):
            now = get_now()
            if now.weekday() == 5 and now.hour >= 20 and now.hour < 22:
                message = "추첨 중입니다. 22시 이후에 결과를 확인해주세요."
            else:
                message = "추첨 중입니다. 추첨 완료 후 확인해주세요."
            return [], {"attempts": attempts, "status": "drawing", "message": message}

        numbers: List[List[str]] = []
        for entry in entries:
            numbers.extend(extract_numbers_from_entry(entry))
        if numbers:
            seen = set()
            unique_numbers = []
            for group in numbers:
                key = tuple(group)
                if key in seen:
                    continue
                seen.add(key)
                unique_numbers.append(group)
            return unique_numbers, {"attempts": attempts}

    return [], {"attempts": attempts}


def fetch_latest_lotto_result() -> Optional[Tuple[int, Optional[str], List[str], Optional[str]]]:
    res = Session().get(MAIN_INFO_URL, timeout=20)
    res.raise_for_status()
    payload = res.json()
    lt645_list = (
        payload.get("data", {})
        .get("result", {})
        .get("pstLtEpstInfo", {})
        .get("lt645", [])
    )
    if not lt645_list:
        return None
    latest = max(lt645_list, key=lambda item: as_int(item.get("ltEpsd")))
    numbers = [
        str(latest.get("tm1WnNo")),
        str(latest.get("tm2WnNo")),
        str(latest.get("tm3WnNo")),
        str(latest.get("tm4WnNo")),
        str(latest.get("tm5WnNo")),
        str(latest.get("tm6WnNo")),
    ]
    bonus = latest.get("bnsWnNo")
    draw_date = latest.get("ltRflYmd")
    return as_int(latest.get("ltEpsd")), draw_date, numbers, str(bonus) if bonus else None


def fetch_lotto_result_by_round(
    draw_no: int,
) -> Optional[Tuple[int, Optional[str], List[str], Optional[str]]]:
    """Fetch lotto result for a specific round.
    
    Uses the new API endpoint after site restructure (Jan 2026).
    Old endpoint (common.do?method=getLottoNumber) now redirects to homepage.
    """
    url = f"https://www.dhlottery.co.kr/lt645/selectPstLt645Info.do?srchLtEpsd={draw_no}"
    try:
        res = Session().get(url, timeout=20)
        res.raise_for_status()
        response = res.json()
        
        # New API returns {"data": {"list": [...]}}
        data_list = response.get("data", {}).get("list", [])
        if not data_list:
            return None
        
        data = data_list[0]
        numbers = [
            str(data.get("tm1WnNo")),
            str(data.get("tm2WnNo")),
            str(data.get("tm3WnNo")),
            str(data.get("tm4WnNo")),
            str(data.get("tm5WnNo")),
            str(data.get("tm6WnNo")),
        ]
        bonus = data.get("bnsWnNo")
        draw_date = data.get("ltRflYmd")  # Already in YYYYMMDD format
        return draw_no, draw_date, numbers, str(bonus) if bonus else None
    except Exception as exc:
        LOG.warning(f"회차 {draw_no} 결과 조회 실패: {exc}")
        return None
