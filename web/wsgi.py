"""
PythonAnywhere用 WSGIエントリーポイント
"""
import sys
import os

# プロジェクトパスを追加
project_home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from web.app import app as application
