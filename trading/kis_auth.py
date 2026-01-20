# -*- coding: utf-8 -*-
# ====|  (REST) 접근 토큰 / (Websocket) 웹소켓 접속키 발급 에 필요한 API 호출 샘플 아래 참고하시기 바랍니다.  |=====================
# ====|  API 호출 공통 함수 포함                                  |=====================

import asyncio
import copy
import json
import logging
import os
import shutil
import tempfile
import time
from base64 import b64decode
from collections import namedtuple
from collections.abc import Callable
from datetime import datetime
from io import StringIO
import stat
import hashlib
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# pip install requests (패키지설치)
import requests

# 웹 소켓 모듈을 선언한다.
import websockets

# pip install PyYAML (패키지설치)
import yaml
from Crypto.Cipher import AES

# pip install pycryptodome
from Crypto.Util.Padding import unpad

from cryptography.fernet import Fernet

class SecurityError(Exception):
    """보안 관련 오류"""
    pass


# ============== KIS Authentication Exception Classes ==============
class KISAuthError(Exception):
    """Base exception for KIS authentication errors"""
    pass


class TokenFileError(KISAuthError):
    """Token file read/write errors"""
    pass


class CredentialMismatchError(KISAuthError):
    """Credential and mode mismatch detected (e.g., demo key with real mode)"""
    pass


class TokenRequestError(KISAuthError):
    """Failed to request new token from KIS API"""
    def __init__(self, message, status_code=None, response_text=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

clearConsole = lambda: os.system("cls" if os.name in ("nt", "dos") else "clear")

key_bytes = 32
# kis_auth.py 파일이 있는 디렉토리 기준으로 config 폴더 찾기
current_dir = os.path.dirname(os.path.abspath(__file__))
config_root = os.path.join(current_dir, "config")
# config_root = "$HOME/KIS/config/"  # 토큰 파일이 저장될 폴더, 제3자가 찾기 어렵도록 경로 설정하시기 바랍니다.
# token_tmp = config_root + 'KIS000000'  # 토큰 로컬저장시 파일 이름 지정, 파일이름을 토큰값이 유추가능한 파일명은 삼가바랍니다.
# token_tmp = config_root + 'KIS' + datetime.today().strftime("%Y%m%d%H%M%S")  # 토큰 로컬저장시 파일명 년월일시분초
# 디렉토리 없으면 생성
os.makedirs(config_root, exist_ok=True)

# Unix/Linux에서 디렉토리 권한 설정 (소유자만 접근)
if os.name != 'nt':
    try:
        os.chmod(config_root, stat.S_IRWXU)  # 700 권한
    except:
        pass  # 권한 변경 실패 시 무시

# 보안 강화된 토큰 파일명 생성
def get_token_filename():
    """예측하기 어려운 토큰 파일명 생성"""
    # 기본: 날짜 기반 (기존 방식과 호환)
    date_str = datetime.today().strftime('%Y%m%d')

    # 보안 강화: 랜덤 서픽스 추가 (선택사항)
    # 환경변수로 보안 레벨 제어
    if os.environ.get('KIS_SECURE_TOKEN', 'false').lower() == 'true':
        random_suffix = hashlib.md5(os.urandom(8)).hexdigest()[:8]
        return os.path.join(config_root, f"KIS_{date_str}_{random_suffix}.token")
    else:
        # 기존 방식 유지 (호환성)
        return os.path.join(config_root, f"KIS{date_str}")

token_tmp = get_token_filename()

# NOTE: Removed empty file creation at module import time (Bug #1)
# Empty token files cause authentication failures and should only be created
# when saving a valid token via save_token()

# 앱키, 앱시크리트, 토큰, 계좌번호 등 저장관리, 자신만의 경로와 파일명으로 설정하시기 바랍니다.
# pip install PyYAML (패키지설치)
with open(os.path.join(config_root, "kis_devlp.yaml"), encoding="UTF-8") as f:
    _cfg = yaml.safe_load(f)

_TRENV = None
_last_auth_time = datetime.now()
_autoReAuth = False
_DEBUG = False
_isPaper = False
_smartSleep = 0.1

# 기본 헤더값 정의
_base_headers = {
    "Content-Type": "application/json",
    "Accept": "text/plain",
    "charset": "UTF-8",
    "User-Agent": _cfg["my_agent"],
}

def _get_or_create_encryption_key():
    """암호화 키 생성 또는 로드"""
    key_file = os.path.join(config_root, ".token_key")

    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            key = f.read()
    else:
        # 새 암호화 키 생성
        key = Fernet.generate_key()
        with open(key_file, 'wb') as f:
            f.write(key)

        # 키 파일 권한 설정 (최고 보안)
        if os.name != 'nt':
            os.chmod(key_file, 0o600)
        else:
            # Windows: 파일 숨김 속성 설정
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(key_file, 2)  # FILE_ATTRIBUTE_HIDDEN
            except:
                pass

    return key

# 토큰 발급 받아 저장 (토큰값, 토큰 유효시간,1일, 6시간 이내 발급신청시는 기존 토큰값과 동일, 발급시 알림톡 발송)
def save_token(my_token: str, my_expired: str):
    """
    Save token securely with encryption and atomic write.

    Improvements:
    - Validates token data before saving (prevents empty token files)
    - Uses atomic write (temp file + rename) to prevent corruption
    - File locking prevents race conditions in multi-process scenarios
    - Cross-platform compatible (Windows and Unix)

    Args:
        my_token: The access token string
        my_expired: Token expiry datetime string (format: "YYYY-MM-DD HH:MM:SS")

    Raises:
        TokenFileError: If token data is invalid or write fails
    """
    # Validate token data BEFORE any file operations
    if not my_token or len(my_token) < 10:
        raise TokenFileError("Cannot save empty or invalid token")

    if not my_expired:
        raise TokenFileError("Cannot save token without expiry date")

    try:
        valid_date = datetime.strptime(my_expired, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        raise TokenFileError(f"Invalid expiry date format: {my_expired}") from e

    # Prepare token data
    token_data = {
        "token": my_token,
        "valid_date": valid_date.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at": datetime.now().isoformat(),
        "pid": os.getpid()
    }

    # Encrypt token data
    key = _get_or_create_encryption_key()
    fernet = Fernet(key)
    json_string = json.dumps(token_data, indent=2)
    encrypted_data = fernet.encrypt(json_string.encode('utf-8'))

    # Atomic write with file locking (prevents race conditions)
    lock_file = os.path.join(config_root, ".token_write.lock")

    try:
        with CrossPlatformFileLock(lock_file, timeout=30):
            # Use atomic write (temp file + rename)
            _atomic_write(token_tmp, encrypted_data)

            # Set secure file permissions
            _set_secure_file_permissions(token_tmp)

            # Clean up old token files
            cleanup_old_tokens()

            logging.info(f"✅ Encrypted token saved atomically: {token_tmp}")

    except TokenFileError:
        raise
    except Exception as e:
        raise TokenFileError(f"Failed to save token: {e}") from e



# 토큰 확인 (토큰값, 토큰 유효시간_1일, 6시간 이내 발급신청시는 기존 토큰값과 동일, 발급시 알림톡 발송)
def read_token() -> Optional[str]:
    """
    Read and validate token with auto-recovery.

    Improvements:
    - Auto-cleanup of empty token files (Issue #137 root cause)
    - Auto-cleanup of corrupted/unreadable token files
    - Auto-cleanup of expired tokens
    - Sorted by modification time (newest first)
    - Cross-platform compatible file deletion

    Returns:
        Valid token string or None if no valid token found
    """
    try:
        # Find all token files (multiple patterns for compatibility)
        token_files = list(Path(config_root).glob("KIS*.token")) + \
                      list(Path(config_root).glob("KIS20*"))

        # Also check current token_tmp path
        if os.path.exists(token_tmp) and Path(token_tmp) not in token_files:
            token_files.append(Path(token_tmp))

        if not token_files:
            logging.debug("No token files found")
            return None

        # Sort by modification time (newest first)
        token_files = sorted(token_files, key=lambda f: f.stat().st_mtime, reverse=True)

        # Try each token file, clean up invalid ones
        for token_file in token_files:
            try:
                file_size = token_file.stat().st_size

                # AUTO-RECOVERY: Delete empty files (Issue #137 fix)
                if file_size == 0:
                    logging.warning(f"⚠️  Found empty token file, deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

                # Try to read and decrypt
                key = _get_or_create_encryption_key()
                fernet = Fernet(key)

                token_data = None
                valid_date_str = None
                token = None

                # Try encrypted format first
                try:
                    with open(token_file, 'rb') as f:
                        encrypted_data = f.read()
                        if not encrypted_data:
                            logging.warning(f"⚠️  Empty token data, deleting: {token_file}")
                            _safe_delete(token_file)
                            continue
                        decrypted_data = fernet.decrypt(encrypted_data)
                        token_data = json.loads(decrypted_data.decode('utf-8'))

                    if token_data and 'valid_date' in token_data and 'token' in token_data:
                        valid_date_str = token_data['valid_date']
                        token = token_data['token']

                except Exception as decrypt_error:
                    # Try legacy JSON format
                    logging.debug(f"Trying legacy format for {token_file}: {decrypt_error}")
                    try:
                        with open(token_file, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if not content:
                                logging.warning(f"⚠️  Empty legacy token, deleting: {token_file}")
                                _safe_delete(token_file)
                                continue
                            token_data = json.loads(content)

                            if token_data and 'valid_date' in token_data and 'token' in token_data:
                                valid_date_str = token_data['valid_date']
                                token = token_data['token']
                    except:
                        # Try YAML format (oldest format)
                        try:
                            with open(token_file, 'r', encoding='UTF-8') as f:
                                content = f.read().strip()
                                if not content:
                                    logging.warning(f"⚠️  Empty YAML token, deleting: {token_file}")
                                    _safe_delete(token_file)
                                    continue
                                tkg_tmp = yaml.safe_load(content)

                                if tkg_tmp and 'valid-date' in tkg_tmp and 'token' in tkg_tmp:
                                    valid_date_str = datetime.strftime(tkg_tmp['valid-date'], "%Y-%m-%d %H:%M:%S")
                                    token = tkg_tmp['token']
                        except Exception as yaml_error:
                            # AUTO-RECOVERY: Delete corrupted files
                            logging.warning(f"⚠️  Corrupted token file, deleting: {token_file} ({yaml_error})")
                            _safe_delete(token_file)
                            continue

                # Validate token data
                if not valid_date_str or not token:
                    logging.warning(f"⚠️  Invalid token data (missing fields), deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

                # Check expiry
                try:
                    valid_date = datetime.strptime(valid_date_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    logging.warning(f"⚠️  Invalid date format in token, deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

                now = datetime.now()

                if valid_date > now:
                    logging.info(f"✅ Valid token found (expires: {valid_date})")
                    return token
                else:
                    # AUTO-RECOVERY: Delete expired tokens
                    logging.info(f"⏰ Token expired at {valid_date}, deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

            except Exception as file_error:
                # AUTO-RECOVERY: Delete unreadable files
                logging.warning(f"⚠️  Error reading token file, deleting: {token_file} ({file_error})")
                _safe_delete(token_file)
                continue

        # No valid token found after checking all files
        logging.info("No valid token found after checking all files")
        return None

    except Exception as e:
        logging.error(f"Error in read_token: {e}")
        return None

def _set_secure_file_permissions(file_path):
    """모든 OS에서 안전한 파일 권한 설정"""
    try:
        if os.name == 'nt':  # Windows
            # Windows에서 ACL 설정으로 소유자만 접근 가능
            try:
                import win32security
                import win32api
                import ntsecuritycon as con

                # 현재 사용자 SID 획득
                username = win32api.GetUserName()
                domain = win32api.GetComputerName()
                user_sid, domain, type = win32security.LookupAccountName(domain, username)

                # 새 ACL 생성 - 소유자만 접근
                sd = win32security.GetFileSecurity(file_path, win32security.DACL_SECURITY_INFORMATION)
                dacl = win32security.ACL()
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION,
                                         con.FILE_ALL_ACCESS, user_sid)
                sd.SetSecurityDescriptorDacl(1, dacl, 0)
                win32security.SetFileSecurity(file_path, win32security.DACL_SECURITY_INFORMATION, sd)

                logging.info(f"Windows ACL set for: {file_path}")
            except ImportError:
                # pywin32가 없으면 기본적인 숨김 속성만 설정
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(file_path, 2)  # FILE_ATTRIBUTE_HIDDEN
                    logging.warning(f"Set hidden attribute for: {file_path} (install pywin32 for better security)")
                except:
                    logging.warning(f"Could not set Windows file attributes for: {file_path}")
        else:  # Unix/Linux/Mac
            # 600 권한 (소유자만 읽기/쓰기)
            os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
            logging.info(f"Unix permissions set to 600 for: {file_path}")

    except Exception as e:
        logging.error(f"Failed to set secure file permissions for {file_path}: {e}")
        # 권한 설정 실패는 치명적 오류로 처리
        raise SecurityError(f"Cannot secure file permissions: {e}")

# 오래된 토큰 파일 정리
def cleanup_old_tokens():
    """1일 이상 된 토큰 파일 자동 삭제"""
    try:
        # 모든 토큰 파일 찾기
        token_patterns = ["KIS*.token", "KIS20*"]  # 여러 패턴 지원
        token_files = []

        for pattern in token_patterns:
            token_files.extend(Path(config_root).glob(pattern))

        now = datetime.now()
        for token_file in token_files:
            # 파일 수정 시간 확인
            file_mtime = datetime.fromtimestamp(token_file.stat().st_mtime)
            age_days = (now - file_mtime).days

            # 1일 이상 된 파일 삭제
            if age_days > 1:
                try:
                    os.remove(token_file)
                    logging.info(f"Cleaned up old token: {token_file}")
                except Exception as e:
                    logging.warning(f"Could not delete old token: {e}")

    except Exception as e:
        logging.error(f"Error during token cleanup: {e}")


# ============== Credential Validation ==============
def validate_credentials(app_key: str, mode: str) -> Tuple[bool, str]:
    """
    Validate app key matches the trading mode to prevent credential mismatch errors.

    - Real mode (prod): App key should start with 'PS' but NOT 'PSVT'
    - Demo mode (vps): App key should start with 'PSVT'

    Args:
        app_key: The KIS app key
        mode: 'prod' for real trading, 'vps' for paper trading

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not app_key or len(app_key) < 10:
        return False, "App key is empty or too short"

    is_demo_key = app_key.startswith('PSVT')

    if mode == 'prod' and is_demo_key:
        return False, (
            "CREDENTIAL MISMATCH! Using DEMO app key (PSVT*) in REAL mode.\n"
            "Check kis_devlp.yaml - 'my_app' should be your real trading key (PS*, not PSVT*).\n"
            "This is the most common cause of 'Error Code: 500' authentication failures."
        )

    if mode == 'vps' and not is_demo_key and app_key.startswith('PS'):
        return False, (
            "CREDENTIAL MISMATCH! Using REAL app key (PS*) in DEMO mode.\n"
            "Check kis_devlp.yaml - 'paper_app' should be your demo key (PSVT*).\n"
            "Using real credentials in demo mode may cause unexpected behavior."
        )

    return True, ""


# ============== Cross-Platform File Lock ==============
class CrossPlatformFileLock:
    """Cross-platform file lock using atomic file creation (works on Windows and Unix)"""

    def __init__(self, lock_path: str, timeout: float = 30.0):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self._lock_fd = None

    def acquire(self) -> bool:
        """Attempt to acquire lock within timeout"""
        start_time = time.time()

        while time.time() - start_time < self.timeout:
            try:
                # O_CREAT | O_EXCL: Only create if file doesn't exist (atomic)
                self._lock_fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(self._lock_fd, str(os.getpid()).encode())
                return True
            except FileExistsError:
                # Lock file exists - check if stale (older than 5 minutes)
                try:
                    lock_age = time.time() - self.lock_path.stat().st_mtime
                    if lock_age > 300:  # 5 minutes
                        logging.warning(f"Removing stale lock file: {self.lock_path}")
                        self.lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(0.1)
            except OSError as e:
                logging.warning(f"Lock acquisition error: {e}")
                time.sleep(0.1)
        return False

    def release(self):
        """Release the lock"""
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except:
                pass
            self._lock_fd = None
        try:
            self.lock_path.unlink()
        except:
            pass

    def __enter__(self):
        if not self.acquire():
            raise TokenFileError(f"Could not acquire file lock: {self.lock_path}")
        return self

    def __exit__(self, *args):
        self.release()


# ============== Retry Logic for Token Request ==============
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    retry=retry_if_exception_type((requests.RequestException,)),
    reraise=True
)
def _request_token_with_retry(url: str, params: dict, headers: dict) -> dict:
    """
    Request token from KIS API with retry logic for transient failures.

    - Retries up to 3 times with exponential backoff (5s, 10s, 20s)
    - Does NOT retry on 401/403 (authentication failures)
    - Raises TokenRequestError on failure
    """
    try:
        res = requests.post(url, data=json.dumps(params), headers=headers, timeout=30)
    except requests.RequestException as e:
        logging.warning(f"Token request network error (will retry): {e}")
        raise

    if res.status_code == 200:
        return res.json()

    # Non-retryable authentication errors
    if res.status_code in (401, 403):
        error_msg = f"Authentication failed: HTTP {res.status_code}"
        logging.error(f"{error_msg} - {res.text}")
        raise TokenRequestError(error_msg, res.status_code, res.text)

    # Retryable server errors (500, 502, 503, 504)
    if res.status_code >= 500:
        error_msg = f"Server error: HTTP {res.status_code}"
        logging.warning(f"{error_msg} (will retry) - {res.text}")
        # Raise requests exception to trigger retry
        raise requests.RequestException(error_msg)

    # Other client errors - don't retry
    error_msg = f"Token request failed: HTTP {res.status_code}"
    logging.error(f"{error_msg} - {res.text}")
    raise TokenRequestError(error_msg, res.status_code, res.text)


# ============== Safe File Delete (Windows compatible) ==============
def _safe_delete(file_path: Path, max_retries: int = 3) -> bool:
    """Safely delete a file with retries (handles Windows locked files)"""
    for attempt in range(max_retries):
        try:
            file_path.unlink()
            return True
        except PermissionError:
            if os.name == 'nt':
                # Windows: File may be locked by another process
                time.sleep(0.2 * (attempt + 1))
            else:
                raise
        except FileNotFoundError:
            return True  # Already deleted

    logging.warning(f"Could not delete file (may be in use): {file_path}")
    return False


# ============== Atomic Write (Cross-platform) ==============
def _atomic_write(file_path_str: str, data: bytes) -> bool:
    """
    Write data to file atomically (write to temp, then rename).
    Works on both Windows and Unix.
    """
    file_path = Path(file_path_str)
    temp_path = None

    try:
        # Write to temp file first
        fd, temp_path = tempfile.mkstemp(
            dir=str(file_path.parent),
            prefix=".tmp_token_"
        )
        try:
            os.write(fd, data)
            os.fsync(fd)  # Ensure data is written to disk
        finally:
            os.close(fd)

        # Set Unix permissions (ignored on Windows)
        if os.name != 'nt':
            os.chmod(temp_path, 0o600)

        # Windows: Must delete target file before rename
        if os.name == 'nt' and file_path.exists():
            for attempt in range(3):
                try:
                    file_path.unlink()
                    break
                except PermissionError:
                    time.sleep(0.1 * (attempt + 1))
            else:
                # If can't delete, try moving to backup
                backup_path = file_path.with_suffix('.old')
                try:
                    shutil.move(str(file_path), str(backup_path))
                except:
                    raise TokenFileError(f"Cannot replace locked file: {file_path}")

        # Atomic rename
        shutil.move(temp_path, str(file_path))
        return True

    except Exception as e:
        # Clean up temp file on failure
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass
        raise TokenFileError(f"Atomic write failed: {e}")


# 토큰 유효시간 체크해서 만료된 토큰이면 재발급처리
def _getBaseHeader():
    if _autoReAuth:
        reAuth()
    return copy.deepcopy(_base_headers)


# 가져오기 : 앱키, 앱시크리트, 종합계좌번호(계좌번호 중 숫자8자리), 계좌상품코드(계좌번호 중 숫자2자리), 토큰, 도메인
def _setTRENV(cfg):
    nt1 = namedtuple(
        "KISEnv",
        ["my_app", "my_sec", "my_acct", "my_prod", "my_htsid", "my_token", "my_url", "my_url_ws"],
    )
    d = {
        "my_app": cfg["my_app"],  # 앱키
        "my_sec": cfg["my_sec"],  # 앱시크리트
        "my_acct": cfg["my_acct"],  # 종합계좌번호(8자리)
        "my_prod": cfg["my_prod"],  # 계좌상품코드(2자리)
        "my_htsid": cfg["my_htsid"],  # HTS ID
        "my_token": cfg["my_token"],  # 토큰
        "my_url": cfg[
            "my_url"
        ],  # 실전 도메인 (https://openapi.koreainvestment.com:9443)
        "my_url_ws": cfg["my_url_ws"],
    }  # 모의 도메인 (https://openapivts.koreainvestment.com:29443)

    # print(cfg['my_app'])
    global _TRENV
    _TRENV = nt1(**d)


def isPaperTrading():  # 모의투자 매매
    return _isPaper


# 실전투자면 'prod', 모의투자면 'vps'를 셋팅 하시기 바랍니다.
def changeTREnv(token_key, svr="prod", product=_cfg["my_prod"]):
    cfg = dict()

    global _isPaper
    if svr == "prod":  # 실전투자
        ak1 = "my_app"  # 실전투자용 앱키
        ak2 = "my_sec"  # 실전투자용 앱시크리트
        _isPaper = False
        _smartSleep = 0.05
    elif svr == "vps":  # 모의투자
        ak1 = "paper_app"  # 모의투자용 앱키
        ak2 = "paper_sec"  # 모의투자용 앱시크리트
        _isPaper = True
        _smartSleep = 0.5

    cfg["my_app"] = _cfg[ak1]
    cfg["my_sec"] = _cfg[ak2]

    if svr == "prod" and product == "01":  # 실전투자 주식투자, 위탁계좌, 투자계좌
        cfg["my_acct"] = _cfg["my_acct_stock"]
    elif svr == "prod" and product == "03":  # 실전투자 선물옵션(파생)
        cfg["my_acct"] = _cfg["my_acct_future"]
    elif svr == "prod" and product == "08":  # 실전투자 해외선물옵션(파생)
        cfg["my_acct"] = _cfg["my_acct_future"]
    elif svr == "prod" and product == "22":  # 실전투자 개인연금저축계좌
        cfg["my_acct"] = _cfg["my_acct_stock"]
    elif svr == "prod" and product == "29":  # 실전투자 퇴직연금계좌
        cfg["my_acct"] = _cfg["my_acct_stock"]
    elif svr == "vps" and product == "01":  # 모의투자 주식투자, 위탁계좌, 투자계좌
        cfg["my_acct"] = _cfg["my_paper_stock"]
    elif svr == "vps" and product == "03":  # 모의투자 선물옵션(파생)
        cfg["my_acct"] = _cfg["my_paper_future"]

    cfg["my_prod"] = product
    cfg["my_htsid"] = _cfg["my_htsid"]
    cfg["my_url"] = _cfg[svr]

    try:
        my_token = _TRENV.my_token if _TRENV is not None else ""
    except (AttributeError, TypeError):
        my_token = ""
    cfg["my_token"] = my_token if token_key else token_key
    cfg["my_url_ws"] = _cfg["ops" if svr == "prod" else "vops"]

    # print(cfg)
    _setTRENV(cfg)


def _getResultObject(json_data):
    _tc_ = namedtuple("res", json_data.keys())

    return _tc_(**json_data)


# Token 발급, 유효기간 1일, 6시간 이내 발급시 기존 token값 유지, 발급시 알림톡 무조건 발송
# 모의투자인 경우  svr='vps', 투자계좌(01)이 아닌경우 product='XX' 변경하세요 (계좌번호 뒤 2자리)
def auth(svr="prod", product=_cfg["my_prod"], url=None):
    """
    Authenticate with KIS API and obtain access token.

    Improvements in this version:
    - Credential validation (detects demo/real key mismatch)
    - Retry logic for transient network failures
    - Raises exceptions instead of silent failure
    - Auto-cleanup of empty/corrupted token files

    Args:
        svr: 'prod' for real trading, 'vps' for paper trading
        product: Account product code (default from config)
        url: API URL (optional, auto-generated from svr)

    Raises:
        CredentialMismatchError: App key doesn't match trading mode
        TokenRequestError: Failed to obtain token from KIS API
        TokenFileError: Failed to save token to file
    """
    p = {
        "grant_type": "client_credentials",
    }

    # Determine which keys to use based on server type
    if svr == "prod":  # 실전투자
        ak1 = "my_app"  # 앱키 (실전투자용)
        ak2 = "my_sec"  # 앱시크리트 (실전투자용)
    elif svr == "vps":  # 모의투자
        ak1 = "paper_app"  # 앱키 (모의투자용)
        ak2 = "paper_sec"  # 앱시크리트 (모의투자용)
    else:
        raise ValueError(f"Invalid server type: {svr}. Must be 'prod' or 'vps'")

    # Get app key and secret from config
    app_key = _cfg.get(ak1)
    app_secret = _cfg.get(ak2)

    if not app_key or not app_secret:
        raise CredentialMismatchError(
            f"Missing credentials in kis_devlp.yaml: {ak1}={app_key is not None}, {ak2}={app_secret is not None}"
        )

    # CRITICAL: Validate credential/mode match (Issue #137 root cause)
    is_valid, error_msg = validate_credentials(app_key, svr)
    if not is_valid:
        logging.error(f"❌ {error_msg}")
        raise CredentialMismatchError(error_msg)

    p["appkey"] = app_key
    p["appsecret"] = app_secret

    # Check for existing valid token
    saved_token = read_token()

    if saved_token is None:
        # No valid token - request new one
        token_url = f"{_cfg[svr]}/oauth2/tokenP"
        logging.info(f"Requesting new token from KIS API ({svr} mode)...")

        try:
            # Use retry logic for transient failures
            result = _request_token_with_retry(token_url, p, _getBaseHeader())

            my_token = result.get("access_token")
            my_expired = result.get("access_token_token_expired")

            if not my_token or not my_expired:
                raise TokenRequestError(
                    "Invalid response from KIS API: missing token or expiry",
                    status_code=200,
                    response_text=str(result)
                )

            # Save the new token
            save_token(my_token, my_expired)
            logging.info(f"✅ New token obtained and saved (expires: {my_expired})")

        except TokenRequestError as e:
            logging.error(f"❌ Token request failed: {e}")
            logging.error(f"   Status Code: {e.status_code}")
            logging.error(f"   Response: {e.response_text}")
            # Re-raise with clear error message
            raise

        except Exception as e:
            logging.error(f"❌ Unexpected error during token request: {e}")
            raise TokenRequestError(f"Unexpected error: {e}")

    else:
        my_token = saved_token
        logging.info("✅ Using existing valid token")

    # Set up environment with token
    changeTREnv(my_token, svr, product)

    # Update base headers
    if _TRENV is not None:
        _base_headers["authorization"] = f"Bearer {my_token}"
        _base_headers["appkey"] = _TRENV.my_app
        _base_headers["appsecret"] = _TRENV.my_sec

    global _last_auth_time
    _last_auth_time = datetime.now()

    if _DEBUG:
        print(f"[{_last_auth_time}] => get AUTH Key completed!")


# end of initialize, 토큰 재발급, 토큰 발급시 유효시간 1일
# 프로그램 실행시 _last_auth_time에 저장하여 유효시간 체크, 유효시간 만료시 토큰 발급 처리
def reAuth(svr="prod", product=_cfg["my_prod"]):
    n2 = datetime.now()
    # BUG FIX: Changed .seconds to .total_seconds()
    # .seconds only returns seconds within the current day (0-86399)
    # .total_seconds() returns the total duration in seconds
    # Also reduced to 23 hours (82800s) for safety margin before 24h expiry
    if (n2 - _last_auth_time).total_seconds() >= 82800:  # 23시간 (안전 마진)
        logging.info("Token approaching expiry, re-authenticating...")
        auth(svr, product)


def getEnv():
    return _cfg


def smart_sleep():
    if _DEBUG:
        print(f"[RateLimit] Sleeping {_smartSleep}s ")

    time.sleep(_smartSleep)


def getTREnv():
    if _TRENV is None:
        raise RuntimeError("인증이 완료되지 않았습니다. auth() 함수를 먼저 호출하세요.")
    return _TRENV


# 주문 API에서 사용할 hash key값을 받아 header에 설정해 주는 함수
# 현재는 hash key 필수 사항아님, 생략가능, API 호출과정에서 변조 우려를 하는 경우 사용
# Input: HTTP Header, HTTP post param
# Output: None
def set_order_hash_key(h, p):
    url = f"{getTREnv().my_url}/uapi/hashkey"  # hashkey 발급 API URL

    res = requests.post(url, data=json.dumps(p), headers=h)
    rescode = res.status_code
    if rescode == 200:
        h["hashkey"] = _getResultObject(res.json()).HASH
    else:
        print("Error:", rescode)


# API 호출 응답에 필요한 처리 공통 함수
class APIResp:
    def __init__(self, resp):
        self._rescode = resp.status_code
        self._resp = resp
        self._header = self._setHeader()
        self._body = self._setBody()
        self._err_code = self._body.msg_cd
        self._err_message = self._body.msg1

    def getResCode(self):
        return self._rescode

    def _setHeader(self):
        fld = dict()
        for x in self._resp.headers.keys():
            if x.islower():
                fld[x] = self._resp.headers.get(x)
        _th_ = namedtuple("header", fld.keys())

        return _th_(**fld)

    def _setBody(self):
        _tb_ = namedtuple("body", self._resp.json().keys())

        return _tb_(**self._resp.json())

    def getHeader(self):
        return self._header

    def getBody(self):
        return self._body

    def getResponse(self):
        return self._resp

    def isOK(self):
        try:
            if self.getBody().rt_cd == "0":
                return True
            else:
                return False
        except:
            return False

    def getErrorCode(self):
        return self._err_code

    def getErrorMessage(self):
        return self._err_message

    def printAll(self):
        print("<Header>")
        for x in self.getHeader()._fields:
            print(f"\t-{x}: {getattr(self.getHeader(), x)}")
        print("<Body>")
        for x in self.getBody()._fields:
            print(f"\t-{x}: {getattr(self.getBody(), x)}")

    def printError(self, url):
        print(
            "-------------------------------\nError in response: ",
            self.getResCode(),
            " url=",
            url,
        )
        print(
            "rt_cd : ",
            self.getBody().rt_cd,
            "/ msg_cd : ",
            self.getErrorCode(),
            "/ msg1 : ",
            self.getErrorMessage(),
        )
        print("-------------------------------")

    # end of class APIResp


class APIRespError(APIResp):
    def __init__(self, status_code, error_text):
        # 부모 생성자 호출하지 않고 직접 초기화
        self.status_code = status_code
        self.error_text = error_text
        self._error_code = str(status_code)
        self._error_message = error_text

    def isOK(self):
        return False

    def getErrorCode(self):
        return self._error_code

    def getErrorMessage(self):
        return self._error_message

    def getBody(self):
        # 빈 객체 리턴 (속성 접근 시 AttributeError 방지)
        class EmptyBody:
            def __getattr__(self, name):
                return None

        return EmptyBody()

    def getHeader(self):
        # 빈 객체 리턴
        class EmptyHeader:
            tr_cont = ""

            def __getattr__(self, name):
                return ""

        return EmptyHeader()

    def printAll(self):
        print(f"=== ERROR RESPONSE ===")
        print(f"Status Code: {self.status_code}")
        print(f"Error Message: {self.error_text}")
        print(f"======================")

    def printError(self, url=""):
        print(f"Error Code : {self.status_code} | {self.error_text}")
        if url:
            print(f"URL: {url}")


########### API call wrapping : API 호출 공통


def _url_fetch(
        api_url, ptr_id, tr_cont, params, appendHeaders=None, postFlag=False, hashFlag=True
):
    url = f"{getTREnv().my_url}{api_url}"

    headers = _getBaseHeader()  # 기본 header 값 정리

    # 추가 Header 설정
    tr_id = ptr_id
    if ptr_id[0] in ("T", "J", "C"):  # 실전투자용 TR id 체크
        if isPaperTrading():  # 모의투자용 TR id 식별
            tr_id = "V" + ptr_id[1:]

    headers["tr_id"] = tr_id  # 트랜젝션 TR id
    headers["custtype"] = "P"  # 일반(개인고객,법인고객) "P", 제휴사 "B"
    headers["tr_cont"] = tr_cont  # 트랜젝션 TR id

    if appendHeaders is not None:
        if len(appendHeaders) > 0:
            for x in appendHeaders.keys():
                headers[x] = appendHeaders.get(x)

    if _DEBUG:
        print("< Sending Info >")
        print(f"URL: {url}, TR: {tr_id}")
        print(f"<header>\n{headers}")
        print(f"<body>\n{params}")

    if postFlag:
        # if (hashFlag): set_order_hash_key(headers, params)
        res = requests.post(url, headers=headers, data=json.dumps(params))
    else:
        res = requests.get(url, headers=headers, params=params)

    if res.status_code == 200:
        ar = APIResp(res)
        if _DEBUG:
            ar.printAll()
        return ar
    else:
        print("Error Code : " + str(res.status_code) + " | " + res.text)
        return APIRespError(res.status_code, res.text)


# auth()
# print("Pass through the end of the line")


########### New - websocket 대응

_base_headers_ws = {
    "content-type": "utf-8",
}


def _getBaseHeader_ws():
    if _autoReAuth:
        reAuth_ws()

    return copy.deepcopy(_base_headers_ws)


def auth_ws(svr="prod", product=_cfg["my_prod"]):
    p = {"grant_type": "client_credentials"}
    if svr == "prod":
        ak1 = "my_app"
        ak2 = "my_sec"
    elif svr == "vps":
        ak1 = "paper_app"
        ak2 = "paper_sec"

    p["appkey"] = _cfg[ak1]
    p["secretkey"] = _cfg[ak2]

    url = f"{_cfg[svr]}/oauth2/Approval"
    res = requests.post(url, data=json.dumps(p), headers=_getBaseHeader())  # 토큰 발급
    rescode = res.status_code
    if rescode == 200:  # 토큰 정상 발급
        approval_key = _getResultObject(res.json()).approval_key
    else:
        print("Get Approval token fail!\nYou have to restart your app!!!")
        return

    changeTREnv(None, svr, product)

    _base_headers_ws["approval_key"] = approval_key

    global _last_auth_time
    _last_auth_time = datetime.now()

    if _DEBUG:
        print(f"[{_last_auth_time}] => get AUTH Key completed!")


def reAuth_ws(svr="prod", product=_cfg["my_prod"]):
    n2 = datetime.now()
    if (n2 - _last_auth_time).seconds >= 86400:
        auth_ws(svr, product)


def data_fetch(tr_id, tr_type, params, appendHeaders=None) -> dict:
    headers = _getBaseHeader_ws()  # 기본 header 값 정리

    headers["tr_type"] = tr_type
    headers["custtype"] = "P"

    if appendHeaders is not None:
        if len(appendHeaders) > 0:
            for x in appendHeaders.keys():
                headers[x] = appendHeaders.get(x)

    if _DEBUG:
        print("< Sending Info >")
        print(f"TR: {tr_id}")
        print(f"<header>\n{headers}")

    inp = {
        "tr_id": tr_id,
    }
    inp.update(params)

    return {"header": headers, "body": {"input": inp}}


# iv, ekey, encrypt 는 각 기능 메소드 파일에 저장할 수 있도록 dict에서 return 하도록
def system_resp(data):
    isPingPong = False
    isUnSub = False
    isOk = False
    tr_msg = None
    tr_key = None
    encrypt, iv, ekey = None, None, None

    rdic = json.loads(data)

    tr_id = rdic["header"]["tr_id"]
    if tr_id != "PINGPONG":
        tr_key = rdic["header"]["tr_key"]
        encrypt = rdic["header"]["encrypt"]
    if rdic.get("body", None) is not None:
        isOk = True if rdic["body"]["rt_cd"] == "0" else False
        tr_msg = rdic["body"]["msg1"]
        # 복호화를 위한 key 를 추출
        if "output" in rdic["body"]:
            iv = rdic["body"]["output"]["iv"]
            ekey = rdic["body"]["output"]["key"]
        isUnSub = True if tr_msg[:5] == "UNSUB" else False
    else:
        isPingPong = True if tr_id == "PINGPONG" else False

    nt2 = namedtuple(
        "SysMsg",
        [
            "isOk",
            "tr_id",
            "tr_key",
            "isUnSub",
            "isPingPong",
            "tr_msg",
            "iv",
            "ekey",
            "encrypt",
        ],
    )
    d = {
        "isOk": isOk,
        "tr_id": tr_id,
        "tr_key": tr_key,
        "tr_msg": tr_msg,
        "isUnSub": isUnSub,
        "isPingPong": isPingPong,
        "iv": iv,
        "ekey": ekey,
        "encrypt": encrypt,
    }

    return nt2(**d)


def aes_cbc_base64_dec(key, iv, cipher_text):
    if key is None or iv is None:
        raise AttributeError("key and iv cannot be None")

    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    return bytes.decode(unpad(cipher.decrypt(b64decode(cipher_text)), AES.block_size))


#####
open_map: dict = {}


def add_open_map(
        name: str,
        request: Callable[[str, str, ...], (dict, list[str])],
        data: str | list[str],
        kwargs: dict = None,
):
    if open_map.get(name, None) is None:
        open_map[name] = {
            "func": request,
            "items": [],
            "kwargs": kwargs,
        }

    if type(data) is list:
        open_map[name]["items"] += data
    elif type(data) is str:
        open_map[name]["items"].append(data)


data_map: dict = {}


def add_data_map(
        tr_id: str,
        columns: list = None,
        encrypt: str = None,
        key: str = None,
        iv: str = None,
):
    if data_map.get(tr_id, None) is None:
        data_map[tr_id] = {"columns": [], "encrypt": False, "key": None, "iv": None}

    if columns is not None:
        data_map[tr_id]["columns"] = columns

    if encrypt is not None:
        data_map[tr_id]["encrypt"] = encrypt

    if key is not None:
        data_map[tr_id]["key"] = key

    if iv is not None:
        data_map[tr_id]["iv"] = iv


class KISWebSocket:
    api_url: str = ""
    on_result: Callable[
        [websockets.ClientConnection, str, pd.DataFrame, dict], None
    ] = None
    result_all_data: bool = False

    retry_count: int = 0
    amx_retries: int = 0

    # init
    def __init__(self, api_url: str, max_retries: int = 3):
        self.api_url = api_url
        self.max_retries = max_retries

    # private
    async def __subscriber(self, ws: websockets.ClientConnection):
        async for raw in ws:
            logging.info("received message >> %s" % raw)
            show_result = False

            df = pd.DataFrame()

            if raw[0] in ["0", "1"]:
                d1 = raw.split("|")
                if len(d1) < 4:
                    raise ValueError("data not found...")

                tr_id = d1[1]

                dm = data_map[tr_id]
                d = d1[3]
                if dm.get("encrypt", None) == "Y":
                    d = aes_cbc_base64_dec(dm["key"], dm["iv"], d)

                df = pd.read_csv(
                    StringIO(d), header=None, sep="^", names=dm["columns"], dtype=object
                )

                show_result = True

            else:
                rsp = system_resp(raw)

                tr_id = rsp.tr_id
                add_data_map(
                    tr_id=rsp.tr_id, encrypt=rsp.encrypt, key=rsp.ekey, iv=rsp.iv
                )

                if rsp.isPingPong:
                    print(f"### RECV [PINGPONG] [{raw}]")
                    await ws.pong(raw)
                    print(f"### SEND [PINGPONG] [{raw}]")

                if self.result_all_data:
                    show_result = True

            if show_result is True and self.on_result is not None:
                self.on_result(ws, tr_id, df, data_map[tr_id])

    async def __runner(self):
        if len(open_map.keys()) > 40:
            raise ValueError("Subscription's max is 40")

        url = f"{getTREnv().my_url_ws}{self.api_url}"

        while self.retry_count < self.max_retries:
            try:
                async with websockets.connect(url) as ws:
                    # request subscribe
                    for name, obj in open_map.items():
                        await self.send_multiple(
                            ws, obj["func"], "1", obj["items"], obj["kwargs"]
                        )

                    # subscriber
                    await asyncio.gather(
                        self.__subscriber(ws),
                    )
            except Exception as e:
                print("Connection exception >> ", e)
                self.retry_count += 1
                await asyncio.sleep(1)

    # func
    @classmethod
    async def send(
            cls,
            ws: websockets.ClientConnection,
            request: Callable[[str, str, ...], (dict, list[str])],
            tr_type: str,
            data: str,
            kwargs: dict = None,
    ):
        k = {} if kwargs is None else kwargs
        msg, columns = request(tr_type, data, **k)

        add_data_map(tr_id=msg["body"]["input"]["tr_id"], columns=columns)

        logging.info("send message >> %s" % json.dumps(msg))

        await ws.send(json.dumps(msg))
        smart_sleep()

    async def send_multiple(
            self,
            ws: websockets.ClientConnection,
            request: Callable[[str, str, ...], (dict, list[str])],
            tr_type: str,
            data: list | str,
            kwargs: dict = None,
    ):
        if type(data) is str:
            await self.send(ws, request, tr_type, data, kwargs)
        elif type(data) is list:
            for d in data:
                await self.send(ws, request, tr_type, d, kwargs)
        else:
            raise ValueError("data must be str or list")

    @classmethod
    def subscribe(
            cls,
            request: Callable[[str, str, ...], (dict, list[str])],
            data: list | str,
            kwargs: dict = None,
    ):
        add_open_map(request.__name__, request, data, kwargs)

    def unsubscribe(
            self,
            ws: websockets.ClientConnection,
            request: Callable[[str, str, ...], (dict, list[str])],
            data: list | str,
    ):
        self.send_multiple(ws, request, "2", data)

    # start
    def start(
            self,
            on_result: Callable[
                [websockets.ClientConnection, str, pd.DataFrame, dict], None
            ],
            result_all_data: bool = False,
    ):
        self.on_result = on_result
        self.result_all_data = result_all_data
        try:
            asyncio.run(self.__runner())
        except KeyboardInterrupt:
            print("Closing by KeyboardInterrupt")
