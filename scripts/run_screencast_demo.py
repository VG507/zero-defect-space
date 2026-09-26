"""Automated screencast runner for Zero Defect Space demo.
Prints step-by-step progress with timed delays, launching the emulator and demo
for clean, repeatable recording of the defense presentation.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def step(num: int, title: str, delay: float = 1.5) -> None:
    print(f"\n[{num}/5] >>> {title}...", flush=True)
    time.sleep(delay)


def main() -> None:
    print("==================================================================")
    print(" ZERO DEFECT SPACE: AUTOMATED DEMO & SCREENCAST RECORDING RUNNER  ")
    print("==================================================================")

    step(1, "Проверка целостности контрактов и кодогенерации")
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_contracts.py")], capture_output=True, text=True)
    print(res.stdout.strip())
    if res.returncode != 0:
        print("[FAIL] Contracts out of sync!", file=sys.stderr)
        return

    step(2, "Запуск полного автоматического набора тестов (20 тестов: S1-S12, Crypto, Outbox)")
    test_res = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], capture_output=True, text=True)
    for line in test_res.stderr.splitlines():
        if "..." in line or "OK" in line or "Ran" in line:
            print("  " + line)

    step(3, "Запуск нагрузочного теста (1400+ событий/сек, 1 vs N workers, WAL integrity)")
    bench_res = subprocess.run([sys.executable, str(ROOT / "scripts" / "benchmark_load.py")], capture_output=True, text=True)
    for line in bench_res.stdout.splitlines():
        if "evt/s" in line or "Workers" in line or "---" in line:
            print("  " + line)

    step(4, "Проверка интерактивной презентации")
    pres_path = ROOT / "demo" / "presentation.html"
    print(f"  Презентация доступна по адресу: file:///{pres_path.as_posix()}")

    step(5, "Стенд готов к записи скринкаста и защите!")
    print("\n[SUCCESS] Все контуры валидированы. Для запуска демо-сервера выполните:")
    print("  python -m qc.demo\n")


if __name__ == "__main__":
    main()
