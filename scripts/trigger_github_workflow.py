#!/usr/bin/env python3
"""
GitHub Actionsワークフローをトリガーするスクリプト
PythonAnywhereのスケジュールタスクから実行
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

# 設定
GITHUB_TOKEN = os.environ.get('GITHUB_PAT')  # Personal Access Token
REPO_OWNER = 'kinoko-cloud'
REPO_NAME = 'slot'

JST = timezone(timedelta(hours=9))

def log(msg):
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] {msg}")

def trigger_workflow(workflow_name):
    """ワークフローをトリガー"""
    if not GITHUB_TOKEN:
        log("ERROR: GITHUB_PAT environment variable not set")
        return False
    
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{workflow_name}/dispatches"
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
    }
    data = {'ref': 'main'}
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 204:
            log(f"✅ Triggered {workflow_name}")
            return True
        else:
            log(f"❌ Failed to trigger {workflow_name}: {response.status_code} {response.text}")
            return False
    except Exception as e:
        log(f"❌ Error triggering {workflow_name}: {e}")
        return False

def check_data_freshness():
    """データの新鮮さをチェック"""
    try:
        # GitHubからavailability.jsonを直接取得
        url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/data/availability.json"
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            log(f"Failed to fetch availability.json: {response.status_code}")
            return None
        
        data = response.json()
        fetched_at = data.get('fetched_at', '')
        if fetched_at:
            fetched_time = datetime.fromisoformat(fetched_at)
            now = datetime.now(JST)
            age_minutes = (now - fetched_time).total_seconds() / 60
            return age_minutes
    except Exception as e:
        log(f"Error checking data: {e}")
    return None

def main():
    log("=== GitHub Workflow Trigger ===")
    
    now = datetime.now(JST)
    hour = now.hour
    
    # 営業時間外（9時前、23時以降）はスキップ
    if hour < 9 or hour >= 23:
        log(f"Outside business hours ({hour}:00), skipping")
        return
    
    # データの新鮮さをチェック
    age_minutes = check_data_freshness()
    if age_minutes is not None:
        log(f"Data age: {age_minutes:.0f} minutes")
        
        # 90分以上古い場合はFetch Availabilityをトリガー
        if age_minutes > 90:
            log("Data is stale, triggering fetch...")
            trigger_workflow('fetch-availability.yml')
            return
    
    # Deploy Static Siteをトリガー（データが新しい場合でも静的サイト生成）
    log("Triggering deploy static...")
    trigger_workflow('deploy-static.yml')

if __name__ == '__main__':
    main()
