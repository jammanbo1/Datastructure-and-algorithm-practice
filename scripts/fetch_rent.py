#!/usr/bin/env python3
"""국토교통부 전월세 실거래가를 받아 data/listings.json 으로 저장해요.

GitHub Actions가 매일 한 번 실행해요(.github/workflows/fetch-rent.yml).
직접 돌려 보려면:
    MOLIT_KEY=발급받은_인증키 python scripts/fetch_rent.py
키 없이 응답 읽기만 확인하려면 (data.go.kr에서 받은 응답 XML을 저장해 두고):
    python scripts/fetch_rent.py --fixture 응답.xml --type rh --lawd 11440

외부 패키지 없이 파이썬 기본 기능만 써요.
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = 'https://apis.data.go.kr/1613000/'
# 집 종류별 전월세 API (공공데이터포털에서 각각 활용신청해야 해요)
RENT = {
    'apt': 'RTMSDataSvcAptRent/getRTMSDataSvcAptRent',      # 아파트
    'rh': 'RTMSDataSvcRHRent/getRTMSDataSvcRHRent',         # 연립·다세대(빌라)
    'sh': 'RTMSDataSvcSHRent/getRTMSDataSvcSHRent',         # 단독·다가구
    'offi': 'RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent',   # 오피스텔
}
# 전세가율 계산용 매매 API (선택: 활용신청을 안 했으면 시세 없이 저장돼요)
TRADE = {
    'apt': 'RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade',
    'rh': 'RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade',
    'offi': 'RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade',
}
GU = {'11410': '서대문구', '11440': '마포구'}
# 신촌·서강대·연대·이대 근처 법정동만 남겨요
DONGS = {
    '서대문구': {'창천동', '대현동', '대신동', '봉원동', '연희동', '신촌동', '북아현동'},
    '마포구': {'노고산동', '신수동', '대흥동', '창전동', '염리동', '서교동', '동교동', '상수동', '구수동', '현석동'},
}
RENT_MONTHS = 3    # 신고가 30일까지 늦게 들어와서 지난 두 달도 매번 다시 받아요
TRADE_MONTHS = 6
MAX_ITEMS = 3000
OUT = Path(__file__).resolve().parent.parent / 'data' / 'listings.json'
KST = timezone(timedelta(hours=9))

# 응답 태그 이름: 2024년부터 쓰는 영문 이름과 예전 한글 이름을 모두 읽어요
TAGS = {
    'dong': ('umdNm', '법정동'),
    'jibun': ('jibun', '지번'),
    'deposit': ('deposit', '보증금액', '보증금'),
    'rent': ('monthlyRent', '월세금액', '월세'),
    'area': ('excluUseAr', '전용면적', 'contractArea', '계약면적', 'totalFloorAr', '연면적'),
    'floor': ('floor', '층'),
    'built': ('buildYear', '건축년도'),
    'y': ('dealYear', '년', '계약년도'),
    'm': ('dealMonth', '월'),
    'd': ('dealDay', '일'),
    'kind': ('contractType', '계약구분'),
    'house': ('houseType', '주택유형'),
    'price': ('dealAmount', '거래금액'),
    'cancel': ('cdealType', '해제여부'),
}
OK_CODES = {'00', '000'}


class ApiError(Exception):
    pass


def pick(el, key):
    for tag in TAGS[key]:
        v = el.findtext(tag)
        if v and v.strip():
            return v.strip()
    return None


def num(s):
    if s is None:
        return None
    try:
        return float(s.replace(',', '').strip())
    except ValueError:
        return None


def months(n, now=None):
    now = now or datetime.now(KST)
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        out.append(f'{y}{m:02d}')
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def parse_xml(raw):
    root = ET.fromstring(raw)
    # 키 오류 같은 공통 오류는 OpenAPI_ServiceResponse 로 와요
    auth = root.findtext('.//returnAuthMsg')
    if root.tag == 'OpenAPI_ServiceResponse' or auth:
        raise ApiError(auth or root.findtext('.//errMsg') or 'API 오류')
    code = (root.findtext('.//resultCode') or '').strip()
    if code and code not in OK_CODES:
        raise ApiError(f'{code} {root.findtext(".//resultMsg") or ""}'.strip())
    total = int(num(root.findtext('.//totalCount')) or 0)
    return list(root.iter('item')), total


def call(path, lawd, ym, key):
    """한 구·한 달 치를 페이지를 넘기며 모두 받아요."""
    items, page = [], 1
    while True:
        q = urllib.parse.urlencode({'serviceKey': key, 'LAWD_CD': lawd, 'DEAL_YMD': ym,
                                    'pageNo': page, 'numOfRows': 1000})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(BASE + path + '?' + q, timeout=30) as r:
                    raw = r.read()
                break
            except OSError as e:
                if attempt == 2:
                    raise ApiError(f'연결 실패: {e}')
                time.sleep(2 * (attempt + 1))
        got, total = parse_xml(raw)
        items += got
        if not got or len(items) >= total:
            return items
        page += 1


def parse_rent(el, typ, gu):
    dong = pick(el, 'dong')
    if dong not in DONGS[gu]:
        return None
    deposit, area = num(pick(el, 'deposit')), num(pick(el, 'area'))
    y, m, d = (int(num(pick(el, k)) or 0) for k in ('y', 'm', 'd'))
    if deposit is None or not area or not y:
        return None
    floor, built = num(pick(el, 'floor')), num(pick(el, 'built'))
    item = {
        'type': typ, 'gu': gu, 'dong': dong,
        'area': round(area, 1),
        'floor': int(floor) if floor else None,
        'built': int(built) if built else None,
        'deposit': int(deposit),
        'rent': int(num(pick(el, 'rent')) or 0),
        'date': f'{y}-{m:02d}-{d:02d}',
        'kind': pick(el, 'kind') or '-',
    }
    if typ == 'sh' and pick(el, 'house'):
        item['sub'] = pick(el, 'house')  # 단독 / 다가구
    # 고유 번호가 없어서 거래 내용으로 만들어요 (지번은 번호에만 쓰고 저장하지 않아요)
    raw = '|'.join(str(x) for x in (typ, gu, dong, pick(el, 'jibun') or '', item['area'], item['floor'],
                                     item['date'], item['deposit'], item['rent']))
    item['_key'] = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return item


def price_per_m2(el, gu):
    """매매 한 건 → (동, ㎡당 가격). 해제된 거래는 빼요."""
    dong = pick(el, 'dong')
    if dong not in DONGS[gu] or (pick(el, 'cancel') or '').upper() == 'O':
        return None
    price, area = num(pick(el, 'price')), num(pick(el, 'area'))
    if not price or not area:
        return None
    return dong, price / area


def assign_ids(items):
    seen = {}
    for it in items:
        k = it.pop('_key')
        n = seen.get(k, 0)
        seen[k] = n + 1
        it['id'] = k if not n else f'{k}-{n}'  # 같은 날 같은 조건 거래가 여러 건이면 번호를 붙여요


def add_sale_estimates(items, trades):
    """전세 거래에 같은 동네 비슷한 집 매매 시세(추정)를 붙여요: 면적 × ㎡당 가격 중간값."""
    by_dong, by_gu = {}, {}
    for typ, gu, dong, ppm in trades:
        by_dong.setdefault((typ, gu, dong), []).append(ppm)
        by_gu.setdefault((typ, gu), []).append(ppm)
    for it in items:
        if it['rent'] or it['type'] not in TRADE:
            continue
        a = by_dong.get((it['type'], it['gu'], it['dong']), [])
        if len(a) < 3:
            a = by_gu.get((it['type'], it['gu']), [])
        if len(a) >= 3:
            it['sale'] = int(round(it['area'] * statistics.median(a)))


def collect(key):
    items, failed, trades = [], set(), []
    for lawd, gu in GU.items():
        for typ, path in RENT.items():
            for ym in months(RENT_MONTHS):
                try:
                    got = call(path, lawd, ym, key)
                except (ApiError, ET.ParseError) as e:
                    print(f'  ! 전월세 {typ} {gu} {ym}: {e}', file=sys.stderr)
                    failed.add((typ, gu))
                    continue
                rows = [x for x in (parse_rent(el, typ, gu) for el in got) if x]
                print(f'  전월세 {typ:4} {gu} {ym}: {len(got)}건 중 근처 {len(rows)}건')
                items += rows
        for typ, path in TRADE.items():
            for ym in months(TRADE_MONTHS):
                try:
                    got = call(path, lawd, ym, key)
                except (ApiError, ET.ParseError) as e:
                    print(f'  ! 매매 {typ} {gu} {ym}: {e} (시세 없이 계속해요)', file=sys.stderr)
                    break
                trades += [(typ, gu) + x for x in (price_per_m2(el, gu) for el in got) if x]
    return items, failed, trades


def load_old():
    try:
        return json.loads(OUT.read_text(encoding='utf-8')).get('items', [])
    except (OSError, ValueError):
        return []


def save(items):
    items.sort(key=lambda x: x['date'], reverse=True)
    data = {'updated_at': datetime.now(KST).isoformat(timespec='seconds'),
            'source': '국토교통부 실거래가 공개시스템', 'items': items[:MAX_ITEMS]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'저장: {OUT} ({len(data["items"])}건)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fixture', help='API 응답 XML 파일을 읽어 결과만 보여 줘요 (저장 안 함)')
    ap.add_argument('--type', default='rh', choices=list(RENT))
    ap.add_argument('--lawd', default='11440', choices=list(GU))
    a = ap.parse_args()

    if a.fixture:
        got, total = parse_xml(Path(a.fixture).read_bytes())
        rows = [x for x in (parse_rent(el, a.type, GU[a.lawd]) for el in got) if x]
        assign_ids(rows)
        print(f'{len(got)}건 중 근처 {len(rows)}건 (totalCount {total})')
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    key = os.environ.get('MOLIT_KEY', '').strip()
    if not key:
        sys.exit('MOLIT_KEY 환경 변수에 공공데이터포털 인증키를 넣어 주세요.')
    if '%' in key:  # 'Encoding' 키를 넣어도 두 번 인코딩되지 않게 풀어 줘요
        key = urllib.parse.unquote(key)

    items, failed, trades = collect(key)
    if len(failed) == len(GU) * len(RENT):
        sys.exit('모든 전월세 요청이 실패했어요. 예전 파일을 그대로 둬요.')
    # 한 달이라도 실패한 종류·구는 새로 받은 것 대신 예전에 받은 거래를 통째로 남겨요 (빠지거나 겹치지 않게)
    items = [x for x in items if (x['type'], x['gu']) not in failed]
    old = [x for x in load_old() if (x.get('type'), x.get('gu')) in failed]
    if failed:
        print(f'  실패한 {len(failed)}개 묶음은 예전 거래 {len(old)}건을 유지해요', file=sys.stderr)
    assign_ids(items)
    add_sale_estimates(items, trades)
    save(items + old)


if __name__ == '__main__':
    main()
