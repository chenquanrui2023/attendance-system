"""PythonAnywhere WSGI 入口"""
import sys
import os

# 项目目录
path = os.path.dirname(os.path.abspath(__file__))
if path not in sys.path:
    sys.path.insert(0, path)

# 切换到项目目录（SQLite 文件会创建在这里）
os.chdir(path)

from app import app as application
