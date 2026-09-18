"""
ops/run_server.py
=================
OFM sunucu gozcusu (Windows). Oturum acilisinda Gorev Zamanlayici `pythonw ops\\run_server.py` ile baslatir
(ops/ofm_server.ps1 -Action Install); pencere acmaz, tek kopya calisir.

    1) PostgreSQL erisilemiyorsa Docker Desktop'i baslatir (kapaliysa) ve `docker compose up -d` calistirir.
    2) Streamlit'i (web_app.py) alt surec olarak baslatir. Surec kapanirsa ya da saglik ucu
       (/_stcore/health) ust uste yanit vermezse yeniden baslatir; art arda coken surecte bekleme uzar.
    3) Her OFM_TICK_SECONDS saniyede `main.py world-tick --all`: suresi dolan ya da herkesin hazir oldugu
       paylasilan dunyalarin haftasi oynatilir (dunya kilidi cift oynatmayi engeller).

Neden Windows servisi / IIS degil: veritabani Docker Desktop'ta calisir ve Docker Desktop kullanici oturumu
acilinca baslar; oturumdan once calisan bir servis veritabanini bulamaz. IIS yalnizca ters vekil olurdu
(URL Rewrite + ARR + WebSocket) ve Python surecini yine ayrica yonetmek gerekirdi.

Ortam degiskenleri: OFM_PORT (8501), OFM_TICK_SECONDS (300), OFM_COMPOSE_PROJECT (cm).

Canli kopya: sunucu E:/cm-live git calisma agacindan (yalnizca commit edilmis kod) calisir; gelistirme E:/cm'de
surer. Yeni surumu yayina almak: ops/ofm_server.ps1 -Action Deploy (canli agaci main'e tasir, yeniden baslatir).
Gunlukler: logs/ofm_server.log, logs/streamlit.log (5 MB'ta doner).
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
PORT = int(os.getenv("OFM_PORT", "8501"))
TICK_SECONDS = int(os.getenv("OFM_TICK_SECONDS", "300"))
COMPOSE_PROJECT = os.getenv("OFM_COMPOSE_PROJECT", "cm")      # canli kopya (E:/cm-live) ayni konteyneri kullansin
TICK_TIMEOUT = 900                       # buyuk dunyada bir hafta ilerlemesi uzun surebilir
HEALTH_INTERVAL = 30
HEALTH_FAILURES_BEFORE_RESTART = 4       # ~2 dakika yanitsiz: yeniden baslat
STARTUP_GRACE = 90                       # ilk acilista saglik ucu gec yanit verebilir
CRASH_WINDOW = 300
CRASH_LIMIT = 5                          # 5 dakikada 5 cokus: uzun bekleme
RESTART_DELAY, CRASH_LOOP_DELAY = 10, 120
LOG_MAX_BYTES = 5 * 1024 * 1024
DOCKER_DESKTOP = Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
MUTEX_NAME = "Local\\OFM_Server"
CREATE_NO_WINDOW = 0x08000000

log = logging.getLogger("ofm_server")


# ---------------------------------------------------------------------------------------------- saf yardimcilar

def python_console_exe(executable: str = sys.executable) -> str:
    """pythonw ile calisan gozcunun alt surecleri icin python.exe (stdout/stderr gunluge yazilabilsin)."""
    path = Path(executable)
    if path.name.lower() == "pythonw.exe" and path.with_name("python.exe").exists():
        return str(path.with_name("python.exe"))
    return executable


def streamlit_command(port: int = PORT, python: str | None = None) -> list[str]:
    """Uretim benzeri Streamlit komutu: dosya izleyici kapali (kod degisikligi oturumu yeniden baslatmaz)."""
    return [python or python_console_exe(), "-m", "streamlit", "run", "web_app.py",
            "--server.port", str(port), "--server.headless", "true",
            "--server.fileWatcherType", "none", "--server.runOnSave", "false",
            "--browser.gatherUsageStats", "false"]


def tick_command(python: str | None = None) -> list[str]:
    return [python or python_console_exe(), "main.py", "world-tick", "--all"]


def restart_delay(crash_times: list[float], now: float) -> int:
    """Son CRASH_WINDOW saniyedeki cokus sayisina gore bekleme (listeyi yerinde budar)."""
    crash_times[:] = [t for t in crash_times if now - t <= CRASH_WINDOW]
    return CRASH_LOOP_DELAY if len(crash_times) >= CRASH_LIMIT else RESTART_DELAY


# ---------------------------------------------------------------------------------------------- sistem

def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "ofm_server.log", maxBytes=LOG_MAX_BYTES, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _single_instance() -> bool:
    """Ayni kullanici oturumunda ikinci gozcu hemen cikar (isimli mutex; surec olunce isletim sistemi birakir)."""
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle or kernel32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
        return False
    globals()["_MUTEX_HANDLE"] = handle                       # surec boyunca acik kalsin
    return True


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        return done.returncode, (done.stdout + done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def db_ready() -> bool:
    code, _ = _run([python_console_exe(), "-c",
                    "import sys, database; sys.exit(0 if database.wait_for_db(retries=1, delay=0, verbose=False) else 1)"],
                   timeout=60)
    return code == 0


def ensure_database() -> None:
    """Veritabani hazir olana kadar: Docker Desktop'i ac, konteyneri kaldir, bekle."""
    started_desktop = False
    while not db_ready():
        code, _ = _run(["docker", "info"], timeout=60)
        if code != 0:
            if not started_desktop and DOCKER_DESKTOP.exists():
                log.info("Docker calismiyor; Docker Desktop baslatiliyor.")
                subprocess.Popen([str(DOCKER_DESKTOP)], cwd=DOCKER_DESKTOP.parent)
                started_desktop = True
            else:
                log.info("Docker henuz hazir degil; bekleniyor.")
        else:
            code, out = _run(["docker", "compose", "-p", COMPOSE_PROJECT, "up", "-d"], timeout=180)
            log.info("docker compose up -d -> %s %s", code, out[-300:])
        time.sleep(15)
    log.info("Veritabani hazir.")


def healthy(port: int = PORT, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=timeout) as resp:
            return resp.status == 200
    except OSError:
        return False


def port_in_use(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _rotate(path: Path) -> None:
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        backup = path.with_suffix(path.suffix + ".1")
        backup.unlink(missing_ok=True)
        path.rename(backup)


def start_streamlit() -> subprocess.Popen:
    log_path = LOG_DIR / "streamlit.log"
    _rotate(log_path)
    out = open(log_path, "a", encoding="utf-8")             # noqa: SIM115 -- alt surec yasadikca acik
    proc = subprocess.Popen(streamlit_command(), cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    log.info("Streamlit baslatildi (pid %s, port %s).", proc.pid, PORT)
    return proc


def tick_loop(stop: threading.Event) -> None:
    while not stop.wait(TICK_SECONDS):
        code, out = _run(tick_command(), timeout=TICK_TIMEOUT)
        if code != 0 or "İlerlemesi gereken dünya yok" not in out:
            log.info("world-tick -> %s %s", code, out[-800:])


def supervise() -> None:
    ensure_database()
    stop = threading.Event()
    threading.Thread(target=tick_loop, args=(stop,), name="world-tick", daemon=True).start()
    proc: subprocess.Popen | None = None
    crash_times: list[float] = []
    failures, started_at = 0, 0.0
    while True:
        if proc is None:
            if port_in_use():
                if healthy():                                    # onceki gozcuden kalan surec hala hizmette
                    time.sleep(HEALTH_INTERVAL)
                    continue
                log.warning("Port %s dolu ama saglik ucu yanit vermiyor; bekleniyor.", PORT)
                time.sleep(HEALTH_INTERVAL)
                continue
            ensure_database()
            proc, failures, started_at = start_streamlit(), 0, time.monotonic()
        time.sleep(HEALTH_INTERVAL)
        if proc.poll() is not None:
            now = time.monotonic()
            crash_times.append(now)
            delay = restart_delay(crash_times, now)
            log.warning("Streamlit kapandi (kod %s); %s sn sonra yeniden baslatilacak.", proc.returncode, delay)
            proc = None
            time.sleep(delay)
            continue
        if healthy():
            failures = 0
        elif time.monotonic() - started_at > STARTUP_GRACE:
            failures += 1
            if failures >= HEALTH_FAILURES_BEFORE_RESTART:
                log.warning("Saglik ucu %s kez yanit vermedi; Streamlit yeniden baslatiliyor.", failures)
                proc.kill()
                proc.wait(timeout=30)
                proc = None


def main() -> int:
    _setup_logging()
    if not _single_instance():
        log.info("Gozcu zaten calisiyor; bu kopya cikiyor.")
        return 0
    log.info("OFM sunucu gozcusu basladi (port %s, world-tick %s sn).", PORT, TICK_SECONDS)
    try:
        supervise()
    except Exception:                                             # gunluge yaz; Gorev Zamanlayici yeniden baslatir
        log.exception("Gozcu beklenmedik hatayla durdu.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
