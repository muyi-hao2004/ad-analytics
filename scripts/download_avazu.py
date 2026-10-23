"""下载 Avazu CTF 数据集，解压并探查字段。

数据源：HuggingFace `reczoo/Avazu_x1`（源自 Kaggle Avazu CTR Prediction）
规模  ：40,428,967 行 / 压缩包约 595 MB
内容  ：约 10 天的移动广告点击日志，含 click 标签 + 时间 + site/app/device 等维度
账号  ：不需要 Kaggle 账号
端点  ：huggingface.co 国内直连不通，脚本会自动切 hf-mirror.com

用法：
    ./.venv-spark/Scripts/python.exe scripts/download_avazu.py
    ./.venv-spark/Scripts/python.exe scripts/download_avazu.py --dir D:/data/avazu
    ./.venv-spark/Scripts/python.exe scripts/download_avazu.py --inspect-only   # 已下过，只看

**建表前必须跑一遍这个脚本**，用打印出来的真实列名写 schema，不要凭记忆。
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import zipfile


def _pick_endpoint() -> str:
    """选 HuggingFace 端点。

    注意：**不能用 TCP 连接来探测**。huggingface.co 的 443 端口在国内
    经常能 TCP 握手成功（所以 socket.create_connection 不报错），
    但真正发 HTTPS 请求时被丢包 → 表现为 ConnectTimeout。
    必须发一次真实的 HTTPS 请求，能拿到 200 才算通。

    系统里若配了 http(s)_proxy，访问 hf-mirror 会返回 502 Bad Gateway，
    所以探测和下载都绕过代理（镜像站本来就是国内直连）。
    """
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        if os.environ.pop(k, None):
            print(f"  已临时清除代理变量 {k}（访问 HuggingFace 走直连）")

    explicit = os.environ.get("HF_ENDPOINT")
    if explicit:
        print(f"使用已设置的 HF_ENDPOINT: {explicit}")
        return explicit

    import urllib.request

    probe = "/api/datasets/reczoo/Avazu_x1"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for ep in ("https://hf-mirror.com", "https://huggingface.co"):
        try:
            req = urllib.request.Request(ep + probe, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=8) as r:
                if r.status == 200:
                    print(f"HuggingFace 端点: {ep}")
                    return ep
        except Exception as exc:  # noqa: BLE001
            print(f"  {ep} 不可用（{type(exc).__name__}）")

    print("两个端点都探测失败，仍按 hf-mirror 尝试")
    return "https://hf-mirror.com"


def _http_download(url: str, dest: str) -> None:
    """用 urllib 直接下文件，支持断点续传和进度打印。

    为什么不用 huggingface_hub.snapshot_download：
    它在 **import 时** 就把 HF_ENDPOINT 读进 constants.ENDPOINT，
    之后再设环境变量是无效的 → 会绕过镜像去连 huggingface.co → ConnectTimeout。
    而且这个仓库只有一个 zip，没必要引入快照逻辑。
    """
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    existing = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        print(f"  发现已有 {existing/1024**2:.1f} MB，尝试续传")

    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=60) as r:
        total = r.headers.get("Content-Length")
        total = int(total) + existing if total else None
        mode = "ab" if existing and r.status == 206 else "wb"
        if mode == "wb":
            existing = 0
        done = existing
        chunk = 1024 * 256
        last_pct = -1
        with open(dest, mode) as fh:
            while True:
                buf = r.read(chunk)
                if not buf:
                    break
                fh.write(buf)
                done += len(buf)
                if total:
                    pct = int(done * 100 / total)
                    if pct >= last_pct + 5:
                        last_pct = pct
                        print(f"  {pct:>3}%  {done/1024**2:>7.1f} / {total/1024**2:.1f} MB")
    print(f"  下载完成: {dest}  ({os.path.getsize(dest)/1024**2:.1f} MB)")


def download(target_dir: str) -> str:
    """下载数据集 zip 到 target_dir。仓库里只有 Avazu_x1.zip（约 595 MB）。"""
    os.makedirs(target_dir, exist_ok=True)
    endpoint = _pick_endpoint()

    dest = os.path.join(target_dir, "Avazu_x1.zip")
    if os.path.exists(dest) and os.path.getsize(dest) > 500 * 1024**2:
        print(f"已存在且大小正常，跳过下载: {dest}")
        return target_dir

    url = f"{endpoint}/datasets/reczoo/Avazu_x1/resolve/main/Avazu_x1.zip"
    print(f"开始下载（约 595 MB，视网速 3~15 分钟）\n  {url}")
    _http_download(url, dest)
    return target_dir


def unzip_all(target_dir: str) -> None:
    """把所有 zip 解压到 target_dir/raw/。已解压过就跳过。"""
    zips = glob.glob(os.path.join(target_dir, "**", "*.zip"), recursive=True)
    if not zips:
        return

    raw_dir = os.path.join(target_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for z in zips:
        marker = os.path.join(raw_dir, f".unzipped_{os.path.basename(z)}")
        if os.path.exists(marker):
            print(f"已解压过，跳过: {os.path.basename(z)}")
            continue
        size_gb = os.path.getsize(z) / 1024**3
        print(f"解压 {os.path.basename(z)}（{size_gb:.2f} GB）→ {raw_dir} ...")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(raw_dir)
        open(marker, "w", encoding="utf-8").close()
    print("解压完成")


def inspect(target_dir: str, n: int = 5) -> None:
    """打印每个 csv 的列名与前 n 行，用来确定建表 schema。"""
    raw_dir = os.path.join(target_dir, "raw")
    search_root = raw_dir if os.path.isdir(raw_dir) else target_dir
    files = sorted(glob.glob(os.path.join(search_root, "**", "*.csv"), recursive=True))

    if not files:
        print("\n没有 csv。目录现状：")
        for p in sorted(glob.glob(os.path.join(target_dir, "**", "*"), recursive=True)):
            if os.path.isfile(p):
                print(f"  {os.path.relpath(p, target_dir):<40} {os.path.getsize(p)/1024**2:>9.1f} MB")
        print("\n如果主文件是 .parquet，直接用 Spark 读，字段自带：")
        print("  spark.read.parquet('路径').printSchema()")
        return

    print("\n找到的 csv：")
    for f in files:
        size_mb = os.path.getsize(f) / 1024**2
        print(f"  {os.path.basename(f):<20} {size_mb:>9.1f} MB")

    if len(files) > 1:
        print(
            "\n注意：这个数据集已经预先切好了 train / valid / test。"
            "\n      **test 那份必须留出来，绝对不能进训练集**（否则就是 test set leakage）。"
            "\n      落 ODS 时建议加一个 data_split 字段把来源标清楚，而不是合并成一张表。"
        )

    for f in files:
        name = os.path.basename(f)
        with open(f, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                print(f"\n{name}: 空文件")
                continue
            print(f"\n===== {name} =====")
            print(f"列数: {len(header)}")
            for i, col in enumerate(header):
                print(f"  [{i:>2}] {col}")
            print(f"前 {n} 行（只显示前 8 列）:")
            for _ in range(n):
                try:
                    row = next(reader)
                except StopIteration:
                    break
                print("  " + " | ".join(row[:8]) + (" ..." if len(row) > 8 else ""))

    print("\n下一步：按上面的真实列名写 ODS 建表语句，不要凭记忆。")
    print("      注意 hour 字段是 yymmddHH 格式（如 14102100），不是时间戳，里面能切出 dt。")


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 Avazu 数据集并探查字段")
    ap.add_argument("--dir", default="D:/data/avazu", help="本地存放目录")
    ap.add_argument("--inspect-only", action="store_true", help="只探查，不下载")
    ap.add_argument("--no-unzip", action="store_true", help="不自动解压")
    args = ap.parse_args()

    if not args.inspect_only:
        download(args.dir)
        if not args.no_unzip:
            unzip_all(args.dir)
    inspect(args.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
