#!/usr/bin/env python3
"""
WhatsApp通知スクリプト（clawdbot経由）

使い方:
  python3 scripts/notify.py --message "テストメッセージ"
  python3 scripts/notify.py --message "テスト" --target +819030684797
  echo "パイプ入力" | python3 scripts/notify.py
"""
import argparse
import subprocess
import sys

DEFAULT_TARGET = "+819030684797"
DEFAULT_CHANNEL = "whatsapp"


def send(message: str, target: str = DEFAULT_TARGET, channel: str = DEFAULT_CHANNEL) -> bool:
    """clawdbot経由でメッセージを送信"""
    if not message.strip():
        print("⚠️  メッセージが空です", file=sys.stderr)
        return False

    cmd = [
        "clawdbot", "message", "send",
        "--channel", channel,
        "--target", target,
        "--message", message,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(result.stdout.strip())
            return True
        else:
            print(f"❌ 送信失敗: {result.stderr.strip()}", file=sys.stderr)
            return False
    except FileNotFoundError:
        print("❌ clawdbotが見つかりません", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("❌ タイムアウト（30秒）", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="WhatsApp通知（clawdbot経由）")
    parser.add_argument("--message", "-m", type=str, help="送信メッセージ")
    parser.add_argument("--target", "-t", type=str, default=DEFAULT_TARGET, help="送信先（E.164形式）")
    parser.add_argument("--channel", type=str, default=DEFAULT_CHANNEL, help="チャンネル")
    args = parser.parse_args()

    # メッセージ取得: 引数 > stdin
    if args.message:
        message = args.message
    elif not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    else:
        print("❌ --message または stdin でメッセージを指定してください", file=sys.stderr)
        sys.exit(1)

    ok = send(message, target=args.target, channel=args.channel)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
