"""下载 Criteo 归因数据集（Criteo Attribution Modeling for Bidding Dataset）。

为什么用这个而不是 Avazu：
    Avazu_x1 被 reczoo 预处理过 —— 列名变成 feat_1..feat_22，业务含义被抹掉，
    而且**没有时间字段**，行也是随机打乱的。没有时间就做不了 dt 分区、做不了增量、
    做不了幂等重跑，而这几个正是这次练习的核心。
    Criteo 这份原始数据带 timestamp / campaign / cost / click / conversion，
    按时间顺序排列，30 天，正好能算 CPA / CTR / ROI，跟你项目口径直接对齐。

数据源：https://huggingface.co/datasets/criteo/criteo-attribution-dataset
规模  ：约 1,650 万行 / 622 MB（gz 压缩）/ 30 天
许可  ：Criteo Research 发布，随论文公开，用于研究

用法：
    ./.venv-spark/Scripts/python.exe scripts/download_criteo.py
    ./.venv-spark/Scripts/python.exe scripts/download_criteo.py --inspect-only
"""

from __future__ import annotations

import argparse
import gzip
import os
import urllib.request

DATASET = "criteo/criteo-attribution-dataset"
FILENAME = "criteo_attribution_dataset.tsv.gz"
EXPECTED_MB = 622


def _pick_endpoint() -> str:
    """选 HuggingFace 端点。

    **不能用 TCP 探测**：huggingface.co 的 443 在国内常常能握手成功，
    但真的发 HTTPS 请求时被丢包。必须发一次真实请求、拿到 200 才算通。
    另外系统代理访问 hf-mirror 会 502，这里直接绕过代理。
    """
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        if os.environ.pop(k, None):
            print(f"  已临时清除代理变量 {k}（直连更快）")

    explicit = os.environ.get("HF_ENDPOINT")
    if explicit:
        print(f"使用已设置的 HF_ENDPOINT: {explicit}")
        return explicit

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for ep in ("https://hf-mirror.com", "https://huggingface.co"):
        try:
            req = urllib.request.Request(
                f"{ep}/api/datasets/{DATASET}", headers={"User-Agent": "Mozilla/5.0"}
            )
            with opener.open(req, timeout=8) as r:
                if r.status == 200:
                    print(f"HuggingFace 端点: {ep}")
                    return ep
        except Exception as exc:  # noqa: BLE001
            print(f"  {ep} 不可用（{type(exc).__name__}）")

    print("探测都失败，仍按 hf-mirror 尝试")
    return "https://hf-mirror.com"


def _download(url: str, dest: str) -> None:
    """流式下载，带进度与断点续传。"""
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
        done, last = existing, -1
        with open(dest, mode) as fh:
            while True:
                buf = r.read(1024 * 512)
                if not buf:
                    break
                fh.write(buf)
                done += len(buf)
                if total:
                    pct = int(done * 100 / total)
                    if pct >= last + 5:
                        last = pct
                        print(f"  {pct:>3}%  {done/1024**2:>7.1f} / {total/1024**2:.1f} MB")
    print(f"  下载完成: {dest}  ({os.path.getsize(dest)/1024**2:.1f} MB)")


def gunzip(src: str, dest: str) -> tuple[str, int]:
    """解压并顺便数行数（反正要读一遍，不额外花时间）。"""
    if os.path.exists(dest) and os.path.getsize(dest) > 100 * 1024**2:
        print(f"已解压过，跳过: {dest} ({os.path.getsize(dest)/1024**2:.0f} MB)")
        return dest, 0

    print(f"解压 {os.path.basename(src)} → {os.path.basename(dest)} ...")
    lines = 0
    with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
        while True:
            buf = fin.read(1024 * 1024)
            if not buf:
                break
            fout.write(buf)
            lines += buf.count(b"\n")
            if lines and lines % 5_000_000 < 1024:
                print(f"  已解压 {lines/1e6:.1f}M 行 / {fout.tell()/1024**2:.0f} MB")
    print(f"解压完成：{lines:,} 行 / {os.path.getsize(dest)/1024**2:.0f} MB")
    return dest, lines


def inspect(tsv: str, n: int = 5) -> None:
    print("\n===== 字段 =====")
    with open(tsv, encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        print(f"列数: {len(header)}")
        for i, col in enumerate(header):
            print(f"  [{i:>2}] {col}")

        print(f"\n前 {n} 行:")
        for _ in range(n):
            line = fh.readline()
            if not line:
                break
            print("  " + " | ".join(line.rstrip("\n").split("\t")))

    print(
        "\n关键字段用途（已按实际列名校正）：\n"
        "  timestamp             事件时间，**相对秒**（从 0 开始，不是 Unix 时间戳）\n"
        "                        → dt = timestamp // 86400，不要用 date -d @ 去转\n"
        "  campaign              广告活动 ID → 你项目里的「渠道」维度，共 675 个\n"
        "  cost                  本次展示的成本（抽象单位，非美元）→ CPC / CPA 的分子\n"
        "  click                 是否点击（0/1）→ CTR\n"
        "  conversion            是否转化（0/1）→ CVR / CPA\n"
        "  cpo                   cost per order，该 campaign 的转化成本口径\n"
        "                        **不是转化金额**，所以这份数据算不了 ROI\n"
        "  attribution           转化是否归因给这次点击\n"
        "  click_pos / click_nb / time_since_last_click   归因窗口相关\n"
        "  cat1~cat9             人群/素材属性（匿名整数编码）\n"
        "\n下一步：跑 scripts/profile_criteo.py 看真实分布，再决定分区粒度。"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 Criteo 归因数据集")
    ap.add_argument("--dir", default="D:/data/criteo", help="本地存放目录")
    ap.add_argument("--inspect-only", action="store_true", help="只探查，不下载")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    gz = os.path.join(args.dir, FILENAME)
    tsv = os.path.join(args.dir, FILENAME.replace(".gz", ""))

    if not args.inspect_only:
        endpoint = _pick_endpoint()
        if os.path.exists(gz) and os.path.getsize(gz) > (EXPECTED_MB - 20) * 1024**2:
            print(f"已存在且大小正常，跳过下载: {gz}")
        else:
            url = f"{endpoint}/datasets/{DATASET}/resolve/main/{FILENAME}"
            print(f"开始下载（约 {EXPECTED_MB} MB）\n  {url}")
            _download(url, gz)
        gunzip(gz, tsv)

    if os.path.exists(tsv):
        inspect(tsv)
    else:
        print(f"找不到 {tsv}，先不带 --inspect-only 跑一次")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
