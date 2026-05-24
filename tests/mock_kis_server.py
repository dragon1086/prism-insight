"""
Mock KIS Open API server (FastAPI)

Implements the subset of KIS REST endpoints needed by prism-insight to run
end-to-end without a real KIS account. The state is in-memory and
deterministic (price seeded from stock code), so tests are reproducible.

Routing: the production client picks this server up when KIS_ENV=mock is set
(see trading/kis_auth.py::_resolve_svr_url).

Run standalone:
    uvicorn tests.mock_kis_server:app --port 8000
or
    python -m tests.mock_kis_server
"""
from __future__ import annotations

import hashlib
import logging
import random
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("mock_kis")

# -----------------------------------------------------------------------------
# Constants & response helpers
# -----------------------------------------------------------------------------

INITIAL_CASH_KRW = 50_000_000
TOKEN_TTL_SECONDS = 24 * 60 * 60
MOCK_TOKEN_PREFIX = "mock-access-"

OK_BODY = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리되었습니다."}
ERR_BODY = {"rt_cd": "1", "msg_cd": "MCA99999", "msg1": "오류가 발생했습니다."}


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {**OK_BODY, **payload}


def _err(msg_cd: str, msg1: str) -> Dict[str, Any]:
    return {"rt_cd": "1", "msg_cd": msg_cd, "msg1": msg1, "output": {}}


# -----------------------------------------------------------------------------
# KIS Open API spec field manifests
#
# Each constant captures the complete response field list per the KIS Open API
# Excel spec. The mock fills meaningful values for fields the production client
# actually consumes; the remainder are emitted with a default ("0" or "") so
# spec-conformant clients (and future code paths) don't trip on KeyError.
# -----------------------------------------------------------------------------

_BALANCE_OUTPUT1_FIELDS = (
    "pdno prdt_name trad_dvsn_name bfdy_buy_qty bfdy_sll_qty thdt_buyqty "
    "thdt_sll_qty hldg_qty ord_psbl_qty pchs_avg_pric pchs_amt prpr evlu_amt "
    "evlu_pfls_amt evlu_pfls_rt evlu_erng_rt loan_dt loan_amt stln_slng_chgs "
    "expd_dt fltt_rt bfdy_cprs_icdc item_mgna_rt_name grta_rt_name sbst_pric "
    "stck_loan_unpr"
).split()

_BALANCE_OUTPUT2_FIELDS = (
    "dnca_tot_amt nxdy_excc_amt prvs_rcdl_excc_amt cma_evlu_amt bfdy_buy_amt "
    "thdt_buy_amt nxdy_auto_rdpt_amt bfdy_sll_amt thdt_sll_amt d2_auto_rdpt_amt "
    "bfdy_tlex_amt thdt_tlex_amt tot_loan_amt scts_evlu_amt tot_evlu_amt "
    "nass_amt fncg_gld_auto_rdpt_yn pchs_amt_smtl_amt evlu_amt_smtl_amt "
    "evlu_pfls_smtl_amt tot_stln_slng_chgs bfdy_tot_asst_evlu_amt asst_icdc_amt "
    "asst_icdc_erng_rt"
).split()

_PSBL_ORDER_FIELDS = (
    "ord_psbl_cash ord_psbl_sbst ruse_psbl_amt fund_rpch_chgs psbl_qty_calc_unpr "
    "nrcvb_buy_amt nrcvb_buy_qty max_buy_amt max_buy_qty cma_evlu_amt "
    "ovrs_re_use_amt_wcrc ord_psbl_frcr_amt_wcrc"
).split()

# NB: KIS spec contains an inconsistent casing on excg_id_dvsn_Cd (capital C in
# the suffix). Preserve verbatim — the spec is the source of truth.
_DAILY_CCLD_OUTPUT1_FIELDS = (
    "ord_dt ord_gno_brno odno orgn_odno ord_dvsn_name sll_buy_dvsn_cd "
    "sll_buy_dvsn_cd_name pdno prdt_name ord_qty ord_unpr ord_tmd tot_ccld_qty "
    "avg_prvs cncl_yn tot_ccld_amt loan_dt ordr_empno ord_dvsn_cd cnc_cfrm_qty "
    "rmn_qty rjct_qty ccld_cndt_name inqr_ip_addr cpbc_ordp_ord_rcit_dvsn_cd "
    "cpbc_ordp_infm_mthd_dvsn_cd infm_tmd ctac_tlno prdt_type_cd excg_dvsn_cd "
    "cpbc_ordp_mtrl_dvsn_cd ord_orgno rsvn_ord_end_dt excg_id_dvsn_Cd "
    "stpm_cndt_pric stpm_efct_occr_dtmd"
).split()

_DAILY_CCLD_OUTPUT2_FIELDS = (
    "tot_ord_qty tot_ccld_qty tot_ccld_amt prsm_tlex_smtl pchs_avg_pric"
).split()

_INQUIRE_PRICE_FIELDS = (
    "iscd_stat_cls_code marg_rate rprs_mrkt_kor_name new_hgpr_lwpr_cls_code "
    "bstp_kor_isnm temp_stop_yn oprc_rang_cont_yn clpr_rang_cont_yn crdt_able_yn "
    "grmn_rate_cls_code elw_pblc_yn stck_prpr prdy_vrss prdy_vrss_sign prdy_ctrt "
    "acml_tr_pbmn acml_vol prdy_vrss_vol_rate stck_oprc stck_hgpr stck_lwpr "
    "stck_mxpr stck_llam stck_sdpr wghn_avrg_stck_prc hts_frgn_ehrt frgn_ntby_qty "
    "pgtr_ntby_qty pvt_scnd_dmrs_prc pvt_frst_dmrs_prc pvt_pont_val "
    "pvt_frst_dmsp_prc pvt_scnd_dmsp_prc dmrs_val dmsp_val cpfn rstc_wdth_prc "
    "stck_fcam stck_sspr aspr_unit hts_deal_qty_unit_val lstn_stcn hts_avls per "
    "pbr stac_month vol_tnrt eps bps d250_hgpr d250_hgpr_date "
    "d250_hgpr_vrss_prpr_rate d250_lwpr d250_lwpr_date d250_lwpr_vrss_prpr_rate "
    "stck_dryy_hgpr dryy_hgpr_vrss_prpr_rate dryy_hgpr_date stck_dryy_lwpr "
    "dryy_lwpr_vrss_prpr_rate dryy_lwpr_date w52_hgpr w52_hgpr_vrss_prpr_ctrt "
    "w52_hgpr_date w52_lwpr w52_lwpr_vrss_prpr_ctrt w52_lwpr_date "
    "whol_loan_rmnd_rate ssts_yn stck_shrn_iscd fcam_cnnm cpfn_cnnm apprch_rate "
    "frgn_hldn_qty vi_cls_code ovtm_vi_cls_code last_ssts_cntg_qty invt_caful_yn "
    "mrkt_warn_cls_code short_over_yn sltr_yn mang_issu_cls_code"
).split()

_DAILY_PRICE_ROW_FIELDS = (
    "stck_bsop_date stck_oprc stck_hgpr stck_lwpr stck_clpr acml_vol "
    "prdy_vrss_vol_rate prdy_vrss prdy_vrss_sign prdy_ctrt hts_frgn_ehrt "
    "frgn_ntby_qty flng_cls_code acml_prtt_rate"
).split()


def _padded(meaningful: Dict[str, Any], fields: List[str], default: str = "0") -> Dict[str, str]:
    """Build a dict with every spec field present, additively.

    Values in `meaningful` win; remaining spec fields are filled with `default`
    so spec-conformant clients see all expected keys. Any key in `meaningful`
    that isn't in `fields` is also preserved — real KIS sometimes returns
    fields beyond the documented spec, and production code may depend on them
    (e.g. `output2.ord_psbl_cash` on inquire-balance).
    """
    result = {f: str(meaningful.get(f, default)) for f in fields}
    for k, v in meaningful.items():
        if k not in result:
            result[k] = str(v)
    return result


# -----------------------------------------------------------------------------
# Pricing model — deterministic per stock_code
# -----------------------------------------------------------------------------

_BASE_PRICE_CACHE: Dict[str, int] = {}


def base_price(stock_code: str) -> int:
    """Deterministic baseline price for a stock code (seeded by hash)."""
    if stock_code in _BASE_PRICE_CACHE:
        return _BASE_PRICE_CACHE[stock_code]
    digest = hashlib.md5(stock_code.encode("utf-8")).hexdigest()
    seed = int(digest[:8], 16)
    # 1,000 ~ 200,000 KRW range
    price = 1000 + (seed % 199_000)
    # snap to 10 KRW grid for realism
    price = (price // 10) * 10
    _BASE_PRICE_CACHE[stock_code] = price
    return price


def jittered_price(stock_code: str) -> int:
    """Current price = base ± up to 0.5%, deterministic per call within a session."""
    base = base_price(stock_code)
    # Use a fresh random per call so the mock looks "live", but capped at ±0.5%.
    delta_pct = random.uniform(-0.005, 0.005)
    price = int(base * (1 + delta_pct))
    # snap to 10 KRW grid
    return max(10, (price // 10) * 10)


# -----------------------------------------------------------------------------
# In-memory state machine
# -----------------------------------------------------------------------------


@dataclass
class Holding:
    quantity: int
    avg_price: float

    def buy(self, qty: int, price: float) -> None:
        new_qty = self.quantity + qty
        if new_qty <= 0:
            self.quantity = 0
            self.avg_price = 0.0
            return
        self.avg_price = ((self.avg_price * self.quantity) + (price * qty)) / new_qty
        self.quantity = new_qty

    def sell(self, qty: int) -> int:
        sold = min(qty, self.quantity)
        self.quantity -= sold
        if self.quantity == 0:
            self.avg_price = 0.0
        return sold


@dataclass
class OrderRecord:
    order_no: str
    account: str
    product: str
    stock_code: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: int
    tr_id: str
    timestamp: datetime
    status: str = "FILLED"


@dataclass
class AccountBook:
    cash: int = INITIAL_CASH_KRW
    holdings: Dict[str, Holding] = field(default_factory=dict)
    orders: List[OrderRecord] = field(default_factory=list)
    order_counter: int = 0

    def next_order_no(self) -> str:
        self.order_counter += 1
        return f"{datetime.now():%Y%m%d}{self.order_counter:06d}"


class MockKISState:
    """In-memory state for all mock accounts. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: Dict[str, AccountBook] = defaultdict(AccountBook)
        self._tokens: Dict[str, datetime] = {}
        self._token_counter = 0

    # ----- accounts -----
    def book(self, account_key: str) -> AccountBook:
        with self._lock:
            return self._accounts[account_key]

    def reset(self) -> None:
        with self._lock:
            self._accounts.clear()
            self._tokens.clear()
            self._token_counter = 0
            _BASE_PRICE_CACHE.clear()

    # ----- tokens -----
    def issue_token(self) -> Dict[str, str]:
        with self._lock:
            self._token_counter += 1
            token = f"{MOCK_TOKEN_PREFIX}{self._token_counter:08d}"
            expiry = datetime.now() + timedelta(seconds=TOKEN_TTL_SECONDS)
            self._tokens[token] = expiry
        return {
            "access_token": token,
            "access_token_token_expired": expiry.strftime("%Y-%m-%d %H:%M:%S"),
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SECONDS,
        }

    def issue_approval_key(self) -> Dict[str, str]:
        with self._lock:
            self._token_counter += 1
            key = f"mock-ws-approval-{self._token_counter:08d}"
        return {"approval_key": key}


STATE = MockKISState()


def _account_key(cano: str, prdt: str) -> str:
    return f"{cano}:{prdt}"


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Mock KIS Open API",
    description="In-memory KIS API for prism-insight development & tests.",
    version="0.1.0",
)


@app.on_event("startup")
def _on_startup() -> None:
    logger.info("Mock KIS server ready — initial cash %d KRW per account", INITIAL_CASH_KRW)


# ---------- OAuth ----------


class TokenRequest(BaseModel):
    grant_type: str = "client_credentials"
    appkey: Optional[str] = None
    appsecret: Optional[str] = None
    secretkey: Optional[str] = None  # WebSocket approval uses this name


@app.post("/oauth2/tokenP")
def oauth_token(req: TokenRequest) -> Dict[str, Any]:
    if not req.appkey or not (req.appsecret or req.secretkey):
        raise HTTPException(status_code=400, detail="missing appkey/appsecret")
    return STATE.issue_token()


@app.post("/oauth2/Approval")
def ws_approval(req: TokenRequest) -> Dict[str, Any]:
    if not req.appkey or not (req.appsecret or req.secretkey):
        raise HTTPException(status_code=400, detail="missing appkey/secretkey")
    return STATE.issue_approval_key()


# ---------- Quotations ----------


@app.get("/uapi/domestic-stock/v1/quotations/inquire-price")
def inquire_price(
    fid_cond_mrkt_div_code: str = Query("J", alias="fid_cond_mrkt_div_code"),
    fid_input_iscd: str = Query(..., alias="fid_input_iscd"),
) -> Dict[str, Any]:
    code = fid_input_iscd
    cur = jittered_price(code)
    base = base_price(code)
    ctrt = round((cur - base) / base * 100, 2) if base else 0.0
    meaningful: Dict[str, Any] = {
        "iscd_stat_cls_code": "55",  # 신용가능
        "marg_rate": "20.00",
        "rprs_mrkt_kor_name": f"MOCK_{code}",
        "bstp_kor_isnm": "MOCK업종",
        "temp_stop_yn": "N",
        "crdt_able_yn": "Y",
        "elw_pblc_yn": "N",
        "stck_prpr": str(cur),
        "prdy_vrss": str(cur - base),
        "prdy_vrss_sign": "2" if cur >= base else "5",
        "prdy_ctrt": f"{ctrt:.2f}",
        "acml_tr_pbmn": str(random.randint(1_000_000_000, 10_000_000_000)),
        "acml_vol": str(random.randint(10_000, 5_000_000)),
        "stck_oprc": str(base),
        "stck_hgpr": str(int(base * 1.02)),
        "stck_lwpr": str(int(base * 0.98)),
        "stck_mxpr": str(int(base * 1.30)),
        "stck_llam": str(int(base * 0.70)),
        "stck_sdpr": str(base),
        "stck_shrn_iscd": code,
        "ssts_yn": "Y",
        "invt_caful_yn": "N",
        "short_over_yn": "N",
        "sltr_yn": "N",
        "mang_issu_cls_code": "0",
        "mrkt_warn_cls_code": "00",
    }
    return _ok({"output": _padded(meaningful, _INQUIRE_PRICE_FIELDS, default="0")})


@app.get("/uapi/domestic-stock/v1/quotations/inquire-daily-price")
def inquire_daily_price(
    fid_cond_mrkt_div_code: str = Query("J", alias="fid_cond_mrkt_div_code"),
    fid_input_iscd: str = Query(..., alias="fid_input_iscd"),
    fid_period_div_code: str = Query("D", alias="fid_period_div_code"),
    fid_org_adj_prc: str = Query("0", alias="fid_org_adj_prc"),
) -> Dict[str, Any]:
    code = fid_input_iscd
    base = base_price(code)
    # 60-day random walk from base (deterministic per code).
    rng = random.Random(int(hashlib.md5(code.encode()).hexdigest()[:8], 16))
    rows: List[Dict[str, str]] = []
    px = float(base)
    prev_close = float(base)
    today = datetime.now()
    for i in range(60):
        date = today - timedelta(days=i)
        change = rng.uniform(-0.03, 0.03)
        px = max(10.0, px * (1 + change))
        close = int(px)
        diff = close - int(prev_close)
        ctrt = round(diff / prev_close * 100, 2) if prev_close else 0.0
        meaningful = {
            "stck_bsop_date": date.strftime("%Y%m%d"),
            "stck_oprc": str(int(px * 0.995)),
            "stck_hgpr": str(int(px * 1.01)),
            "stck_lwpr": str(int(px * 0.99)),
            "stck_clpr": str(close),
            "acml_vol": str(rng.randint(10_000, 1_000_000)),
            "prdy_vrss_vol_rate": f"{rng.uniform(50, 200):.2f}",
            "prdy_vrss": str(diff),
            "prdy_vrss_sign": "2" if diff >= 0 else "5",
            "prdy_ctrt": f"{ctrt:.2f}",
            "hts_frgn_ehrt": f"{rng.uniform(0, 50):.2f}",
            "frgn_ntby_qty": str(rng.randint(-100_000, 100_000)),
            "flng_cls_code": "00",
            "acml_prtt_rate": "1.00",
        }
        rows.append(_padded(meaningful, _DAILY_PRICE_ROW_FIELDS, default="0"))
        prev_close = float(close)
    return _ok({"output": rows})


# ---------- Trading: order-cash ----------


class OrderCashRequest(BaseModel):
    CANO: str
    ACNT_PRDT_CD: str
    PDNO: str
    ORD_DVSN: str
    ORD_QTY: str
    ORD_UNPR: str = "0"
    EXCG_ID_DVSN_CD: Optional[str] = None
    SLL_TYPE: Optional[str] = None
    CNDT_PRIC: Optional[str] = None


BUY_TR_IDS = {"TTTC0012U", "VTTC0012U"}
SELL_TR_IDS = {"TTTC0011U", "VTTC0011U"}


@app.post("/uapi/domestic-stock/v1/trading/order-cash")
def order_cash(
    body: OrderCashRequest,
    tr_id: str = Header(..., alias="tr_id"),
) -> Dict[str, Any]:
    if tr_id not in BUY_TR_IDS | SELL_TR_IDS:
        return _err("MCA00001", f"unsupported tr_id={tr_id}")

    side = "BUY" if tr_id in BUY_TR_IDS else "SELL"
    qty = int(body.ORD_QTY)
    if qty <= 0:
        return _err("APBK0918", "quantity must be > 0")

    # Resolve fill price: ORD_UNPR > 0 → limit, else current market price.
    unit_price = int(body.ORD_UNPR or 0)
    fill_price = unit_price if unit_price > 0 else jittered_price(body.PDNO)

    book = STATE.book(_account_key(body.CANO, body.ACNT_PRDT_CD))
    with STATE._lock:
        if side == "BUY":
            total = qty * fill_price
            if total > book.cash:
                return _err("APBK0552", "insufficient cash")
            book.cash -= total
            holding = book.holdings.get(body.PDNO) or Holding(0, 0.0)
            holding.buy(qty, float(fill_price))
            book.holdings[body.PDNO] = holding
        else:  # SELL
            holding = book.holdings.get(body.PDNO)
            if not holding or holding.quantity <= 0:
                return _err("APBK0550", "no holding to sell")
            if qty > holding.quantity:
                return _err("APBK0551", f"qty {qty} exceeds holding {holding.quantity}")
            holding.sell(qty)
            book.cash += qty * fill_price
            if holding.quantity == 0:
                # keep entry to allow zero-qty reads
                pass

        order_no = book.next_order_no()
        book.orders.append(OrderRecord(
            order_no=order_no,
            account=body.CANO,
            product=body.ACNT_PRDT_CD,
            stock_code=body.PDNO,
            side=side,
            quantity=qty,
            price=fill_price,
            tr_id=tr_id,
            timestamp=datetime.now(),
        ))

    ord_tmd = datetime.now().strftime("%H%M%S")
    # KIS spec example uses UPPERCASE keys (ODNO, ORD_TMD) but the production
    # client reads `output.get('odno', '')` lowercase. Emit BOTH so the mock
    # satisfies spec-conformant contract tests AND the existing client code.
    return _ok({
        "output": {
            "KRX_FWDG_ORD_ORGNO": "00950",
            "ODNO": order_no,
            "ORD_TMD": ord_tmd,
            "odno": order_no,
            "ord_tmd": ord_tmd,
        }
    })


# ---------- Trading: reserved order ----------


class ReservedOrderRequest(BaseModel):
    CANO: str
    ACNT_PRDT_CD: str
    PDNO: str
    ORD_QTY: str
    ORD_UNPR: str
    SLL_BUY_DVSN_CD: str  # "01" SELL, "02" BUY
    ORD_DVSN_CD: str
    ORD_OBJT_CBLC_DVSN_CD: Optional[str] = None
    LOAN_DT: Optional[str] = None
    LDNG_DT: Optional[str] = None
    RSVN_ORD_END_DT: Optional[str] = None


@app.post("/uapi/domestic-stock/v1/trading/order-resv")
def order_reserved(body: ReservedOrderRequest, tr_id: str = Header(..., alias="tr_id")) -> Dict[str, Any]:
    side = "BUY" if body.SLL_BUY_DVSN_CD == "02" else "SELL"
    qty = int(body.ORD_QTY)
    if qty <= 0:
        return _err("APBK0918", "quantity must be > 0")

    # Reserved orders are "queued" — for the mock we fill them right away so
    # downstream code can be exercised end-to-end. Real KIS would defer until
    # 7:30 AM next trading day.
    unit_price = int(body.ORD_UNPR or 0)
    fill_price = unit_price if unit_price > 0 else jittered_price(body.PDNO)

    book = STATE.book(_account_key(body.CANO, body.ACNT_PRDT_CD))
    with STATE._lock:
        if side == "BUY":
            total = qty * fill_price
            if total > book.cash:
                return _err("APBK0552", "insufficient cash")
            book.cash -= total
            holding = book.holdings.get(body.PDNO) or Holding(0, 0.0)
            holding.buy(qty, float(fill_price))
            book.holdings[body.PDNO] = holding
        else:
            holding = book.holdings.get(body.PDNO)
            if not holding or holding.quantity <= 0:
                return _err("APBK0550", "no holding to sell")
            sold = holding.sell(min(qty, holding.quantity))
            book.cash += sold * fill_price

        seq = book.next_order_no()
        book.orders.append(OrderRecord(
            order_no=seq,
            account=body.CANO,
            product=body.ACNT_PRDT_CD,
            stock_code=body.PDNO,
            side=side,
            quantity=qty,
            price=fill_price,
            tr_id=tr_id,
            timestamp=datetime.now(),
            status="RESERVED-FILLED",
        ))

    # KIS spec for 주식예약주문 uniquely uses `msg` (not `msg1`) in the response
    # envelope. Emit `msg` alongside `msg1` so both spec-conformant clients
    # and the generic _ok() envelope work.
    # Output key: spec body table lists lowercase `rsvn_ord_seq`, but the
    # spec Response Example and the production client both use UPPERCASE
    # `RSVN_ORD_SEQ` (trading/domestic_stock_trading.py:704,1044). Emit both
    # so spec-conformant contract tests AND existing clients pass.
    return {
        **OK_BODY,
        "msg": OK_BODY["msg1"],
        "output": {"RSVN_ORD_SEQ": seq, "rsvn_ord_seq": seq},
    }


# ---------- Inquiries ----------


@app.get("/uapi/domestic-stock/v1/trading/inquire-balance")
def inquire_balance(
    CANO: str = Query(..., alias="CANO"),
    ACNT_PRDT_CD: str = Query(..., alias="ACNT_PRDT_CD"),
) -> Dict[str, Any]:
    book = STATE.book(_account_key(CANO, ACNT_PRDT_CD))
    output1: List[Dict[str, str]] = []
    eval_total = 0.0
    purchase_total = 0.0
    for code, h in book.holdings.items():
        if h.quantity <= 0:
            continue
        cur = jittered_price(code)
        eval_amount = cur * h.quantity
        purchase_amount = h.avg_price * h.quantity
        profit = eval_amount - purchase_amount
        rate = (profit / purchase_amount * 100) if purchase_amount else 0.0
        eval_total += eval_amount
        purchase_total += purchase_amount
        meaningful = {
            "pdno": code,
            "prdt_name": f"MOCK_{code}",
            "trad_dvsn_name": "현금",
            "hldg_qty": str(h.quantity),
            "ord_psbl_qty": str(h.quantity),
            "pchs_avg_pric": f"{h.avg_price:.2f}",
            "pchs_amt": f"{purchase_amount:.0f}",
            "prpr": str(cur),
            "evlu_amt": f"{eval_amount:.0f}",
            "evlu_pfls_amt": f"{profit:.0f}",
            "evlu_pfls_rt": f"{rate:.2f}",
            "fltt_rt": f"{rate:.2f}",
            "item_mgna_rt_name": "20%",
            "grta_rt_name": "40%",
        }
        output1.append(_padded(meaningful, _BALANCE_OUTPUT1_FIELDS, default="0"))

    output2_meaningful = {
        "dnca_tot_amt": f"{book.cash:.0f}",
        "nxdy_excc_amt": f"{book.cash:.0f}",
        "prvs_rcdl_excc_amt": f"{book.cash:.0f}",
        "tot_evlu_amt": f"{book.cash + eval_total:.0f}",
        "scts_evlu_amt": f"{eval_total:.0f}",
        "pchs_amt_smtl_amt": f"{purchase_total:.0f}",
        "evlu_amt_smtl_amt": f"{eval_total:.0f}",
        "evlu_pfls_smtl_amt": f"{eval_total - purchase_total:.0f}",
        "nass_amt": f"{book.cash + eval_total:.0f}",
        "fncg_gld_auto_rdpt_yn": "N",
        "bfdy_tot_asst_evlu_amt": f"{book.cash + eval_total:.0f}",
        # `ord_psbl_cash` is not in the documented spec for inquire-balance
        # output2, but `trading/domestic_stock_trading.py:1508` reads it for
        # the `available_amount` summary. KIS's real response includes it in
        # practice — keep emitting it.
        "ord_psbl_cash": f"{book.cash:.0f}",
    }
    output2 = _padded(output2_meaningful, _BALANCE_OUTPUT2_FIELDS, default="0")
    # Spec requires body-level pagination cursors.
    return {
        **OK_BODY,
        "ctx_area_fk100": "",
        "ctx_area_nk100": "",
        "output1": output1,
        "output2": [output2],
    }


@app.get("/uapi/domestic-stock/v1/trading/inquire-psbl-order")
def inquire_psbl_order(
    CANO: str = Query(..., alias="CANO"),
    ACNT_PRDT_CD: str = Query(..., alias="ACNT_PRDT_CD"),
    PDNO: str = Query("", alias="PDNO"),
    ORD_UNPR: str = Query("0", alias="ORD_UNPR"),
) -> Dict[str, Any]:
    book = STATE.book(_account_key(CANO, ACNT_PRDT_CD))
    unit_price = int(ORD_UNPR or 0)
    if unit_price <= 0 and PDNO:
        unit_price = jittered_price(PDNO)
    max_qty = (book.cash // unit_price) if unit_price > 0 else 0
    max_amt = max_qty * unit_price
    meaningful = {
        "ord_psbl_cash": str(book.cash),
        "psbl_qty_calc_unpr": str(unit_price),
        # nrcvb_* (미수없는) == max_* in the no-margin mock.
        "nrcvb_buy_amt": str(max_amt),
        "nrcvb_buy_qty": str(max_qty),
        "max_buy_amt": str(max_amt),
        "max_buy_qty": str(max_qty),
    }
    return _ok({"output": _padded(meaningful, _PSBL_ORDER_FIELDS, default="0")})


@app.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld")
def inquire_daily_ccld(
    CANO: str = Query(..., alias="CANO"),
    ACNT_PRDT_CD: str = Query(..., alias="ACNT_PRDT_CD"),
) -> Dict[str, Any]:
    book = STATE.book(_account_key(CANO, ACNT_PRDT_CD))
    today = datetime.now().strftime("%Y%m%d")
    rows: List[Dict[str, str]] = []
    tot_qty = 0
    tot_amt = 0
    for record in book.orders:
        if record.timestamp.strftime("%Y%m%d") != today:
            continue
        amt = record.quantity * record.price
        tot_qty += record.quantity
        tot_amt += amt
        side_cd = "01" if record.side == "SELL" else "02"
        meaningful = {
            "ord_dt": record.timestamp.strftime("%Y%m%d"),
            "ord_gno_brno": "00950",
            "odno": record.order_no,
            "orgn_odno": "0000000000",
            "ord_dvsn_name": "시장가" if int(record.price) == jittered_price(record.stock_code) else "지정가",
            "sll_buy_dvsn_cd": side_cd,
            "sll_buy_dvsn_cd_name": "매도" if record.side == "SELL" else "매수",
            "pdno": record.stock_code,
            "prdt_name": f"MOCK_{record.stock_code}",
            "ord_qty": str(record.quantity),
            "ord_unpr": str(record.price),
            "ord_tmd": record.timestamp.strftime("%H%M%S"),
            "tot_ccld_qty": str(record.quantity),
            "avg_prvs": str(record.price),
            "cncl_yn": "N",
            "tot_ccld_amt": str(amt),
            "ord_dvsn_cd": "01",
            "rmn_qty": "0",
            "rjct_qty": "0",
            "ccld_cndt_name": "DAY",
            "prdt_type_cd": "300",
            "excg_dvsn_cd": "02",
            "ord_orgno": "00950",
            "excg_id_dvsn_Cd": "KRX",
        }
        rows.append(_padded(meaningful, _DAILY_CCLD_OUTPUT1_FIELDS, default="0"))

    output2_meaningful = {
        "tot_ord_qty": str(tot_qty),
        "tot_ccld_qty": str(tot_qty),
        "tot_ccld_amt": str(tot_amt),
        "prsm_tlex_smtl": "0",
        "pchs_avg_pric": str(int(tot_amt / tot_qty)) if tot_qty else "0",
    }
    return {
        **OK_BODY,
        "ctx_area_fk100": "",
        "ctx_area_nk100": "",
        "output1": rows,
        "output2": [_padded(output2_meaningful, _DAILY_CCLD_OUTPUT2_FIELDS, default="0")],
    }


# ---------- Admin / introspection (test only) ----------


@app.post("/__mock__/reset")
def admin_reset() -> Dict[str, Any]:
    STATE.reset()
    return {"reset": True}


@app.get("/__mock__/state")
def admin_state(account_key: Optional[str] = None) -> Dict[str, Any]:
    if account_key:
        book = STATE.book(account_key)
        return {
            "cash": book.cash,
            "holdings": {k: {"qty": v.quantity, "avg_price": v.avg_price} for k, v in book.holdings.items()},
            "order_count": len(book.orders),
        }
    return {"accounts": list(STATE._accounts.keys()), "token_count": STATE._token_counter}


@app.post("/__mock__/seed_holding")
def admin_seed_holding(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pre-seed a holding for tests (bypasses cash/order paths)."""
    cano = payload["CANO"]
    prdt = payload["ACNT_PRDT_CD"]
    code = payload["PDNO"]
    qty = int(payload["qty"])
    avg = float(payload.get("avg_price") or base_price(code))
    book = STATE.book(_account_key(cano, prdt))
    with STATE._lock:
        book.holdings[code] = Holding(qty, avg)
    return {"seeded": {"account": _account_key(cano, prdt), "stock": code, "qty": qty, "avg_price": avg}}


# -----------------------------------------------------------------------------
# Standalone runner
# -----------------------------------------------------------------------------


def run_in_thread(host: str = "127.0.0.1", port: int = 8000, log_level: str = "warning"):
    """Start the mock server in a background daemon thread (for tests)."""
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="mock-kis", daemon=True)
    thread.start()
    # wait until uvicorn signals started
    import time
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock KIS server failed to start within 10s")
    return server, thread


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
