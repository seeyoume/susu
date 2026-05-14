"""管理工具 - 生成卡密 / 列出 / 禁用"""
import argparse
import json
import sys
import urllib.request


def _req(server, path, token, method="GET", body=None):
    url = server.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Admin-Token": token},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def cmd_create(args):
    payload = {"plan": args.plan, "count": args.count, "note": args.note}
    if args.plan == "custom":
        if not args.seconds:
            print("✗ --plan custom 必须配合 --seconds 使用"); sys.exit(1)
        payload["custom_seconds"] = args.seconds
    r = _req(args.server, "/admin/create", args.token, "POST", payload)
    if not r.get("ok"):
        print("✗ 失败:", r.get("msg")); sys.exit(1)
    ds = r.get("duration_seconds", 0)
    if ds < 3600:
        dur_label = f"{ds//60} 分钟"
    elif ds < 86400:
        dur_label = f"{ds//3600} 小时"
    else:
        dur_label = f"{ds//86400} 天"
    print(f"✓ 已生成 {len(r['keys'])} 张 {args.plan} 卡密 (时长 {dur_label}):")
    print()
    for k in r["keys"]:
        print(f"  {k}")
    print()


def cmd_list(args):
    path = "/admin/list"
    if args.status:
        path += f"?status={args.status}"
    r = _req(args.server, path, args.token)
    if not r.get("ok"):
        print("✗"); sys.exit(1)
    print(f"共 {r['count']} 条:")
    print(f"{'卡密':<22} {'套餐':<10} {'状态':<10} {'机器ID':<34} {'到期'}")
    print("-" * 110)
    for k in r["keys"]:
        from datetime import datetime
        exp = datetime.fromtimestamp(k["expires_at"]).strftime("%Y-%m-%d") if k["expires_at"] else "—"
        print(f"{k['key']:<22} {k['plan']:<10} {k['status']:<10} {(k['machine_id'] or '—'):<34} {exp}")


def cmd_revoke(args):
    r = _req(args.server, "/admin/revoke", args.token, "POST", {"key": args.key})
    print("✓ 已禁用" if r.get("ok") else f"✗ {r.get('msg')}")


def cmd_stats(args):
    r = _req(args.server, "/admin/stats", args.token)
    if not r.get("ok"):
        print("✗"); return
    print(f"总数: {r['total']}  已激活: {r['used']}  生效中: {r['active']}  未使用: {r['unused']}  已禁用: {r['revoked']}")


def main():
    p = argparse.ArgumentParser(description="绮绮采集器 卡密管理工具")
    p.add_argument("--server", required=True, help="例如 http://你的IP:5000")
    p.add_argument("--token", required=True, help="ADMIN_TOKEN")
    sp = p.add_subparsers(dest="cmd", required=True)

    pc = sp.add_parser("create", help="生成卡密")
    pc.add_argument("--plan", choices=[
        "trial_5min", "trial_15min", "trial_30min",
        "trial_1h", "trial_3h", "trial_6h",
        "day", "week", "month", "quarter", "year",
        "custom",
    ], required=True)
    pc.add_argument("--count", type=int, default=1)
    pc.add_argument("--note", default="")
    pc.add_argument("--seconds", type=int, default=0,
                    help="自定义时长（秒）；--plan custom 时必填")
    pc.set_defaults(func=cmd_create)

    pl = sp.add_parser("list", help="列出卡密")
    pl.add_argument("--status", choices=["unused", "used", "revoked"], default="")
    pl.set_defaults(func=cmd_list)

    pr = sp.add_parser("revoke", help="禁用卡密")
    pr.add_argument("--key", required=True)
    pr.set_defaults(func=cmd_revoke)

    ps = sp.add_parser("stats", help="统计")
    ps.set_defaults(func=cmd_stats)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
