#!/usr/bin/env python3
"""
台データオンラインからSBJ台番号を取得するスクリプト
"""

from playwright.sync_api import sync_playwright
import re

REMOVE_ADS_SCRIPT = """
() => {
    document.querySelectorAll('#gn_interstitial_outer_area, .yads_ad_item, [id*="google_ads"]').forEach(el => el.remove());
}
"""


def get_sbj_units(hall_id: str, hall_name: str):
    """SBJ台番号を取得"""
    print(f"=" * 70)
    print(f"{hall_name} (hall_id: {hall_id}) SBJ台番号取得")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})

        try:
            # 規約同意ページ
            url = f"https://daidata.goraggio.com/{hall_id}/all_list?ps=S"
            print(f"\nアクセス: {url}")
            page.goto(url, wait_until='load', timeout=60000)
            page.wait_for_timeout(2000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            # 規約同意フォームを送信
            page.evaluate('() => { const form = document.querySelector("form"); if (form) form.submit(); }')
            page.wait_for_timeout(3000)
            page.evaluate(REMOVE_ADS_SCRIPT)

            # 全ページスクロールして全データを取得
            print("ページをスクロールして全データを読み込み中...")
            for _ in range(10):
                page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(500)

            # スクリーンショット
            page.screenshot(path=f'data/raw/daidata_{hall_id}_all_list.png', full_page=True)
            print(f"スクリーンショット保存: data/raw/daidata_{hall_id}_all_list.png")

            # ページテキスト取得
            text = page.inner_text('body')

            # SBJ（スーパーブラックジャック）を探す - 様々な表記に対応
            sbj_patterns = [
                'スーパーブラックジャック',
                'ｽｰﾊﾟｰﾌﾞﾗｯｸｼﾞｬｯｸ',
                'Lｽｰﾊﾟｰﾌﾞﾗｯｸｼﾞｬｯｸ',
                'Lスーパーブラックジャック',
                'ブラックジャック',
                'ﾌﾞﾗｯｸｼﾞｬｯｸ',
            ]

            print(f"\n【SBJ検索】")
            found_sbj = False
            for pattern in sbj_patterns:
                if pattern in text:
                    print(f"  '{pattern}' が見つかりました")
                    found_sbj = True
                    break

            if not found_sbj:
                print("  SBJが見つかりません - 全機種リストを確認")

            # 台番号と機種名のマッピングを取得
            # パターン: 台番号 貸玉 機種名
            # 例: 3001	21.7円スロット	L防振り
            lines = text.split('\n')
            sbj_units = []

            for line in lines:
                # 台番号（3-4桁）で始まる行を探す
                match = re.match(r'^\s*(\d{3,4})\s+[\d.]+円スロット\s+(.+?)(?:\s+\d|$)', line)
                if match:
                    unit_num = match.group(1)
                    machine_name = match.group(2).strip()

                    # SBJかどうかチェック
                    is_sbj = False
                    for pattern in sbj_patterns:
                        if pattern in machine_name:
                            is_sbj = True
                            break

                    if is_sbj:
                        sbj_units.append(unit_num)
                        print(f"  SBJ台発見: {unit_num} - {machine_name}")

            # 全機種リストを表示（デバッグ用）
            if not sbj_units:
                print("\n【全機種リスト（一部）】")
                machine_set = set()
                for line in lines:
                    match = re.match(r'^\s*(\d{3,4})\s+[\d.]+円スロット\s+(.+?)(?:\s+\d|$)', line)
                    if match:
                        machine_name = match.group(2).strip()
                        machine_set.add(machine_name)

                for m in sorted(machine_set)[:30]:
                    print(f"  - {m}")

                print(f"\n  合計 {len(machine_set)} 機種")

            # 結果を表示
            print(f"\n{'=' * 70}")
            print(f"結果: {hall_name} SBJ台番号")
            print(f"{'=' * 70}")
            if sbj_units:
                # 重複除去・ソート
                sbj_units = sorted(set(sbj_units), key=lambda x: int(x))
                print(f"台数: {len(sbj_units)}台")
                print(f"台番号: {sbj_units}")
                print(f"\nPython形式:")
                print(f"'units': {sbj_units},")
            else:
                print("SBJ台が見つかりませんでした")
                print("この店舗にはSBJが設置されていない可能性があります")

            return sbj_units

        except Exception as e:
            print(f"エラー: {e}")
            import traceback
            traceback.print_exc()
            return []

        finally:
            browser.close()


if __name__ == "__main__":
    # 西武新宿駅前エスパス
    print("\n" + "=" * 80)
    print("1. 西武新宿駅前エスパス")
    print("=" * 80)
    seibu_units = get_sbj_units("100950", "西武新宿駅前エスパス")

    # 秋葉原エスパス駅前
    print("\n" + "=" * 80)
    print("2. 秋葉原エスパス駅前")
    print("=" * 80)
    akiba_units = get_sbj_units("100928", "秋葉原エスパス駅前")

    # 最終結果
    print("\n" + "=" * 80)
    print("最終結果 - config/rankings.py に追加する内容")
    print("=" * 80)

    print("""
    'seibu_shinjuku_espass_sbj': {{
        'name': '西武新宿駅前エスパス',
        'hall_id': '100950',
        'machine': 'sbj',
        'units': {},
        'data_source': 'daidata',
    }},
    'akihabara_espass_sbj': {{
        'name': '秋葉原エスパス駅前',
        'hall_id': '100928',
        'machine': 'sbj',
        'units': {},
        'data_source': 'daidata',
    }},
""".format(seibu_units, akiba_units))
