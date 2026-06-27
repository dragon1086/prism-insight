"""
Created on 20250601 
@author: LaivData SJPark with cursor
"""


import sys
from typing import Optional, Tuple
import logging

import pandas as pd

sys.path.extend(['../..', '.'])
import kis_auth as ka

# Logging configuration
logging.basicConfig(level=logging.INFO)

##############################################################################################
# [Domestic Stock] Order/Account > Stock Balance Inquiry[v1_Domestic Stock-006]
##############################################################################################

# Constants
API_URL = "/uapi/domestic-stock/v1/trading/inquire-balance"

def inquire_balance(
    env_dv: str,  # Real/Demo mode
    cano: str,  # Account number
    acnt_prdt_cd: str,  # Account product code
    afhr_flpr_yn: str,  # After-hours single price/exchange flag
    inqr_dvsn: str,  # Inquiry division
    unpr_dvsn: str,  # Unit price division
    fund_sttl_icld_yn: str,  # Fund settlement included flag
    fncg_amt_auto_rdpt_yn: str,  # Loan amount auto repayment flag
    prcs_dvsn: str,  # Process division
    FK100: str = "",  # Continuous inquiry search condition 100
    NK100: str = "",  # Continuous inquiry key 100
    tr_cont: str = "",  # Continuous transaction flag
    dataframe1: Optional[pd.DataFrame] = None,  # Cumulative dataframe 1
    dataframe2: Optional[pd.DataFrame] = None,  # Cumulative dataframe 2
    depth: int = 0,  # Internal recursion depth (auto-managed)
    max_depth: int = 10  # Max recursion limit
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Stock balance inquiry API.
    For real accounts, up to 50 items can be retrieved per call, subsequent values via continuous inquiry.
    For demo accounts, up to 20 items can be retrieved per call, subsequent values via continuous inquiry.

    * Balances with 0 quantity after same-day liquidation may show as 0, but will disappear from balance after D-2.

    Args:
        env_dv (str): [Required] Real/Demo mode (ex. real:live, demo:paper)
        cano (str): [Required] Account number (ex. First 8 digits of account number format 8-2)
        acnt_prdt_cd (str): [Required] Account product code (ex. Last 2 digits of account number format 8-2)
        afhr_flpr_yn (str): [Required] After-hours single price/exchange flag (ex. N:default, Y:after-hours, X:NXT)
        inqr_dvsn (str): [Required] Inquiry division (ex. 01 – By loan date | 02 – By stock)
        unpr_dvsn (str): [Required] Unit price division (ex. 01)
        fund_sttl_icld_yn (str): [Required] Fund settlement included flag (ex. N, Y)
        fncg_amt_auto_rdpt_yn (str): [Required] Loan amount auto repayment flag (ex. N)
        prcs_dvsn (str): [Required] Process division (ex. 00: Include previous day, 01: Exclude previous day)
        FK100 (str): Continuous inquiry search condition 100
        NK100 (str): Continuous inquiry key 100
        tr_cont (str): Continuous transaction flag
        dataframe1 (Optional[pd.DataFrame]): Cumulative dataframe 1
        dataframe2 (Optional[pd.DataFrame]): Cumulative dataframe 2
        depth (int): Internal recursion depth (auto-managed)
        max_depth (int): Max recursion limit

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame]: Stock balance inquiry data (output1, output2)

    Example:
        >>> df1, df2 = inquire_balance(env_dv="real", cano=trenv.my_acct, acnt_prdt_cd=trenv.my_prod, afhr_flpr_yn="N", inqr_dvsn="01", unpr_dvsn="01", fund_sttl_icld_yn="N", fncg_amt_auto_rdpt_yn="N", prcs_dvsn="00")
        >>> print(df1)
        >>> print(df2)
    """

    # Validate required parameters
    if env_dv == "":
        raise ValueError("env_dv is required (e.g. 'real:live, demo:paper')")

    if cano == "":
        raise ValueError("cano is required (e.g. 'First 8 digits of account number format 8-2')")

    if acnt_prdt_cd == "":
        raise ValueError("acnt_prdt_cd is required (e.g. 'Last 2 digits of account number format 8-2')")

    if afhr_flpr_yn == "":
        raise ValueError("afhr_flpr_yn is required (e.g. 'N:default, Y:after-hours, X:NXT')")

    if inqr_dvsn == "":
        raise ValueError("inqr_dvsn is required (e.g. '01 – By loan date | 02 – By stock')")

    if unpr_dvsn == "":
        raise ValueError("unpr_dvsn is required (e.g. '01')")

    if fund_sttl_icld_yn == "":
        raise ValueError("fund_sttl_icld_yn is required (e.g. 'N, Y')")

    if fncg_amt_auto_rdpt_yn == "":
        raise ValueError("fncg_amt_auto_rdpt_yn is required (e.g. 'N')")

    if prcs_dvsn == "":
        raise ValueError("prcs_dvsn is required (e.g. '00: Include previous day, 01: Exclude previous day')")

    if depth > max_depth:
        logging.warning("Max recursive depth reached.")
        if dataframe1 is None:
            dataframe1 = pd.DataFrame()
        if dataframe2 is None:
            dataframe2 = pd.DataFrame()
        return dataframe1, dataframe2

    # Set tr_id
    if env_dv == "real":
        tr_id = "TTTC8434R"
    elif env_dv == "demo":
        tr_id = "VTTC8434R"
    else:
        raise ValueError("env_dv is required (e.g. 'real' or 'demo')")

    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": afhr_flpr_yn,
        "OFL_YN": "",
        "INQR_DVSN": inqr_dvsn,
        "UNPR_DVSN": unpr_dvsn,
        "FUND_STTL_ICLD_YN": fund_sttl_icld_yn,
        "FNCG_AMT_AUTO_RDPT_YN": fncg_amt_auto_rdpt_yn,
        "PRCS_DVSN": prcs_dvsn,
        "CTX_AREA_FK100": FK100,
        "CTX_AREA_NK100": NK100
    }
    
    res = ka._url_fetch(API_URL, tr_id, tr_cont, params)
    
    if res.isOK():
        # Process output1
        current_data1 = pd.DataFrame(res.getBody().output1)
        if dataframe1 is not None:
            dataframe1 = pd.concat([dataframe1, current_data1], ignore_index=True)
        else:
            dataframe1 = current_data1

        # Process output2
        current_data2 = pd.DataFrame(res.getBody().output2)
        if dataframe2 is not None:
            dataframe2 = pd.concat([dataframe2, current_data2], ignore_index=True)
        else:
            dataframe2 = current_data2
            
        tr_cont = res.getHeader().tr_cont
        FK100 = res.getBody().ctx_area_fk100
        NK100 = res.getBody().ctx_area_nk100

        if tr_cont in ["M", "F"]:  # Next page exists
            logging.info("Call Next page...")
            ka.smart_sleep()  # Delay for stable system operation
            return inquire_balance(
                env_dv, cano, acnt_prdt_cd, afhr_flpr_yn, inqr_dvsn, unpr_dvsn,
                fund_sttl_icld_yn, fncg_amt_auto_rdpt_yn, prcs_dvsn, FK100, NK100,
                "N", dataframe1, dataframe2, depth + 1, max_depth
            )
        else:
            logging.info("Data fetch complete.")
            return dataframe1, dataframe2
    else:
        res.printError(url=API_URL)
        return pd.DataFrame(), pd.DataFrame() 